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


# Calculate co-integration
def calculate_cointegration(series_1, series_2):
    coint_flag = 0
    try:
        coint_res = coint(series_1, series_2)
    except ValueError:
        return (0, 1, 0, 0, 0, 0)
    coint_t = float(coint_res[0])
    p_value = float(coint_res[1])
    critical_value = float(coint_res[2][1])
    model = sm.OLS(series_1, series_2).fit()
    hedge_ratio = float(model.params[0])
    spread = calculate_spread(series_1, series_2, hedge_ratio)
    zero_crossings = int(len(np.where(np.diff(np.sign(spread)))[0]))
    if p_value < 0.05 and coint_t < critical_value and zero_crossings >= min_zero_crossings:
        coint_flag = 1
    return (coint_flag, round(p_value, 4), round(coint_t, 2), round(critical_value, 2), round(hedge_ratio, 5), zero_crossings)


# Put close prices into a list
def extract_close_prices(prices):
    close_prices = []
    for price_values in prices:
        close_price = float(price_values["close"])
        if math.isnan(close_price):
            return []
        close_prices.append(close_price)
    return close_prices


# Calculate cointegrated pairs
def get_cointegrated_pairs(prices):

    # Loop through coins and check for co-integration
    coint_pair_list = []
    included_list = []
    for sym_1 in prices.keys():

        # Check each coin against the first (sym_1)
        for sym_2 in prices.keys():
            if sym_2 != sym_1:

                # Get unique combination id and ensure one off check
                sorted_characters = sorted(sym_1 + sym_2)
                unique = "".join(sorted_characters)
                if unique in included_list:
                    continue

                # Get close prices
                series_1 = extract_close_prices(prices[sym_1])
                series_2 = extract_close_prices(prices[sym_2])

                # Check for cointegration and add cointegrated pair
                coint_flag, p_value, t_value, c_value, hedge_ratio, zero_crossings = calculate_cointegration(series_1, series_2)
                if coint_flag == 1:
                    included_list.append(unique)
                    coint_pair_list.append({
                        "sym_1": sym_1,
                        "sym_2": sym_2,
                        "p_value": p_value,
                        "t_value": t_value,
                        "c_value": c_value,
                        "hedge_ratio": hedge_ratio,
                        "zero_crossings": zero_crossings
                    })

    # Output results
    df_coint = pd.DataFrame(coint_pair_list)
    if not df_coint.empty:
        # Bước 1: Lọc bỏ các cặp có hedge_ratio phi thực tế (Tùy chỉnh ngưỡng cho phù hợp với mức vốn)
        df_coint = df_coint[(df_coint['hedge_ratio'] >= 0.01) & (df_coint['hedge_ratio'] <= 100)]
        
        # Bước 2: Tính toán thứ hạng (Rank) độc lập cho từng tiêu chí
        # zero_crossings: Giá trị càng cao, thứ hạng càng tốt (ascending=False)
        df_coint['rank_zero'] = df_coint['zero_crossings'].rank(ascending=False)
        
        # t_value: Càng âm sâu càng tốt (ascending=True)
        df_coint['rank_t_val'] = df_coint['t_value'].rank(ascending=True)
        
        # p_value: Càng sát 0 càng tốt (ascending=True)
        df_coint['rank_p_val'] = df_coint['p_value'].rank(ascending=True)
        
        # Bước 3: Tính điểm tổng hợp (Composite Score). Điểm càng THẤP thì cặp đó càng lý tưởng.
        # Gán trọng số. Ví dụ: t_value (40%), zero_crossings (40%), p_value (20%)
        df_coint['composite_score'] = (df_coint['rank_t_val'] * 0.4) + \
                                      (df_coint['rank_zero'] * 0.4) + \
                                      (df_coint['rank_p_val'] * 0.2)
        
        # Bước 4: Sắp xếp danh sách dựa trên điểm tổng hợp từ tốt nhất (nhỏ nhất) đến kém nhất
        df_coint = df_coint.sort_values("composite_score", ascending=True)
        
        # (Tùy chọn) Xóa các cột thứ hạng tạm thời để file csv xuất ra gọn gàng hơn
        df_coint = df_coint.drop(columns=['rank_zero', 'rank_t_val', 'rank_p_val'])
        
        # Lưu file
        df_coint.to_csv("2_cointegrated_pairs.csv", index=False)
    return df_coint
