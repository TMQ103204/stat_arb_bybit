from func_calcultions import get_trade_details, _get_taker_fee_rate
from logger_setup import get_logger
from bybit_response import get_result_dict, get_ret_code

logger = get_logger("execution")


def _resolve_session_private(session_priv=None):
    if session_priv is not None:
        return session_priv
    from config_execution_api import session_private
    return session_private


def _resolve_session_public(session_pub=None):
    if session_pub is not None:
        return session_pub
    from config_execution_api import session_public
    return session_public


def should_use_market(z_score: float, t1=None, t2=None,
                      market_thresh=None, min_profit=None,
                      z_window=None) -> bool:
    """Return True if |z_score| is high enough AND expected profit after taker fees
    exceeds the configured minimum, making a market order worthwhile."""
    if market_thresh is None:
        from config_execution_api import market_order_zscore_thresh
        market_thresh = float(market_order_zscore_thresh)
    if min_profit is None:
        from config_execution_api import min_profit_pct
        min_profit = float(min_profit_pct)
    if z_window is None:
        from config_execution_api import z_score_window
        z_window = int(z_score_window)
    if t1 is None:
        from config_execution_api import ticker_1
        t1 = ticker_1
    if t2 is None:
        from config_execution_api import ticker_2
        t2 = ticker_2

    if abs(z_score) < market_thresh:
        return False

    fee_1 = _get_taker_fee_rate(t1, fallback_rate=0.00055) * 100
    fee_2 = _get_taker_fee_rate(t2, fallback_rate=0.00055) * 100
    round_trip_fee_pct = (fee_1 + fee_2) * 2

    expected_move_pct = abs(z_score) / z_window * 100.0
    net_profit_pct = expected_move_pct - round_trip_fee_pct
    use_market = net_profit_pct >= min_profit
    if use_market:
        logger.info(
            "MARKET ORDER decision: |z|=%.4f expected_move=%.2f%% fees=%.2f%% net=%.2f%% >= %.2f%%",
            abs(z_score), expected_move_pct, round_trip_fee_pct, net_profit_pct, min_profit,
        )
    return use_market


# Set leverage (updated for Bybit V5 Unified Trading Account)
def set_leverage(ticker, lev=None, session_priv=None):
    _priv = _resolve_session_private(session_priv)
    if lev is None:
        from config_execution_api import leverage as cfg_leverage
        lev = cfg_leverage
    lev_str = str(int(lev))
    try:
        _priv.set_leverage(
            category="linear",
            symbol=ticker,
            buyLeverage=lev_str,
            sellLeverage=lev_str
        )
        logger.info("set_leverage %s → %sx", ticker, lev_str)
    except Exception as e:
        logger.debug("set_leverage for %s: %s", ticker, e)

    return


# Place limit or market order (updated for Bybit V5 API)
def place_order(ticker, price, quantity, direction, stop_loss,
                force_market=False, limit_basis=None, session_priv=None):

    _priv = _resolve_session_private(session_priv)
    if limit_basis is None:
        from config_execution_api import limit_order_basis
        limit_basis = limit_order_basis

    side = "Buy" if direction == "Long" else "Sell"

    # Build common order params
    params = dict(
        category="linear",
        symbol=ticker,
        side=side,
        qty=str(quantity),
        timeInForce="GTC",
        reduceOnly=False,
        closeOnTrigger=False,
    )

    # Only attach stopLoss if a valid value was calculated (stop_loss_fail_safe > 0)
    if stop_loss and stop_loss > 0:
        params["stopLoss"] = str(stop_loss)

    # Market order: when forced (leg-gap rescue) or limit_order_basis is off
    if force_market or not limit_basis:
        params["orderType"] = "Market"
        order = _priv.place_order(**params)
        logger.info("MARKET order sent: %s %s qty=%s", direction, ticker, quantity)
    else:
        # Aggressive limit: GTC at best ask (Long) or best bid (Short).
        params["orderType"] = "Limit"
        params["price"] = str(price)
        order = _priv.place_order(**params)

    return order


# Initialise execution (updated for Bybit V5 API)
def initialise_order_execution(ticker, direction, capital, force_market=False, z_score=0.0,
                               session_pub=None, session_priv=None,
                               limit_basis=None, sl_failsafe=None,
                               t1=None, t2=None,
                               market_thresh=None, min_profit=None, z_window=None):
    sess = _resolve_session_public(session_pub)
    _priv = _resolve_session_private(session_priv)

    try:
        orderbook = sess.get_orderbook(category="linear", symbol=ticker)
    except Exception as e:
        logger.error("Failed to get orderbook for %s: %s", ticker, e)
        return 0

    if get_ret_code(orderbook) != 0:
        return 0

    ob_result = get_result_dict(orderbook)

    if ob_result:
        entry_price, stop_loss, quantity = get_trade_details(
            ob_result, direction, capital,
            sl_failsafe=sl_failsafe, session_pub=session_pub
        )
        if quantity > 0:
            use_market = force_market or should_use_market(
                z_score, t1=t1, t2=t2,
                market_thresh=market_thresh, min_profit=min_profit,
                z_window=z_window
            )
            if limit_basis is None:
                from config_execution_api import limit_order_basis
                limit_basis = limit_order_basis
            if not limit_basis:
                use_market = True

            order_type_label = "MARKET" if use_market else "LIMIT/GTC (aggressive)"
            logger.info(
                "Placing %s order: %s %s qty=%.6f price=%.6f",
                order_type_label, direction, ticker, quantity, entry_price,
            )
            try:
                order = place_order(ticker, entry_price, quantity, direction, stop_loss,
                                    use_market, limit_basis=limit_basis,
                                    session_priv=session_priv)
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
