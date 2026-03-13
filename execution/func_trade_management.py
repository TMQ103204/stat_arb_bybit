from config_execution_api import signal_positive_ticker
from config_execution_api import signal_negative_ticker
from config_execution_api import signal_trigger_thresh
from config_execution_api import zscore_stop_loss
from config_execution_api import tradeable_capital_usdt
from config_execution_api import limit_order_basis
from config_execution_api import session_private
from func_price_calls import get_ticker_trade_liquidity
from func_get_zscore import get_latest_zscore
from func_execution_calls import initialise_order_execution
from func_close_positions import close_all_positions
from func_order_review import check_order
from logger_setup import get_logger
import time
import concurrent.futures

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

    # Get and save the latest z-score
    latest = get_latest_zscore()
    if latest is None:
        return kill_switch, signal_side
    zscore, signal_sign_positive = latest
    zscore = _to_float(zscore)

    # Switch to hot if meets signal threshold
    # Note: You can add in coint-flag check too if you want extra vigilence
    if abs(zscore) > float(signal_trigger_thresh):

        # Active hot trigger
        hot = True
        logger.info("-- Trade Status HOT --")
        logger.info("-- Placing and Monitoring Existing Trades --")

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

        # Fill targets
        capital_long = tradeable_capital_usdt * 0.5
        capital_short = tradeable_capital_usdt - capital_long
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
        while kill_switch == 0:

            # Place both legs simultaneously to minimise spread between entry prices
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future_long = None
                future_short = None

                if counts_long == 0:
                    future_long = executor.submit(
                        initialise_order_execution, long_ticker, "Long", initial_capital_usdt, force_market_long
                    )
                if counts_short == 0:
                    future_short = executor.submit(
                        initialise_order_execution, short_ticker, "Short", initial_capital_usdt, force_market_short
                    )

                if future_long is not None:
                    order_long_id = future_long.result()
                    counts_long = 1 if order_long_id else 0
                    if counts_long == 1 and not is_retry_long:
                        remaining_capital_long -= initial_capital_usdt
                    is_retry_long = False
                    force_market_long = False

                if future_short is not None:
                    order_short_id = future_short.result()
                    counts_short = 1 if order_short_id else 0
                    if counts_short == 1 and not is_retry_short:
                        remaining_capital_short -= initial_capital_usdt
                    is_retry_short = False
                    force_market_short = False

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
            latest_new = get_latest_zscore()
            if latest_new is None:
                session_private.cancel_all_orders(category="linear", symbol=signal_positive_ticker)
                session_private.cancel_all_orders(category="linear", symbol=signal_negative_ticker)
                kill_switch = 1
                continue
            zscore_new, signal_sign_p_new = latest_new
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
                return kill_switch, signal_side
            # ────────────────────────────────────────────────────────────────────────

            if kill_switch == 0:
                if abs(zscore_new) > float(signal_trigger_thresh) * 0.9 and signal_sign_p_new == signal_sign_positive:

                    # Check long order status
                    if counts_long == 1:
                        order_status_long = check_order(long_ticker, order_long_id, remaining_capital_long, "Long")

                    # Check short order status
                    if counts_short == 1:
                        order_status_short = check_order(short_ticker, order_short_id, remaining_capital_short, "Short")

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

                    # If position filled - place another trade
                    if order_status_long == "Position Filled" and order_status_short == "Position Filled":
                        counts_long = 0
                        counts_short = 0
                        retry_long = 0
                        retry_short = 0

                    # If order cancelled for long - try again (with retry limit)
                    if order_status_long == "Try Again":
                        retry_long += 1
                        logger.warning("Long order retry %d/%d", retry_long, max_retries)
                        if retry_long >= max_retries:
                            logger.error("Max retries reached for long order. Stopping...")
                            session_private.cancel_all_orders(category="linear", symbol=signal_positive_ticker)
                            session_private.cancel_all_orders(category="linear", symbol=signal_negative_ticker)
                            kill_switch = 1
                        else:
                            counts_long = 0
                            is_retry_long = True
                            # Opposite leg already filled — escalate to Market Order to close the leg gap
                            if order_status_short in ("Position Filled", "Trade Complete"):
                                force_market_long = True
                                logger.warning(
                                    "Short leg already filled — forcing Market Order for long retry "
                                    "to eliminate leg gap."
                                )

                    # If order cancelled for short - try again (with retry limit)
                    if order_status_short == "Try Again":
                        retry_short += 1
                        logger.warning("Short order retry %d/%d", retry_short, max_retries)
                        if retry_short >= max_retries:
                            logger.error("Max retries reached for short order. Stopping...")
                            session_private.cancel_all_orders(category="linear", symbol=signal_positive_ticker)
                            session_private.cancel_all_orders(category="linear", symbol=signal_negative_ticker)
                            kill_switch = 1
                        else:
                            counts_short = 0
                            is_retry_short = True
                            # Opposite leg already filled — escalate to Market Order to close the leg gap
                            if order_status_long in ("Position Filled", "Trade Complete"):
                                force_market_short = True
                                logger.warning(
                                    "Long leg already filled — forcing Market Order for short retry "
                                    "to eliminate leg gap."
                                )

                else:
                    # Cancel all active orders
                    session_private.cancel_all_orders(category="linear", symbol=signal_positive_ticker)
                    session_private.cancel_all_orders(category="linear", symbol=signal_negative_ticker)
                    kill_switch = 1

    # Output status
    return kill_switch, signal_side
