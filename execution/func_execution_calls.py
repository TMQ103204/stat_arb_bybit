from config_execution_api import session_private
from config_execution_api import limit_order_basis
from config_execution_api import session_public
from config_execution_api import market_order_zscore_thresh
from config_execution_api import min_profit_pct
from config_execution_api import taker_fee_pct
from config_execution_api import z_score_window
from func_calcultions import get_trade_details
from logger_setup import get_logger
from bybit_response import get_result_dict, get_ret_code

logger = get_logger("execution")


def should_use_market(z_score: float) -> bool:
    """Return True if |z_score| is high enough AND expected profit after taker fees
    exceeds the configured minimum, making a market order worthwhile.

    Logic:
      expected_move_pct  = |z| / window * 100
      round_trip_fee_pct = taker_fee_pct * 4   (open 2 legs + close 2 legs)
      net_profit_pct     = expected_move_pct - round_trip_fee_pct
    """
    if abs(z_score) < float(market_order_zscore_thresh):
        return False
    expected_move_pct = abs(z_score) / float(z_score_window) * 100.0
    round_trip_fee_pct = float(taker_fee_pct) * 4.0
    net_profit_pct = expected_move_pct - round_trip_fee_pct
    use_market = net_profit_pct >= float(min_profit_pct)
    if use_market:
        logger.info(
            "MARKET ORDER decision: |z|=%.4f expected_move=%.2f%% fees=%.2f%% net=%.2f%% >= %.2f%%",
            abs(z_score), expected_move_pct, round_trip_fee_pct, net_profit_pct, min_profit_pct,
        )
    return use_market


# Set leverage (updated for Bybit V5 Unified Trading Account)
# NOTE: switch_margin_mode is NOT supported on UTA accounts — margin mode
# is managed at the account level, not per-symbol. Only set_leverage is needed.
def set_leverage(ticker):
    try:
        session_private.set_leverage(
            category="linear",
            symbol=ticker,
            buyLeverage="1",
            sellLeverage="1"
        )
    except Exception as e:
        logger.debug("set_leverage for %s: %s", ticker, e)

    return


# Place limit or market order (updated for Bybit V5 API)
def place_order(ticker, price, quantity, direction, stop_loss, force_market=False):

    side = "Buy" if direction == "Long" else "Sell"

    # Market order: when forced (leg-gap rescue) or limit_order_basis is off
    if force_market or not limit_order_basis:
        order = session_private.place_order(
            category="linear",
            symbol=ticker,
            side=side,
            orderType="Market",
            qty=str(quantity),
            timeInForce="GTC",
            reduceOnly=False,
            closeOnTrigger=False,
            stopLoss=str(stop_loss)
        )
        logger.info("MARKET order sent: %s %s qty=%s", direction, ticker, quantity)
    else:
        # Aggressive limit: GTC at best ask (Long) or best bid (Short).
        # Fills immediately like a taker order, but protects against slippage
        # worse than the visible orderbook price.
        order = session_private.place_order(
            category="linear",
            symbol=ticker,
            side=side,
            orderType="Limit",
            qty=str(quantity),
            price=str(price),
            timeInForce="GTC",        # was PostOnly — GTC allows immediate taker fills
            reduceOnly=False,
            closeOnTrigger=False,
            stopLoss=str(stop_loss)
        )

    return order


# Initialise execution (updated for Bybit V5 API)
def initialise_order_execution(ticker, direction, capital, force_market=False, z_score=0.0):
    try:
        orderbook = session_public.get_orderbook(category="linear", symbol=ticker)
    except Exception as e:
        logger.error("Failed to get orderbook for %s: %s", ticker, e)
        return 0

    if get_ret_code(orderbook) != 0:
        return 0

    ob_result = get_result_dict(orderbook)

    if ob_result:
        entry_price, stop_loss, quantity = get_trade_details(ob_result, direction, capital)
        if quantity > 0:
            # Auto-upgrade to market order only if:
            #   1. Caller forced it (leg-gap rescue), OR
            #   2. limit_order_basis is False (market-only mode), OR
            #   3. z-score is high enough AND net profit covers taker fees
            use_market = force_market or (not limit_order_basis) or should_use_market(z_score)
            order_type_label = "MARKET" if use_market else "LIMIT/GTC (aggressive)"
            logger.info(
                "Placing %s order: %s %s qty=%.6f price=%.6f",
                order_type_label, direction, ticker, quantity, entry_price,
            )
            try:
                order = place_order(ticker, entry_price, quantity, direction, stop_loss, use_market)
                order_result = get_result_dict(order)
                if "orderId" in order_result:
                    logger.info(
                        "Order placed: %s %s qty=%.6f price=%.6f",
                        direction, ticker, quantity, entry_price,
                    )
                    return order_result["orderId"]
            except Exception as e:
                err_str = str(e)
                logger.error("Failed to place order %s %s: %s", direction, ticker, err_str)
                # Insufficient balance — no point retrying
                if "110007" in err_str:
                    logger.critical("INSUFFICIENT BALANCE (110007) — cannot place order.")
                    return -1  # sentinel for irrecoverable error
    return 0
