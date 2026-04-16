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


def _resolve_session_public(session_pub=None):
    if session_pub is not None:
        return session_pub
    from config_execution_api import session_public
    return session_public


def _resolve_retry(retry_fn=None):
    if retry_fn is not None:
        return retry_fn
    from config_execution_api import retry_api_call
    return retry_api_call


# Get latest z-score (updated for Bybit V5 API)
def get_latest_zscore(t1=None, t2=None, session_pub=None, retry_fn=None,
                      tf=None, kl=None, window=None):

    if t1 is None:
        from config_execution_api import ticker_1
        t1 = ticker_1
    if t2 is None:
        from config_execution_api import ticker_2
        t2 = ticker_2

    sess = _resolve_session_public(session_pub)
    retry = _resolve_retry(retry_fn)

    # Get latest asset orderbook prices and add dummy price for latest
    try:
        orderbook_1 = retry(sess.get_orderbook, category="linear", symbol=t1)
    except Exception as e:
        logger.error("Failed to get orderbook for %s: %s", t1, e)
        return None

    # Return structured orderbook 1
    if get_ret_code(orderbook_1) != 0:
        return None

    mid_price_1, _, _, = get_trade_details(get_result_dict(orderbook_1))
    time.sleep(0.5) # Using to prevent overwhelming REST API with requests and getting blocked
    try:
        orderbook_2 = retry(sess.get_orderbook, category="linear", symbol=t2)
    except Exception as e:
        logger.error("Failed to get orderbook for %s: %s", t2, e)
        return None

    # Return structured orderbook 2
    if get_ret_code(orderbook_2) != 0:
        return None

    mid_price_2, _, _, = get_trade_details(get_result_dict(orderbook_2))
    time.sleep(0.5) # Using to prevent overwhelming REST API with requests and getting blocked

    # Get latest price history
    series_1, series_2 = get_latest_klines(t1=t1, t2=t2, session_pub=session_pub,
                                            retry_fn=retry_fn, tf=tf, kl=kl)

    # Get z_score and confirm if hot
    if len(series_1) > 0 and len(series_2) > 0:

        # Replace last kline price with latest orderbook mid price
        series_1 = series_1[:-1]
        series_2 = series_2[:-1]
        series_1.append(mid_price_1)
        series_2.append(mid_price_2)

        # Get latest zscore
        _, zscore_list = calculate_metrics(series_1, series_2, window=window)
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
def get_latest_zscore_with_hedge(frozen_hedge_ratio=None,
                                 frozen_mean=None,
                                 frozen_std=None,
                                 t1=None, t2=None,
                                 session_pub=None, retry_fn=None,
                                 tf=None, kl=None, window=None):

    if t1 is None:
        from config_execution_api import ticker_1
        t1 = ticker_1
    if t2 is None:
        from config_execution_api import ticker_2
        t2 = ticker_2

    sess = _resolve_session_public(session_pub)
    retry = _resolve_retry(retry_fn)

    # Get latest asset orderbook prices
    try:
        orderbook_1 = retry(sess.get_orderbook, category="linear", symbol=t1)
    except Exception as e:
        logger.error("Failed to get orderbook for %s: %s", t1, e)
        return None

    if get_ret_code(orderbook_1) != 0:
        return None

    mid_price_1, _, _, = get_trade_details(get_result_dict(orderbook_1))
    time.sleep(0.5)

    try:
        orderbook_2 = retry(sess.get_orderbook, category="linear", symbol=t2)
    except Exception as e:
        logger.error("Failed to get orderbook for %s: %s", t2, e)
        return None

    if get_ret_code(orderbook_2) != 0:
        return None

    mid_price_2, _, _, = get_trade_details(get_result_dict(orderbook_2))
    time.sleep(0.5)

    # Get latest price history
    series_1, series_2 = get_latest_klines(t1=t1, t2=t2, session_pub=session_pub,
                                            retry_fn=retry_fn, tf=tf, kl=kl)

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
            window=window,
        )
        if len(zscore_list) == 0:
            return None
        zscore = _to_float(zscore_list[-1])
        signal_sign_positive = zscore > 0

        return (zscore, signal_sign_positive, hedge_ratio, entry_mean, entry_std)

    return None
