"""
    interval: 60, "D"
    from: integer from timestamp in milliseconds (V5 API)
    limit: max size of 200
"""

from config_strategy_api import session
from config_strategy_api import timeframe
from config_strategy_api import kline_limit
from bybit_response import get_result_list, get_ret_code
import datetime
import time

def _get_time_start_seconds() -> int:
    now = datetime.datetime.now()
    if timeframe == 60:
        start_time = now - datetime.timedelta(hours=kline_limit)
    elif timeframe == "D":
        start_time = now - datetime.timedelta(days=kline_limit)
    else:
        # Fallback to hours when timeframe is unexpected.
        start_time = now - datetime.timedelta(hours=kline_limit)
    return int(start_time.timestamp())

# Get historical prices (klines) - updated for Bybit V5 API
def get_price_klines(symbol):

    # Get prices (V5 uses start in milliseconds)
    time_start_seconds = _get_time_start_seconds()
    prices = session.get_mark_price_kline(
        category="linear",
        symbol=symbol,
        interval=str(timeframe),
        limit=kline_limit,
        start=time_start_seconds * 1000
    )

    # Manage API calls
    time.sleep(0.1)

    # Return output - V5 returns data in result.list (newest first)
    if get_ret_code(prices) != 0:
        return []

    result_list = get_result_list(prices)
    if len(result_list) != kline_limit:
        return []

    # Convert V5 array format to dict format and reverse to oldest first
    klines = []
    for item in reversed(result_list):
        klines.append({
            "start_at": int(item[0]) // 1000,
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4])
        })
    return klines
