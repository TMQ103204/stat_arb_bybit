"""
    API Documentation
    https://bybit-exchange.github.io/docs/v5/intro
"""

# API Imports
from pybit.unified_trading import HTTP
from dotenv import load_dotenv
import os

# Load .env from project root
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# CONFIG VARIABLES
# mode options: "test" (testnet), "demo" (mainnet demo - real prices, virtual money), "live" (real money)
mode = "demo"
ticker_1 = "KASUSDT"
ticker_2 = "BEATUSDT"
signal_positive_ticker = ticker_2
signal_negative_ticker = ticker_1

limit_order_basis = True # will ensure positions (except for Close) will be placed on limit basis

tradeable_capital_usdt = 10000 # total tradeable capital to be split between both pairs
stop_loss_fail_safe = 0.15 # stop loss at market order in case of drastic event
signal_trigger_thresh = 1.1 # z-score threshold which determines trade (must be above zero)
zscore_stop_loss = 3      # emergency stop-loss: absolute z-score beyond which all positions are closed at market
time_stop_loss_hours = 48 # maximum time in hours to hold a position before emergency close

timeframe = 60 # make sure matches your strategy
kline_limit = 200 # make sure matches your strategy
z_score_window = 21 # make sure matches your strategy

# API KEYS from .env
api_key_mainnet = os.getenv("API_KEY_MAINNET", "")
api_secret_mainnet = os.getenv("API_SECRET_MAINNET", "")
api_key_testnet = os.getenv("API_KEY_TESTNET", "")
api_secret_testnet = os.getenv("API_SECRET_TESTNET", "")

# SELECTED API
if mode == "test":
    api_key = api_key_testnet
    api_secret = api_secret_testnet
else:
    api_key = api_key_mainnet
    api_secret = api_secret_mainnet

# SESSION Activation
if mode == "test":
    session_public = HTTP(testnet=True)
    session_private = HTTP(testnet=True, api_key=api_key, api_secret=api_secret)
elif mode == "demo":
    session_public = HTTP(demo=True)
    session_private = HTTP(demo=True, api_key=api_key, api_secret=api_secret)
else:
    session_public = HTTP()
    session_private = HTTP(api_key=api_key, api_secret=api_secret)
