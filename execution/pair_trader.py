"""
PairTrader — Encapsulates the complete trading lifecycle for a single pair.

This is the parameterized equivalent of `main_execution.py`'s while-True loop.
Each PairTrader owns one PairConfig + PairState and runs as an independent
thread within the PortfolioManager.
"""

import warnings
warnings.simplefilter(action='ignore', category=FutureWarning)

import os
import sys
import time
import logging
import json
from typing import Optional, cast

# Ensure project root is importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pair_config import PairConfig, PairState
from func_position_calls import open_position_confirmation, active_position_confirmation
from func_trade_management import manage_new_trades
from func_execution_calls import set_leverage
from func_close_positions import close_all_positions
from func_get_zscore import get_latest_zscore_with_hedge
from func_save_status import save_status
from func_calcultions import (calculate_exact_live_profit, get_wallet_equity,
                               snapshot_cumrealised_pnl)
from logger_setup import get_logger


class PairTrader:
    """Runs the full SEEKING → HOLDING → CLOSING lifecycle for one pair."""

    def __init__(self, config: PairConfig,
                 session_public, session_private, retry_fn,
                 portfolio_halt_check=None):
        """
        Args:
            config:             PairConfig for this pair.
            session_public:     Shared pybit HTTP session (public).
            session_private:    Shared pybit HTTP session (authenticated).
            retry_fn:           retry_api_call function.
            portfolio_halt_check: callable() -> bool from PortfolioManager.
                                  When True, this trader should stop seeking.
        """
        self.config = config
        self.state = PairState(config)
        self.session_pub = session_public
        self.session_priv = session_private
        self.retry_fn = retry_fn
        self.portfolio_halt_check = portfolio_halt_check or (lambda: False)

        # Per-pair logger — writes to pair-specific log file
        self.logger = get_logger(f"pair_{config.pair_id}")
        self._running = False

    # ── Convenience accessors ────────────────────────────────────────────────
    @property
    def pair_id(self) -> str:
        return self.config.pair_id

    @property
    def c(self) -> PairConfig:
        return self.config

    @property
    def s(self) -> PairState:
        return self.state

    def _common_kwargs(self) -> dict:
        """Common kwargs passed to parameterized execution functions.
        NOTE: does NOT include 'window' — manage_new_trades uses 'z_window',
        while zscore functions use 'window'. Pass explicitly where needed."""
        return dict(
            t1=self.c.ticker_1,
            t2=self.c.ticker_2,
            session_pub=self.session_pub,
            session_priv=self.session_priv,
            retry_fn=self.retry_fn,
            tf=self.c.timeframe,
            kl=self.c.kline_limit,
        )

    def _zscore_kwargs(self) -> dict:
        """Kwargs for get_latest_zscore_with_hedge (no session_priv)."""
        return dict(
            t1=self.c.ticker_1,
            t2=self.c.ticker_2,
            session_pub=self.session_pub,
            retry_fn=self.retry_fn,
            tf=self.c.timeframe,
            kl=self.c.kline_limit,
        )

    def _save_status(self, msg: str, extra: dict = None):
        """Save pair-specific status.json."""
        status = {
            "pair_id": self.pair_id,
            "ticker_1": self.c.ticker_1,
            "ticker_2": self.c.ticker_2,
            "message": msg,
            "kill_switch": self.s.kill_switch,
            "signal_side": self.s.signal_side,
            "current_zscore": round(self.s.current_zscore, 6),
            "current_pnl": round(self.s.current_pnl, 4),
            "current_pnl_pct": round(self.s.current_pnl_pct, 4),
            "hold_minutes": round(self.s.hold_minutes, 1),
            "trade_count": self.s.trade_count,
            "is_halted": self.s.is_halted,

        }
        if extra:
            status.update(extra)
        save_status(status, pair_id=self.pair_id)

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self):
        """Main trading loop — runs in a thread, blocks until stopped."""
        self._running = True
        self.logger.info("PairTrader started: %s (%s / %s)",
                         self.pair_id, self.c.ticker_1, self.c.ticker_2)

        # Set leverage
        effective_lev = self.c.leverage if self.c.custom_thresholds else 1
        self.logger.info("Setting leverage to %dx...", effective_lev)
        set_leverage(self.c.signal_positive_ticker, lev=effective_lev,
                     session_priv=self.session_priv)
        set_leverage(self.c.signal_negative_ticker, lev=effective_lev,
                     session_priv=self.session_priv)

        self._save_status("Seeking trades...")

        while self._running:
            try:
                time.sleep(2)

                # ── Portfolio-level halt ──────────────────────────────────────
                if self.portfolio_halt_check():
                    self.logger.warning("Portfolio halt signal received. Pausing.")
                    self._save_status("Paused (portfolio halt)")
                    time.sleep(10)
                    continue

                # ── Per-pair command signal (from dashboard) ──────────────────
                self._check_command_signal()
                if not self._running:
                    break

                self._tick()

            except Exception as e:
                self.logger.exception("Unexpected error in tick: %s", e)
                time.sleep(10)

        self.logger.info("PairTrader stopped: %s", self.pair_id)

    def stop(self):
        """Signal the run loop to stop."""
        self._running = False

    def _check_command_signal(self):
        """Check for per-pair command file from dashboard.
        
        Files: execution/cmd_{pair_id}.json
        Actions:
          - "close": close all positions for this pair, then stop
          - "pause": stop seeking/trading (no close)
        """
        cmd_file = os.path.join(os.path.dirname(__file__), f"cmd_{self.pair_id}.json")
        if not os.path.exists(cmd_file):
            return

        try:
            with open(cmd_file, "r") as f:
                cmd = json.load(f)
            os.remove(cmd_file)  # consume the command

            action = cmd.get("action", "")
            self.logger.info("Received command: %s", action)

            if action == "close":
                # Close positions then stop
                self.logger.info("CLOSE command: closing positions for %s", self.pair_id)
                if self.s.kill_switch == 1:
                    # Has open positions — close them
                    from func_close_positions import close_all_positions
                    long_t, short_t = self._resolve_leg_tickers()
                    close_all_positions(
                        kill_switch=self.s.kill_switch,
                        ticker_1=long_t, ticker_2=short_t,
                        session_priv=self.session_priv,
                        retry_fn=self.retry_fn,
                    )
                    self.s.kill_switch = 0
                    self.s.reset_for_new_trade()
                    self.logger.info("Positions closed for %s", self.pair_id)
                self._save_status("Closed by user")
                self._running = False

            elif action == "pause":
                self.logger.info("PAUSE command: stopping trader %s", self.pair_id)
                self._save_status("Paused by user")
                self._running = False

        except Exception as e:
            self.logger.warning("Error reading command signal: %s", e)

    # ── Single tick ──────────────────────────────────────────────────────────

    def _tick(self):
        """Execute one iteration of the main trading loop."""
        c = self.c
        s = self.s
        kw = self._common_kwargs()

        # Check positions on exchange
        is_p_open = open_position_confirmation(
            c.signal_positive_ticker, session_priv=self.session_priv, retry_fn=self.retry_fn)
        is_n_open = open_position_confirmation(
            c.signal_negative_ticker, session_priv=self.session_priv, retry_fn=self.retry_fn)
        is_p_active = active_position_confirmation(
            c.signal_positive_ticker, session_priv=self.session_priv, retry_fn=self.retry_fn)
        is_n_active = active_position_confirmation(
            c.signal_negative_ticker, session_priv=self.session_priv, retry_fn=self.retry_fn)

        has_p = is_p_open or is_p_active
        has_n = is_n_open or is_n_active
        both_legs = has_p and has_n
        half_leg = has_p ^ has_n
        no_positions = not has_p and not has_n

        # ── HALF-POSITION guard ──────────────────────────────────────────────
        if half_leg and s.kill_switch == 0:
            orphan = c.signal_positive_ticker if has_p else c.signal_negative_ticker
            self.logger.critical("HALF-POSITION DETECTED: %s has orphan leg. Closing.", orphan)
            time.sleep(3)
            close_all_positions(s.kill_switch,
                                pos_ticker=c.signal_positive_ticker,
                                neg_ticker=c.signal_negative_ticker,
                                session_priv=self.session_priv, retry_fn=self.retry_fn)
            self._save_status("Orphan half-position closed.")

        # ── SEEKING: no positions, look for new trades ───────────────────────
        if no_positions and s.kill_switch == 0:
            self._save_status("Seeking trades...")

            # Determine capital for this entry
            entry_capital = c.allocated_capital

            ks, side, h_ratio, mean, std = manage_new_trades(
                s.kill_switch,
                pos_ticker=c.signal_positive_ticker,
                neg_ticker=c.signal_negative_ticker,
                trigger_thresh=c.signal_trigger_thresh,
                stop_loss_z=c.zscore_stop_loss,
                capital=entry_capital,
                lev=c.leverage,
                limit_basis=c.limit_order_basis,
                sl_failsafe=c.stop_loss_fail_safe,
                market_thresh=c.market_order_zscore_thresh,
                min_profit=c.min_profit_pct,
                z_window=c.z_score_window,
                **kw,
            )
            s.kill_switch = ks
            s.signal_side = side

            if ks == 1:
                s.freeze_entry_params(h_ratio, mean, std)
                self.logger.info("Trade entered: hedge=%.6f mean=%.8f std=%.8f",
                                 h_ratio or 0, mean or 0, std or 0)
                # Snapshot PnL baseline
                long_t, short_t = self._resolve_leg_tickers()
                s.baseline_realised_long, s.baseline_realised_short = \
                    snapshot_cumrealised_pnl(long_t, short_t, session_priv=self.session_priv)



        # ── RE-ATTACH: both legs open but ks=0 (bot restart) ─────────────────
        if both_legs and s.kill_switch == 0:
            s.kill_switch = 1
            s.position_open_time = time.time()
            if not s.signal_side or s.entry_hedge_ratio is None:
                result = get_latest_zscore_with_hedge(window=self.c.z_score_window, **self._zscore_kwargs())
                if result is not None:
                    z_re, _, hr, mn, st = result
                    z_re = float(cast(float, z_re))
                    s.signal_side = "positive" if z_re > 0 else "negative"
                    s.freeze_entry_params(hr, mn, st)
                    self.logger.info("Re-attached: side=%s z=%.4f", s.signal_side, z_re)
                else:
                    s.signal_side = "positive"
                    self.logger.warning("Re-attached with fallback signal_side=positive")
            long_t, short_t = self._resolve_leg_tickers()
            s.baseline_realised_long, s.baseline_realised_short = \
                snapshot_cumrealised_pnl(long_t, short_t, session_priv=self.session_priv)
            self._save_status(f"Re-attached (side={s.signal_side})")

        # ── HOLDING: monitor and manage exit ─────────────────────────────────
        if s.kill_switch == 1:
            self._tick_holding()

        # ── CLOSING: close all positions ─────────────────────────────────────
        if s.kill_switch == 2:
            self._tick_closing()

    # ── HOLDING tick ─────────────────────────────────────────────────────────

    def _tick_holding(self):
        c = self.c
        s = self.s
        kw = self._common_kwargs()

        result = get_latest_zscore_with_hedge(
            s.entry_hedge_ratio, s.entry_mean, s.entry_std,
            window=c.z_score_window, **self._zscore_kwargs())
        if result is None:
            return
        zscore, _, _, _, _ = result
        zscore = float(cast(float, zscore))
        s.current_zscore = zscore

        target_exit = float(c.exit_threshold) if c.custom_thresholds else 0.0

        long_t, short_t = self._resolve_leg_tickers()
        pnl, pnl_pct = calculate_exact_live_profit(
            long_t, short_t, s.baseline_realised_long, s.baseline_realised_short,
            session_priv=self.session_priv)

        if pnl is None:
            self.logger.warning("PnL calc failed — skipping tick.")
            return

        s.current_pnl = pnl
        s.current_pnl_pct = pnl_pct
        s.last_close_pnl = pnl

        self.logger.info(
            "HOLDING | Z: %.4f | Side: %s | Hold: %.0fm | PnL: %.3f USDT (%.3f%%)",
            zscore, s.signal_side, s.hold_minutes, pnl, pnl_pct)
        self._save_status("Holding position...")

        # ── Exit rules ───────────────────────────────────────────────────────
        if abs(zscore) > float(c.zscore_stop_loss):
            self.logger.critical("Z-SCORE STOP LOSS: %.4f > %.4f", zscore, c.zscore_stop_loss)
            s.kill_switch = 2
        elif s.hold_hours > c.time_stop_loss_hours:
            self.logger.critical("TIME STOP LOSS: held %.1fh > %.1fh", s.hold_hours, c.time_stop_loss_hours)
            s.kill_switch = 2
        elif s.signal_side == "positive" and zscore < target_exit:
            self.logger.info("TAKE PROFIT: Z %.4f < exit %.4f", zscore, target_exit)
            s.kill_switch = 2
        elif s.signal_side == "negative" and zscore >= -target_exit:
            self.logger.info("TAKE PROFIT: Z %.4f >= exit %.4f", zscore, -target_exit)
            s.kill_switch = 2

    # ── CLOSING tick ─────────────────────────────────────────────────────────

    def _tick_closing(self):
        c = self.c
        s = self.s

        self.logger.info("Closing all positions...")
        self._save_status("Closing positions...")

        ks = close_all_positions(
            s.kill_switch,
            pos_ticker=c.signal_positive_ticker,
            neg_ticker=c.signal_negative_ticker,
            session_priv=self.session_priv, retry_fn=self.retry_fn)
        s.kill_switch = ks

        if ks == 0:
            # Verify positions are actually closed
            time.sleep(2)
            still_p = open_position_confirmation(
                c.signal_positive_ticker, session_priv=self.session_priv, retry_fn=self.retry_fn)
            still_n = open_position_confirmation(
                c.signal_negative_ticker, session_priv=self.session_priv, retry_fn=self.retry_fn)
            if still_p or still_n:
                self.logger.critical("CLOSE VERIFICATION FAILED: positions still open. Retrying...")
                s.kill_switch = 2
                return

        # ── Session loss circuit breaker ──────────────────────────────────────
        if s.last_close_pnl < 0:
            s.session_realized_loss += abs(s.last_close_pnl)

        wallet_info = get_wallet_equity(session_priv=self.session_priv)
        real_capital = wallet_info["starting_capital"] if wallet_info else 0.0
        loss_pct = (s.session_realized_loss / real_capital * 100) if real_capital > 0 else 0
        self.logger.info("Session loss: this=%.3f | cumulative=%.3f (%.2f%% of $%.2f)",
                         s.last_close_pnl, s.session_realized_loss, loss_pct, real_capital)

        if loss_pct >= c.max_session_loss_pct:
            self.logger.critical("SESSION LOSS LIMIT: %.2f%% >= %.2f%%. HALTING PAIR.",
                                 loss_pct, c.max_session_loss_pct)
            s.is_halted = True
            self._save_status(f"HALTED: session loss {loss_pct:.1f}%")
            self._running = False
            return

        s.trade_count += 1
        s.reset_for_new_trade()

        # ── auto_trade check: stop seeking if disabled ────────────────────────
        if not c.auto_trade:
            self.logger.info("auto_trade=False — stopping after close (no new trade).")
            self._save_status("Stopped (auto_trade=off)")
            self._running = False
            return

        self._save_status("Post-close cooldown...")

        # Cooldown before seeking next trade
        cooldown = 300  # 5 minutes
        self.logger.info("Post-close cooldown: %ds...", cooldown)
        time.sleep(cooldown)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _resolve_leg_tickers(self):
        """Return (long_ticker, short_ticker) based on current signal_side."""
        c = self.c
        s = self.s
        if s.signal_side == "positive":
            return c.signal_positive_ticker, c.signal_negative_ticker
        else:
            return c.signal_negative_ticker, c.signal_positive_ticker


