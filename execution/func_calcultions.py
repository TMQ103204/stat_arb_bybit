from config_execution_api import stop_loss_fail_safe
from config_execution_api import session_public
from logger_setup import get_logger
import math

logger = get_logger("calculations")

# Cache for instrument info to avoid repeated API calls
_instrument_cache = {}

def _get_instrument_info(symbol):
    """Fetch and cache instrument info (tick size + qty step) from API."""
    if symbol in _instrument_cache:
        return _instrument_cache[symbol]
    try:
        info = session_public.get_instruments_info(category="linear", symbol=symbol)
        item = info["result"]["list"][0]
        price_filter = item["priceFilter"]
        lot_filter = item["lotSizeFilter"]
        tick_size = float(price_filter["tickSize"])
        qty_step = float(lot_filter["qtyStep"])
        _instrument_cache[symbol] = (tick_size, qty_step)
        return (tick_size, qty_step)
    except Exception as e:
        logger.warning("Could not fetch instrument info for %s: %s", symbol, e)
        return (None, None)


def _decimals_from_step(step):
    """Get number of decimal places from a step size value."""
    if step is None or step <= 0:
        return 8
    step_str = f"{step:.10f}".rstrip('0')
    return len(step_str.split('.')[-1]) if '.' in step_str else 0


# Get qty step size from exchange instrument info
def get_qty_step(symbol):
    _, qty_step = _get_instrument_info(symbol)
    return qty_step


# Round quantity down to the nearest valid step
def round_qty_to_step(quantity, qty_step):
    if qty_step is None or qty_step <= 0:
        return round(quantity)
    # Floor to nearest step
    floored = math.floor(quantity / qty_step) * qty_step
    # Determine decimal places from step size to avoid floating point artifacts
    step_str = f"{qty_step:.10f}".rstrip('0')
    decimals = len(step_str.split('.')[-1]) if '.' in step_str else 0
    return round(floored, decimals)


# Puts all close prices in a list
def extract_close_prices(prices):
    close_prices = []
    for price_values in prices:
        if math.isnan(price_values["close"]):
            return []
        close_prices.append(price_values["close"])
    return close_prices


# Get trade details and latest prices (updated for Bybit V5 orderbook format)
def get_trade_details(orderbook, direction="Long", capital=0):

    # Set calculation and output variables
    mid_price = 0
    quantity = 0
    stop_loss = 0

    # Get prices, stop loss and quantity
    if orderbook:

        # V5 orderbook uses "s" for symbol, "b" for bids, "a" for asks
        symbol = orderbook.get("s", "")
        tick_size, qty_step = _get_instrument_info(symbol)
        price_rounding = _decimals_from_step(tick_size)

        # V5 format: bids = [[price, size], ...] (sorted desc), asks = [[price, size], ...] (sorted asc)
        bids = orderbook.get("b", [])
        asks = orderbook.get("a", [])

        # Calculate price, size, stop loss
        nearest_ask = float(asks[0][0]) if len(asks) > 0 else 0
        nearest_bid = float(bids[0][0]) if len(bids) > 0 else 0

        # Calculate hard stop loss
        if direction == "Long" and nearest_bid > 0:
            mid_price = nearest_bid
            stop_loss = round(mid_price * (1 - stop_loss_fail_safe), price_rounding)
        elif direction != "Long" and nearest_ask > 0:
            mid_price = nearest_ask
            stop_loss = round(mid_price * (1 + stop_loss_fail_safe), price_rounding)

        # Calculate quantity
        if mid_price > 0:
            raw_quantity = capital / mid_price
            if qty_step is not None:
                quantity = round_qty_to_step(raw_quantity, qty_step)
            else:
                quantity = round(raw_quantity)

    # Output results
    return (mid_price, stop_loss, quantity)
