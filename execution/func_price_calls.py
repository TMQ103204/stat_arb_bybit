from config_execution_api import ticker_1
from config_execution_api import ticker_2
from config_execution_api import session_public
from config_execution_api import timeframe
from config_execution_api import kline_limit
from func_calcultions import extract_close_prices
from logger_setup import get_logger
from bybit_response import get_result_list, get_ret_code
import datetime
import time

logger = get_logger("price_calls")


# Get trade liquidity for ticker (updated for Bybit V5 API)
def get_ticker_trade_liquidity(ticker):

    # Get trades history
    try:
        trades = session_public.get_public_trade_history(
            category="linear",
            symbol=ticker,
            limit=50
        )
    except Exception as e:
        logger.error("Failed to get trade history for %s: %s", ticker, e)
        return (0, 0)

    # Get the list for calculating the average liquidity
    quantity_list = []
    trades_list = get_result_list(trades)
    if get_ret_code(trades) == 0:
        for trade in trades_list:
            quantity_list.append(float(trade["size"]))

    # Return output
    if len(quantity_list) > 0:
        avg_liq = sum(quantity_list) / len(quantity_list)
        res_trades_price = float(trades_list[0]["price"])
        return (avg_liq, res_trades_price)
    return (0, 0)


# Get start times
def get_timestamps():
    now = datetime.datetime.now()
    time_start_date = now
    time_next_date = now
    if timeframe == 60:
        time_start_date = now - datetime.timedelta(hours=kline_limit)
        time_next_date = now + datetime.timedelta(seconds=30)
    elif timeframe == "D":
        time_start_date = now - datetime.timedelta(days=kline_limit)
        time_next_date = now + datetime.timedelta(minutes=1)
    else:
        time_start_date = now - datetime.timedelta(hours=kline_limit)
        time_next_date = now + datetime.timedelta(seconds=30)
    time_start_seconds = int(time_start_date.timestamp())
    time_now_seconds = int(now.timestamp())
    time_next_seconds = int(time_next_date.timestamp())
    return (time_start_seconds, time_now_seconds, time_next_seconds)


# Get historical prices (klines) - updated for Bybit V5 API
def get_price_klines(ticker):

    # Get prices (V5 uses start in milliseconds)
    time_start_seconds, _, _ = get_timestamps()
    try:
        prices = session_public.get_mark_price_kline(
            category="linear",
            symbol=ticker,
            interval=str(timeframe),
            limit=kline_limit,
            start=time_start_seconds * 1000
        )
    except Exception as e:
        logger.error("Failed to get klines for %s: %s", ticker, e)
        return []

    # Manage API calls
    time.sleep(0.1)

    # Return prices output - V5 returns data in result.list (newest first)
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


# Get latest klines
def get_latest_klines():
    series_1 = []
    series_2 = []
    prices_1 = get_price_klines(ticker_1)
    prices_2 = get_price_klines(ticker_2)
    if len(prices_1) > 0:
        series_1 = extract_close_prices(prices_1)
    if len(prices_2) > 0:
        series_2 = extract_close_prices(prices_2)
    return (series_1, series_2)
