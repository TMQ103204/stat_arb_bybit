from func_position_calls import query_existing_order
from func_position_calls import get_open_positions
from func_position_calls import get_active_positions
from func_calcultions import get_trade_details
from config_execution_api import session_public
from logger_setup import get_logger

logger = get_logger("order_review")


# Check order items (updated for Bybit V5 API)
def check_order(ticker, order_id, remaining_capital, direction="Long"):

    # Get current orderbook
    try:
        orderbook = session_public.get_orderbook(category="linear", symbol=ticker)
    except Exception as e:
        logger.error("Failed to get orderbook for %s: %s", ticker, e)
        return None

    # Return structured orderbook
    if orderbook["retCode"] != 0:
        return None

    # Get latest price
    mid_price, _, _ = get_trade_details(orderbook["result"])

    logger.debug("mid_price for %s: %.6f", ticker, mid_price)

    # Get trade details
    order_price, order_quantity, order_status = query_existing_order(ticker, order_id, direction)

    # Get open positions
    position_price, position_quantity = get_open_positions(ticker, direction)

    # Get active positions
    # active_order_price, active_order_quantity = get_active_positions(ticker)

    # Calculate position value in USDT
    position_value_usdt = position_quantity * position_price if position_price > 0 else position_quantity * mid_price

    # Determine action - trade complete - stop placing orders
    if position_value_usdt >= remaining_capital and position_quantity > 0:
        logger.info("position_qty %.4f @ %.4f = %.2f USDT | target %.2f USDT", position_quantity, position_price, position_value_usdt, remaining_capital)
        return "Trade Complete"

    # Determine action - position filled - buy more
    if order_status == "Filled":
        return "Position Filled"

    # Determine action - order active - do nothing
    active_items = ["Created", "New"]
    if order_status in active_items:
        return "Order Active"

    # Determine action - partial filled order - do nothing
    if order_status == "PartiallyFilled":
        return "Partial Fill"

    # Determine action - order failed - try place order again
    cancel_items = ["Cancelled", "Rejected", "PendingCancel"]
    if order_status in cancel_items:
        return "Try Again"
