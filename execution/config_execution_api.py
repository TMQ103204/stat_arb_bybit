"""
    API Documentation
    https://bybit-exchange.github.io/docs/v5/intro
"""

# API Imports
from pybit.unified_trading import HTTP
from dotenv import load_dotenv
import os
import logging

_cfg_logger = logging.getLogger("config")

# Load .env from project root
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# CONFIG VARIABLES
# mode options: "test" (testnet), "demo" (mainnet demo - real prices, virtual money), "live" (real money)
mode = "live"
ticker_1 = "NAORISUSDT"
ticker_2 = "PNUTUSDT"
signal_positive_ticker = ticker_2
signal_negative_ticker = ticker_1

limit_order_basis = True # will ensure positions (except for Close) will be placed on limit basis
auto_trade = False # If False, bot will gracefully stop instead of seeking new trades after a close

tradeable_capital_usdt = 12 # total tradeable capital to be split between both pairs
leverage = 1               # leverage multiplier (1x to 50x) — applied to both legs via set_leverage
stop_loss_fail_safe = 2 # stop loss at market order in case of drastic event
signal_trigger_thresh = 2 # z-score threshold which determines trade (must be above zero)
zscore_stop_loss = 5      # emergency stop-loss: absolute z-score beyond which all positions are closed at market
time_stop_loss_hours = 48 # maximum time in hours to hold a position before emergency close
max_session_loss_pct = 3.0 # halt bot entirely if cumulative session loss exceeds this % of tradeable capital

custom_thresholds = True  # If True, use custom exit_threshold; if False, exit at z-score 0 (mean reversion)
exit_threshold = 0.0       # custom z-score exit threshold (only used when custom_thresholds = True)

# If |z_score| >= market_order_zscore_thresh AND expected net profit >= min_profit_pct
# the bot will use Market orders instead of aggressive Limit orders.
# Set market_order_zscore_thresh very high (e.g. 99) to disable market order entirely.
market_order_zscore_thresh = 99  # DISABLED — always use limit orders
min_profit_pct = 0    # minimum expected net profit (%) to trigger market order (used by hybrid order logic)
taker_fee_pct = 0.055  # Bybit taker fee per side (%) — used for entry sizing estimates

timeframe = 60 # make sure matches your strategy
kline_limit = 200 # make sure matches your strategy
z_score_window = 21 # make sure matches your strategy

# API KEYS from .env
api_key_demo     = os.getenv("API_KEY_DEMO", "")
api_secret_demo  = os.getenv("API_SECRET_DEMO", "")
api_key_mainnet  = os.getenv("API_KEY_MAINNET", "")
api_secret_mainnet = os.getenv("API_SECRET_MAINNET", "")
api_key_testnet  = os.getenv("API_KEY_TESTNET", "")
api_secret_testnet = os.getenv("API_SECRET_TESTNET", "")

# SELECTED API
if mode == "test":
    api_key = api_key_testnet
    api_secret = api_secret_testnet
elif mode == "demo":
    api_key = api_key_demo
    api_secret = api_secret_demo
else:  # "live"
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

# ── Startup mode banner ──────────────────────────────────────────────────────
if mode == "live":
    _cfg_logger.warning(
        "\n"
        "╔══════════════════════════════════════════════════╗\n"
        "║  ⚠️  LIVE MODE — REAL MONEY TRADING ACTIVE  ⚠️   ║\n"
        "║  All orders will execute on Bybit MAINNET.       ║\n"
        "╚══════════════════════════════════════════════════╝"
    )
elif mode == "demo":
    _cfg_logger.info("[DEMO MODE] Using virtual money on Bybit demo environment.")
elif mode == "test":
    _cfg_logger.info("[TEST MODE] Using Bybit testnet.")
# ─────────────────────────────────────────────────────────────────────────────

# ── API Retry Wrapper ────────────────────────────────────────────────────────
import time

def retry_api_call(func, *args, max_retries=3, backoff_factor=1.5, **kwargs):
    """
    Executes a Bybit API call with automatic retries on network/connection errors.
    Useful for handling transient drops like ConnectionResetError (10054).
    """
    import urllib3.exceptions
    import requests.exceptions
    
    retry_exceptions = (
        ConnectionError,
        TimeoutError,
        urllib3.exceptions.ProtocolError,
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
    )
    
    last_exception = None
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except retry_exceptions as e:
            last_exception = e
            _cfg_logger.warning("API connection dropped (%s) — attempt %d/%d retrying in %.1fs", 
                              type(e).__name__, attempt + 1, max_retries, backoff_factor ** attempt)
            time.sleep(backoff_factor ** attempt)
        except Exception as e:
            # If it's a generic Exception but string contains connection clues, retry it too
            if "Connection aborted" in str(e) or "10054" in str(e) or "ConnectionResetError" in str(e):
                last_exception = e
                _cfg_logger.warning("API connection reset (%s) — attempt %d/%d retrying in %.1fs", 
                                  str(e)[:40], attempt + 1, max_retries, backoff_factor ** attempt)
                time.sleep(backoff_factor ** attempt)
            else:
                raise  # Unhandled exception (e.g. Invalid API Key), fail immediately
    
    _cfg_logger.error("API call failed permanently after %d retries: %s", max_retries, last_exception)
    raise last_exception
# ─────────────────────────────────────────────────────────────────────────────
