"""
reset_bot.py
============
Run this script BEFORE starting main_execution.py with a new pair.
It will:
  1. Cancel ALL open limit/trigger orders for the configured tickers
  2. Close ALL open positions for the configured tickers (market order)
  3. Print a clear status summary so you know the account is clean

Usage:
    cd execution
    python reset_bot.py
"""

import os
import sys
import time

# -- allow running from the execution/ directory directly
EXECUTION_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(EXECUTION_DIR)
sys.path.insert(0, EXECUTION_DIR)   # for config_execution_api, logger_setup, etc.
sys.path.insert(0, PROJECT_ROOT)    # for bybit_response (lives in project root)

from config_execution_api import (
    signal_positive_ticker,
    signal_negative_ticker,
    session_private,
)
from logger_setup import get_logger
from bybit_response import get_result_list, get_ret_code

logger = get_logger("reset_bot")

SYMBOLS = [signal_positive_ticker, signal_negative_ticker]

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def cancel_orders(symbol: str) -> int:
    """Cancel all open orders for *symbol*. Returns number cancelled."""
    try:
        res = session_private.cancel_all_orders(category="linear", symbol=symbol)
        if get_ret_code(res) == 0:
            # Verify
            check = session_private.get_open_orders(category="linear", symbol=symbol)
            remaining = len(get_result_list(check)) if get_ret_code(check) == 0 else -1
            logger.info("[%s] cancel_all_orders OK | remaining open orders: %s", symbol, remaining)
            return remaining
        else:
            logger.error("[%s] cancel_all_orders failed: %s", symbol, res.get("retMsg"))
            return -1
    except Exception as e:
        logger.error("[%s] cancel_all_orders exception: %s", symbol, e)
        return -1


def get_open_position(symbol: str):
    """Return (side, size) of any open position, or ('', 0) if none."""
    try:
        res = session_private.get_positions(category="linear", symbol=symbol)
        if get_ret_code(res) == 0:
            for pos in get_result_list(res):
                if float(pos.get("size", 0)) > 0:
                    return pos["side"], float(pos["size"])
    except Exception as e:
        logger.error("[%s] get_positions exception: %s", symbol, e)
    return "", 0


def close_position(symbol: str, side: str, size: float) -> bool:
    """Close *size* of *side* for *symbol* at market. Returns True on success."""
    close_side = "Sell" if side == "Buy" else "Buy"
    try:
        res = session_private.place_order(
            category="linear",
            symbol=symbol,
            side=close_side,
            orderType="Market",
            qty=str(size),
            timeInForce="IOC",
            reduceOnly=True,
            positionIdx=0,
        )
        if get_ret_code(res) == 0:
            order_id = res.get("result", {}).get("orderId", "?")
            logger.info("[%s] Closed %s qty=%.6f | orderId=%s", symbol, side, size, order_id)
            return True
        else:
            logger.error("[%s] close position failed: %s", symbol, res.get("retMsg"))
            return False
    except Exception as e:
        logger.error("[%s] close position exception: %s", symbol, e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Main reset routine
# ─────────────────────────────────────────────────────────────────────────────

def reset_bot():
    print("\n" + "=" * 60)
    print("  BOT RESET — Cleaning API state before new trade pair")
    print("  Configured tickers:", SYMBOLS)
    print("=" * 60 + "\n")

    results = {}

    for symbol in SYMBOLS:
        print(f"--- {symbol} ---")

        # Step 1: Cancel orders
        remaining_orders = cancel_orders(symbol)
        print(f"  [Orders]    Remaining after cancel: {remaining_orders}")

        # Step 2: Close positions
        side, size = get_open_position(symbol)
        if size > 0:
            print(f"  [Position]  Found open {side} size={size} — closing at market...")
            ok = close_position(symbol, side, size)
            print(f"  [Position]  Close order placed: {'OK' if ok else 'FAILED'}")
        else:
            print("  [Position]  No open position — nothing to close")

        results[symbol] = {
            "remaining_orders": remaining_orders,
            "closed_size": size,
        }

    # Final confirmation — wait briefly for fills then re-check
    time.sleep(2)
    print("\n" + "=" * 60)
    print("  FINAL VERIFICATION")
    print("=" * 60)
    all_clean = True
    for symbol in SYMBOLS:
        side, size = get_open_position(symbol)
        check = session_private.get_open_orders(category="linear", symbol=symbol)
        orders_left = len(get_result_list(check)) if get_ret_code(check) == 0 else "?"
        clean = (size == 0 and orders_left == 0)
        status = "CLEAN" if clean else "!! CHECK MANUALLY !!"
        print(f"  {symbol:15s}  position={size}  orders={orders_left}  -> {status}")
        if not clean:
            all_clean = False

    print()
    if all_clean:
        print("  Account is CLEAN. You can now start the bot with the new pair.")
    else:
        print("  WARNING: Some positions/orders remain. Check Bybit manually before restarting the bot.")
    print("=" * 60 + "\n")

    return all_clean


if __name__ == "__main__":
    reset_bot()
