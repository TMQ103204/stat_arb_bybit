from config_execution_api import signal_positive_ticker
from config_execution_api import signal_negative_ticker
from config_execution_api import session_private, retry_api_call
from logger_setup import get_logger
from bybit_response import get_result_list, get_ret_code
import time

logger = get_logger("close_pos")

# Get position information (updated for Bybit V5 API)
def get_position_info(ticker, max_retries=3):

    # Declare output variables
    side = ""
    size = 0.0

    # Extract position info with retries
    for attempt in range(max_retries):
        try:
            position_response = retry_api_call(session_private.get_positions, category="linear", symbol=ticker)
            if get_ret_code(position_response) == 0:
                for pos in get_result_list(position_response):
                    if float(pos["size"]) > 0:
                        size = float(pos["size"])
                        side = pos["side"]
                        return side, size
                # If retCode is 0 and no size > 0 found, position is indeed 0
                return side, size
            else:
                logger.error("API error checking position %s: %s", ticker, position_response)
        except Exception as e:
            logger.error("Failed to get position info for %s (attempt %d/%d): %s", ticker, attempt + 1, max_retries, e)
            time.sleep(2)
            
    # Raise exception to prevent false 0 size return
    raise Exception(f"Failed to fetch position for {ticker} after {max_retries} attempts.")


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
        return True
    except Exception as e:
        logger.error("Failed to close %s %s: %s", ticker, side, e)
        return False


# Close all positions for both tickers
def close_all_positions(kill_switch):

    # Cancel all active orders
    try:
        session_private.cancel_all_orders(category="linear", symbol=signal_positive_ticker)
        session_private.cancel_all_orders(category="linear", symbol=signal_negative_ticker)
    except Exception as e:
        logger.error("Failed to cancel orders: %s", e)

    # Let the API settle
    time.sleep(1)

    # Get position information
    try:
        side_1, size_1 = get_position_info(signal_positive_ticker)
        side_2, size_2 = get_position_info(signal_negative_ticker)
    except Exception as e:
        logger.error("Aborting close_all_positions due to position fetch failure: %s", e)
        return kill_switch

    success = True
    # Close each position using its OWN opposite side
    if size_1 > 0 and side_1:
        close_side_1 = "Sell" if side_1 == "Buy" else "Buy"
        if not place_market_close_order(signal_positive_ticker, close_side_1, size_1):
            success = False

    if size_2 > 0 and side_2:
        close_side_2 = "Sell" if side_2 == "Buy" else "Buy"
        if not place_market_close_order(signal_negative_ticker, close_side_2, size_2):
            success = False

    # Output results
    if success:
        return 0
    else:
        logger.warning("Failed to close one or more positions. kill_switch remains %s", kill_switch)
        return kill_switch
