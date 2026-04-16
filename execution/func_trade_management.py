from func_calcultions import get_wallet_equity
from func_price_calls import get_ticker_trade_liquidity
from func_get_zscore import get_latest_zscore, get_latest_zscore_with_hedge
from func_execution_calls import initialise_order_execution
from func_close_positions import close_all_positions
from func_order_review import check_order
from func_position_calls import open_position_confirmation
from logger_setup import get_logger
import time
import concurrent.futures

# Number of retries before escalating a limit order to a market order
# (must be < max_retries so there is at least one market-order attempt)
FORCE_MARKET_AFTER_RETRY = 3

logger = get_logger("trade_mgmt")


def _to_float(value) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _resolve_session_private(session_priv=None):
    if session_priv is not None:
        return session_priv
    from config_execution_api import session_private
    return session_private


# Manage new trade assessment and order placing
# All pair-specific settings are now optional params with fallback to globals.
def manage_new_trades(kill_switch,
                      # ── Pair-specific params (optional for backward compat) ──
                      pos_ticker=None, neg_ticker=None,
                      trigger_thresh=None, stop_loss_z=None,
                      capital=None, lev=None,
                      limit_basis=None, sl_failsafe=None,
                      market_thresh=None, min_profit=None,
                      t1=None, t2=None,
                      z_window=None, tf=None, kl=None,
                      # ── Session params ──
                      session_pub=None, session_priv=None, retry_fn=None):

    # Resolve pair-specific defaults from old config when not provided
    if pos_ticker is None:
        from config_execution_api import signal_positive_ticker
        pos_ticker = signal_positive_ticker
    if neg_ticker is None:
        from config_execution_api import signal_negative_ticker
        neg_ticker = signal_negative_ticker
    if trigger_thresh is None:
        from config_execution_api import signal_trigger_thresh
        trigger_thresh = float(signal_trigger_thresh)
    else:
        trigger_thresh = float(trigger_thresh)
    if stop_loss_z is None:
        from config_execution_api import zscore_stop_loss
        stop_loss_z = float(zscore_stop_loss)
    else:
        stop_loss_z = float(stop_loss_z)
    if limit_basis is None:
        from config_execution_api import limit_order_basis
        limit_basis = limit_order_basis

    _priv = _resolve_session_private(session_priv)

    # Set variables
    order_long_id = ""
    order_short_id = ""
    signal_side = ""
    hot = False
    entry_hedge_ratio = None
    entry_mean = None
    entry_std = None

    # Get and save the latest z-score (with hedge_ratio, mean, std for freezing)
    latest = get_latest_zscore_with_hedge(
        t1=t1, t2=t2, session_pub=session_pub, retry_fn=retry_fn,
        tf=tf, kl=kl, window=z_window
    )
    if latest is None:
        return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
    zscore, signal_sign_positive, entry_hedge_ratio, entry_mean, entry_std = latest
    zscore = _to_float(zscore)

    # ── Stop-loss zone guard ──────────────────────────────────────────────────
    if abs(zscore) >= stop_loss_z:
        logger.warning(
            "Z-score %.4f is in stop-loss zone (>= %.4f). "
            "Skipping new trade entry to prevent re-entry loop.",
            zscore, stop_loss_z
        )
        return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std

    # Switch to hot if meets signal threshold
    if abs(zscore) > trigger_thresh:
        hot = True
        logger.info("-- Trade Status HOT --")
        logger.info("-- Placing and Monitoring Existing Trades --")
    else:
        logger.info("Seeking trades... Current Z-Score: %.4f (Threshold: %.4f)", zscore, trigger_thresh)

    # Place and manage trades
    if hot and kill_switch == 0:

        # Get trades history for liquidity
        avg_liquidity_ticker_p, last_price_p = get_ticker_trade_liquidity(
            pos_ticker, session_pub=session_pub, retry_fn=retry_fn)
        avg_liquidity_ticker_n, last_price_n = get_ticker_trade_liquidity(
            neg_ticker, session_pub=session_pub, retry_fn=retry_fn)

        # Determine long ticker vs short ticker
        if signal_sign_positive:
            long_ticker = pos_ticker
            short_ticker = neg_ticker
            avg_liquidity_long = avg_liquidity_ticker_p
            avg_liquidity_short = avg_liquidity_ticker_n
            last_price_long = last_price_p
            last_price_short = last_price_n
        else:
            long_ticker = neg_ticker
            short_ticker = pos_ticker
            avg_liquidity_long = avg_liquidity_ticker_n
            avg_liquidity_short = avg_liquidity_ticker_p
            last_price_long = last_price_n
            last_price_short = last_price_p

        # Fill targets — use min(user config cap, real wallet balance)
        if capital is None:
            from config_execution_api import tradeable_capital_usdt
            capital = float(tradeable_capital_usdt)

        wallet_info = get_wallet_equity(session_priv=session_priv)
        if wallet_info:
            wallet_balance = wallet_info["wallet_balance"]
            available_capital = min(float(capital), wallet_balance)
            logger.info("Capital: config cap=$%.2f | wallet=$%.4f | using=$%.4f",
                        float(capital), wallet_balance, available_capital)
        else:
            available_capital = float(capital)
            logger.warning("Wallet API failed, using config capital: $%.2f", available_capital)
        capital_long = available_capital * 0.5
        capital_short = available_capital - capital_long
        initial_fill_target_long_usdt = avg_liquidity_long * last_price_long
        initial_fill_target_short_usdt = avg_liquidity_short * last_price_short
        initial_capital_injection_usdt = min(initial_fill_target_long_usdt, initial_fill_target_short_usdt)

        # Ensure initial capital does not exceed limits set in configuration
        if limit_basis:
            if initial_capital_injection_usdt > capital_long:
                initial_capital_usdt = capital_long
            else:
                initial_capital_usdt = initial_capital_injection_usdt
        else:
            initial_capital_usdt = capital_long

        # Set remaining capital
        remaining_capital_long = capital_long
        remaining_capital_short = capital_short

        # Trade until filled or signal is false
        order_status_long = ""
        order_status_short = ""
        counts_long = 0
        counts_short = 0
        retry_long = 0
        retry_short = 0
        is_retry_long = False
        is_retry_short = False
        force_market_long = False
        force_market_short = False
        max_retries = 5
        while kill_switch == 0:

            # Place both legs simultaneously to minimise spread between entry prices
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future_long = None
                future_short = None

                # Common kwargs for order execution
                exec_kwargs = dict(
                    session_pub=session_pub, session_priv=session_priv,
                    limit_basis=limit_basis, sl_failsafe=sl_failsafe,
                    t1=t1, t2=t2,
                    market_thresh=market_thresh, min_profit=min_profit,
                    z_window=z_window
                )

                if counts_long == 0:
                    future_long = executor.submit(
                        initialise_order_execution, long_ticker, "Long",
                        initial_capital_usdt, force_market_long, zscore,
                        **exec_kwargs
                    )
                if counts_short == 0:
                    future_short = executor.submit(
                        initialise_order_execution, short_ticker, "Short",
                        initial_capital_usdt, force_market_short, zscore,
                        **exec_kwargs
                    )

                if future_long is not None:
                    order_long_id = future_long.result()
                    if order_long_id == -1:
                        logger.critical("INSUFFICIENT BALANCE on Long leg. Aborting trade entry.")
                        _priv.cancel_all_orders(category="linear", symbol=pos_ticker)
                        _priv.cancel_all_orders(category="linear", symbol=neg_ticker)
                        time.sleep(3)
                        close_all_positions(kill_switch, pos_ticker=pos_ticker, neg_ticker=neg_ticker,
                                            session_priv=session_priv, retry_fn=retry_fn)
                        kill_switch = 2
                        return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
                    counts_long = 1 if order_long_id else 0
                    if counts_long == 1 and not is_retry_long:
                        remaining_capital_long -= initial_capital_usdt
                    is_retry_long = False
                    force_market_long = False

                if future_short is not None:
                    order_short_id = future_short.result()
                    if order_short_id == -1:
                        logger.critical("INSUFFICIENT BALANCE on Short leg. Aborting trade entry.")
                        _priv.cancel_all_orders(category="linear", symbol=pos_ticker)
                        _priv.cancel_all_orders(category="linear", symbol=neg_ticker)
                        time.sleep(3)
                        close_all_positions(kill_switch, pos_ticker=pos_ticker, neg_ticker=neg_ticker,
                                            session_priv=session_priv, retry_fn=retry_fn)
                        kill_switch = 2
                        return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
                    counts_short = 1 if order_short_id else 0
                    if counts_short == 1 and not is_retry_short:
                        remaining_capital_short -= initial_capital_usdt
                    is_retry_short = False
                    force_market_short = False

            # ── Immediate asymmetry detection ─────────────────────────
            one_placed = bool(order_long_id) != bool(order_short_id)
            if one_placed and (counts_long + counts_short == 1):
                failed_side = "Long" if not order_long_id else "Short"
                logger.critical(
                    "ORDER PLACEMENT ASYMMETRY: %s failed while other succeeded. "
                    "Escalating to market order immediately.", failed_side
                )
                if not order_long_id:
                    force_market_long = True
                else:
                    force_market_short = True

            # Update signal side
            if zscore > 0:
                signal_side = "positive"
            else:
                signal_side = "negative"

            # Handle kill switch for Market orders
            if not limit_basis and counts_long and counts_short:
                kill_switch = 1

            # Allow time to register the limit orders
            time.sleep(0.5)

            # Check limit orders and ensure z_score is still within range
            latest_new = get_latest_zscore_with_hedge(
                entry_hedge_ratio, entry_mean, entry_std,
                t1=t1, t2=t2, session_pub=session_pub, retry_fn=retry_fn,
                tf=tf, kl=kl, window=z_window
            )
            if latest_new is None:
                _priv.cancel_all_orders(category="linear", symbol=pos_ticker)
                _priv.cancel_all_orders(category="linear", symbol=neg_ticker)
                kill_switch = 1
                continue
            zscore_new, signal_sign_p_new, _, _, _ = latest_new
            zscore_new = _to_float(zscore_new)

            # ── Emergency Z-score stop-loss ──────────────────────────────────
            if abs(zscore_new) > stop_loss_z:
                logger.critical(
                    "Z-SCORE BREAKDOWN: %.4f exceeds stop-loss threshold %.4f. "
                    "Cancelling all orders and closing positions at market.",
                    zscore_new, stop_loss_z
                )
                _priv.cancel_all_orders(category="linear", symbol=pos_ticker)
                _priv.cancel_all_orders(category="linear", symbol=neg_ticker)
                close_all_positions(kill_switch, pos_ticker=pos_ticker, neg_ticker=neg_ticker,
                                    session_priv=session_priv, retry_fn=retry_fn)
                kill_switch = 1
                return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std

            if kill_switch == 0:
                if abs(zscore_new) > trigger_thresh * 0.9 and signal_sign_p_new == signal_sign_positive:

                    # Check long order status
                    if counts_long == 1:
                        order_status_long = check_order(
                            long_ticker, order_long_id, remaining_capital_long, "Long",
                            session_pub=session_pub, session_priv=session_priv, retry_fn=retry_fn)
                        if order_status_long is None:
                            logger.warning("check_order returned None for long %s – treating as Order Active", long_ticker)
                            order_status_long = "Order Active"

                    # Check short order status
                    if counts_short == 1:
                        order_status_short = check_order(
                            short_ticker, order_short_id, remaining_capital_short, "Short",
                            session_pub=session_pub, session_priv=session_priv, retry_fn=retry_fn)
                        if order_status_short is None:
                            logger.warning("check_order returned None for short %s – treating as Order Active", short_ticker)
                            order_status_short = "Order Active"

                    logger.info("Long: %s | Short: %s | zscore: %.4f", order_status_long, order_status_short, zscore_new)

                    # Determine if each side is still pending
                    waiting_states = ("Order Active", "Partial Fill")
                    long_waiting = order_status_long in waiting_states
                    short_waiting = order_status_short in waiting_states

                    if long_waiting and short_waiting:
                        continue

                    if (long_waiting and order_status_short != "Try Again") or \
                       (short_waiting and order_status_long != "Try Again"):
                        continue

                    if order_status_long == "Trade Complete" and order_status_short == "Trade Complete":
                        kill_switch = 1

                    if order_status_long == "Position Filled" and order_status_short == "Position Filled":
                        logger.info("Both legs filled. Holding position.")
                        kill_switch = 1

                    # If order cancelled for long - try again (with retry limit)
                    if order_status_long == "Try Again":
                        retry_long += 1
                        logger.warning("Long order retry %d/%d", retry_long, max_retries)
                        if retry_long >= max_retries:
                            logger.error("Max retries reached for long order. Stopping...")
                            _priv.cancel_all_orders(category="linear", symbol=pos_ticker)
                            _priv.cancel_all_orders(category="linear", symbol=neg_ticker)
                            short_filled = order_status_short in ("Position Filled", "Trade Complete")
                            if short_filled:
                                logger.critical(
                                    "HALF-POSITION DETECTED: Short leg filled but Long failed after "
                                    "%d retries. Closing all positions.", max_retries
                                )
                                time.sleep(3)
                                close_all_positions(kill_switch, pos_ticker=pos_ticker, neg_ticker=neg_ticker,
                                                    session_priv=session_priv, retry_fn=retry_fn)
                                kill_switch = 2
                                return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
                            else:
                                logger.warning("Max retries reached for long order and no position opened. Resetting.")
                                kill_switch = 0
                                continue
                        else:
                            counts_long = 0
                            is_retry_long = True
                            if order_status_short in ("Position Filled", "Trade Complete"):
                                force_market_long = True
                                logger.warning("Short leg already filled — forcing Market for long retry.")
                            elif retry_long >= FORCE_MARKET_AFTER_RETRY:
                                force_market_long = True
                                logger.warning("Long order retry %d/%d — escalating to Market.", retry_long, max_retries)

                    # If order cancelled for short - try again (with retry limit)
                    if order_status_short == "Try Again":
                        retry_short += 1
                        logger.warning("Short order retry %d/%d", retry_short, max_retries)
                        if retry_short >= max_retries:
                            logger.error("Max retries reached for short order. Stopping...")
                            _priv.cancel_all_orders(category="linear", symbol=pos_ticker)
                            _priv.cancel_all_orders(category="linear", symbol=neg_ticker)
                            long_filled = order_status_long in ("Position Filled", "Trade Complete")
                            if long_filled:
                                logger.critical(
                                    "HALF-POSITION DETECTED: Long leg filled but Short failed after "
                                    "%d retries. Closing all positions.", max_retries
                                )
                                time.sleep(3)
                                close_all_positions(kill_switch, pos_ticker=pos_ticker, neg_ticker=neg_ticker,
                                                    session_priv=session_priv, retry_fn=retry_fn)
                                kill_switch = 2
                                return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
                            else:
                                logger.warning("Max retries reached for short order and no position opened. Resetting.")
                                kill_switch = 0
                                continue
                        else:
                            counts_short = 0
                            is_retry_short = True
                            if order_status_long in ("Position Filled", "Trade Complete"):
                                force_market_short = True
                                logger.warning("Long leg already filled — forcing Market for short retry.")
                            elif retry_short >= FORCE_MARKET_AFTER_RETRY:
                                force_market_short = True
                                logger.warning("Short order retry %d/%d — escalating to Market.", retry_short, max_retries)

                else:
                    # Z-score dropped below threshold – cancel all pending orders
                    logger.warning(
                        "Z-score %.4f dropped below active threshold. Cancelling all orders.",
                        zscore_new
                    )
                    _priv.cancel_all_orders(category="linear", symbol=pos_ticker)
                    _priv.cancel_all_orders(category="linear", symbol=neg_ticker)

                    # ── Half-position guard ──────────────────────────────────────
                    time.sleep(1)
                    actual_long_open = open_position_confirmation(
                        long_ticker, session_priv=session_priv, retry_fn=retry_fn)
                    actual_short_open = open_position_confirmation(
                        short_ticker, session_priv=session_priv, retry_fn=retry_fn)

                    if actual_long_open or actual_short_open:
                        if not (actual_long_open and actual_short_open):
                            orphan = long_ticker if actual_long_open else short_ticker
                            logger.critical(
                                "HALF-POSITION DETECTED on z-score exit: %s has position "
                                "but other leg does not. Closing all.", orphan
                            )
                            time.sleep(3)
                            close_all_positions(kill_switch, pos_ticker=pos_ticker, neg_ticker=neg_ticker,
                                                session_priv=session_priv, retry_fn=retry_fn)
                            kill_switch = 2
                            return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
                        else:
                            logger.info("Both legs already filled despite z-score dropout. Entering HOLDING.")
                            kill_switch = 1
                            return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std

                    kill_switch = 1

    # Output status
    return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
