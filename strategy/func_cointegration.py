from config_strategy_api import z_score_window, min_zero_crossings
from statsmodels.tsa.stattools import coint
import statsmodels.api as sm
import pandas as pd
import numpy as np
import math
import re


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

    # ── Duplicate-asset detection ────────────────────────────────────────
    # Extracts base asset from symbol to detect pairs like BTCUSDT vs BTC-0626USDT
    # or 1000PEPEUSDT vs PEPEUSDT which are the same underlying, not a real pair.
    def _extract_base_asset(symbol):
        s = symbol.upper()
        for suffix in ("USDT", "PERP", "USD"):
            if s.endswith(suffix):
                s = s[:-len(suffix)]
        s = re.sub(r'[-]?\d{2}[A-Z]{3}\d{2,4}$', '', s)  # -26JUN25
        s = re.sub(r'[-]?\d{4,}$', '', s)                  # -0626, 0926
        if s.startswith("1000") and len(s) > 4:
            s = s[4:]
        return s

    # Loop through coins and check for co-integration
    coint_pair_list = []
    included_set = set()
    symbols = list(prices.keys())
    total_pairs = len(symbols) * (len(symbols) - 1) // 2
    checked = 0
    basic_pass = 0
    duplicate_asset_rejected = 0

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

            # ── Duplicate-asset filter ─────────────────────────────────
            base_1 = _extract_base_asset(sym_1)
            base_2 = _extract_base_asset(sym_2)
            if base_1 == base_2:
                duplicate_asset_rejected += 1
                continue
            # ──────────────────────────────────────────────────────────

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

    print(f"Done: {checked} pairs checked, {basic_pass} passed basic, "
          f"{duplicate_asset_rejected} duplicate-asset rejected, "
          f"{len(coint_pair_list)} total candidates.")

    # Output results
    df_coint = pd.DataFrame(coint_pair_list)
    if not df_coint.empty:
        total_before = len(df_coint)

        # ── HARD FILTERS (reject high-risk pairs) ────────────────────

        # 1. Hedge ratio phi thuc te (too extreme = unbalanced position)
        df_coint = df_coint[(df_coint['hedge_ratio'] >= 0.2) & (df_coint['hedge_ratio'] <= 5.0)]
        print(f"  Filter hedge_ratio [0.2-5.0]: {total_before} -> {len(df_coint)}")

        # 2. Half-life: qua ngan = noise, qua dai = ket von
        before = len(df_coint)
        df_coint = df_coint[(df_coint['half_life'] >= 1) & (df_coint['half_life'] <= 80)]
        print(f"  Filter half_life [1-80]: {before} -> {len(df_coint)}")

        # 3. Win rate: cap phai thang > 50% trong backtest
        before = len(df_coint)
        df_coint = df_coint[df_coint['win_rate'] >= 0.5]
        print(f"  Filter win_rate >= 0.5: {before} -> {len(df_coint)}")

        # 4. Enough trades: it nhat 2 trade trong backtest
        before = len(df_coint)
        df_coint = df_coint[df_coint['total_trades'] >= 2]
        print(f"  Filter total_trades >= 2: {before} -> {len(df_coint)}")

        # 5. Rolling stability: cointegration on dinh o CA 2 nua du lieu
        before = len(df_coint)
        df_coint = df_coint[df_coint['is_stable'] == True]
        print(f"  Filter is_stable = True: {before} -> {len(df_coint)}")

        if df_coint.empty:
            print("[WARNING] No pairs survived the advanced filters.")
            df_coint.to_csv("2_cointegrated_pairs.csv", index=False)
            return df_coint

        # ── COMPOSITE SCORE (Safety-First Ranking) ───────────────────
        # Lower score = safer pair = higher on the list
        #
        # win_rate   30% — historical success rate is the strongest signal
        # half_life  25% — faster reversion = less risk exposure
        # hurst      20% — lower H = more mean-reverting (H<0.5 ideal)
        # p_value    15% — stronger cointegration evidence
        # total_trades 10% — more backtest samples = more confidence

        df_coint['rank_wr']     = df_coint['win_rate'].rank(ascending=False)
        df_coint['rank_hl']     = df_coint['half_life'].rank(ascending=True)
        df_coint['rank_hurst']  = df_coint['hurst'].rank(ascending=True)
        df_coint['rank_pval']   = df_coint['p_value'].rank(ascending=True)
        df_coint['rank_trades'] = df_coint['total_trades'].rank(ascending=False)

        df_coint['composite_score'] = (
            df_coint['rank_wr']     * 0.30 +
            df_coint['rank_hl']     * 0.25 +
            df_coint['rank_hurst']  * 0.20 +
            df_coint['rank_pval']   * 0.15 +
            df_coint['rank_trades'] * 0.10
        )

        # Score thap nhat = cap an toan nhat o tren cung
        df_coint = df_coint.sort_values("composite_score", ascending=True)

        # Xoa cot tam
        df_coint = df_coint.drop(columns=[
            'rank_wr', 'rank_hl', 'rank_hurst', 'rank_pval',
            'rank_trades', 'is_stable'
        ])

        # Luu file
        df_coint.to_csv("2_cointegrated_pairs.csv", index=False)
        print(f"[OK] {len(df_coint)} pairs survived — top pairs are safest.")
    return df_coint

