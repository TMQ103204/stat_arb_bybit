from config_strategy_api import (
    z_score_window, min_zero_crossings, timeframe, session,
    max_hurst, max_half_life_hours, max_net_funding_rate, min_backtest_profit_pct
)
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


# ── Advanced Filter: Backtest with REAL costs ────────────────────────────────
# Simulates actual trades with ALL costs: trading fees + funding fees.
# Returns real profitability metrics, not just "did z-score cross zero?".
def calculate_realistic_backtest(zscore_array, spread_array, avg_price_1,
                                  trigger_thresh=1.1, taker_fee_rate=0.00055,
                                  net_funding_rate_8h=0.0, timeframe_hours=1):
    """
    Simulate trades with ALL costs:
      1. Entry when |z| > trigger_thresh
      2. Exit when z crosses 0
      3. Deduct: 4 × taker_fee_rate per round-trip (open+close 2 legs)
      4. Deduct: net funding cost based on hold time
      5. Calculate: actual % P&L per trade

    Args:
        zscore_array:       array of z-scores
        spread_array:       array of spread values (same length)
        avg_price_1:        average price of sym_1 (for % calculation)
        trigger_thresh:     z-score threshold to enter
        taker_fee_rate:     per-side taker fee (e.g. 0.00055 = 0.055%)
        net_funding_rate_8h: net funding cost per 8h for the pair
        timeframe_hours:    hours per candle

    Returns:
        (avg_net_profit_pct, win_rate, total_trades, profit_factor)
    """
    trades = []
    in_trade = False
    entry_spread = 0.0
    entry_idx = 0
    trade_side = None

    for i in range(len(zscore_array)):
        z = zscore_array[i]
        if np.isnan(z):
            continue

        if not in_trade:
            if abs(z) > trigger_thresh:
                in_trade = True
                trade_side = "positive" if z > 0 else "negative"
                entry_spread = spread_array[i]
                entry_idx = i
        else:
            # Check for mean reversion (z crosses 0)
            should_exit = (
                (trade_side == "positive" and z < 0) or
                (trade_side == "negative" and z >= 0)
            )
            if should_exit:
                exit_spread = spread_array[i]
                hold_candles = i - entry_idx
                hold_hours = hold_candles * timeframe_hours

                # P&L from spread movement
                if trade_side == "positive":
                    # Positive z → short spread → profit when spread FALLS
                    spread_pnl = entry_spread - exit_spread
                else:
                    # Negative z → long spread → profit when spread RISES
                    spread_pnl = exit_spread - entry_spread

                # Convert to % (relative to notional per unit of sym_1)
                spread_pnl_pct = (spread_pnl / avg_price_1) * 100 if avg_price_1 > 0 else 0

                # Costs in %
                trading_fee_pct = taker_fee_rate * 4 * 100  # 4 legs
                funding_cost_pct = abs(net_funding_rate_8h) * (hold_hours / 8) * 2 * 100  # 2 legs

                net_pnl_pct = spread_pnl_pct - trading_fee_pct - funding_cost_pct

                trades.append({
                    "net_pnl_pct": net_pnl_pct,
                    "spread_pnl_pct": spread_pnl_pct,
                    "costs_pct": trading_fee_pct + funding_cost_pct,
                    "hold_hours": hold_hours,
                })

                in_trade = False
                trade_side = None

    # If still in a trade at end of data → count as loss
    if in_trade and len(spread_array) > 0:
        exit_spread = spread_array[-1]
        hold_candles = len(spread_array) - 1 - entry_idx
        hold_hours = hold_candles * timeframe_hours

        if trade_side == "positive":
            spread_pnl = entry_spread - exit_spread
        else:
            spread_pnl = exit_spread - entry_spread

        spread_pnl_pct = (spread_pnl / avg_price_1) * 100 if avg_price_1 > 0 else 0
        trading_fee_pct = taker_fee_rate * 4 * 100
        funding_cost_pct = abs(net_funding_rate_8h) * (hold_hours / 8) * 2 * 100
        net_pnl_pct = spread_pnl_pct - trading_fee_pct - funding_cost_pct

        trades.append({"net_pnl_pct": net_pnl_pct, "spread_pnl_pct": spread_pnl_pct,
                        "costs_pct": trading_fee_pct + funding_cost_pct,
                        "hold_hours": hold_hours})

    if len(trades) == 0:
        return 0.0, 0.0, 0, 0.0

    pnl_list = [t["net_pnl_pct"] for t in trades]
    profits = [p for p in pnl_list if p > 0]
    losses = [abs(p) for p in pnl_list if p <= 0]

    avg_net_profit = float(np.mean(pnl_list))
    win_rate = len(profits) / len(trades)
    profit_factor = sum(profits) / sum(losses) if sum(losses) > 0 else (999.0 if profits else 0.0)

    return round(avg_net_profit, 4), round(win_rate, 4), len(trades), round(profit_factor, 4)


