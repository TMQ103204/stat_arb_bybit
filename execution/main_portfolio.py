"""
main_portfolio.py — Entry point for Multi-Pair StatArb Bot.

Usage:
    cd execution
    python main_portfolio.py

This replaces main_execution.py for multi-pair mode.
The old main_execution.py still works for single-pair backward compatibility.
"""

# Suppress Pandas FutureWarnings
import warnings
warnings.simplefilter(action='ignore', category=FutureWarning)

import os
import sys
import logging

# Ensure execution dir is in path for relative imports
EXECUTION_DIR = os.path.dirname(__file__)
if EXECUTION_DIR not in sys.path:
    sys.path.insert(0, EXECUTION_DIR)
PROJECT_ROOT = os.path.dirname(EXECUTION_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from logger_setup import get_logger

# Setup logging
logger = get_logger("main_portfolio")


def main():
    """Initialize and run the multi-pair portfolio bot."""

    # Import portfolio config (ACTIVE_PAIRS, sessions, etc.)
    from portfolio_config import (
        ACTIVE_PAIRS, MODE,
        MAX_PORTFOLIO_DRAWDOWN_PCT, MAX_TOTAL_EXPOSURE_USDT,
        AUTO_ROTATION_ENABLED, SCAN_INTERVAL_HOURS,
        ROTATION_BUFFER, MAX_ROTATIONS_PER_CYCLE, ROTATION_COOLDOWN_MIN,
        create_sessions,
    )
    from portfolio_manager import PortfolioManager

    # ── Validate configuration ───────────────────────────────────────────────
    if not ACTIVE_PAIRS:
        logger.critical("No ACTIVE_PAIRS configured in portfolio_config.py. Exiting.")
        print("\n❌ ERROR: ACTIVE_PAIRS is empty in portfolio_config.py")
        print("   Add at least one PairConfig to start trading.\n")
        sys.exit(1)

    # Check for duplicate pair_ids
    pair_ids = [p.pair_id for p in ACTIVE_PAIRS]
    if len(pair_ids) != len(set(pair_ids)):
        logger.critical("Duplicate pair_ids found in ACTIVE_PAIRS! Each pair must have a unique pair_id.")
        sys.exit(1)

    # Check for ticker overlap warning
    all_tickers = []
    for p in ACTIVE_PAIRS:
        all_tickers.extend([p.ticker_1, p.ticker_2])
    ticker_counts = {}
    for t in all_tickers:
        ticker_counts[t] = ticker_counts.get(t, 0) + 1
    overlapping = {t: c for t, c in ticker_counts.items() if c > 1}
    if overlapping:
        logger.warning(
            "⚠️  TICKER OVERLAP DETECTED: %s. "
            "These tickers appear in multiple pairs, increasing correlation risk.",
            overlapping
        )

    # ── Create shared API sessions ───────────────────────────────────────────
    logger.info("Creating API sessions (mode=%s)...", MODE)
    session_pub, session_priv, retry_fn = create_sessions(MODE)

    # ── Build rotation config ────────────────────────────────────────────────
    rotation_config = None
    if AUTO_ROTATION_ENABLED:
        rotation_config = {
            "scan_interval_hours": SCAN_INTERVAL_HOURS,
            "rotation_buffer": ROTATION_BUFFER,
            "max_rotations_per_cycle": MAX_ROTATIONS_PER_CYCLE,
            "rotation_cooldown_min": ROTATION_COOLDOWN_MIN,
        }

    # ── Print startup banner ─────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print(f"  MULTI-PAIR STATARB BOT — {MODE.upper()} MODE")
    print(f"  Active Pairs: {len(ACTIVE_PAIRS)}")
    print("─" * 60)
    for p in ACTIVE_PAIRS:
        print(f"  • {p.pair_id}: {p.ticker_1} / {p.ticker_2} "
              f"(${p.allocated_capital}, {p.leverage}x)")
    print("─" * 60)
    print(f"  Max Drawdown: {MAX_PORTFOLIO_DRAWDOWN_PCT}%")
    print(f"  Max Exposure:  ${MAX_TOTAL_EXPOSURE_USDT}")
    if AUTO_ROTATION_ENABLED:
        print(f"  Auto Rotation: ON (scan every {SCAN_INTERVAL_HOURS}h, buffer={ROTATION_BUFFER})")
    else:
        print(f"  Auto Rotation: OFF")
    print("═" * 60 + "\n")

    # ── Initialize and start PortfolioManager ────────────────────────────────
    pm = PortfolioManager(
        pairs=ACTIVE_PAIRS,
        session_pub=session_pub,
        session_priv=session_priv,
        retry_fn=retry_fn,
        max_drawdown_pct=MAX_PORTFOLIO_DRAWDOWN_PCT,
        max_total_exposure=MAX_TOTAL_EXPOSURE_USDT,
        rotation_config=rotation_config,
    )

    pm.start()
    logger.info("All pair traders launched. Monitoring...")

    # Block until stopped
    pm.wait()

    logger.info("Portfolio bot finished.")


if __name__ == "__main__":
    main()
