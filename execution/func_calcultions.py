from config_execution_api import stop_loss_fail_safe
from config_execution_api import session_public
from logger_setup import get_logger
from bybit_response import get_result_list
import math
import time

logger = get_logger("calculations")

# Cache for instrument info to avoid repeated API calls
_instrument_cache = {}

# ── Fee rate cache ────────────────────────────────────────────────────────────
# get_fee_rates requires a real (mainnet) authenticated session.
# Demo accounts get ErrCode 10001 on this endpoint, so we always use mainnet
# credentials. Rates are cached for 1 hour to avoid hammering the API.
_fee_rate_cache: dict = {}          # {symbol: (rate_float, fetched_at_ts)}
_FEE_CACHE_TTL = 3600               # seconds before re-fetching
_fee_session = None                 # lazy-initialised mainnet session

def _get_fee_session():
    """Return a mainnet HTTP session for fee-rate queries (created once)."""
    global _fee_session
    if _fee_session is None:
        from config_execution_api import api_key_mainnet, api_secret_mainnet
        from pybit.unified_trading import HTTP
        _fee_session = HTTP(api_key=api_key_mainnet, api_secret=api_secret_mainnet)
        logger.debug("Fee-rate session initialised (mainnet).")
    return _fee_session


def _get_taker_fee_rate(symbol: str, fallback_rate: float) -> float:
    """Return taker fee rate for symbol, using cache then live API then fallback."""
    global _fee_rate_cache
    now = time.time()
    cached = _fee_rate_cache.get(symbol)
    if cached and (now - cached[1]) < _FEE_CACHE_TTL:
        return cached[0]
    try:
        sess = _get_fee_session()
        resp = sess.get_fee_rates(category="linear", symbol=symbol)
        rate = float(resp["result"]["list"][0]["takerFeeRate"])
        _fee_rate_cache[symbol] = (rate, now)
        logger.debug("Fee rate for %s: %.6f (%.4f%%)", symbol, rate, rate * 100)
        return rate
    except Exception as e:
        logger.warning("Could not fetch fee rate for %s (%s): using fallback %.4f%%", symbol, e, fallback_rate * 100)
        return fallback_rate
# ─────────────────────────────────────────────────────────────────────────────

def _get_instrument_info(symbol):
    """Fetch and cache instrument info (tick size + qty step) from API."""
    if symbol in _instrument_cache:
        return _instrument_cache[symbol]
    try:
        info = session_public.get_instruments_info(category="linear", symbol=symbol)
        info_list = get_result_list(info)
        if len(info_list) == 0:
            return (None, None)
        item = info_list[0]
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
        if direction == "Long" and nearest_ask > 0:
            # Aggressive limit: use best ask so the order crosses the spread and fills immediately
            mid_price = nearest_ask
            stop_loss = round(mid_price * (1 - stop_loss_fail_safe), price_rounding)
        elif direction != "Long" and nearest_bid > 0:
            # Aggressive limit: use best bid so the order crosses the spread and fills immediately
            mid_price = nearest_bid
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


def calculate_exact_live_profit(long_ticker, short_ticker):
    """
    Calculate the current net PnL of an open pair trade using live Bybit data.

    Queries the orderbook for realistic exit prices (best bid for long,
    best ask for short), reads actual position entry prices and sizes,
    and fetches each symbol's real-time taker fee rate from the API.
    This correctly handles cases where the two coins have different fee tiers
    (e.g. 0.055% vs 0.11%).

    Returns:
        (total_net_pnl_usdt, net_profit_pct) — both 0.0 on any error.
    """
    from config_execution_api import session_private as _priv
    try:
        # -- 1. Live exit prices from the orderbook ---------------------------
        ob_long  = session_public.get_orderbook(category="linear", symbol=long_ticker,  limit=1)
        ob_short = session_public.get_orderbook(category="linear", symbol=short_ticker, limit=1)

        # Long exits at the best bid; short exits at the best ask
        best_bid_long  = float(ob_long["result"]["b"][0][0])
        best_ask_short = float(ob_short["result"]["a"][0][0])

        # -- 2. Current position data -----------------------------------------
        pos_long_res  = _priv.get_positions(category="linear", symbol=long_ticker)
        pos_short_res = _priv.get_positions(category="linear", symbol=short_ticker)

        pos_long  = get_result_list(pos_long_res)[0]
        pos_short = get_result_list(pos_short_res)[0]

        size_long       = float(pos_long["size"])
        avg_price_long  = float(pos_long["avgPrice"])
        entry_pnl_long  = float(pos_long["cumRealisedPnl"])   # negative = fees paid on entry

        size_short      = float(pos_short["size"])
        avg_price_short = float(pos_short["avgPrice"])
        entry_pnl_short = float(pos_short["cumRealisedPnl"])  # negative = fees paid on entry

        if size_long == 0 or size_short == 0:
            return 0.0, 0.0

        # -- 3. Real-time taker fee rates (handles 0.055% vs 0.11% per coin) --
        # Uses a dedicated mainnet session (demo blocks this endpoint).
        # Rates are cached for 1 hour so we don't call the API every tick.
        from config_execution_api import taker_fee_pct as _fallback_fee_pct
        _fallback_rate = _fallback_fee_pct / 100.0
        taker_rate_long  = _get_taker_fee_rate(long_ticker,  _fallback_rate)
        taker_rate_short = _get_taker_fee_rate(short_ticker, _fallback_rate)

        # -- 4. Gross PnL per leg ---------------------------------------------
        gross_pnl_long  = (best_bid_long  - avg_price_long)  * size_long
        gross_pnl_short = (avg_price_short - best_ask_short) * size_short

        # -- 5. Exit fee (market close) per leg --------------------------------
        exit_fee_long  = best_bid_long  * size_long  * taker_rate_long
        exit_fee_short = best_ask_short * size_short * taker_rate_short

        # -- 6. Net PnL = Gross + entry realised PnL (already signed) - exit fee
        net_pnl_long  = gross_pnl_long  + entry_pnl_long  - exit_fee_long
        net_pnl_short = gross_pnl_short + entry_pnl_short - exit_fee_short

        total_net_pnl_usdt = net_pnl_long + net_pnl_short
        total_capital      = (avg_price_long * size_long) + (avg_price_short * size_short)
        net_profit_pct     = (total_net_pnl_usdt / total_capital) * 100 if total_capital > 0 else 0.0

        return total_net_pnl_usdt, net_profit_pct

    except Exception as e:
        logger.warning("calculate_exact_live_profit failed (%s %s): %s", long_ticker, short_ticker, e)
        return 0.0, 0.0
