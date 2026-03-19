# Remove Pandas Future Warnings
import warnings
warnings.simplefilter(action='ignore', category=FutureWarning)

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
from logger_setup import get_logger
from typing import cast
import time

logger = get_logger("main")

""" RUN STATBOT """
if __name__ == "__main__":

    # Initial printout
    logger.info("StatBot initiated...")

    # Initialise variables
    status_dict = {"message": "starting..."}
    order_long = {}
    order_short = {}
    signal_sign_positive = False
    signal_side = ""
    kill_switch = 0
    position_open_time = 0.0    # Save status
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
            checks_all = [is_p_ticker_open, is_n_ticker_open, is_p_ticker_active, is_n_ticker_active]
            is_manage_new_trades = not any(checks_all)

            # Save status
            status_dict["message"] = "Initial checks made..."
            status_dict["checks"] = str(checks_all)
            save_status(status_dict)

            # Check for signal and place new trades
            if is_manage_new_trades and kill_switch == 0:
                status_dict["message"] = "Managing new trades..."
                save_status(status_dict)
                kill_switch, signal_side = manage_new_trades(kill_switch)
                if kill_switch == 1:
                    position_open_time = time.time()

            # If trades are already open but kill_switch is 0 (e.g., bot restarted), set to 1
            if not is_manage_new_trades and kill_switch == 0:
                kill_switch = 1
                position_open_time = time.time()
                # Determine signal_side from current z-score for re-attach
                if not signal_side:
                    reattach_result = get_latest_zscore()
                    if reattach_result is not None:
                        reattach_zscore = float(cast(float, reattach_result[0]))
                        signal_side = "positive" if reattach_zscore > 0 else "negative"
                        logger.info("Re-attached: signal_side=%s (z-score=%.4f)", signal_side, reattach_zscore)
                    else:
                        signal_side = "positive"  # fallback
                        logger.warning("Re-attached with fallback signal_side=positive")
                status_dict["message"] = f"Re-attached to open positions (side={signal_side})"
                save_status(status_dict)

            # Managing open kill switch if positions change or should reach 2
            # Check for signal to be false
            if kill_switch == 1:

                # Get and save the latest z-score
                result = get_latest_zscore()
                if result is not None:
                    zscore, signal_sign_positive = result
                    zscore = float(cast(float, zscore))
                else:
                    continue

                from config_execution_api import zscore_stop_loss, time_stop_loss_hours

                # Log live position status
                hold_minutes = (time.time() - position_open_time) / 60 if position_open_time > 0 else 0
                hold_hours = hold_minutes / 60
                logger.info(
                    "HOLDING | Z-Score: %.4f | Side: %s | Hold: %.0fm (%.1fh/%.0fh) | SL: %.1f",
                    zscore, signal_side, hold_minutes, hold_hours, float(time_stop_loss_hours), float(zscore_stop_loss)
                )

                # 1. Close positions (Stop-Loss: Z-score divergence)
                if abs(zscore) > float(zscore_stop_loss):
                    logger.critical("Z-SCORE STOP LOSS REACHED: %.4f exceeds threshold %.4f", zscore, float(zscore_stop_loss))
                    kill_switch = 2

                # 2. Close positions (Stop-Loss: Time-based)
                elif position_open_time > 0 and (time.time() - position_open_time) > float(time_stop_loss_hours) * 3600:
                    logger.critical("TIME STOP LOSS REACHED: Position open for > %s hours.", time_stop_loss_hours)
                    kill_switch = 2

                # 3. Close positions (Take Profit: Mean Reversion)
                elif signal_side == "positive" and zscore < 0:
                    logger.info("TAKE PROFIT: Z-Score crossed below 0 (was positive side)")
                    kill_switch = 2
                elif signal_side == "negative" and zscore >= 0:
                    logger.info("TAKE PROFIT: Z-Score crossed above 0 (was negative side)")
                    kill_switch = 2

                # NOTE: Do NOT reset kill_switch to 0 based on is_manage_new_trades here.
                # The position API can be flaky and temporarily report no positions,
                # causing kill_switch to flip 0→1 in a loop with stale z-scores.

            # Close all active orders and positions
            if kill_switch == 2:
                logger.info("Closing all positions...")
                status_dict["message"] = "Closing existing trades..."
                save_status(status_dict)
                kill_switch = close_all_positions(kill_switch)

                # Sleep for 5 seconds
                time.sleep(5)

        except Exception as e:
            logger.exception("Unexpected error in main loop: %s", e)
            time.sleep(10)
