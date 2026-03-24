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
from func_position_calls import open_position_confirmation
from func_position_calls import active_position_confirmation
from func_trade_management import manage_new_trades
from func_execution_calls import set_leverage
from func_close_positions import close_all_positions
from func_get_zscore import get_latest_zscore
from func_save_status import save_status
from func_calcultions import calculate_exact_live_profit
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
    peak_profit_pct = 0.0   # highest net profit % seen while in HOLDING; used by trailing TP
    save_status(status_dict)

    # Set leverage in case forgotten to do so on the platform
    logger.info("Setting leverage...")
    set_leverage(signal_positive_ticker)
    set_leverage(signal_negative_ticker)

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
                kill_switch, signal_side = manage_new_trades(kill_switch)
                if kill_switch == 1:
                    position_open_time = time.time()

            # If BOTH legs are open but kill_switch is 0 (e.g., bot restarted), re-attach to HOLDING
            if both_legs_open and kill_switch == 0:
                kill_switch = 1
                position_open_time = time.time()
                # Determine signal_side from current z-score for re-attach
                if not signal_side:
                    reattach_result = get_latest_zscore()
                    if reattach_result is not None:
                        reattach_zscore = float(cast(float, reattach_result[0]))
                        signal_side = "positive" if reattach_zscore > 0 else "negative"
                        logger.info("Re-attached (both legs): signal_side=%s (z-score=%.4f)", signal_side, reattach_zscore)
                    else:
                        signal_side = "positive"  # fallback
                        logger.warning("Re-attached with fallback signal_side=positive")
                status_dict["message"] = f"Re-attached to full hedge position (side={signal_side})"
                save_status(status_dict)

            # Manage open position: trailing take-profit + stop-losses
            if kill_switch == 1:

                # Get and save the latest z-score
                result = get_latest_zscore()
                if result is not None:
                    zscore, signal_sign_positive = result
                    zscore = float(cast(float, zscore))
                else:
                    continue

                from config_execution_api import (
                    zscore_stop_loss, time_stop_loss_hours,
                    min_profit_pct, trailing_callback_pct
                )

                # Determine long/short tickers from the logged signal side
                long_ticker  = signal_positive_ticker if signal_side == "positive" else signal_negative_ticker
                short_ticker = signal_negative_ticker if signal_side == "positive" else signal_positive_ticker

                # Live net PnL — accounts for exact entry/exit fees per coin (0.055% or 0.11%)
                live_net_pnl_usdt, live_net_profit_pct = calculate_exact_live_profit(long_ticker, short_ticker)

                hold_minutes = (time.time() - position_open_time) / 60 if position_open_time > 0 else 0

                logger.info(
                    "HOLDING | Z: %.4f | Side: %s | Hold: %.0fm | Net PnL: %.3f USDT (%.3f%%)",
                    zscore, signal_side, hold_minutes, live_net_pnl_usdt, live_net_profit_pct
                )

                # ── Trailing Take-Profit ──────────────────────────────────────────────
                # Activates only after net profit has cleared the minimum threshold.
                # Once active, the bot rides the profit upward and closes when it
                # pulls back trailing_callback_pct from its all-time peak.

                # Update peak as long as profit is climbing
                if live_net_profit_pct > peak_profit_pct:
                    peak_profit_pct = live_net_profit_pct

                if peak_profit_pct >= float(min_profit_pct):
                    # Profit has reached the activation threshold — trailing mode
                    trailing_trigger_pct = peak_profit_pct - float(trailing_callback_pct)

                    if live_net_profit_pct <= trailing_trigger_pct:
                        logger.info(
                            "TRAILING TAKE-PROFIT: profit pulled back to %.3f%% from peak %.3f%% "
                            "(trigger %.3f%%). Closing position.",
                            live_net_profit_pct, peak_profit_pct, trailing_trigger_pct
                        )
                        kill_switch = 2
                    else:
                        logger.info(
                            "TRAILING ACTIVE: peak=%.3f%% | trigger=%.3f%% | current=%.3f%%",
                            peak_profit_pct, trailing_trigger_pct, live_net_profit_pct
                        )

                # ── Stop-loss rules (run regardless of trailing state) ────────────────

                # 1. Emergency stop-loss: Z-score structural breakdown
                elif abs(zscore) > float(zscore_stop_loss):
                    logger.critical(
                        "Z-SCORE STOP LOSS REACHED: %.4f exceeds threshold %.4f",
                        zscore, float(zscore_stop_loss)
                    )
                    kill_switch = 2

                # 2. Time stop-loss: position held too long
                elif position_open_time > 0 and (time.time() - position_open_time) > float(time_stop_loss_hours) * 3600:
                    logger.critical("TIME STOP LOSS REACHED: position open for > %s hours.", time_stop_loss_hours)
                    kill_switch = 2

                # 3. Mean-reversion take-profit (only active before trailing kicks in)
                elif signal_side == "positive" and zscore < 0:
                    logger.info("TAKE PROFIT: Z-score crossed below 0 (was positive side)")
                    kill_switch = 2
                elif signal_side == "negative" and zscore >= 0:
                    logger.info("TAKE PROFIT: Z-score crossed above 0 (was negative side)")
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
                peak_profit_pct = 0.0  # reset trailing peak for the next trade cycle

                # Sleep after closing — let market settle before seeking new signal
                time.sleep(60)

        except Exception as e:
            logger.exception("Unexpected error in main loop: %s", e)
            time.sleep(10)
