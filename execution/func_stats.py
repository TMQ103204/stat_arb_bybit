from config_execution_api import z_score_window
from statsmodels.tsa.stattools import coint
import statsmodels.api as sm
import pandas as pd


# Calculate Z-Score
def calculate_zscore(spread):
    df = pd.DataFrame(spread)
    mean = df.rolling(center=False, window=z_score_window).mean()
    std = df.rolling(center=False, window=z_score_window).std()
    x = df.rolling(center=False, window=1).mean()
    df["ZSCORE"] = (x - mean) / std
    return df["ZSCORE"].astype(float).values


# Calculate spread
def calculate_spread(series_1, series_2, hedge_ratio):
    spread = pd.Series(series_1) - (pd.Series(series_2) * hedge_ratio)
    return spread


# Calculate metrics
def calculate_metrics(series_1, series_2):
    coint_flag = 0
    coint_res = coint(series_1, series_2)
    coint_t = coint_res[0]
    p_value = coint_res[1]
    critical_value = coint_res[2][1]
    model = sm.OLS(series_1, series_2).fit()
    hedge_ratio = model.params[0]
    spread = calculate_spread(series_1, series_2, hedge_ratio)
    zscore_list = calculate_zscore(spread)
    if p_value < 0.5 and coint_t < critical_value:
        coint_flag = 1
    return (coint_flag, zscore_list.tolist())


# Calculate metrics with optional frozen hedge_ratio
# When frozen_hedge_ratio is provided, OLS is skipped and the frozen value
# is used directly. This ensures z-score during HOLDING reflects the same
# spread definition as at entry time, so z-score movement = real P&L direction.
def calculate_metrics_with_hedge(series_1, series_2, frozen_hedge_ratio=None):
    if frozen_hedge_ratio is not None:
        # HOLDING mode: use the entry-time hedge_ratio, skip OLS
        hedge_ratio = frozen_hedge_ratio
    else:
        # SEEKING mode: compute fresh hedge_ratio via OLS
        model = sm.OLS(series_1, series_2).fit()
        hedge_ratio = model.params[0]

    spread = calculate_spread(series_1, series_2, hedge_ratio)
    zscore_list = calculate_zscore(spread)
    return (zscore_list.tolist(), float(hedge_ratio))

