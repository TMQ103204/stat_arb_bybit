"""
PortfolioManager — Orchestrates multiple PairTrader instances.

Responsibilities:
  1. Create and manage threads for each PairTrader
  2. Monitor portfolio-level risk (total drawdown, correlation)
  3. Provide a centralized halt mechanism
  4. Report aggregate status to dashboard
"""

import warnings
warnings.simplefilter(action='ignore', category=FutureWarning)

import os
import sys
import time
import json
import threading
import logging

# Ensure project root is importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pair_config import PairConfig, PairState
from pair_trader import PairTrader
from pair_rotator import PairRotator
from func_calcultions import get_wallet_equity

logger = logging.getLogger("portfolio")


class PortfolioManager:
    """Manages multiple PairTrader instances running concurrently in threads."""

    def __init__(self, pairs: list, session_pub, session_priv, retry_fn,
                 max_drawdown_pct: float = 15.0,
                 max_total_exposure: float = 500.0,
                 rotation_config: dict = None):
        """
        Args:
            pairs:              List of PairConfig objects.
            session_pub:        Shared public HTTP session.
            session_priv:       Shared private HTTP session.
            retry_fn:           retry_api_call function.
            max_drawdown_pct:   Halt all trading if portfolio drawdown exceeds this %.
            max_total_exposure: Max total notional across all pairs (USDT).
            rotation_config:    Dict of PairRotator kwargs (or None to disable).
        """
        self.session_pub = session_pub
        self.session_priv = session_priv
        self.retry_fn = retry_fn
        self.max_drawdown_pct = max_drawdown_pct
        self.max_total_exposure = max_total_exposure

        self._portfolio_halt = False
        self._lock = threading.Lock()

        # Create PairTrader instances (dict for O(1) lookup)
        self.traders: dict[str, PairTrader] = {}
        for pc in pairs:
            trader = PairTrader(
                config=pc,
                session_public=session_pub,
                session_private=session_priv,
                retry_fn=retry_fn,
                portfolio_halt_check=self._is_halted,
            )
            self.traders[pc.pair_id] = trader

        self._threads: dict[str, threading.Thread] = {}
        self._monitor_thread: threading.Thread = None

        # Auto pair rotator
        self._rotator = None
        if rotation_config:
            self._rotator = PairRotator(**rotation_config)

        logger.info("PortfolioManager initialized with %d pairs:", len(self.traders))
        for pid, t in self.traders.items():
            logger.info("  • %s: %s / %s ($%.0f, %dx lev)",
                        t.pair_id, t.c.ticker_1, t.c.ticker_2,
                        t.c.allocated_capital, t.c.leverage)

    def _is_halted(self) -> bool:
        """Thread-safe check for portfolio-level halt."""
        return self._portfolio_halt

    def start(self):
        """Start all PairTrader threads + the portfolio monitor thread."""
        logger.info("Starting %d pair trader threads...", len(self.traders))

        for pid, trader in self.traders.items():
            t = threading.Thread(
                target=trader.run,
                name=f"PairTrader-{trader.pair_id}",
                daemon=True,
            )
            t.start()
            self._threads[pid] = t
            logger.info("  Started thread: %s", t.name)

        # Start portfolio monitor
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="PortfolioMonitor",
            daemon=True,
        )
        self._monitor_thread.start()
        logger.info("Portfolio monitor started.")

        # Start auto rotation if configured
        if self._rotator:
            self._rotator.start(self)
            logger.info("Auto pair rotator started.")

    def _monitor_loop(self):
        """Background loop: check portfolio-level risk every 10 seconds."""
        initial_equity = None

        while True:
            try:
                time.sleep(10)

                # Get wallet equity
                wallet = get_wallet_equity(session_priv=self.session_priv)
                if wallet is None:
                    continue

                current_equity = wallet["equity"]
                if initial_equity is None:
                    initial_equity = current_equity
                    logger.info("Portfolio initial equity: $%.2f", initial_equity)
                    continue

                # Calculate portfolio drawdown
                drawdown_pct = ((initial_equity - current_equity) / initial_equity * 100) \
                    if initial_equity > 0 else 0

                # Aggregate pair statuses
                active_pairs = sum(1 for t in self.traders.values() if t.state.kill_switch == 1)
                seeking_pairs = sum(1 for t in self.traders.values() if t.state.kill_switch == 0 and t._running)
                halted_pairs = sum(1 for t in self.traders.values() if t.state.is_halted)
                total_pnl = sum(t.state.current_pnl for t in self.traders.values())

                # Save aggregate portfolio status
                self._save_portfolio_status(
                    equity=current_equity,
                    drawdown_pct=drawdown_pct,
                    active_pairs=active_pairs,
                    seeking_pairs=seeking_pairs,
                    halted_pairs=halted_pairs,
                    total_pnl=total_pnl,
                )

                # Check drawdown limit
                if drawdown_pct >= self.max_drawdown_pct:
                    logger.critical(
                        "PORTFOLIO DRAWDOWN LIMIT: %.2f%% >= %.2f%%. HALTING ALL PAIRS.",
                        drawdown_pct, self.max_drawdown_pct
                    )
                    self._halt_all()

                # Periodic log
                if int(time.time()) % 60 < 10:  # ~every minute
                    logger.info(
                        "Portfolio: $%.2f (DD: %.2f%%) | Active: %d | Seeking: %d | Halted: %d | PnL: $%.3f",
                        current_equity, drawdown_pct, active_pairs, seeking_pairs, halted_pairs, total_pnl
                    )

            except Exception as e:
                logger.exception("Portfolio monitor error: %s", e)
                time.sleep(10)

    def _halt_all(self):
        """Trigger portfolio-level halt — all traders will pause seeking."""
        with self._lock:
            self._portfolio_halt = True
        logger.critical("PORTFOLIO HALT ACTIVATED — all pairs will pause seeking.")

    def _save_portfolio_status(self, **kwargs):
        """Save aggregate portfolio status to status_portfolio.json."""
        status = {
            "mode": "multi-pair",
            "pairs_total": len(self.traders),
            "portfolio_halted": self._portfolio_halt,
        }
        status.update(kwargs)

        # Add per-pair summary
        pair_summaries = []
        for t in self.traders.values():
            pair_summaries.append({
                "pair_id": t.pair_id,
                "ticker_1": t.c.ticker_1,
                "ticker_2": t.c.ticker_2,
                "kill_switch": t.state.kill_switch,
                "signal_side": t.state.signal_side,
                "current_zscore": round(t.state.current_zscore, 4),
                "current_pnl": round(t.state.current_pnl, 4),
                "hold_minutes": round(t.state.hold_minutes, 1),
                "is_halted": t.state.is_halted,
                "trade_count": t.state.trade_count,
            })
        status["pairs"] = pair_summaries

        # Add rotation log if available
        if self._rotator:
            status["rotation_log"] = self._rotator.rotation_log[-10:]  # last 10

        try:
            with open("status_portfolio.json", "w") as f:
                json.dump(status, f, indent=4)
        except Exception as e:
            logger.warning("Failed to save portfolio status: %s", e)

    # ── Dynamic pair management (used by PairRotator) ─────────────────────────

    def add_pair(self, config: PairConfig):
        """Add a new pair to the portfolio and start its trader thread."""
        pid = config.pair_id
        if pid in self.traders:
            logger.warning("Pair %s already exists — skipping add", pid)
            return

        trader = PairTrader(
            config=config,
            session_public=self.session_pub,
            session_private=self.session_priv,
            retry_fn=self.retry_fn,
            portfolio_halt_check=self._is_halted,
        )
        self.traders[pid] = trader

        t = threading.Thread(
            target=trader.run,
            name=f"PairTrader-{pid}",
            daemon=True,
        )
        t.start()
        self._threads[pid] = t
        logger.info("Added and started pair: %s (%s / %s)",
                     pid, config.ticker_1, config.ticker_2)

    def stop_pair(self, pair_id: str):
        """Stop a specific pair trader and remove it from the portfolio."""
        trader = self.traders.get(pair_id)
        if not trader:
            logger.warning("Pair %s not found — cannot stop", pair_id)
            return

        trader.stop()
        thread = self._threads.get(pair_id)
        if thread:
            thread.join(timeout=15)
            del self._threads[pair_id]
        del self.traders[pair_id]
        logger.info("Stopped and removed pair: %s", pair_id)

    def wait(self):
        """Block the main thread until all trader threads finish (or KeyboardInterrupt)."""
        try:
            while True:
                # Check if all traders have stopped
                all_stopped = all(not t._running for t in self.traders.values())
                if all_stopped:
                    logger.info("All pair traders have stopped.")
                    break
                time.sleep(5)
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received. Stopping all traders...")
            self.stop_all()

    def stop_all(self):
        """Signal all traders to stop gracefully."""
        # Stop rotator first
        if self._rotator:
            self._rotator.stop()

        logger.info("Stopping all %d pair traders...", len(self.traders))
        for trader in self.traders.values():
            trader.stop()
        # Wait for threads to finish
        for t in self._threads.values():
            t.join(timeout=30)
        logger.info("All traders stopped.")
