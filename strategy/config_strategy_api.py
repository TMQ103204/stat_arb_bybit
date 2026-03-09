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

# CONFIG
# mode options: "test" (testnet), "demo" (mainnet demo - real prices, virtual money), "live" (real money)
mode = "demo"
timeframe = 60
kline_limit = 200
z_score_window = 21

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
    session = HTTP(testnet=True, api_key=api_key, api_secret=api_secret)
elif mode == "demo":
    session = HTTP(demo=True, api_key=api_key, api_secret=api_secret)
else:
    session = HTTP(api_key=api_key, api_secret=api_secret)
