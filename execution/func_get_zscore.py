from config_execution_api import session_public, ticker_1, ticker_2, retry_api_call
from func_calcultions import get_trade_details
from func_price_calls import get_latest_klines
from func_stats import calculate_metrics, calculate_metrics_with_hedge
from logger_setup import get_logger
from bybit_response import get_result_dict, get_ret_code
import time

logger = get_logger("zscore")


def _to_float(value) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0

# Get latest z-score (updated for Bybit V5 API)
def get_latest_zscore():

    # Get latest asset orderbook prices and add dummy price for latest
    try:
        orderbook_1 = retry_api_call(session_public.get_orderbook, category="linear", symbol=ticker_1)
    except Exception as e:
        logger.error("Failed to get orderbook for %s: %s", ticker_1, e)
        return None

    # Return structured orderbook 1
    if get_ret_code(orderbook_1) != 0:
        return None

    mid_price_1, _, _, = get_trade_details(get_result_dict(orderbook_1))
    time.sleep(0.5) # Using to prevent overwhelming REST API with requests and getting blocked
    try:
        orderbook_2 = retry_api_call(session_public.get_orderbook, category="linear", symbol=ticker_2)
    except Exception as e:
        logger.error("Failed to get orderbook for %s: %s", ticker_2, e)
        return None

    # Return structured orderbook 2
    if get_ret_code(orderbook_2) != 0:
        return None

    mid_price_2, _, _, = get_trade_details(get_result_dict(orderbook_2))
    time.sleep(0.5) # Using to prevent overwhelming REST API with requests and getting blocked

    # Get latest price history
    series_1, series_2 = get_latest_klines()

    # Get z_score and confirm if hot
    if len(series_1) > 0 and len(series_2) > 0:

        # Replace last kline price with latest orderbook mid price
        series_1 = series_1[:-1]
        series_2 = series_2[:-1]
        series_1.append(mid_price_1)
        series_2.append(mid_price_2)

        # Get latest zscore
        _, zscore_list = calculate_metrics(series_1, series_2)
        if len(zscore_list) == 0:
            return None
        zscore = _to_float(zscore_list[-1])
        if zscore > 0:
            signal_sign_positive = True
        else:
            signal_sign_positive = False

        # Return output
        return (zscore, signal_sign_positive)

    # Return output if not true
    return None


# Get latest z-score with optional frozen hedge_ratio and frozen mean/std
# Used for HOLDING phase: pass the entry-time hedge_ratio, entry_mean,
# and entry_std to ensure z-score movements correlate with actual P&L.
# When frozen_mean/frozen_std are provided, z-score is computed as
# (current_spread - frozen_mean) / frozen_std, preventing phantom decay.
# Returns (zscore, signal_sign_positive, hedge_ratio, entry_mean, entry_std) or None.
def get_latest_zscore_with_hedge(frozen_hedge_ratio=None,
                                 frozen_mean=None,
                                 frozen_std=None):

    # Get latest asset orderbook prices
    try:
        orderbook_1 = retry_api_call(session_public.get_orderbook, category="linear", symbol=ticker_1)
    except Exception as e:
        logger.error("Failed to get orderbook for %s: %s", ticker_1, e)
        return None

    if get_ret_code(orderbook_1) != 0:
        return None

    mid_price_1, _, _, = get_trade_details(get_result_dict(orderbook_1))
    time.sleep(0.5)

    try:
        orderbook_2 = retry_api_call(session_public.get_orderbook, category="linear", symbol=ticker_2)
    except Exception as e:
        logger.error("Failed to get orderbook for %s: %s", ticker_2, e)
        return None

    if get_ret_code(orderbook_2) != 0:
        return None

    mid_price_2, _, _, = get_trade_details(get_result_dict(orderbook_2))
    time.sleep(0.5)

    # Get latest price history
    series_1, series_2 = get_latest_klines()

    if len(series_1) > 0 and len(series_2) > 0:

        # Replace last kline price with latest orderbook mid price
        series_1 = series_1[:-1]
        series_2 = series_2[:-1]
        series_1.append(mid_price_1)
        series_2.append(mid_price_2)

        # Get z-score using frozen or fresh parameters
        zscore_list, hedge_ratio, entry_mean, entry_std = calculate_metrics_with_hedge(
            series_1, series_2,
            frozen_hedge_ratio,
            frozen_mean,
            frozen_std,
        )
        if len(zscore_list) == 0:
            return None
        zscore = _to_float(zscore_list[-1])
        signal_sign_positive = zscore > 0

        return (zscore, signal_sign_positive, hedge_ratio, entry_mean, entry_std)

    return None


