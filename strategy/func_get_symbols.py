from config_strategy_api import session
from bybit_response import get_result_list, get_ret_code

# Minimum 24h turnover in USDT to filter out illiquid symbols
MIN_TURNOVER_24H = 200_000
TOP_N_SYMBOLS = 50

# Get symbols that are tradeable (updated for Bybit V5 API)
def get_tradeable_symbols():

    # Get available USDT linear symbols
    valid_symbols = set()
    symbols = session.get_instruments_info(category="linear")
    if get_ret_code(symbols) == 0:
        for symbol in get_result_list(symbols):
            if symbol["quoteCoin"] == "USDT" and symbol["status"] == "Trading":
                valid_symbols.add(symbol["symbol"])

    # Get 24h ticker data and filter by turnover
    sym_list = []
    tickers = session.get_tickers(category="linear")
    if get_ret_code(tickers) == 0:
        for ticker in get_result_list(tickers):
            if ticker["symbol"] in valid_symbols:
                turnover_24h = float(ticker.get("turnover24h", "0"))
                if turnover_24h >= MIN_TURNOVER_24H:
                    sym_list.append({"symbol": ticker["symbol"], "turnover24h": turnover_24h})

    # Sort symbols by turnover descending and take top N
    sym_list = sorted(sym_list, key=lambda x: x["turnover24h"], reverse=True)[:TOP_N_SYMBOLS]
    
    # Clean up JSON structure (remove turnover24h key)
    sym_list = [{"symbol": x["symbol"]} for x in sym_list]

    print(f"Found top {len(sym_list)} liquid symbols (>= ${MIN_TURNOVER_24H:,.0f} 24h turnover) from {len(valid_symbols)} total")
    return sym_list
