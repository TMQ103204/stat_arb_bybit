"""
PairRotator — Automatic pair rotation engine for multi-pair StatArb.

Periodically re-scans the strategy pipeline to find fresh cointegrated pairs,
compares them with the current portfolio, and swaps underperforming SEEKING
pairs for higher-ranked candidates.

Design principles:
  1. NEVER rotate a pair that is currently HOLDING (has open positions)
  2. Only rotate SEEKING pairs (kill_switch == 0, no open positions)
  3. Require a minimum score delta (buffer) to avoid churning
  4. Respect cooldown periods between rotations
  5. Log all rotation decisions for audit trail
"""

import os
import sys
import time
import logging
import threading
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# Ensure project root is importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

STRATEGY_DIR = Path(PROJECT_ROOT) / "strategy"
COINTEGRATED_CSV = STRATEGY_DIR / "2_cointegrated_pairs.csv"

logger = logging.getLogger("pair_rotator")


class PairRotator:
    """Scans strategy results and proposes pair rotations."""

    def __init__(self,
                 scan_interval_hours: float = 6,
                 rotation_buffer: float = 0.2,
                 max_rotations_per_cycle: int = 1,
                 rotation_cooldown_min: float = 30):
        """
        Args:
            scan_interval_hours:     How often to re-scan (hours).
            rotation_buffer:         Min normalized score improvement to rotate.
            max_rotations_per_cycle: Max pairs to swap per scan.
            rotation_cooldown_min:   Min minutes between rotations.
        """
        self.scan_interval_sec = scan_interval_hours * 3600
        self.rotation_buffer = rotation_buffer
        self.max_rotations = max_rotations_per_cycle
        self.cooldown_sec = rotation_cooldown_min * 60

        self._last_scan_time = 0.0
        self._last_rotation_time = 0.0
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Rotation history for audit
        self.rotation_log: List[Dict] = []

    # ── Strategy scan results ────────────────────────────────────────────────

    def load_scan_results(self) -> List[Dict]:
        """Load ranked pairs from the latest strategy scan CSV.

        Returns list of dicts with at least: sym_1, sym_2, composite_score,
        zero_crossings, p_value, hedge_ratio.
        """
        import pandas as pd

        if not COINTEGRATED_CSV.exists():
            logger.warning("No cointegrated pairs CSV found at %s", COINTEGRATED_CSV)
            return []

        try:
            df = pd.read_csv(COINTEGRATED_CSV)
            if df.empty:
                return []

            # Ensure required columns exist
            required = ["sym_1", "sym_2", "composite_score"]
            missing = [c for c in required if c not in df.columns]
            if missing:
                logger.error("CSV missing columns: %s", missing)
                return []

            # Lower composite_score = better (rank 1 is best)
            df = df.sort_values("composite_score", ascending=True)

            records = df.to_dict("records")
            logger.info("Loaded %d candidate pairs from strategy scan", len(records))
            return records

        except Exception as e:
            logger.error("Failed to load scan results: %s", e)
            return []

    # ── Score normalization ───────────────────────────────────────────────────

    @staticmethod
    def normalize_scores(candidates: List[Dict]) -> List[Dict]:
        """Normalize composite_score to [0,1] range where 0 = best.

        This makes the rotation_buffer threshold independent of the number
        of candidate pairs in the scan.
        """
        if not candidates:
            return candidates

        scores = [c["composite_score"] for c in candidates]
        min_s, max_s = min(scores), max(scores)
        rng = max_s - min_s if max_s > min_s else 1.0

        for c in candidates:
            c["norm_score"] = (c["composite_score"] - min_s) / rng

        return candidates

    # ── Rotation proposal ────────────────────────────────────────────────────

    def propose_rotations(self, candidates: List[Dict],
                          active_pairs: List[Dict],
                          pair_states: Dict) -> List[Tuple[str, Dict]]:
        """Compare active pairs with scan candidates and propose swaps.

        Args:
            candidates:   Ranked list from load_scan_results (normalized).
            active_pairs: Current ACTIVE_PAIRS configs as dicts.
            pair_states:  Dict of {pair_id: PairState} from PortfolioManager.

        Returns:
            List of (pair_id_to_remove, new_candidate_dict) tuples.
        """
        proposals = []

        # Build set of currently active tickers for overlap detection
        active_tickers = set()
        active_pair_ids = set()
        for ap in active_pairs:
            t1 = ap.get("ticker_1") or ap.get("ticker_1", "")
            t2 = ap.get("ticker_2") or ap.get("ticker_2", "")
            pid = ap.get("pair_id", "")
            active_tickers.add(t1)
            active_tickers.add(t2)
            active_pair_ids.add(pid)

        # Find SEEKING pairs (eligible for rotation)
        seekable = []
        for ap in active_pairs:
            pid = ap.get("pair_id", "")
            state = pair_states.get(pid)
            # Only consider pairs that are SEEKING (kill_switch == 0)
            if state is None or state.kill_switch == 0:
                # Calculate their score from candidates (if present)
                t1 = ap.get("ticker_1", "")
                t2 = ap.get("ticker_2", "")
                current_score = None
                for c in candidates:
                    if c["sym_1"] == t1 and c["sym_2"] == t2:
                        current_score = c.get("norm_score", 1.0)
                        break
                    if c["sym_2"] == t1 and c["sym_1"] == t2:
                        current_score = c.get("norm_score", 1.0)
                        break
                if current_score is None:
                    # Pair no longer in scan results = very bad score
                    current_score = 1.0
                seekable.append((pid, current_score, ap))

        if not seekable:
            logger.info("No seeking pairs available for rotation")
            return proposals

        # Sort by worst score first (highest norm_score = worst)
        seekable.sort(key=lambda x: x[1], reverse=True)

        # Find candidates not already in portfolio and not overlapping tickers
        available = []
        for c in candidates:
            sym1, sym2 = c["sym_1"], c["sym_2"]
            pair_key = f"{sym1.replace('USDT','')}_{sym2.replace('USDT','')}"
            # Skip if already active or if any ticker overlaps
            if pair_key in active_pair_ids:
                continue
            if sym1 in active_tickers or sym2 in active_tickers:
                continue
            available.append(c)

        if not available:
            logger.info("No new candidate pairs available (all overlap with active)")
            return proposals

        # Propose rotations: worst seeking pair → best available candidate
        for worst_pid, worst_score, worst_config in seekable:
            if len(proposals) >= self.max_rotations:
                break
            if not available:
                break

            best_candidate = available[0]
            best_score = best_candidate.get("norm_score", 1.0)

            # Only rotate if improvement exceeds buffer
            improvement = worst_score - best_score
            if improvement >= self.rotation_buffer:
                proposals.append((worst_pid, best_candidate))
                # Remove this candidate from available pool
                available.pop(0)
                logger.info(
                    "ROTATION PROPOSED: Remove %s (score=%.3f) → Add %s/%s (score=%.3f, Δ=%.3f)",
                    worst_pid, worst_score,
                    best_candidate["sym_1"], best_candidate["sym_2"],
                    best_score, improvement)
            else:
                logger.info(
                    "No rotation: best candidate improvement %.3f < buffer %.3f",
                    improvement, self.rotation_buffer)
                break  # If best isn't good enough, rest won't be either

        return proposals

    # ── Apply rotation ───────────────────────────────────────────────────────

    def apply_rotation(self, portfolio_manager, pair_id_remove: str,
                       candidate: Dict, default_config: Dict = None):
        """Execute a rotation: stop old pair, add new pair to portfolio.

        Args:
            portfolio_manager: Reference to PortfolioManager instance.
            pair_id_remove:    Pair ID to remove (must be SEEKING).
            candidate:         New pair dict from scan results.
            default_config:    Default PairConfig fields to merge.
        """
        from pair_config import PairConfig

        sym1 = candidate["sym_1"]
        sym2 = candidate["sym_2"]
        new_pair_id = f"{sym1.replace('USDT', '')}_{sym2.replace('USDT', '')}"

        logger.info("EXECUTING ROTATION: %s → %s", pair_id_remove, new_pair_id)

        # Merge defaults with candidate-specific values
        cfg_dict = {
            "pair_id": new_pair_id,
            "ticker_1": sym1,
            "ticker_2": sym2,
            "signal_positive_ticker": sym2,  # convention: sym2 is positive
            "signal_negative_ticker": sym1,
            "allocated_capital": 50,
            "leverage": 2,
            "signal_trigger_thresh": 1.1,
            "exit_threshold": 0.0,
            "custom_thresholds": True,
            "zscore_stop_loss": 10,
            "time_stop_loss_hours": 48,
            "max_session_loss_pct": 10.0,
            "limit_order_basis": True,
            "timeframe": 60,
            "kline_limit": 200,
            "z_score_window": 21,
        }

        if default_config:
            cfg_dict.update(default_config)

        # Override with candidate-specific
        cfg_dict["pair_id"] = new_pair_id
        cfg_dict["ticker_1"] = sym1
        cfg_dict["ticker_2"] = sym2
        cfg_dict["signal_positive_ticker"] = sym2
        cfg_dict["signal_negative_ticker"] = sym1

        # Use hedge_ratio-based signal assignment if available
        hedge = candidate.get("hedge_ratio", 1.0)
        if float(hedge) < 0:
            cfg_dict["signal_positive_ticker"] = sym1
            cfg_dict["signal_negative_ticker"] = sym2

        new_config = PairConfig(**cfg_dict)

        # Stop old trader
        try:
            portfolio_manager.stop_pair(pair_id_remove)
            time.sleep(2)  # Brief pause for cleanup
        except Exception as e:
            logger.error("Failed to stop pair %s: %s", pair_id_remove, e)
            return False

        # Start new trader
        try:
            portfolio_manager.add_pair(new_config)
            self._last_rotation_time = time.time()

            # Log rotation
            self.rotation_log.append({
                "time": time.time(),
                "removed": pair_id_remove,
                "added": new_pair_id,
                "sym_1": sym1,
                "sym_2": sym2,
                "score": candidate.get("composite_score", "?"),
            })

            logger.info("ROTATION COMPLETE: %s → %s", pair_id_remove, new_pair_id)
            return True

        except Exception as e:
            logger.error("Failed to add new pair %s: %s", new_pair_id, e)
            return False

    # ── Background rotation loop ─────────────────────────────────────────────

    def start(self, portfolio_manager) -> threading.Thread:
        """Start the background rotation thread.

        Args:
            portfolio_manager: PortfolioManager reference.
        """
        self._running = True
        self._thread = threading.Thread(
            target=self._rotation_loop,
            args=(portfolio_manager,),
            name="PairRotator",
            daemon=True,
        )
        self._thread.start()
        logger.info("PairRotator started (scan every %.1fh, buffer=%.2f)",
                     self.scan_interval_sec / 3600, self.rotation_buffer)
        return self._thread

    def stop(self):
        """Stop the rotation thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("PairRotator stopped")

    def _rotation_loop(self, portfolio_manager):
        """Main rotation loop — runs in background thread."""
        # Initial delay: let all pairs start trading first
        time.sleep(60)

        while self._running:
            try:
                now = time.time()

                # Check if it's time to scan
                if now - self._last_scan_time < self.scan_interval_sec:
                    time.sleep(30)
                    continue

                # Check rotation cooldown
                if now - self._last_rotation_time < self.cooldown_sec:
                    logger.debug("Rotation cooldown active (%.0fs remaining)",
                                 self.cooldown_sec - (now - self._last_rotation_time))
                    time.sleep(30)
                    continue

                logger.info("=== AUTO ROTATION SCAN ===")
                self._last_scan_time = now

                # 1. Run strategy scan (re-use existing pipeline)
                self._trigger_strategy_scan()

                # 2. Load fresh results
                candidates = self.load_scan_results()
                if not candidates:
                    logger.info("No candidates from scan — skipping rotation")
                    time.sleep(60)
                    continue

                candidates = self.normalize_scores(candidates)

                # 3. Get active pairs and states from portfolio manager
                active_configs = []
                pair_states = {}
                for pid, trader in portfolio_manager.traders.items():
                    active_configs.append({
                        "pair_id": trader.config.pair_id,
                        "ticker_1": trader.config.ticker_1,
                        "ticker_2": trader.config.ticker_2,
                    })
                    pair_states[pid] = trader.state

                # 4. Propose rotations
                proposals = self.propose_rotations(candidates, active_configs, pair_states)

                if not proposals:
                    logger.info("No rotation needed — all pairs performing well")
                    continue

                # 5. Execute rotations
                for pair_id_remove, candidate in proposals:
                    success = self.apply_rotation(
                        portfolio_manager, pair_id_remove, candidate)
                    if not success:
                        logger.warning("Rotation failed for %s — stopping cycle",
                                       pair_id_remove)
                        break

            except Exception as e:
                logger.exception("Rotation loop error: %s", e)
                time.sleep(60)

    def _trigger_strategy_scan(self):
        """Run the strategy pipeline to get fresh scan results.

        Calls main_strategy.py in a subprocess so it doesn't block
        the rotation thread.
        """
        import subprocess

        strategy_script = STRATEGY_DIR / "main_strategy.py"
        if not strategy_script.exists():
            logger.error("Strategy script not found: %s", strategy_script)
            return

        logger.info("Running strategy scan: %s", strategy_script)
        try:
            result = subprocess.run(
                [sys.executable, str(strategy_script)],
                cwd=str(STRATEGY_DIR),
                capture_output=True, text=True,
                timeout=600,  # 10 min max
            )
            if result.returncode != 0:
                logger.error("Strategy scan failed (code %d):\n%s",
                             result.returncode, result.stderr[-500:])
            else:
                logger.info("Strategy scan complete (%d chars output)",
                             len(result.stdout))
        except subprocess.TimeoutExpired:
            logger.error("Strategy scan timed out (10min)")
        except Exception as e:
            logger.error("Strategy scan error: %s", e)