# ── Fetch Funding Rates from Bybit API ──────────────────────────────────────
_funding_rate_cache = {}

def fetch_all_funding_rates():
    """Fetch current funding rates for all linear USDT symbols from Bybit.
    Returns dict: {symbol: funding_rate_per_8h}
    Uses get_tickers which returns fundingRate for each symbol.
    """
    global _funding_rate_cache
    if _funding_rate_cache:
        return _funding_rate_cache

    try:
        resp = session.get_tickers(category="linear")
        ret_code = resp.get("retCode", -1)
        if ret_code != 0:
            print(f"  [WARN] get_tickers failed: {resp.get('retMsg', '')}")
            return {}
        tickers = resp.get("result", {}).get("list", [])
        for t in tickers:
            sym = t.get("symbol", "")
            fr = t.get("fundingRate", "0")
            try:
                _funding_rate_cache[sym] = float(fr)
            except (ValueError, TypeError):
                _funding_rate_cache[sym] = 0.0
        print(f"  Fetched funding rates for {len(_funding_rate_cache)} symbols")
    except Exception as e:
        print(f"  [WARN] Failed to fetch funding rates: {e}")

    return _funding_rate_cache


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
def run_advanced_filters(series_1, series_2, spread, sym_1, sym_2, funding_rates):
    half_life = calculate_half_life(spread)
    hurst = calculate_hurst_exponent(spread)
    is_stable = check_rolling_stability(
        np.array(series_1, dtype=float),
        np.array(series_2, dtype=float)
    )

    # Net funding rate: if we long sym_1 and short sym_2,
    # we PAY funding on long (positive = we pay) and RECEIVE on short (positive = we receive)
    # net_funding = -funding_1 + funding_2  (absolute worst case: use abs)
    fr_1 = funding_rates.get(sym_1, 0.0)
    fr_2 = funding_rates.get(sym_2, 0.0)
    net_funding_rate = abs(fr_1) + abs(fr_2)  # worst case: both negative

    # Realistic backtest with ALL costs
    zscore_array = calculate_zscore(spread)
    spread_array = np.array(spread, dtype=float)
    avg_price_1 = float(np.mean(series_1)) if len(series_1) > 0 else 1.0
    timeframe_hours = timeframe / 60  # Convert minutes to hours

    avg_net_profit, win_rate, total_trades, profit_factor = calculate_realistic_backtest(
        zscore_array, spread_array, avg_price_1,
        trigger_thresh=1.1,
        taker_fee_rate=0.00055,
        net_funding_rate_8h=net_funding_rate,
        timeframe_hours=timeframe_hours,
    )

    # ── pct_per_zscore: how much % profit does 1 z-score move represent? ──
    # This explains why some pairs with z=1 are very profitable while others
    # with z=4 barely break even. High pct_per_zscore = each z-score is "worth more".
    spread_series = pd.Series(spread_array)
    rolling_std = spread_series.rolling(window=z_score_window).std().iloc[-1]
    pct_per_zscore = round((rolling_std / avg_price_1) * 100, 4) if avg_price_1 > 0 else 0.0

    return (half_life, hurst, is_stable, win_rate, total_trades,
            avg_net_profit, profit_factor, net_funding_rate, pct_per_zscore)


