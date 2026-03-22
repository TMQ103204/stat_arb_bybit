from config_execution_api import session_private, retry_api_call
from logger_setup import get_logger
from bybit_response import get_result_list, get_ret_code
import time

logger = get_logger("position")

# Check for open positions (updated for Bybit V5 API)
def open_position_confirmation(ticker, max_retries=3):
    for attempt in range(max_retries):
        try:
            position = retry_api_call(session_private.get_positions, category="linear", symbol=ticker)
            if get_ret_code(position) == 0:
                for item in get_result_list(position):
                    if float(item["size"]) > 0:
                        return True
            return False  # API call succeeded but no open size — position is not open
        except Exception as e:
            logger.error(
                "Network error checking open position for %s (attempt %d/%d): %s",
                ticker, attempt + 1, max_retries, e
            )
            time.sleep(2)
    # All retries exhausted — assume position is NOT open to avoid phantom locks
    return False


# Check for active positions (updated for Bybit V5 API)
def active_position_confirmation(ticker, max_retries=3):
    for attempt in range(max_retries):
        try:
            active_order = retry_api_call(
                session_private.get_open_orders,
                category="linear",
                symbol=ticker
            )
            if get_ret_code(active_order) == 0:
                if len(get_result_list(active_order)) > 0:
                    return True
            return False  # API call succeeded but no open orders
        except Exception as e:
            logger.error(
                "Network error checking active orders for %s (attempt %d/%d): %s",
                ticker, attempt + 1, max_retries, e
            )
            time.sleep(2)
    # All retries exhausted — assume no active orders
    return False


# Get open position price and quantity (updated for Bybit V5 API)
def get_open_positions(ticker, direction="Long"):

    # Get position
    try:
        position = retry_api_call(session_private.get_positions, category="linear", symbol=ticker)
    except Exception as e:
        logger.error("Failed to get_open_positions for %s: %s", ticker, e)
        return (0, 0)

    # Determine target side
    target_side = "Buy" if direction == "Long" else "Sell"

    # Construct a response
    if get_ret_code(position) == 0:
        for pos in get_result_list(position):
            if pos["side"] == target_side and float(pos["size"]) > 0:
                order_price = float(pos["avgPrice"])
                order_quantity = float(pos["size"])
                return order_price, order_quantity
    return (0, 0)


# Get active position price and quantity (updated for Bybit V5 API)
def get_active_positions(ticker):

    # Get open orders
    try:
        active_order = retry_api_call(
            session_private.get_open_orders,
            category="linear",
            symbol=ticker
        )
    except Exception as e:
        logger.error("Failed to get_active_positions for %s: %s", ticker, e)
        return (0, 0)

    # Construct a response
    order_list = get_result_list(active_order)
    if get_ret_code(active_order) == 0:
        if len(order_list) > 0:
            order_price = float(order_list[0]["price"])
            order_quantity = float(order_list[0]["qty"])
            return order_price, order_quantity
    return (0, 0)


# Query existing order (updated for Bybit V5 API)
def query_existing_order(ticker, order_id, direction):

    # First check open orders (unfilled/partially filled)
    try:
        order = retry_api_call(
            session_private.get_open_orders,
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
        order = retry_api_call(
            session_private.get_order_history,
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
