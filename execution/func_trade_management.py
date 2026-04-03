from config_execution_api import signal_positive_ticker
from config_execution_api import signal_negative_ticker
from config_execution_api import signal_trigger_thresh
from config_execution_api import zscore_stop_loss
from func_calcultions import get_wallet_equity
from config_execution_api import limit_order_basis
from config_execution_api import session_private
from func_price_calls import get_ticker_trade_liquidity
from func_get_zscore import get_latest_zscore, get_latest_zscore_with_hedge
from func_execution_calls import initialise_order_execution
from func_close_positions import close_all_positions
from func_order_review import check_order
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

# Manage new trade assessment and order placing
def manage_new_trades(kill_switch):

    # Set variables
    order_long_id = ""
    order_short_id = ""
    signal_side = ""
    hot = False
    entry_hedge_ratio = None
    entry_mean = None
    entry_std = None

    # Get and save the latest z-score (with hedge_ratio, mean, std for freezing)
    latest = get_latest_zscore_with_hedge()
    if latest is None:
        return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
    zscore, signal_sign_positive, entry_hedge_ratio, entry_mean, entry_std = latest
    zscore = _to_float(zscore)

    # ── Stop-loss zone guard ──────────────────────────────────────────────────
    # If Z-score is at or above the stop-loss threshold, do NOT open new trades.
    # This prevents the destructive loop: open → stop-loss → re-open → stop-loss.
    if abs(zscore) >= float(zscore_stop_loss):
        logger.warning(
            "Z-score %.4f is in stop-loss zone (>= %.4f). "
            "Skipping new trade entry to prevent re-entry loop.",
            zscore, float(zscore_stop_loss)
        )
        return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
    # ──────────────────────────────────────────────────────────────────────────

    # Switch to hot if meets signal threshold
    # Note: You can add in coint-flag check too if you want extra vigilence
    if abs(zscore) > float(signal_trigger_thresh):

        # Active hot trigger
        hot = True
        logger.info("-- Trade Status HOT --")
        logger.info("-- Placing and Monitoring Existing Trades --")
    else:
        logger.info("Seeking trades... Current Z-Score: %.4f (Threshold: %s)", zscore, signal_trigger_thresh)

    # Place and manage trades
    if hot and kill_switch == 0:

        # Get trades history for liquidity
        avg_liquidity_ticker_p, last_price_p = get_ticker_trade_liquidity(signal_positive_ticker)
        avg_liquidity_ticker_n, last_price_n = get_ticker_trade_liquidity(signal_negative_ticker)

        # Determine long ticker vs short ticker
        if signal_sign_positive:
            long_ticker = signal_positive_ticker
            short_ticker = signal_negative_ticker
            avg_liquidity_long = avg_liquidity_ticker_p
            avg_liquidity_short = avg_liquidity_ticker_n
            last_price_long = last_price_p
            last_price_short = last_price_n
        else:
            long_ticker = signal_negative_ticker
            short_ticker = signal_positive_ticker
            avg_liquidity_long = avg_liquidity_ticker_n
            avg_liquidity_short = avg_liquidity_ticker_p
            last_price_long = last_price_n
            last_price_short = last_price_p

        # Fill targets — use min(user config cap, real wallet balance)
        # User sets tradeable_capital_usdt in config/dashboard as their desired cap.
        # We never exceed the actual wallet balance to avoid rejected orders.
        from config_execution_api import tradeable_capital_usdt
        wallet_info = get_wallet_equity()
        if wallet_info:
            wallet_balance = wallet_info["wallet_balance"]
            available_capital = min(float(tradeable_capital_usdt), wallet_balance)
            logger.info("Capital: config cap=$%.2f | wallet=$%.4f | using=$%.4f",
                        float(tradeable_capital_usdt), wallet_balance, available_capital)
        else:
            # Fallback: use config if wallet API fails
            available_capital = float(tradeable_capital_usdt)
            logger.warning("Wallet API failed, using config capital: $%.2f", available_capital)
        capital_long = available_capital * 0.5
        capital_short = available_capital - capital_long
        initial_fill_target_long_usdt = avg_liquidity_long * last_price_long
        initial_fill_target_short_usdt = avg_liquidity_short * last_price_short
        initial_capital_injection_usdt = min(initial_fill_target_long_usdt, initial_fill_target_short_usdt)

        # Ensure initial cpaital does not exceed limits set in configuration
        if limit_order_basis:
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
        max_retries = 5  # Maximum retry attempts before giving up
        # FORCE_MARKET_AFTER_RETRY (=3) < max_retries (=5) so market kick-in happens before giving up
        while kill_switch == 0:

            # Place both legs simultaneously to minimise spread between entry prices
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future_long = None
                future_short = None

                if counts_long == 0:
                    future_long = executor.submit(
                        initialise_order_execution, long_ticker, "Long", initial_capital_usdt, force_market_long, zscore
                    )
                if counts_short == 0:
                    future_short = executor.submit(
                        initialise_order_execution, short_ticker, "Short", initial_capital_usdt, force_market_short, zscore
                    )

                if future_long is not None:
                    order_long_id = future_long.result()
                    # ── Bug #6: insufficient balance sentinel ──
                    if order_long_id == -1:
                        logger.critical("INSUFFICIENT BALANCE on Long leg. Aborting trade entry.")
                        session_private.cancel_all_orders(category="linear", symbol=signal_positive_ticker)
                        session_private.cancel_all_orders(category="linear", symbol=signal_negative_ticker)
                        time.sleep(3)
                        close_all_positions(kill_switch)
                        kill_switch = 2  # signal main loop to handle auto_trade + circuit breaker
                        return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
                    counts_long = 1 if order_long_id else 0
                    if counts_long == 1 and not is_retry_long:
                        remaining_capital_long -= initial_capital_usdt
                    is_retry_long = False
                    force_market_long = False

                if future_short is not None:
                    order_short_id = future_short.result()
                    # ── Bug #6: insufficient balance sentinel ──
                    if order_short_id == -1:
                        logger.critical("INSUFFICIENT BALANCE on Short leg. Aborting trade entry.")
                        session_private.cancel_all_orders(category="linear", symbol=signal_positive_ticker)
                        session_private.cancel_all_orders(category="linear", symbol=signal_negative_ticker)
                        time.sleep(3)
                        close_all_positions(kill_switch)
                        kill_switch = 2  # signal main loop to handle auto_trade + circuit breaker
                        return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
                    counts_short = 1 if order_short_id else 0
                    if counts_short == 1 and not is_retry_short:
                        remaining_capital_short -= initial_capital_usdt
                    is_retry_short = False
                    force_market_short = False

            # ── Bug #3: Immediate asymmetry detection ─────────────────────────
            # If one leg got an order but the other returned 0, escalate
            # immediately to market order instead of waiting for retries.
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
            # ──────────────────────────────────────────────────────────────────

            # Update signal side
            if zscore > 0:
                signal_side = "positive"
            else:
                signal_side = "negative"

            # Handle kill switch for Market orders
            if not limit_order_basis and counts_long and counts_short:
                kill_switch = 1

            # Allow time to register the limit orders (reduced to minimise price drift between legs)
            time.sleep(0.5)

            # Check limit orders and ensure z_score is still within range
            # Use frozen hedge_ratio + frozen mean/std so z-score is consistent with entry
            latest_new = get_latest_zscore_with_hedge(entry_hedge_ratio, entry_mean, entry_std)
            if latest_new is None:
                session_private.cancel_all_orders(category="linear", symbol=signal_positive_ticker)
                session_private.cancel_all_orders(category="linear", symbol=signal_negative_ticker)
                kill_switch = 1
                continue
            zscore_new, signal_sign_p_new, _, _, _ = latest_new
            zscore_new = _to_float(zscore_new)

            # ── Emergency Z-score stop-loss ─────────────────────────────────────────
            # If the spread has diverged beyond the configured threshold, close all
            # positions immediately at market price to cap the loss.
            if abs(zscore_new) > float(zscore_stop_loss):
                logger.critical(
                    "Z-SCORE BREAKDOWN: %.4f exceeds stop-loss threshold %.4f. "
                    "Cancelling all orders and closing positions at market.",
                    zscore_new, zscore_stop_loss
                )
                session_private.cancel_all_orders(category="linear", symbol=signal_positive_ticker)
                session_private.cancel_all_orders(category="linear", symbol=signal_negative_ticker)
                close_all_positions(kill_switch)
                kill_switch = 1
                return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
            # ────────────────────────────────────────────────────────────────────────

            if kill_switch == 0:
                if abs(zscore_new) > float(signal_trigger_thresh) * 0.9 and signal_sign_p_new == signal_sign_positive:

                    # Check long order status
                    if counts_long == 1:
                        order_status_long = check_order(long_ticker, order_long_id, remaining_capital_long, "Long")
                        # Bug Fix: check_order can return None on API error – treat as still waiting
                        if order_status_long is None:
                            logger.warning("check_order returned None for long %s – treating as Order Active", long_ticker)
                            order_status_long = "Order Active"

                    # Check short order status
                    if counts_short == 1:
                        order_status_short = check_order(short_ticker, order_short_id, remaining_capital_short, "Short")
                        # Bug Fix: check_order can return None on API error – treat as still waiting
                        if order_status_short is None:
                            logger.warning("check_order returned None for short %s – treating as Order Active", short_ticker)
                            order_status_short = "Order Active"

                    logger.info("Long: %s | Short: %s | zscore: %.4f", order_status_long, order_status_short, zscore_new)

                    # Determine if each side is still pending
                    waiting_states = ("Order Active", "Partial Fill")
                    long_waiting = order_status_long in waiting_states
                    short_waiting = order_status_short in waiting_states

                    # If both sides still pending, do nothing
                    if long_waiting and short_waiting:
                        continue

                    # If one side pending and the other doesn't need retry, wait
                    if (long_waiting and order_status_short != "Try Again") or \
                       (short_waiting and order_status_long != "Try Again"):
                        continue

                    # If orders trade complete, do nothing - stop opening trades
                    if order_status_long == "Trade Complete" and order_status_short == "Trade Complete":
                        kill_switch = 1

                    # If both legs filled - stop the entry loop and hold the position
                    if order_status_long == "Position Filled" and order_status_short == "Position Filled":
                        logger.info("Both legs filled. Holding position.")
                        kill_switch = 1

                    # If order cancelled for long - try again (with retry limit)
                    if order_status_long == "Try Again":
                        retry_long += 1
                        logger.warning("Long order retry %d/%d", retry_long, max_retries)
                        if retry_long >= max_retries:
                            logger.error("Max retries reached for long order. Stopping...")
                            session_private.cancel_all_orders(category="linear", symbol=signal_positive_ticker)
                            session_private.cancel_all_orders(category="linear", symbol=signal_negative_ticker)
                            # ── Half-position guard ────────────────────────────────────────────
                            short_filled = order_status_short in ("Position Filled", "Trade Complete")
                            if short_filled:
                                logger.critical(
                                    "HALF-POSITION DETECTED: Short leg filled but Long failed after "
                                    "%d retries. Closing all positions to avoid unhedged exposure.",
                                    max_retries
                                )
                                # BUG FIX #1+#2: Sleep 3s so Bybit API has time to register
                                # the freshly-filled position before we query its size.
                                # Without this, get_positions() returns size=0 and the
                                # close order is silently skipped, leaving a naked leg open.
                                time.sleep(3)
                                close_all_positions(kill_switch)
                                kill_switch = 2  # signal main loop to handle auto_trade + circuit breaker
                                return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
                            else:
                                logger.warning(
                                    "Max retries reached for long order and no position opened. "
                                    "Resetting to seek trades."
                                )
                                kill_switch = 0
                                continue
                            # ──────────────────────────────────────────────────────────────────
                        else:
                            counts_long = 0
                            is_retry_long = True
                            # Escalate to Market Order when:
                            #   a) opposite leg already filled (leg gap risk), OR
                            #   b) retry count has reached the escalation threshold
                            if order_status_short in ("Position Filled", "Trade Complete"):
                                force_market_long = True
                                logger.warning(
                                    "Short leg already filled — forcing Market Order for long retry "
                                    "to eliminate leg gap."
                                )
                            elif retry_long >= FORCE_MARKET_AFTER_RETRY:
                                force_market_long = True
                                logger.warning(
                                    "Long order retry %d/%d — escalating to Market Order "
                                    "(PostOnly kept being rejected).",
                                    retry_long, max_retries
                                )

                    # If order cancelled for short - try again (with retry limit)
                    if order_status_short == "Try Again":
                        retry_short += 1
                        logger.warning("Short order retry %d/%d", retry_short, max_retries)
                        if retry_short >= max_retries:
                            logger.error("Max retries reached for short order. Stopping...")
                            session_private.cancel_all_orders(category="linear", symbol=signal_positive_ticker)
                            session_private.cancel_all_orders(category="linear", symbol=signal_negative_ticker)
                            # ── Half-position guard ────────────────────────────────────────────
                            long_filled = order_status_long in ("Position Filled", "Trade Complete")
                            if long_filled:
                                logger.critical(
                                    "HALF-POSITION DETECTED: Long leg filled but Short failed after "
                                    "%d retries. Closing all positions to avoid unhedged exposure.",
                                    max_retries
                                )
                                # BUG FIX #1+#2: Sleep 3s so Bybit API has time to register
                                # the freshly-filled position before we query its size.
                                # Without this, get_positions() returns size=0 and the
                                # close order is silently skipped, leaving a naked leg open.
                                time.sleep(3)
                                close_all_positions(kill_switch)
                                kill_switch = 2  # signal main loop to handle auto_trade + circuit breaker
                                return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
                            else:
                                logger.warning(
                                    "Max retries reached for short order and no position opened. "
                                    "Resetting to seek trades."
                                )
                                kill_switch = 0
                                continue
                            # ──────────────────────────────────────────────────────────────────
                        else:
                            counts_short = 0
                            is_retry_short = True
                            # Escalate to Market Order when:
                            #   a) opposite leg already filled (leg gap risk), OR
                            #   b) retry count has reached the escalation threshold
                            if order_status_long in ("Position Filled", "Trade Complete"):
                                force_market_short = True
                                logger.warning(
                                    "Long leg already filled — forcing Market Order for short retry "
                                    "to eliminate leg gap."
                                )
                            elif retry_short >= FORCE_MARKET_AFTER_RETRY:
                                force_market_short = True
                                logger.warning(
                                    "Short order retry %d/%d — escalating to Market Order "
                                    "(PostOnly kept being rejected).",
                                    retry_short, max_retries
                                )

                else:
                    # Z-score dropped below threshold – cancel all pending orders
                    logger.warning(
                        "Z-score %.4f dropped below active threshold. Cancelling all orders.",
                        zscore_new
                    )
                    session_private.cancel_all_orders(category="linear", symbol=signal_positive_ticker)
                    session_private.cancel_all_orders(category="linear", symbol=signal_negative_ticker)

                    # ── Half-position guard ────────────────────────────────────────────────
                    # BUG FIX: order_status_long/short may still be "" if z-score dropped
                    # before any check_order() call was made (first monitoring tick).
                    # Query ACTUAL positions on the exchange instead of relying on stale
                    # order_status variables.
                    from func_position_calls import open_position_confirmation
                    time.sleep(1)  # let cancellation register on exchange
                    actual_long_open = open_position_confirmation(long_ticker)
                    actual_short_open = open_position_confirmation(short_ticker)

                    if actual_long_open or actual_short_open:
                        if not (actual_long_open and actual_short_open):
                            # Only one leg filled — unhedged exposure!
                            orphan = long_ticker if actual_long_open else short_ticker
                            logger.critical(
                                "HALF-POSITION DETECTED on z-score exit: %s has position "
                                "but other leg does not. "
                                "Closing all positions to avoid unhedged exposure.",
                                orphan
                            )
                            time.sleep(3)  # let Bybit register the fill before querying size
                            close_all_positions(kill_switch)
                            kill_switch = 2  # signal main loop to handle auto_trade + circuit breaker
                            return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
                        else:
                            # Both legs filled — safe to enter HOLDING
                            logger.info(
                                "Both legs already filled despite z-score dropout. "
                                "Entering HOLDING."
                            )
                            kill_switch = 1
                            return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
                    # ──────────────────────────────────────────────────────────────────────

                    kill_switch = 1

    # Output status
    return kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std