# Put close prices into a list
def extract_close_prices(prices):
    close_prices = []
    for price_values in prices:
        close_price = float(price_values["close"])
        if math.isnan(close_price):
            return []
        close_prices.append(close_price)
    return close_prices


# Calculate cointegrated pairs (NET PROFITABILITY pipeline)
def get_cointegrated_pairs(prices):

    # ── Duplicate-asset detection ────────────────────────────────────────
    def _extract_base_asset(symbol):
        s = symbol.upper()
        for suffix in ("USDT", "PERP", "USD"):
            if s.endswith(suffix):
                s = s[:-len(suffix)]
        s = re.sub(r'[-]?\d{2}[A-Z]{3}\d{2,4}$', '', s)
        s = re.sub(r'[-]?\d{4,}$', '', s)
        if s.startswith("1000") and len(s) > 4:
            s = s[4:]
        return s

    # ── Fetch funding rates from Bybit (once) ────────────────────────────
    print("Fetching live funding rates from Bybit...")
    funding_rates = fetch_all_funding_rates()

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

            # STEP 2: Advanced filters WITH realistic backtest
            (half_life, hurst, is_stable, win_rate, total_trades,
             avg_net_profit, profit_factor, net_funding_rate,
             pct_per_zscore) = run_advanced_filters(
                series_1, series_2, basic["spread"], sym_1, sym_2, funding_rates
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
                "total_trades": total_trades,
                "avg_net_profit": avg_net_profit,
                "profit_factor": profit_factor,
                "net_funding_rate": round(net_funding_rate, 6),
                "pct_per_zscore": pct_per_zscore,
            })

    print(f"Done: {checked} pairs checked, {basic_pass} passed basic, "
          f"{duplicate_asset_rejected} duplicate-asset rejected, "
          f"{len(coint_pair_list)} total candidates.")

    # Output results
    df_coint = pd.DataFrame(coint_pair_list)
    if not df_coint.empty:
        total_before = len(df_coint)

        # ══════════════════════════════════════════════════════════════
        # HARD FILTERS — aggressive quality gates
        # ══════════════════════════════════════════════════════════════

        # 1. Hedge ratio (too extreme = unbalanced position)
        df_coint = df_coint[(df_coint['hedge_ratio'] >= 0.2) & (df_coint['hedge_ratio'] <= 5.0)]
        print(f"  Filter hedge_ratio [0.2-5.0]: {total_before} -> {len(df_coint)}")

        # 2. Half-life: too short = noise, too long = capital locked
        before = len(df_coint)
        hl_max = max_half_life_hours  # from config (default 24)
        df_coint = df_coint[(df_coint['half_life'] >= 1) & (df_coint['half_life'] <= hl_max)]
        print(f"  Filter half_life [1-{hl_max}]: {before} -> {len(df_coint)}")

        # 3. ★ NEW: Hurst exponent HARD FILTER — reject trending pairs
        before = len(df_coint)
        df_coint = df_coint[df_coint['hurst'] < max_hurst]
        print(f"  Filter hurst < {max_hurst}: {before} -> {len(df_coint)}")

        # 4. Rolling stability: REMOVED as hard filter.
        # With only 200 candles (100 per half), the split-half cointegration test
        # is too noisy and rejects ALL profitable pairs. The realistic backtest
        # profitability filter (step 8) is a much more reliable quality gate.
        # is_stable is kept as informational data in the output.
        stable_count = len(df_coint[df_coint['is_stable'] == True])
        print(f"  Info: {stable_count}/{len(df_coint)} pairs have is_stable=True (not filtered)")

        # 5. ★ NEW: Net funding rate — reject high-cost pairs
        before = len(df_coint)
        df_coint = df_coint[df_coint['net_funding_rate'] <= max_net_funding_rate]
        print(f"  Filter net_funding <= {max_net_funding_rate}: {before} -> {len(df_coint)}")

        # 6. Enough trades in backtest
        before = len(df_coint)
        df_coint = df_coint[df_coint['total_trades'] >= 3]
        print(f"  Filter total_trades >= 3: {before} -> {len(df_coint)}")

        # 7. ★ NEW: Win rate after fees ≥ 50%
        before = len(df_coint)
        df_coint = df_coint[df_coint['win_rate'] >= 0.5]
        print(f"  Filter win_rate >= 0.5 (after fees): {before} -> {len(df_coint)}")

        # 8. ★ NEW: Average net profit > 0 (MUST be profitable after ALL costs)
        before = len(df_coint)
        df_coint = df_coint[df_coint['avg_net_profit'] > min_backtest_profit_pct]
        print(f"  Filter avg_net_profit > {min_backtest_profit_pct}%: {before} -> {len(df_coint)}")

        if df_coint.empty:
            print("[WARNING] No pairs survived the quality filters. "
                  "This means no pair is TRULY profitable after fees + funding. "
                  "Consider loosening filters or trying a different timeframe.")
            df_coint.to_csv("2_cointegrated_pairs.csv", index=False)
            return df_coint

        # ══════════════════════════════════════════════════════════════
        # NET PROFITABILITY RANKING
        # ══════════════════════════════════════════════════════════════
        # Higher = better for all metrics (descending rank = lower score = top)
        #
        # avg_net_profit  35% — THE most important: actual $ profit after costs
        # half_life       25% — faster reversion = less risk + less funding
        # hurst           20% — lower H = stronger mean-reversion tendency
        # profit_factor   10% — ratio of gross profits to gross losses
        # p_value         10% — strength of cointegration evidence

        df_coint['rank_profit'] = df_coint['avg_net_profit'].rank(ascending=False)
        df_coint['rank_hl']     = df_coint['half_life'].rank(ascending=True)
        df_coint['rank_hurst']  = df_coint['hurst'].rank(ascending=True)
        df_coint['rank_pf']     = df_coint['profit_factor'].rank(ascending=False)
        df_coint['rank_pval']   = df_coint['p_value'].rank(ascending=True)

        df_coint['composite_score'] = (
            df_coint['rank_profit'] * 0.35 +
            df_coint['rank_hl']     * 0.25 +
            df_coint['rank_hurst']  * 0.20 +
            df_coint['rank_pf']     * 0.10 +
            df_coint['rank_pval']   * 0.10
        )

        # Lowest score = best pair on top
        df_coint = df_coint.sort_values("composite_score", ascending=True)

        # Drop temp columns
        df_coint = df_coint.drop(columns=[
            'rank_profit', 'rank_hl', 'rank_hurst', 'rank_pf',
            'rank_pval', 'is_stable'
        ])

        # Save
        df_coint.to_csv("2_cointegrated_pairs.csv", index=False)
        print(f"\n{'='*60}")
        print(f"[OK] {len(df_coint)} pairs survived NET PROFITABILITY filter")
        print(f"{'='*60}")
        if len(df_coint) > 0:
            top = df_coint.iloc[0]
            print(f"  #1: {top['sym_1']} / {top['sym_2']}")
            print(f"      avg_net_profit: {top['avg_net_profit']:.4f}%")
            print(f"      pct_per_zscore: {top['pct_per_zscore']:.4f}% (profit per 1σ move)")
            print(f"      win_rate: {top['win_rate']:.0%}")
            print(f"      profit_factor: {top['profit_factor']:.2f}")
            print(f"      half_life: {top['half_life']:.1f}h")
            print(f"      hurst: {top['hurst']:.4f}")
            print(f"      net_funding: {top['net_funding_rate']:.6f}")
            print(f"      total_trades: {top['total_trades']}")
    return df_coint

