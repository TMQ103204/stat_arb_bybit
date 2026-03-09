from config_execution_api import session_private
from config_execution_api import limit_order_basis
from config_execution_api import session_public
from func_calcultions import get_trade_details
from logger_setup import get_logger

logger = get_logger("execution")

# Set leverage (updated for Bybit V5 API)
def set_leverage(ticker):

    # Switch to isolated margin mode
    try:
        session_private.switch_margin_mode(
            category="linear",
            symbol=ticker,
            tradeMode=1,  # 1 = Isolated Margin Mode
            buyLeverage="1",
            sellLeverage="1"
        )
    except Exception as e:
        logger.debug("switch_margin_mode for %s: %s", ticker, e)

    # Set leverage
    try:
        session_private.set_leverage(
            category="linear",
            symbol=ticker,
            buyLeverage="1",
            sellLeverage="1"
        )
    except Exception as e:
        logger.debug("set_leverage for %s: %s", ticker, e)

    # Return
    return


# Place limit or market order (updated for Bybit V5 API)
def place_order(ticker, price, quantity, direction, stop_loss):

    # Set variables
    if direction == "Long":
        side = "Buy"
    else:
        side = "Sell"

    # Place limit order
    if limit_order_basis:
        order = session_private.place_order(
            category="linear",
            symbol=ticker,
            side=side,
            orderType="Limit",
            qty=str(quantity),
            price=str(price),
            timeInForce="PostOnly",
            reduceOnly=False,
            closeOnTrigger=False,
            stopLoss=str(stop_loss)
        )
    else:
        order = session_private.place_order(
            category="linear",
            symbol=ticker,
            side=side,
            orderType="Market",
            qty=str(quantity),
            timeInForce="GTC",
            reduceOnly=False,
            closeOnTrigger=False,
            stopLoss=str(stop_loss)
        )

    # Return order
    return order


# Initialise execution (updated for Bybit V5 API)
def initialise_order_execution(ticker, direction, capital):
    try:
        orderbook = session_public.get_orderbook(category="linear", symbol=ticker)
    except Exception as e:
        logger.error("Failed to get orderbook for %s: %s", ticker, e)
        return 0

    # Return structured orderbook
    if not isinstance(orderbook, dict) or orderbook.get("retCode") != 0:
        return 0

    ob_result = orderbook.get("result")

    if ob_result:
        mid_price, stop_loss, quantity = get_trade_details(ob_result, direction, capital)
        if quantity > 0:
            try:
                order = place_order(ticker, mid_price, quantity, direction, stop_loss)
                if isinstance(order, dict) and "result" in order:
                    if "orderId" in order["result"]:
                        logger.info("Order placed: %s %s qty=%.6f price=%.6f", direction, ticker, quantity, mid_price)
                        return order["result"]["orderId"]
            except Exception as e:
                logger.error("Failed to place order %s %s: %s", direction, ticker, e)
    return 0
