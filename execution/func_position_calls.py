from config_execution_api import session_private
from logger_setup import get_logger

logger = get_logger("position")

# Check for open positions (updated for Bybit V5 API)
def open_position_confirmation(ticker):
    try:
        position = session_private.get_positions(category="linear", symbol=ticker)
        if position["retCode"] == 0:
            for item in position["result"]["list"]:
                if float(item["size"]) > 0:
                    return True
    except Exception as e:
        logger.error("Error checking open position for %s: %s", ticker, e)
        return True
    return False


# Check for active positions (updated for Bybit V5 API)
def active_position_confirmation(ticker):
    try:
        active_order = session_private.get_open_orders(
            category="linear",
            symbol=ticker
        )
        if active_order["retCode"] == 0:
            if len(active_order["result"]["list"]) > 0:
                return True
    except:
        return True
    return False


# Get open position price and quantity (updated for Bybit V5 API)
def get_open_positions(ticker, direction="Long"):

    # Get position
    position = session_private.get_positions(category="linear", symbol=ticker)

    # Determine target side
    target_side = "Buy" if direction == "Long" else "Sell"

    # Construct a response
    if position["retCode"] == 0:
        for pos in position["result"]["list"]:
            if pos["side"] == target_side and float(pos["size"]) > 0:
                order_price = float(pos["avgPrice"])
                order_quantity = float(pos["size"])
                return order_price, order_quantity
    return (0, 0)


# Get active position price and quantity (updated for Bybit V5 API)
def get_active_positions(ticker):

    # Get open orders
    active_order = session_private.get_open_orders(
        category="linear",
        symbol=ticker
    )

    # Construct a response
    if active_order["retCode"] == 0:
        if len(active_order["result"]["list"]) > 0:
            order_price = float(active_order["result"]["list"][0]["price"])
            order_quantity = float(active_order["result"]["list"][0]["qty"])
            return order_price, order_quantity
    return (0, 0)


# Query existing order (updated for Bybit V5 API)
def query_existing_order(ticker, order_id, direction):

    # First check open orders (unfilled/partially filled)
    try:
        order = session_private.get_open_orders(
            category="linear",
            symbol=ticker,
            orderId=order_id
        )
        if order["retCode"] == 0 and len(order["result"]["list"]) > 0:
            item = order["result"]["list"][0]
            return float(item["price"]), float(item["qty"]), item["orderStatus"]
    except:
        pass

    # Then check order history (filled/cancelled/rejected)
    try:
        order = session_private.get_order_history(
            category="linear",
            symbol=ticker,
            orderId=order_id
        )
        if order["retCode"] == 0 and len(order["result"]["list"]) > 0:
            item = order["result"]["list"][0]
            return float(item["price"]), float(item["qty"]), item["orderStatus"]
    except:
        pass

    return (0, 0, "")
