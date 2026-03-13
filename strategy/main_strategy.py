import warnings
warnings.simplefilter(action='ignore', category=FutureWarning)

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from func_get_symbols import get_tradeable_symbols
from func_prices_json import store_price_history
from func_cointegration import get_cointegrated_pairs
from func_plot_trends import plot_trends
import pandas as pd
import json


"""STRATEGY CODE"""
if __name__ == "__main__":

    coint_pairs = pd.DataFrame()

    # STEP 1 - Get list of symbols
    print("Getting symbols...")
    sym_response = get_tradeable_symbols()

    # STEP 2 - Construct and save price history
    print("Constructing and saving price data to JSON...")
    if len(sym_response) > 0:
        store_price_history(sym_response)

    # STEP 3 - Find Cointegrated pairs
    print("Calculating co-integration...")
    with open("1_price_list.json") as json_file:
        price_data = json.load(json_file)
        if len(price_data) > 0:
            coint_pairs = get_cointegrated_pairs(price_data)

    # STEP 4 - Plot trends and save for backtesting
    print("Plotting trends...")
    if not coint_pairs.empty:
        symbol_1 = coint_pairs.iloc[0]["sym_1"]
        symbol_2 = coint_pairs.iloc[0]["sym_2"]
        print(f"Best cointegrated pair: {symbol_1} vs {symbol_2}")
        with open("1_price_list.json") as json_file:
            price_data = json.load(json_file)
            if len(price_data) > 0:
                plot_trends(symbol_1, symbol_2, price_data)
    else:
        print("No cointegrated pairs found.")
