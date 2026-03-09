from config_execution_api import signal_positive_ticker
from config_execution_api import signal_negative_ticker
from config_execution_api import session_private
from logger_setup import get_logger

logger = get_logger("close_pos")

# Get position information (updated for Bybit V5 API)
def get_position_info(ticker):

    # Declare output variables
    side = ""
    size = 0

    # Extract position info
    position_response = session_private.get_positions(category="linear", symbol=ticker)
    position = dict(position_response) if not isinstance(position_response, dict) else position_response
    if position["retCode"] == 0:
        for pos in position["result"]["list"]:
            if float(pos["size"]) > 0:
                size = float(pos["size"])
                side = pos["side"]
                break

    # Return output
    return side, size


#  Place market close order (updated for Bybit V5 API)
def place_market_close_order(ticker, side, size):

    # Close position
    try:
        session_private.place_order(
            category="linear",
            symbol=ticker,
            side=side,
            orderType="Market",
            qty=str(size),
            timeInForce="GTC",
            reduceOnly=True
        )
        logger.info("Closed %s %s qty=%.6f", side, ticker, size)
    except Exception as e:
        logger.error("Failed to close %s %s: %s", ticker, side, e)

    # Return
    return


# Close all positions for both tickers
def close_all_positions(kill_switch):

    # Cancel all active orders
    try:
        session_private.cancel_all_orders(category="linear", symbol=signal_positive_ticker)
        session_private.cancel_all_orders(category="linear", symbol=signal_negative_ticker)
    except Exception as e:
        logger.error("Failed to cancel orders: %s", e)

    # Get position information
    side_1, size_1 = get_position_info(signal_positive_ticker)
    side_2, size_2 = get_position_info(signal_negative_ticker)

    if size_1 > 0:
        place_market_close_order(signal_positive_ticker, side_2, size_1) # use side 2

    if size_2 > 0:
        place_market_close_order(signal_negative_ticker, side_1, size_2) # use side 1

    # Output results
    kill_switch = 0
    return kill_switch
