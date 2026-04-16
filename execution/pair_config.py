"""
PairConfig & PairState — Foundation for multi-pair trading.

PairConfig: immutable settings for one trading pair.
PairState:  mutable runtime state for one trading pair.
"""

from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass(frozen=True)
class PairConfig:
    """Immutable configuration for a single trading pair."""

    pair_id: str                        # e.g. "BTCUSDT_ETHUSDT"
    ticker_1: str                       # e.g. "BTCUSDT"
    ticker_2: str                       # e.g. "ETHUSDT"
    signal_positive_ticker: str         # ticker that is "positive" in strategy
    signal_negative_ticker: str         # ticker that is "negative" in strategy

    # ── Capital & Leverage ───────────────────────────────────────────────────
    allocated_capital: float = 50.0     # USDT allocated to this pair
    leverage: int = 2                   # leverage multiplier

    # ── Signal thresholds ────────────────────────────────────────────────────
    signal_trigger_thresh: float = 1.1  # z-score entry threshold
    exit_threshold: float = 0.0         # z-score exit (0 = mean reversion)
    custom_thresholds: bool = True      # use custom exit_threshold
    zscore_stop_loss: float = 10.0      # emergency z-score stop-loss
    time_stop_loss_hours: float = 48.0  # max hold time in hours
    max_session_loss_pct: float = 10.0  # halt pair if cumulative loss exceeds

    # ── Order settings ───────────────────────────────────────────────────────
    limit_order_basis: bool = True      # use aggressive limit orders
    auto_trade: bool = True             # auto-seek new trade after close
    stop_loss_fail_safe: float = 0.0    # price-based stop-loss (0 = disabled)
    market_order_zscore_thresh: float = 99.0  # z-score to auto-upgrade to market
    min_profit_pct: float = 0.0         # min expected profit for market order

    # ── Strategy params (must match strategy scan) ───────────────────────────
    timeframe: int = 60                 # candle timeframe in minutes
    kline_limit: int = 200              # number of candles for analysis
    z_score_window: int = 21            # rolling window for z-score

    # ── Fee ──────────────────────────────────────────────────────────────────
    taker_fee_pct: float = 0.055        # taker fee per side (%)

class PairState:
    """Mutable runtime state for a single trading pair."""

    def __init__(self, config: PairConfig):
        self.config = config

        # ── Trading state ────────────────────────────────────────────────────
        self.kill_switch: int = 0          # 0=SEEKING, 1=HOLDING, 2=CLOSING
        self.signal_side: str = ""         # "positive" or "negative"

        # ── Frozen parameters (set at trade entry) ───────────────────────────
        self.entry_hedge_ratio: Optional[float] = None
        self.entry_mean: Optional[float] = None
        self.entry_std: Optional[float] = None

        # ── Position tracking ────────────────────────────────────────────────
        self.position_open_time: float = 0.0
        self.baseline_realised_long: float = 0.0
        self.baseline_realised_short: float = 0.0

        # ── Session risk tracking ────────────────────────────────────────────
        self.session_realized_loss: float = 0.0
        self.last_close_pnl: float = 0.0

        # ── Live monitoring ──────────────────────────────────────────────────
        self.current_zscore: float = 0.0
        self.current_pnl: float = 0.0
        self.current_pnl_pct: float = 0.0
        self.trade_count: int = 0
        self.is_halted: bool = False       # True if circuit breaker tripped



    def reset_for_new_trade(self):
        """Reset state after closing a position, ready to seek new trades."""
        self.kill_switch = 0
        self.signal_side = ""
        self.entry_hedge_ratio = None
        self.entry_mean = None
        self.entry_std = None
        self.position_open_time = 0.0
        self.baseline_realised_long = 0.0
        self.baseline_realised_short = 0.0
        self.last_close_pnl = 0.0
        self.current_zscore = 0.0
        self.current_pnl = 0.0
        self.current_pnl_pct = 0.0


    def freeze_entry_params(self, hedge_ratio, mean, std):
        """Freeze parameters at trade entry time."""
        self.entry_hedge_ratio = hedge_ratio
        self.entry_mean = mean
        self.entry_std = std
        self.position_open_time = time.time()

    @property
    def hold_minutes(self) -> float:
        if self.position_open_time <= 0:
            return 0.0
        return (time.time() - self.position_open_time) / 60

    @property
    def hold_hours(self) -> float:
        return self.hold_minutes / 60

    def __repr__(self):
        return (f"PairState({self.config.pair_id}: "
                f"ks={self.kill_switch}, side={self.signal_side}, "
                f"z={self.current_zscore:.4f}, pnl={self.current_pnl:.3f})")
