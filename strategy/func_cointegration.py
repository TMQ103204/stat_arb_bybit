from config_strategy_api import z_score_window, min_zero_crossings
from statsmodels.tsa.stattools import coint
import statsmodels.api as sm
import pandas as pd
import numpy as np
import math


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


# ── Advanced Filter: Half-life of Mean Reversion ────────────────────────────
# Measures how many bars it takes for the spread to revert halfway to its mean.
# Too short (<1) = noise, too long (>80) = capital locked for too long.
def calculate_half_life(spread):
    spread = np.array(spread)
    spread_lag = spread[:-1]
    spread_diff = np.diff(spread)
    spread_lag = sm.add_constant(spread_lag)
    try:
        model = sm.OLS(spread_diff, spread_lag).fit()
        gamma = model.params[1]
        if gamma >= 0:
            return 999  # Not mean-reverting
        half_life = -np.log(2) / gamma
        return round(half_life, 2)
    except Exception:
        return 999


# ── Advanced Filter: Hurst Exponent (Rescaled Range) ────────────────────────
# H < 0.5 = mean-reverting (what we want)
# H = 0.5 = random walk (bad)
# H > 0.5 = trending (very bad for stat arb)
def calculate_hurst_exponent(spread):
    spread = np.array(spread, dtype=float)
    n = len(spread)
    if n < 20:
        return 0.5  # Not enough data, assume random walk

    max_k = min(n // 2, 100)
    sizes = []
    rs_values = []

    for k in range(10, max_k + 1, 2):
        num_chunks = n // k
        if num_chunks < 1:
            continue
        rs_list = []
        for i in range(num_chunks):
            chunk = spread[i * k:(i + 1) * k]
            mean_chunk = np.mean(chunk)
            deviations = chunk - mean_chunk
            cumulative = np.cumsum(deviations)
            r = np.max(cumulative) - np.min(cumulative)
            s = np.std(chunk, ddof=1)
            if s > 0:
                rs_list.append(r / s)
        if len(rs_list) > 0:
            sizes.append(k)
            rs_values.append(np.mean(rs_list))

    if len(sizes) < 3:
        return 0.5

    log_sizes = np.log(sizes)
    log_rs = np.log(rs_values)
    try:
        poly = np.polyfit(log_sizes, log_rs, 1)
        return round(poly[0], 4)
    except Exception:
        return 0.5


# ── Advanced Filter: Rolling Stability Check ─────────────────────────────────
# Splits data in half and runs cointegration on each half independently.
# Only keeps pairs where BOTH halves pass (p < 0.05).
def check_rolling_stability(series_1, series_2):
    n = len(series_1)
    mid = n // 2
    if mid < 30:
        return False  # Not enough data

    try:
        # First half
        _, p1, _ = coint(series_1[:mid], series_2[:mid])
        # Second half
        _, p2, _ = coint(series_1[mid:], series_2[mid:])
        return float(p1) < 0.05 and float(p2) < 0.05
    except Exception:
        return False


# ── Advanced Filter: Backtest Win Rate ───────────────────────────────────────
# Simulates historical Z-score: counts how many times z-score crossed the
# trigger threshold and then successfully reverted to 0 (win) vs. didn't (loss).
def calculate_backtest_win_rate(zscore_array, trigger_thresh=1.1):
    wins = 0
    losses = 0
    in_trade = False
    trade_side = None  # 'positive' or 'negative'

    for z in zscore_array:
        if np.isnan(z):
            continue

        if not in_trade:
            if z > trigger_thresh:
                in_trade = True
                trade_side = "positive"
            elif z < -trigger_thresh:
                in_trade = True
                trade_side = "negative"
        else:
            # Check for mean reversion (success)
            if trade_side == "positive" and z < 0:
                wins += 1
                in_trade = False
                trade_side = None
            elif trade_side == "negative" and z >= 0:
                wins += 1
                in_trade = False
                trade_side = None

    # If still in a trade at the end of data, count as a loss
    if in_trade:
        losses += 1

    total = wins + losses
    if total == 0:
        return 0.0, 0  # No trades observed
    return round(wins / total, 4), total


# Calculate co-integration (basic test only — fast)
def calculate_cointegration_basic(series_1, series_2):
    try:
        coint_res = coint(series_1, series_2)
    except ValueError:
        return None
    coint_t = float(coint_res[0])
    p_value = float(coint_res[1])
    critical_value = float(coint_res[2][1])
    model = sm.OLS(series_1, series_2).fit()
    hedge_ratio = float(model.params[0])
    spread = calculate_spread(series_1, series_2, hedge_ratio)
    zero_crossings = int(len(np.where(np.diff(np.sign(spread)))[0]))

    # Quick reject: basic test must pass first
    if not (p_value < 0.05 and coint_t < critical_value and zero_crossings >= min_zero_crossings):
        return None

    return {
        "p_value": round(p_value, 4),
        "t_value": round(coint_t, 2),
        "c_value": round(critical_value, 2),
        "hedge_ratio": round(hedge_ratio, 5),
        "zero_crossings": zero_crossings,
        "spread": spread,
    }


# Run advanced filters on a pair that already passed basic test
def run_advanced_filters(series_1, series_2, spread):
    half_life = calculate_half_life(spread)
    hurst = calculate_hurst_exponent(spread)
    is_stable = check_rolling_stability(
        np.array(series_1, dtype=float),
        np.array(series_2, dtype=float)
    )
    zscore_array = calculate_zscore(spread)
    win_rate, total_trades = calculate_backtest_win_rate(zscore_array)
    return half_life, hurst, is_stable, win_rate, total_trades


# Put close prices into a list
def extract_close_prices(prices):
    close_prices = []
    for price_values in prices:
        close_price = float(price_values["close"])
        if math.isnan(close_price):
            return []
        close_prices.append(close_price)
    return close_prices


# Calculate cointegrated pairs (enhanced with advanced filtering & ranking)
def get_cointegrated_pairs(prices):

    # Loop through coins and check for co-integration
    coint_pair_list = []
    included_set = set()
    symbols = list(prices.keys())
    total_pairs = len(symbols) * (len(symbols) - 1) // 2
    checked = 0
    basic_pass = 0

    print(f"Scanning {total_pairs} pairs from {len(symbols)} symbols...")

    for i, sym_1 in enumerate(symbols):
        for sym_2 in symbols[i + 1:]:

            # Get unique combination id
            sorted_characters = sorted(sym_1 + sym_2)
            unique = "".join(sorted_characters)
            if unique in included_set:
                continue

            checked += 1
            if checked % 500 == 0:
                print(f"  Progress: {checked}/{total_pairs} checked, {basic_pass} passed basic test...")

            # Get close prices
            series_1 = extract_close_prices(prices[sym_1])
            series_2 = extract_close_prices(prices[sym_2])
            if not series_1 or not series_2:
                continue

            # STEP 1: Fast basic cointegration test
            basic = calculate_cointegration_basic(series_1, series_2)
            if basic is None:
                continue

            basic_pass += 1

            # STEP 2: Only run expensive advanced filters on pairs that pass
            half_life, hurst, is_stable, win_rate, total_trades = run_advanced_filters(
                series_1, series_2, basic["spread"]
            )

            included_set.add(unique)
            coint_pair_list.append({
                "sym_1": sym_1,
                "sym_2": sym_2,
                "p_value": basic["p_value"],
                "t_value": basic["t_value"],
                "c_value": basic["c_value"],
                "hedge_ratio": basic["hedge_ratio"],
                "zero_crossings": basic["zero_crossings"],
                "half_life": half_life,
                "hurst": hurst,
                "is_stable": is_stable,
                "win_rate": win_rate,
                "total_trades": total_trades
            })

    print(f"Done: {checked} pairs checked, {basic_pass} passed basic test, {len(coint_pair_list)} total candidates.")

    # Output results
    df_coint = pd.DataFrame(coint_pair_list)
    if not df_coint.empty:
        # Bước 1: Lọc bỏ các cặp có hedge_ratio phi thực tế
        df_coint = df_coint[(df_coint['hedge_ratio'] >= 0.01) & (df_coint['hedge_ratio'] <= 100)]

        # Bước 2: Lọc nâng cao
        # - Half-life hợp lý (1 đến 80 nến)
        df_coint = df_coint[(df_coint['half_life'] >= 1) & (df_coint['half_life'] <= 80)]
        # - Hurst Exponent < 0.5 (chứng minh mean-reverting)
        df_coint = df_coint[df_coint['hurst'] < 0.5]
        # - Rolling Stability: cả 2 nửa dữ liệu đều cointegrated
        df_coint = df_coint[df_coint['is_stable'] == True]
        # - Win rate >= 60%
        df_coint = df_coint[df_coint['win_rate'] >= 0.6]
        # - Ít nhất 2 trades trong backtest
        df_coint = df_coint[df_coint['total_trades'] >= 2]

        if df_coint.empty:
            print("⚠ No pairs survived the advanced filters.")
            df_coint.to_csv("2_cointegrated_pairs.csv", index=False)
            return df_coint

        # Bước 3: Tính Composite Score mới
        df_coint['rank_hl'] = df_coint['half_life'].rank(ascending=True)
        df_coint['rank_hurst'] = df_coint['hurst'].rank(ascending=True)
        df_coint['rank_wr'] = df_coint['win_rate'].rank(ascending=False)
        df_coint['rank_zero'] = df_coint['zero_crossings'].rank(ascending=False)
        df_coint['rank_t_val'] = df_coint['t_value'].rank(ascending=True)

        df_coint['composite_score'] = (
            df_coint['rank_wr']    * 0.30 +
            df_coint['rank_hl']    * 0.20 +
            df_coint['rank_hurst'] * 0.20 +
            df_coint['rank_zero']  * 0.15 +
            df_coint['rank_t_val'] * 0.15
        )

        # Bước 4: Sắp xếp theo composite score
        df_coint = df_coint.sort_values("composite_score", ascending=True)

        # Xóa cột tạm + cột boolean
        df_coint = df_coint.drop(columns=[
            'rank_hl', 'rank_hurst', 'rank_wr', 'rank_zero', 'rank_t_val', 'is_stable'
        ])

        # Lưu file
        df_coint.to_csv("2_cointegrated_pairs.csv", index=False)
        print(f"✅ {len(df_coint)} pairs survived advanced filtering.")
    return df_coint

