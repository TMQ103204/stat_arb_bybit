from config_strategy_api import session

# Minimum 24h turnover in USDT to filter out illiquid symbols
MIN_TURNOVER_24H = 500_000

# Get symbols that are tradeable (updated for Bybit V5 API)
def get_tradeable_symbols():

    # Get available USDT linear symbols
    valid_symbols = set()
    symbols = session.get_instruments_info(category="linear")
    if symbols["retCode"] == 0:
        for symbol in symbols["result"]["list"]:
            if symbol["quoteCoin"] == "USDT" and symbol["status"] == "Trading":
                valid_symbols.add(symbol["symbol"])

    # Get 24h ticker data and filter by turnover
    sym_list = []
    tickers = session.get_tickers(category="linear")
    if tickers["retCode"] == 0:
        for ticker in tickers["result"]["list"]:
            if ticker["symbol"] in valid_symbols:
                turnover_24h = float(ticker.get("turnover24h", "0"))
                if turnover_24h >= MIN_TURNOVER_24H:
                    sym_list.append({"symbol": ticker["symbol"]})

    print(f"Found {len(sym_list)} liquid symbols (>= ${MIN_TURNOVER_24H:,.0f} 24h turnover) from {len(valid_symbols)} total")
    return sym_list
