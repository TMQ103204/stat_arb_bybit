from logger_setup import get_logger
from bybit_response import get_result_list, get_ret_code
import time

logger = get_logger("position")


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


# Check for open positions (updated for Bybit V5 API & API Error Handling)
def open_position_confirmation(ticker, max_retries=3, session_priv=None, retry_fn=None):
    _priv = _resolve_session_private(session_priv)
    retry = _resolve_retry(retry_fn)

    for attempt in range(max_retries):
        try:
            position = retry(_priv.get_positions, category="linear", symbol=ticker)
            ret_code = get_ret_code(position)

            if ret_code == 0:
                for item in get_result_list(position):
                    if float(item["size"]) > 0:
                        return True
                return False
            else:
                logger.warning(
                    "API Error checking open position for %s (retCode: %s). Retrying %d/%d...",
                    ticker, ret_code, attempt + 1, max_retries
                )
                time.sleep(2)
                continue

        except Exception as e:
            logger.error(
                "Network error checking open position for %s (attempt %d/%d): %s",
                ticker, attempt + 1, max_retries, e
            )
            time.sleep(2)

    logger.critical(
        "POSITION CHECK EXHAUSTED for %s after %d retries — returning False (uncertain state).",
        ticker, max_retries
    )
    return False


# Check for active positions (updated for Bybit V5 API & API Error Handling)
def active_position_confirmation(ticker, max_retries=3, session_priv=None, retry_fn=None):
    _priv = _resolve_session_private(session_priv)
    retry = _resolve_retry(retry_fn)

    for attempt in range(max_retries):
        try:
            active_order = retry(
                _priv.get_open_orders,
                category="linear",
                symbol=ticker
            )
            ret_code = get_ret_code(active_order)

            if ret_code == 0:
                if len(get_result_list(active_order)) > 0:
                    return True
                return False
            else:
                logger.warning(
                    "API Error checking active orders for %s (retCode: %s). Retrying %d/%d...",
                    ticker, ret_code, attempt + 1, max_retries
                )
                time.sleep(2)
                continue

        except Exception as e:
            logger.error(
                "Network error checking active orders for %s (attempt %d/%d): %s",
                ticker, attempt + 1, max_retries, e
            )
            time.sleep(2)

    logger.critical(
        "ACTIVE ORDER CHECK EXHAUSTED for %s after %d retries — returning False (uncertain state).",
        ticker, max_retries
    )
    return False


# Get open position price and quantity (updated for Bybit V5 API)
def get_open_positions(ticker, direction="Long", session_priv=None, retry_fn=None):
    _priv = _resolve_session_private(session_priv)
    retry = _resolve_retry(retry_fn)

    try:
        position = retry(_priv.get_positions, category="linear", symbol=ticker)
    except Exception as e:
        logger.error("Failed to get_open_positions for %s: %s", ticker, e)
        return (0, 0)

    target_side = "Buy" if direction == "Long" else "Sell"

    if get_ret_code(position) == 0:
        for pos in get_result_list(position):
            if pos["side"] == target_side and float(pos["size"]) > 0:
                order_price = float(pos["avgPrice"])
                order_quantity = float(pos["size"])
                return order_price, order_quantity
    return (0, 0)


# Get active position price and quantity (updated for Bybit V5 API)
def get_active_positions(ticker, session_priv=None, retry_fn=None):
    _priv = _resolve_session_private(session_priv)
    retry = _resolve_retry(retry_fn)

    try:
        active_order = retry(
            _priv.get_open_orders,
            category="linear",
            symbol=ticker
        )
    except Exception as e:
        logger.error("Failed to get_active_positions for %s: %s", ticker, e)
        return (0, 0)

    order_list = get_result_list(active_order)
    if get_ret_code(active_order) == 0:
        if len(order_list) > 0:
            order_price = float(order_list[0]["price"])
            order_quantity = float(order_list[0]["qty"])
            return order_price, order_quantity
    return (0, 0)


# Query existing order (updated for Bybit V5 API)
def query_existing_order(ticker, order_id, direction, session_priv=None, retry_fn=None):
    _priv = _resolve_session_private(session_priv)
    retry = _resolve_retry(retry_fn)

    # First check open orders (unfilled/partially filled)
    try:
        order = retry(
            _priv.get_open_orders,
            category="linear",
            symbol=ticker,
            orderId=order_id
        )
        order_list = get_result_list(order)
        if get_ret_code(order) == 0 and len(order_list) > 0:
            item = order_list[0]
            return float(item["price"]), float(item["qty"]), item["orderStatus"]
    except:
        pass

    # Then check order history (filled/cancelled/rejected)
    try:
        order = retry(
            _priv.get_order_history,
            category="linear",
            symbol=ticker,
            orderId=order_id
        )
        order_list = get_result_list(order)
        if get_ret_code(order) == 0 and len(order_list) > 0:
            item = order_list[0]
            return float(item["price"]), float(item["qty"]), item["orderStatus"]
    except:
        pass

    return (0, 0, "")
