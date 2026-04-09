# Remove Pandas Future Warnings
import warnings
warnings.simplefilter(action='ignore', category=FutureWarning)

import argparse

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# General Imports
from config_execution_api import signal_positive_ticker
from config_execution_api import signal_negative_ticker
from config_execution_api import auto_trade
from config_execution_api import custom_thresholds, leverage
from func_position_calls import open_position_confirmation
from func_position_calls import active_position_confirmation
from func_trade_management import manage_new_trades
from func_execution_calls import set_leverage
from func_close_positions import close_all_positions
from func_get_zscore import get_latest_zscore, get_latest_zscore_with_hedge
from func_save_status import save_status
from func_calcultions import calculate_exact_live_profit, get_wallet_equity, snapshot_cumrealised_pnl
from logger_setup import get_logger
from typing import cast
import time

logger = get_logger("main")

""" RUN STATBOT """
if __name__ == "__main__":

    # ── CLI arguments ────────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="StatArb Bybit Bot")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Cancel all orders and close all positions for current pair, then exit."
    )
    args = parser.parse_args()

    if args.reset:
        from reset_bot import reset_bot
        ok = reset_bot()
        sys.exit(0 if ok else 1)
    # ─────────────────────────────────────────────────────────────────────────

    # ── Validate both tickers are configured ─────────────────────────────────
    if not signal_positive_ticker or not signal_positive_ticker.strip():
        logger.critical("signal_positive_ticker (ticker_2) is EMPTY. "
                        "Stat arb requires two tickers. Set ticker_2 in config_execution_api.py")
        print("\n❌ ERROR: ticker_2 is empty in config_execution_api.py")
        print("   Stat arb bot needs TWO tickers to trade. Exiting.\n")
        sys.exit(1)
    if not signal_negative_ticker or not signal_negative_ticker.strip():
        logger.critical("signal_negative_ticker (ticker_1) is EMPTY. "
                        "Stat arb requires two tickers. Set ticker_1 in config_execution_api.py")
        print("\n❌ ERROR: ticker_1 is empty in config_execution_api.py")
        print("   Stat arb bot needs TWO tickers to trade. Exiting.\n")
        sys.exit(1)
    # ─────────────────────────────────────────────────────────────────────────

    # Initial printout
    logger.info("StatBot initiated...")

    # Initialise variables
    status_dict = {"message": "starting..."}
    order_long = {}
    order_short = {}
    signal_sign_positive = False
    signal_side = ""
    kill_switch = 0
    position_open_time = 0.0
    entry_hedge_ratio = None  # frozen hedge_ratio at trade entry
    entry_mean = None         # frozen spread mean at trade entry
    entry_std = None          # frozen spread std at trade entry
    session_realized_loss = 0.0  # cumulative realized loss (USDT) this session — for circuit breaker
    last_close_pnl = 0.0  # net PnL of the last tick before close — used by circuit breaker
    baseline_realised_long = 0.0   # cumRealisedPnl snapshot at trade entry (long leg)
    baseline_realised_short = 0.0  # cumRealisedPnl snapshot at trade entry (short leg)
    save_status(status_dict)

    # Set leverage — only apply custom leverage when custom_thresholds is enabled
    effective_leverage = leverage if custom_thresholds else 1
    logger.info("Setting leverage to %dx (custom_thresholds=%s)...", effective_leverage, custom_thresholds)
    set_leverage(signal_positive_ticker, effective_leverage)
    set_leverage(signal_negative_ticker, effective_leverage)

    # Commence bot
    logger.info("Seeking trades...")
    while True:

        try:
            # Pause - protect API
            time.sleep(2)

            # Check if open trades already exist
            is_p_ticker_open = open_position_confirmation(signal_positive_ticker)
            is_n_ticker_open = open_position_confirmation(signal_negative_ticker)
            is_p_ticker_active = active_position_confirmation(signal_positive_ticker)
            is_n_ticker_active = active_position_confirmation(signal_negative_ticker)

            # BUG FIX #3: Instead of any(), classify the state of the two legs precisely.
            # any() would re-attach to HOLDING even when only ONE leg is open — exactly
            # the half-position scenario that caused the 54-minute naked POPCAT trade.
            #
            # has_p: positive ticker has a position OR an active order
            # has_n: negative ticker has a position OR an active order
            has_p = is_p_ticker_open or is_p_ticker_active
            has_n = is_n_ticker_open or is_n_ticker_active
            both_legs_open   = has_p and has_n   # full hedge — safe to re-attach
            half_leg_open    = has_p ^ has_n      # exactly one leg — emergency close!
            no_positions     = not has_p and not has_n  # clean slate — seek new trades
            is_manage_new_trades = no_positions

            checks_all = [is_p_ticker_open, is_n_ticker_open, is_p_ticker_active, is_n_ticker_active]

            # Save status
            status_dict["message"] = "Initial checks made..."
            status_dict["checks"] = str(checks_all)
            save_status(status_dict)

            # ── HALF-POSITION DETECTED on startup / restart ──────────────────────────
            # This catches naked legs that survived a crash / manual restart.
            # Close the orphan leg immediately and reset to SEEKING.
            if half_leg_open and kill_switch == 0:
                orphan_ticker = signal_positive_ticker if has_p else signal_negative_ticker
                logger.critical(
                    "STARTUP HALF-POSITION DETECTED: Only %s has an open leg. "
                    "Closing orphan position to avoid unhedged exposure.",
                    orphan_ticker
                )
                time.sleep(3)  # let Bybit API settle before querying size
                kill_switch = close_all_positions(kill_switch)
                if kill_switch == 0:
                    status_dict["message"] = "Orphan half-position closed. Seeking new trades."
                else:
                    status_dict["message"] = "Failed to close orphan. Retrying..."
                save_status(status_dict)

            # Check for signal and place new trades
            if is_manage_new_trades and kill_switch == 0:
                status_dict["message"] = "Managing new trades..."
                save_status(status_dict)
                kill_switch, signal_side, entry_hedge_ratio, entry_mean, entry_std = manage_new_trades(kill_switch)
                if kill_switch == 1:
                    position_open_time = time.time()
                    logger.info("Trade entered with frozen hedge_ratio=%.6f  mean=%.8f  std=%.8f",
                                entry_hedge_ratio if entry_hedge_ratio else 0,
                                entry_mean if entry_mean else 0,
                                entry_std if entry_std else 0)
                    # Snapshot cumRealisedPnl so PnL only counts THIS trade
                    long_t  = signal_positive_ticker if signal_side == "positive" else signal_negative_ticker
                    short_t = signal_negative_ticker if signal_side == "positive" else signal_positive_ticker
                    baseline_realised_long, baseline_realised_short = snapshot_cumrealised_pnl(long_t, short_t)

            # If BOTH legs are open but kill_switch is 0 (e.g., bot restarted), re-attach to HOLDING
            if both_legs_open and kill_switch == 0:
                kill_switch = 1
                position_open_time = time.time()
                # Determine signal_side and freeze hedge_ratio + mean/std for re-attach
                if not signal_side or entry_hedge_ratio is None:
                    reattach_result = get_latest_zscore_with_hedge()
                    if reattach_result is not None:
                        reattach_zscore, _, entry_hedge_ratio, entry_mean, entry_std = reattach_result
                        reattach_zscore = float(cast(float, reattach_zscore))
                        signal_side = "positive" if reattach_zscore > 0 else "negative"
                        logger.info("Re-attached (both legs): signal_side=%s (z-score=%.4f) "
                                    "hedge_ratio=%.6f mean=%.8f std=%.8f",
                                    signal_side, reattach_zscore,
                                    entry_hedge_ratio, entry_mean, entry_std)
                    else:
                        signal_side = "positive"  # fallback
                        logger.warning("Re-attached with fallback signal_side=positive")
                # Snapshot cumRealisedPnl baseline for re-attached trade
                long_t  = signal_positive_ticker if signal_side == "positive" else signal_negative_ticker
                short_t = signal_negative_ticker if signal_side == "positive" else signal_positive_ticker
                baseline_realised_long, baseline_realised_short = snapshot_cumrealised_pnl(long_t, short_t)
                status_dict["message"] = f"Re-attached to full hedge position (side={signal_side})"
                save_status(status_dict)

            # Manage open position: stop-losses & take-profit
            if kill_switch == 1:

                # Get and save the latest z-score using frozen hedge_ratio + mean/std
                result = get_latest_zscore_with_hedge(entry_hedge_ratio, entry_mean, entry_std)
                if result is not None:
                    zscore, signal_sign_positive, _, _, _ = result
                    zscore = float(cast(float, zscore))
                else:
                    continue

                from config_execution_api import (
                    zscore_stop_loss, time_stop_loss_hours, custom_thresholds, exit_threshold
                )

                # Determine actual exit target
                target_exit = float(exit_threshold) if custom_thresholds else 0.0

                # Determine long/short tickers from the logged signal side
                long_ticker  = signal_positive_ticker if signal_side == "positive" else signal_negative_ticker
                short_ticker = signal_negative_ticker if signal_side == "positive" else signal_positive_ticker

                # Live net PnL — accounts for exact entry/exit fees per coin (0.055% or 0.11%)
                live_net_pnl_usdt, live_net_profit_pct = calculate_exact_live_profit(
                    long_ticker, short_ticker, baseline_realised_long, baseline_realised_short)

                # ── Bug #2 fix: if PnL calculation failed, skip this tick ─────────
                if live_net_pnl_usdt is None:
                    logger.warning("PnL calculation failed — skipping this tick.")
                    continue
                # ──────────────────────────────────────────────────────────────────

                # Snapshot PnL for circuit breaker (used after close)
                last_close_pnl = live_net_pnl_usdt

                hold_minutes = (time.time() - position_open_time) / 60 if position_open_time > 0 else 0

                logger.info(
                    "HOLDING | Z: %.4f | Side: %s | Hold: %.0fm | Net PnL: %.3f USDT (%.3f%%)",
                    zscore, signal_side, hold_minutes, live_net_pnl_usdt, live_net_profit_pct
                )

                # ── Exit rules ────────────────────────────────────────────────────────

                # 1. Emergency stop-loss: Z-score structural breakdown
                if abs(zscore) > float(zscore_stop_loss):
                    logger.critical(
                        "Z-SCORE STOP LOSS REACHED: %.4f exceeds threshold %.4f",
                        zscore, float(zscore_stop_loss)
                    )
                    kill_switch = 2

                # 2. Time stop-loss: position held too long
                elif position_open_time > 0 and (time.time() - position_open_time) > float(time_stop_loss_hours) * 3600:
                    logger.critical("TIME STOP LOSS REACHED: position open for > %s hours.", time_stop_loss_hours)
                    kill_switch = 2

                # 3. Mean-reversion or target take-profit
                elif signal_side == "positive" and zscore < target_exit:
                    logger.info("TAKE PROFIT: Z-score crossed below exit target %.4f", target_exit)
                    kill_switch = 2
                elif signal_side == "negative" and zscore >= -target_exit:
                    logger.info("TAKE PROFIT: Z-score crossed above exit target %.4f", -target_exit)
                    kill_switch = 2

                # NOTE: Do not reset kill_switch to 0 based on is_manage_new_trades here.
                # The position API can be flaky and temporarily report no positions,
                # causing kill_switch to flip 0->1 in a loop with stale z-scores.

            # Close all active orders and positions
            if kill_switch == 2:
                logger.info("Closing all positions...")
                status_dict["message"] = "Closing existing trades..."
                save_status(status_dict)
                kill_switch = close_all_positions(kill_switch)

                # ── Bug #4 fix: verify positions are actually closed ──────────────
                if kill_switch == 0:
                    time.sleep(2)
                    still_open_p = open_position_confirmation(signal_positive_ticker)
                    still_open_n = open_position_confirmation(signal_negative_ticker)
                    if still_open_p or still_open_n:
                        logger.critical(
                            "CLOSE VERIFICATION FAILED: positions still open after close_all. "
                            "Retrying..."
                        )
                        kill_switch = 2  # force retry
                        continue
                # ──────────────────────────────────────────────────────────────────

                # ── Bug #1 fix: session loss circuit breaker ──────────────────────
                from config_execution_api import max_session_loss_pct
                if last_close_pnl < 0:
                    session_realized_loss += abs(last_close_pnl)
                # Use REAL starting capital from wallet API, not config value
                wallet_info = get_wallet_equity()
                real_capital = wallet_info["starting_capital"] if wallet_info else 0.0
                session_loss_pct = (session_realized_loss / real_capital) * 100 if real_capital > 0 else 0
                logger.info(
                    "Session loss tracker: this trade %.3f USDT | cumulative %.3f USDT (%.2f%% of $%.2f)",
                    last_close_pnl, session_realized_loss, session_loss_pct, real_capital
                )
                if session_loss_pct >= float(max_session_loss_pct):
                    logger.critical(
                        "SESSION LOSS CIRCUIT BREAKER: cumulative loss %.2f%% >= %.2f%%. HALTING BOT.",
                        session_loss_pct, float(max_session_loss_pct)
                    )
                    status_dict["message"] = f"HALTED: session loss {session_loss_pct:.1f}% exceeded limit"
                    save_status(status_dict)
                    sys.exit(1)
                last_close_pnl = 0.0  # reset for next trade
                entry_hedge_ratio = None  # reset frozen hedge_ratio for next trade
                entry_mean = None         # reset frozen mean for next trade
                entry_std = None          # reset frozen std for next trade
                # ──────────────────────────────────────────────────────────────────

                # Sleep after closing — let market settle before seeking new signal.
                # Extended cooldown prevents rapid re-entry when Z-score remains extreme.
                cooldown_seconds = 300  # 5 minutes
                logger.info("Post-close cooldown: sleeping %d seconds...", cooldown_seconds)
                time.sleep(cooldown_seconds)

                # ── Auto-Trade Check ──
                if not auto_trade:
                    logger.info("Auto-trade is disabled. Shutting down bot gracefully.")
                    status_dict["message"] = "Positions closed. Auto-trade is OFF. Bot stopped."
                    save_status(status_dict)
                    sys.exit(0)

        except Exception as e:
            logger.exception("Unexpected error in main loop: %s", e)
            time.sleep(10)
