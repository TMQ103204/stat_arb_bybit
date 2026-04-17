"""
Portfolio-level configuration for multi-pair StatArb trading.

This file defines:
  1. Which pairs to trade simultaneously
  2. Portfolio-level risk limits
  3. API session initialization (shared across all pairs)

Edit ACTIVE_PAIRS to add/remove pairs. Each pair gets its own PairConfig.
"""

import os
import sys
import logging

# Ensure project root is importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pair_config import PairConfig
from pybit.unified_trading import HTTP
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_logger = logging.getLogger("portfolio_config")

# ═══════════════════════════════════════════════════════════════════════════════
# MODE: synced from config_execution_api.py (can be "demo" / "live" / "test")
# ═══════════════════════════════════════════════════════════════════════════════
try:
    from config_execution_api import mode as _exec_mode
    MODE = _exec_mode
except ImportError:
    MODE = "demo"

# ═══════════════════════════════════════════════════════════════════════════════
# ACTIVE PAIRS — Add pairs from strategy scan results here
# ═══════════════════════════════════════════════════════════════════════════════
ACTIVE_PAIRS = [
    PairConfig(
        pair_id="IP_SNX",
        ticker_1="IPUSDT",
        ticker_2="SNXUSDT",
        signal_positive_ticker="SNXUSDT",
        signal_negative_ticker="IPUSDT",
        allocated_capital=10,
        leverage=2,
        signal_trigger_thresh=1.1,
        exit_threshold=0,
        custom_thresholds=True,
        zscore_stop_loss=10,
        stop_loss_fail_safe=0,
        auto_trade=True,
        time_stop_loss_hours=48,
        max_session_loss_pct=10.0,
        limit_order_basis=True,
        timeframe=60,
        kline_limit=200,
        z_score_window=21,
    ),
    PairConfig(
        pair_id="ENSO_RENDER",
        ticker_1="ENSOUSDT",
        ticker_2="RENDERUSDT",
        signal_positive_ticker="RENDERUSDT",
        signal_negative_ticker="ENSOUSDT",
        allocated_capital=10,
        leverage=2,
        signal_trigger_thresh=1.1,
        exit_threshold=0,
        custom_thresholds=True,
        zscore_stop_loss=10,
        stop_loss_fail_safe=0,
        auto_trade=True,
        time_stop_loss_hours=48,
        max_session_loss_pct=10.0,
        limit_order_basis=True,
        timeframe=60,
        kline_limit=200,
        z_score_window=21,
    ),
    PairConfig(
        pair_id="AKT_CRV",
        ticker_1="AKTUSDT",
        ticker_2="CRVUSDT",
        signal_positive_ticker="CRVUSDT",
        signal_negative_ticker="AKTUSDT",
        allocated_capital=10,
        leverage=2,
        signal_trigger_thresh=1.1,
        exit_threshold=0,
        custom_thresholds=True,
        zscore_stop_loss=10,
        stop_loss_fail_safe=0,
        auto_trade=True,
        time_stop_loss_hours=48,
        max_session_loss_pct=10.0,
        limit_order_basis=True,
        timeframe=60,
        kline_limit=200,
        z_score_window=21,
    ),
    PairConfig(
        pair_id="CC_ENA",
        ticker_1="CCUSDT",
        ticker_2="ENAUSDT",
        signal_positive_ticker="ENAUSDT",
        signal_negative_ticker="CCUSDT",
        allocated_capital=10,
        leverage=2,
        signal_trigger_thresh=1.1,
        exit_threshold=0,
        custom_thresholds=True,
        zscore_stop_loss=10,
        stop_loss_fail_safe=0,
        auto_trade=True,
        time_stop_loss_hours=48,
        max_session_loss_pct=10.0,
        limit_order_basis=True,
        timeframe=60,
        kline_limit=200,
        z_score_window=21,
    ),
    PairConfig(
        pair_id="JUP_MIRA",
        ticker_1="JUPUSDT",
        ticker_2="MIRAUSDT",
        signal_positive_ticker="MIRAUSDT",
        signal_negative_ticker="JUPUSDT",
        allocated_capital=10,
        leverage=2,
        signal_trigger_thresh=1.1,
        exit_threshold=0,
        custom_thresholds=True,
        zscore_stop_loss=10,
        stop_loss_fail_safe=0,
        auto_trade=True,
        time_stop_loss_hours=48,
        max_session_loss_pct=10.0,
        limit_order_basis=True,
        timeframe=60,
        kline_limit=200,
        z_score_window=21,
    ),
]

# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO-LEVEL RISK LIMITS
# ═══════════════════════════════════════════════════════════════════════════════
MAX_TOTAL_EXPOSURE_USDT = 500       # Max total notional across all pairs
MAX_PAIRS_SIMULTANEOUS = 5          # Max number of concurrent active pairs
MAX_PORTFOLIO_DRAWDOWN_PCT = 15.0   # Halt everything if portfolio drops 15%
POST_CLOSE_COOLDOWN_SEC = 300       # Cooldown between trades per pair

# ═══════════════════════════════════════════════════════════════════════════════
# AUTO PAIR ROTATION
# ═══════════════════════════════════════════════════════════════════════════════
AUTO_ROTATION_ENABLED = False       # Enable automatic pair rotation
SCAN_INTERVAL_HOURS = 6             # How often to re-scan strategy (hours)
ROTATION_BUFFER = 0.2               # Min score delta to trigger rotation (0-1)
MAX_ROTATIONS_PER_CYCLE = 1         # Max pairs to rotate per scan cycle
ROTATION_COOLDOWN_MIN = 30          # Min minutes between rotations

# ═══════════════════════════════════════════════════════════════════════════════
# API SESSION SETUP — Create SHARED sessions once for all pairs
# ═══════════════════════════════════════════════════════════════════════════════

def create_sessions(mode: str = None):
    """Create and return (session_public, session_private, retry_fn) for the given mode."""
    if mode is None:
        mode = MODE

    # Load API keys from .env
    api_key_demo       = os.getenv("API_KEY_DEMO", "")
    api_secret_demo    = os.getenv("API_SECRET_DEMO", "")
    api_key_mainnet    = os.getenv("API_KEY_MAINNET", "")
    api_secret_mainnet = os.getenv("API_SECRET_MAINNET", "")
    api_key_testnet    = os.getenv("API_KEY_TESTNET", "")
    api_secret_testnet = os.getenv("API_SECRET_TESTNET", "")

    if mode == "test":
        api_key = api_key_testnet
        api_secret = api_secret_testnet
        session_pub = HTTP(testnet=True)
        session_priv = HTTP(testnet=True, api_key=api_key, api_secret=api_secret)
    elif mode == "demo":
        api_key = api_key_demo
        api_secret = api_secret_demo
        session_pub = HTTP(demo=True)
        session_priv = HTTP(demo=True, api_key=api_key, api_secret=api_secret)
    else:  # "live"
        api_key = api_key_mainnet
        api_secret = api_secret_mainnet
        session_pub = HTTP()
        session_priv = HTTP(api_key=api_key, api_secret=api_secret)

    # Mode banner
    if mode == "live":
        _logger.warning(
            "\n"
            "╔══════════════════════════════════════════════════╗\n"
            "║  ⚠️  LIVE MODE — REAL MONEY TRADING ACTIVE  ⚠️  ║\n"
            "║  All orders will execute on Bybit MAINNET.       ║\n"
            "╚══════════════════════════════════════════════════╝"
        )
    elif mode == "demo":
        _logger.info("[DEMO MODE] Using virtual money on Bybit demo environment.")
    elif mode == "test":
        _logger.info("[TEST MODE] Using Bybit testnet.")

    # Import retry wrapper from existing config
    from config_execution_api import retry_api_call

    return session_pub, session_priv, retry_api_call
