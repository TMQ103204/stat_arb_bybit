from logger_setup import get_logger
from bybit_response import get_result_list, get_ret_code
import time

logger = get_logger("close_pos")


def _resolve_session_private(session_priv=None):
    if session_priv is not None:
        return session_priv
    from config_execution_api import session_private
    return session_private


def _resolve_retry(retry_fn=None):
    if retry_fn is not None:
        return retry_fn
    from config_execution_api import retry_api_call
    return retry_api_call


# Get position information (updated for Bybit V5 API)
def get_position_info(ticker, max_retries=3, session_priv=None, retry_fn=None):
    _priv = _resolve_session_private(session_priv)
    retry = _resolve_retry(retry_fn)

    side = ""
    size = 0.0

    for attempt in range(max_retries):
        try:
            position_response = retry(_priv.get_positions, category="linear", symbol=ticker)
            if get_ret_code(position_response) == 0:
                for pos in get_result_list(position_response):
                    if float(pos["size"]) > 0:
                        size = float(pos["size"])
                        side = pos["side"]
                        return side, size
                return side, size
            else:
                logger.error("API error checking position %s: %s", ticker, position_response)
        except Exception as e:
            logger.error("Failed to get position info for %s (attempt %d/%d): %s", ticker, attempt + 1, max_retries, e)
            time.sleep(2)

    raise Exception(f"Failed to fetch position for {ticker} after {max_retries} attempts.")


# Place market close order (updated for Bybit V5 API)
def place_market_close_order(ticker, side, size, session_priv=None):
    _priv = _resolve_session_private(session_priv)

    try:
        _priv.place_order(
            category="linear",
            symbol=ticker,
            side=side,
            orderType="Market",
            qty=str(size),
            timeInForce="GTC",
            reduceOnly=True
        )
        logger.info("Closed %s %s qty=%.6f", side, ticker, size)
        return True
    except Exception as e:
        logger.error("Failed to close %s %s: %s", ticker, side, e)
        return False


# Close all positions for both tickers
def close_all_positions(kill_switch, pos_ticker=None, neg_ticker=None,
                        session_priv=None, retry_fn=None):
    if pos_ticker is None:
        from config_execution_api import signal_positive_ticker
        pos_ticker = signal_positive_ticker
    if neg_ticker is None:
        from config_execution_api import signal_negative_ticker
        neg_ticker = signal_negative_ticker

    _priv = _resolve_session_private(session_priv)

    # Cancel all active orders
    try:
        _priv.cancel_all_orders(category="linear", symbol=pos_ticker)
        _priv.cancel_all_orders(category="linear", symbol=neg_ticker)
    except Exception as e:
        logger.error("Failed to cancel orders: %s", e)

    # Removed API settle delay to minimize slippage during position close

    # Get position information
    try:
        side_1, size_1 = get_position_info(pos_ticker, session_priv=session_priv, retry_fn=retry_fn)
        side_2, size_2 = get_position_info(neg_ticker, session_priv=session_priv, retry_fn=retry_fn)
    except Exception as e:
        logger.error("Aborting close_all_positions due to position fetch failure: %s", e)
        return kill_switch

    success = True
    # Close each position using its OWN opposite side
    if size_1 > 0 and side_1:
        close_side_1 = "Sell" if side_1 == "Buy" else "Buy"
        if not place_market_close_order(pos_ticker, close_side_1, size_1, session_priv=session_priv):
            success = False

    if size_2 > 0 and side_2:
        close_side_2 = "Sell" if side_2 == "Buy" else "Buy"
        if not place_market_close_order(neg_ticker, close_side_2, size_2, session_priv=session_priv):
            success = False

    if success:
        return 0
    else:
        logger.warning("Failed to close one or more positions. kill_switch remains %s", kill_switch)
        return kill_switch
