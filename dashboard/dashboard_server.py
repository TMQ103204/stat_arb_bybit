"""
Stat-Arb Trading Dashboard – Backend API Server
Flask server providing REST APIs for the trading dashboard.
"""

import os
import sys
import json
import csv
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent  # project root
STRATEGY_DIR = BASE_DIR / "strategy"
EXECUTION_DIR = BASE_DIR / "execution"
DASHBOARD_DIR = BASE_DIR / "docs"

STRATEGY_CONFIG = STRATEGY_DIR / "config_strategy_api.py"
EXECUTION_CONFIG = EXECUTION_DIR / "config_execution_api.py"
SYMBOLS_FILE = STRATEGY_DIR / "func_get_symbols.py"
# Strategy writes CSV/JSON to strategy/ folder (relative paths with cwd=strategy)
COINTEGRATED_CSV = STRATEGY_DIR / "2_cointegrated_pairs.csv"
BACKTEST_CSV = STRATEGY_DIR / "3_backtest_file.csv"
PRICE_JSON = STRATEGY_DIR / "1_price_list.json"
STATUS_JSON = EXECUTION_DIR / "status.json"

# ── App ──────────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=str(DASHBOARD_DIR))
CORS(app)

# ── Process tracking ─────────────────────────────────────────────────────────
strategy_process = None
strategy_output = []
strategy_lock = threading.Lock()

execution_process = None
execution_output = []
execution_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def parse_strategy_config():
    """Parse config_strategy_api.py and func_get_symbols.py for key parameters."""
    content = STRATEGY_CONFIG.read_text(encoding="utf-8")
    config = {}
    m = re.search(r'^mode\s*=\s*["\'](\w+)["\']', content, re.MULTILINE)
    config["mode"] = m.group(1) if m else "demo"
    m = re.search(r'^timeframe\s*=\s*(\d+)', content, re.MULTILINE)
    config["timeframe"] = int(m.group(1)) if m else 60
    m = re.search(r'^kline_limit\s*=\s*(\d+)', content, re.MULTILINE)
    config["kline_limit"] = int(m.group(1)) if m else 200
    m = re.search(r'^z_score_window\s*=\s*(\d+)', content, re.MULTILINE)
    config["z_score_window"] = int(m.group(1)) if m else 21
    m = re.search(r'^min_zero_crossings\s*=\s*(\d+)', content, re.MULTILINE)
    config["min_zero_crossings"] = int(m.group(1)) if m else 20
    # Liquidity from func_get_symbols.py
    if SYMBOLS_FILE.exists():
        sym_content = SYMBOLS_FILE.read_text(encoding="utf-8")
        m = re.search(r'^MIN_TURNOVER_24H\s*=\s*([\d_]+)', sym_content, re.MULTILINE)
        config["min_turnover_24h"] = int(m.group(1).replace("_", "")) if m else 2000000
    else:
        config["min_turnover_24h"] = 2000000
    return config


def write_strategy_config(config):
    """Rewrite config_strategy_api.py and func_get_symbols.py with updated values."""
    content = STRATEGY_CONFIG.read_text(encoding="utf-8")
    content = re.sub(r'^mode\s*=\s*["\'](\w+)["\']', f'mode = "{config["mode"]}"', content, flags=re.MULTILINE)
    content = re.sub(r'^timeframe\s*=\s*\d+', f'timeframe = {config["timeframe"]}', content, flags=re.MULTILINE)
    content = re.sub(r'^kline_limit\s*=\s*\d+', f'kline_limit = {config["kline_limit"]}', content, flags=re.MULTILINE)
    content = re.sub(r'^z_score_window\s*=\s*\d+', f'z_score_window = {config["z_score_window"]}', content, flags=re.MULTILINE)
    if "min_zero_crossings" in config:
        try:
            min_zero_crossings = int(config["min_zero_crossings"])
        except (TypeError, ValueError):
            min_zero_crossings = 20
        if re.search(r'^min_zero_crossings\s*=\s*\d+', content, re.MULTILINE):
            content = re.sub(r'^min_zero_crossings\s*=\s*\d+',
                             f'min_zero_crossings = {min_zero_crossings}',
                             content,
                             flags=re.MULTILINE)
        else:
            content = content.rstrip() + f'\nmin_zero_crossings = {min_zero_crossings}\n'
    STRATEGY_CONFIG.write_text(content, encoding="utf-8")
    # Write liquidity to func_get_symbols.py
    if "min_turnover_24h" in config and SYMBOLS_FILE.exists():
        sym_content = SYMBOLS_FILE.read_text(encoding="utf-8")
        turnover = int(config["min_turnover_24h"])
        sym_content = re.sub(r'^MIN_TURNOVER_24H\s*=\s*[\d_]+',
                             f'MIN_TURNOVER_24H = {turnover:_}', sym_content, flags=re.MULTILINE)
        SYMBOLS_FILE.write_text(sym_content, encoding="utf-8")


def parse_execution_config():
    """Parse config_execution_api.py and extract key parameters."""
    content = EXECUTION_CONFIG.read_text(encoding="utf-8")
    config = {}
    m = re.search(r'^mode\s*=\s*["\'](\w+)["\']', content, re.MULTILINE)
    config["mode"] = m.group(1) if m else "demo"
    m = re.search(r'^ticker_1\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
    config["ticker_1"] = m.group(1) if m else ""
    m = re.search(r'^ticker_2\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
    config["ticker_2"] = m.group(1) if m else ""
    m = re.search(r'^signal_positive_ticker\s*=\s*(\w+)', content, re.MULTILINE)
    config["signal_positive_ticker"] = m.group(1) if m else "ticker_2"
    m = re.search(r'^signal_negative_ticker\s*=\s*(\w+)', content, re.MULTILINE)
    config["signal_negative_ticker"] = m.group(1) if m else "ticker_1"
    m = re.search(r'^limit_order_basis\s*=\s*(True|False)', content, re.MULTILINE)
    config["limit_order_basis"] = m.group(1) == "True" if m else True
    m = re.search(r'^auto_trade\s*=\s*(True|False)', content, re.MULTILINE | re.IGNORECASE)
    config["auto_trade"] = m.group(1).capitalize() == "True" if m else False
    m = re.search(r'^tradeable_capital_usdt\s*=\s*([\d.]+)', content, re.MULTILINE)
    config["tradeable_capital_usdt"] = float(m.group(1)) if m else 10000
    m = re.search(r'^stop_loss_fail_safe\s*=\s*([\d.]+)', content, re.MULTILINE)
    config["stop_loss_fail_safe"] = float(m.group(1)) if m else 0.15
    m = re.search(r'^signal_trigger_thresh\s*=\s*([\d.]+)', content, re.MULTILINE)
    config["signal_trigger_thresh"] = float(m.group(1)) if m else 1.1
    m = re.search(r'^zscore_stop_loss\s*=\s*([\d.]+)', content, re.MULTILINE)
    config["zscore_stop_loss"] = float(m.group(1)) if m else 3.0
    m = re.search(r'^custom_thresholds\s*=\s*(True|False)', content, re.MULTILINE)
    config["custom_thresholds"] = m.group(1) == "True" if m else False
    m = re.search(r'^exit_threshold\s*=\s*([\d.]+)', content, re.MULTILINE)
    config["exit_threshold"] = float(m.group(1)) if m else 0.0
    m = re.search(r'^timeframe\s*=\s*(\d+)', content, re.MULTILINE)
    config["timeframe"] = int(m.group(1)) if m else 60
    m = re.search(r'^kline_limit\s*=\s*(\d+)', content, re.MULTILINE)
    config["kline_limit"] = int(m.group(1)) if m else 200
    m = re.search(r'^z_score_window\s*=\s*(\d+)', content, re.MULTILINE)
    config["z_score_window"] = int(m.group(1)) if m else 21
    # Hybrid order strategy params
    m = re.search(r'^market_order_zscore_thresh\s*=\s*([\d.]+)', content, re.MULTILINE)
    config["market_order_zscore_thresh"] = float(m.group(1)) if m else 2.0
    m = re.search(r'^min_profit_pct\s*=\s*([\d.]+)', content, re.MULTILINE)
    config["min_profit_pct"] = float(m.group(1)) if m else 0.5
    m = re.search(r'^taker_fee_pct\s*=\s*([\d.]+)', content, re.MULTILINE)
    config["taker_fee_pct"] = float(m.group(1)) if m else 0.055
    m = re.search(r'^leverage\s*=\s*(\d+)', content, re.MULTILINE)
    config["leverage"] = int(m.group(1)) if m else 1
    return config


def write_execution_config(config):
    """Rewrite config_execution_api.py with updated values."""
    content = EXECUTION_CONFIG.read_text(encoding="utf-8")
    # Fallback: if timeframe/kline_limit/z_score_window not supplied (removed from UI),
    # preserve the current values from the file.
    for key, pattern, default in [
        ("timeframe", r'^timeframe\s*=\s*(\d+)', 60),
        ("kline_limit", r'^kline_limit\s*=\s*(\d+)', 200),
        ("z_score_window", r'^z_score_window\s*=\s*(\d+)', 21),
    ]:
        if key not in config:
            m = re.search(pattern, content, re.MULTILINE)
            config[key] = int(m.group(1)) if m else default
    content = re.sub(r'^mode\s*=\s*["\'](\w+)["\']', f'mode = "{config["mode"]}"', content, flags=re.MULTILINE)
    content = re.sub(r'^ticker_1\s*=\s*["\'][^"\']+["\']', f'ticker_1 = "{config["ticker_1"]}"', content, flags=re.MULTILINE)
    content = re.sub(r'^ticker_2\s*=\s*["\'][^"\']+["\']', f'ticker_2 = "{config["ticker_2"]}"', content, flags=re.MULTILINE)
    content = re.sub(r'^limit_order_basis\s*=\s*(True|False)',
                     f'limit_order_basis = {config["limit_order_basis"]}', content, flags=re.MULTILINE)
    if "auto_trade" in config:
        if re.search(r'^auto_trade\s*=\s*(True|False)', content, re.MULTILINE):
            content = re.sub(r'^auto_trade\s*=\s*(True|False)',
                             f'auto_trade = {config["auto_trade"]}', content, flags=re.MULTILINE)
        else:
            content = re.sub(r'(^limit_order_basis\s*=\s*(?:True|False).*$)',
                             r'\1\nauto_trade = ' + str(config["auto_trade"]), content, flags=re.MULTILINE)
    content = re.sub(r'^tradeable_capital_usdt\s*=\s*[\d.]+',
                     f'tradeable_capital_usdt = {config["tradeable_capital_usdt"]}', content, flags=re.MULTILINE)
    content = re.sub(r'^stop_loss_fail_safe\s*=\s*[\d.]+',
                     f'stop_loss_fail_safe = {config["stop_loss_fail_safe"]}', content, flags=re.MULTILINE)
    content = re.sub(r'^signal_trigger_thresh\s*=\s*[\d.]+',
                     f'signal_trigger_thresh = {config["signal_trigger_thresh"]}', content, flags=re.MULTILINE)
    content = re.sub(r'^zscore_stop_loss\s*=\s*[\d.]+',
                     f'zscore_stop_loss = {config["zscore_stop_loss"]}', content, flags=re.MULTILINE)
    # custom_thresholds & exit_threshold
    if "custom_thresholds" in config:
        val = config["custom_thresholds"]
        if isinstance(val, str):
            val = val.capitalize() == "True"
        if re.search(r'^custom_thresholds\s*=\s*(True|False)', content, re.MULTILINE):
            content = re.sub(r'^custom_thresholds\s*=\s*(True|False)',
                             f'custom_thresholds = {val}', content, flags=re.MULTILINE)
        else:
            content = content.rstrip() + f'\ncustom_thresholds = {val}\n'
    if "exit_threshold" in config:
        et = float(config["exit_threshold"])
        if re.search(r'^exit_threshold\s*=\s*[\d.]+', content, re.MULTILINE):
            content = re.sub(r'^exit_threshold\s*=\s*[\d.]+',
                             f'exit_threshold = {et}', content, flags=re.MULTILINE)
        else:
            content = content.rstrip() + f'\nexit_threshold = {et}\n'
    content = re.sub(r'^timeframe\s*=\s*\d+', f'timeframe = {config["timeframe"]}', content, flags=re.MULTILINE)
    content = re.sub(r'^kline_limit\s*=\s*\d+', f'kline_limit = {config["kline_limit"]}', content, flags=re.MULTILINE)
    content = re.sub(r'^z_score_window\s*=\s*\d+', f'z_score_window = {config["z_score_window"]}', content, flags=re.MULTILINE)
    if "market_order_zscore_thresh" in config:
        content = re.sub(r'^market_order_zscore_thresh\s*=\s*[\d.]+',
                         f'market_order_zscore_thresh = {config["market_order_zscore_thresh"]}', content, flags=re.MULTILINE)
    if "min_profit_pct" in config:
        content = re.sub(r'^min_profit_pct\s*=\s*[\d.]+',
                         f'min_profit_pct = {config["min_profit_pct"]}', content, flags=re.MULTILINE)
    if "taker_fee_pct" in config:
        content = re.sub(r'^taker_fee_pct\s*=\s*[\d.]+',
                         f'taker_fee_pct = {config["taker_fee_pct"]}', content, flags=re.MULTILINE)
    if "leverage" in config:
        lev = max(1, min(50, int(config["leverage"])))
        if re.search(r'^leverage\s*=\s*\d+', content, re.MULTILINE):
            content = re.sub(r'^leverage\s*=\s*\d+',
                             f'leverage = {lev}', content, flags=re.MULTILINE)
        else:
            content = content.rstrip() + f'\nleverage = {lev}\n'
    EXECUTION_CONFIG.write_text(content, encoding="utf-8")


def stream_process_output(proc, output_list, lock):
    """Read process stdout/stderr line by line into output_list."""
    for line in iter(proc.stdout.readline, ""):
        with lock:
            output_list.append(line.rstrip("\n"))
            # Keep last 500 lines
            if len(output_list) > 500:
                output_list.pop(0)
    proc.stdout.close()


def kill_all_bot_processes():
    """Kill ALL running main_execution.py processes system-wide (not just dashboard-spawned ones).
    This prevents the 'dual bot' problem where a process started from a terminal
    and one from the dashboard both write to the same bot.log simultaneously."""
    import subprocess as _sp
    killed = []
    try:
        # Use tasklist on Windows / ps on Unix to find all python processes
        if sys.platform == "win32":
            result = _sp.run(
                ["wmic", "process", "where",
                 "name='python.exe'", "get", "processid,commandline"],
                capture_output=True, text=True
            )
            for line in result.stdout.splitlines():
                if "main_execution.py" in line:
                    parts = line.strip().split()
                    pid = int(parts[-1])
                    try:
                        _sp.run(["taskkill", "/F", "/PID", str(pid)],
                                capture_output=True)
                        killed.append(pid)
                    except Exception:
                        pass
        else:
            result = _sp.run(["pgrep", "-f", "main_execution.py"],
                             capture_output=True, text=True)
            for pid_str in result.stdout.splitlines():
                try:
                    os.kill(int(pid_str), signal.SIGTERM)
                    killed.append(int(pid_str))
                except Exception:
                    pass
    except Exception as e:
        print(f"kill_all_bot_processes error: {e}")
    return killed


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES – Config
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/config/strategy", methods=["GET"])
def get_strategy_config():
    try:
        return jsonify(parse_strategy_config())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/config/strategy", methods=["POST"])
def set_strategy_config():
    try:
        data = request.json
        write_strategy_config(data)
        return jsonify({"status": "saved", "config": parse_strategy_config()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/config/execution", methods=["GET"])
def get_execution_config():
    try:
        return jsonify(parse_execution_config())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/config/execution", methods=["POST"])
def set_execution_config():
    try:
        data = request.json
        write_execution_config(data)
        return jsonify({"status": "saved", "config": parse_execution_config()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES – Strategy
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/strategy/run", methods=["POST"])
def run_strategy():
    global strategy_process, strategy_output
    with strategy_lock:
        if strategy_process and strategy_process.poll() is None:
            return jsonify({"error": "Strategy is already running"}), 409
        strategy_output = ["▶ Starting strategy pipeline..."]
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-u", str(STRATEGY_DIR / "main_strategy.py")],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=str(STRATEGY_DIR), env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    strategy_process = proc
    t = threading.Thread(target=stream_process_output, args=(proc, strategy_output, strategy_lock), daemon=True)
    t.start()
    return jsonify({"status": "started", "pid": proc.pid})


@app.route("/api/strategy/status", methods=["GET"])
def strategy_status():
    global strategy_process
    running = strategy_process is not None and strategy_process.poll() is None
    with strategy_lock:
        lines = list(strategy_output[-100:])
    return jsonify({"running": running, "output": lines})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES – Execution
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/execution/start", methods=["POST"])
def start_execution():
    global execution_process, execution_output
    # Kill ALL stray main_execution.py processes first (including those
    # started from an external terminal) to prevent dual-bot log pollution.
    killed = kill_all_bot_processes()
    with execution_lock:
        execution_output = []
        if killed:
            execution_output.append(f"⚠️ Killed {len(killed)} stray bot process(es): {killed}")
        execution_output.append("▶ Starting execution bot...")
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-u", str(EXECUTION_DIR / "main_execution.py")],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=str(EXECUTION_DIR), env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    execution_process = proc
    t = threading.Thread(target=stream_process_output, args=(proc, execution_output, execution_lock), daemon=True)
    t.start()
    return jsonify({"status": "started", "pid": proc.pid, "killed_pids": killed})


@app.route("/api/execution/stop", methods=["POST"])
def stop_execution():
    global execution_process
    if execution_process and execution_process.poll() is None:
        if sys.platform == "win32":
            execution_process.terminate()
        else:
            os.kill(execution_process.pid, signal.SIGTERM)
        execution_process.wait(timeout=10)
        with execution_lock:
            execution_output.append("⏹ Bot stopped by user.")
        return jsonify({"status": "stopped"})
    return jsonify({"status": "not_running"})


@app.route("/api/execution/reset", methods=["POST"])
def reset_execution():
    """Cancel all open orders and close all positions for the configured pair.
    Automatically stops the bot first (if running), then runs reset_bot.py,
    and returns whether the account ended up clean."""
    global execution_process, execution_output

    # Kill ALL stray main_execution.py processes (not just dashboard-spawned ones)
    killed = kill_all_bot_processes()

    with execution_lock:
        execution_output = []
        if killed:
            execution_output.append(f"⏹ Killed {len(killed)} bot process(es): {killed}")
        execution_output.append("🔄 Resetting — cancelling orders and closing positions...")

    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        result = subprocess.run(
            [sys.executable, "-u", str(EXECUTION_DIR / "reset_bot.py")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=str(EXECUTION_DIR), env=env,
            timeout=60,
        )
        lines = (result.stdout or "").splitlines()
        with execution_lock:
            for line in lines:
                execution_output.append(line)
                if len(execution_output) > 500:
                    execution_output.pop(0)

        clean = result.returncode == 0
        status = "clean" if clean else "failed"
        with execution_lock:
            execution_output.append(
                "✅ Reset complete — account is CLEAN. You can now Start the bot." if clean
                else "⚠️ Reset finished with warnings. Check logs."
            )
        return jsonify({"status": status, "output": lines, "clean": clean})
    except subprocess.TimeoutExpired:
        with execution_lock:
            execution_output.append("⏱ Reset timed out after 60s.")
        return jsonify({"error": "reset_timeout"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/execution/test-leverage", methods=["POST"])
def test_leverage():
    """Test setting leverage on Bybit for the configured pair.
    Reads leverage + tickers from config and calls set_leverage API."""
    try:
        cfg = parse_execution_config()
        mode = cfg.get("mode", "demo")
        ticker_1 = cfg.get("ticker_1", "")
        ticker_2 = cfg.get("ticker_2", "")
        lev = max(1, min(50, int(cfg.get("leverage", 1))))

        if not ticker_1 or not ticker_2:
            return jsonify({"error": "No tickers configured"}), 400

        from dotenv import load_dotenv as _ld
        _ld(str(BASE_DIR / ".env"))

        if mode == "test":
            ak, sk = os.getenv("API_KEY_TESTNET", ""), os.getenv("API_SECRET_TESTNET", "")
        elif mode == "demo":
            ak, sk = os.getenv("API_KEY_DEMO", ""), os.getenv("API_SECRET_DEMO", "")
        else:
            ak, sk = os.getenv("API_KEY_MAINNET", ""), os.getenv("API_SECRET_MAINNET", "")

        if not ak or not sk:
            return jsonify({"error": f"No API key for mode '{mode}'"}), 400

        from pybit.unified_trading import HTTP as _H
        if mode == "test":
            sess = _H(testnet=True, api_key=ak, api_secret=sk)
        elif mode == "demo":
            sess = _H(demo=True, api_key=ak, api_secret=sk)
        else:
            sess = _H(api_key=ak, api_secret=sk)

        results = []
        for ticker in [ticker_1, ticker_2]:
            try:
                resp = sess.set_leverage(
                    category="linear", symbol=ticker,
                    buyLeverage=str(lev), sellLeverage=str(lev)
                )
                ret_code = resp.get("retCode", -1)
                ret_msg = resp.get("retMsg", "")
                ok = ret_code == 0 or "not modified" in ret_msg.lower()
                results.append({"symbol": ticker, "success": ok,
                                "retCode": ret_code, "retMsg": ret_msg})
            except Exception as e:
                err_str = str(e).lower()
                if "not modified" in err_str or "110043" in err_str:
                    results.append({"symbol": ticker, "success": True,
                                    "retMsg": "Already at target leverage (no change needed)"})
                else:
                    results.append({"symbol": ticker, "success": False, "error": str(e)})

        all_ok = all(r["success"] for r in results)
        return jsonify({"status": "ok" if all_ok else "partial_fail",
                        "leverage": lev, "results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/execution/status", methods=["GET"])
def execution_status():
    global execution_process
    running = execution_process is not None and execution_process.poll() is None
    # Read status.json
    status_data = {}
    try:
        if STATUS_JSON.exists():
            status_data = json.loads(STATUS_JSON.read_text(encoding="utf-8"))
    except Exception:
        pass
    with execution_lock:
        lines = list(execution_output[-100:])
    return jsonify({"running": running, "status": status_data, "output": lines})


@app.route("/api/execution/zscore-live", methods=["GET"])
def execution_zscore_live():
    """Get live z-score using the exact same execution pipeline as the bot."""
    try:
        base_path = str(BASE_DIR)
        execution_path = str(EXECUTION_DIR)
        if base_path not in sys.path:
            sys.path.insert(0, base_path)
        if execution_path not in sys.path:
            sys.path.insert(0, execution_path)

        from func_get_zscore import get_latest_zscore
        from config_execution_api import ticker_1, ticker_2, signal_trigger_thresh, zscore_stop_loss

        latest = get_latest_zscore()
        if latest is None:
            return jsonify({
                "available": False,
                "zscore": None,
                "ticker_1": ticker_1,
                "ticker_2": ticker_2,
                "source": "execution_live_midprice",
                "reason": "data_unavailable"
            })

        zscore, signal_sign_positive = latest
        zscore = float(zscore)

        return jsonify({
            "available": True,
            "zscore": zscore,
            "signal_sign_positive": bool(signal_sign_positive),
            "ticker_1": ticker_1,
            "ticker_2": ticker_2,
            "signal_trigger_thresh": float(signal_trigger_thresh),
            "zscore_stop_loss": float(zscore_stop_loss),
            "source": "execution_live_midprice"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES – Data
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/backtest/pair", methods=["GET"])
def get_backtest_pair():
    try:
        sym1 = request.args.get('sym1')
        sym2 = request.args.get('sym2')
        if not sym1 or not sym2:
            return jsonify({"error": "Missing sym1 or sym2"}), 400

        sys.path.insert(0, str(STRATEGY_DIR))
        from func_cointegration import extract_close_prices, calculate_cointegration_basic, calculate_spread, calculate_zscore

        if not PRICE_JSON.exists():
            return jsonify({"error": "Price data not found. Run strategy first."}), 400

        with open(PRICE_JSON, "r") as f:
            prices = json.load(f)

        if sym1 not in prices or sym2 not in prices:
            return jsonify({"error": f"Price data not found for {sym1} or {sym2}"}), 400

        prices_1 = extract_close_prices(prices[sym1])
        prices_2 = extract_close_prices(prices[sym2])

        basic = calculate_cointegration_basic(prices_1, prices_2)
        if basic is None:
            return jsonify({"error": f"{sym1} vs {sym2} are not cointegrated"}), 400
        hedge_ratio = basic["hedge_ratio"]
        spread = calculate_spread(prices_1, prices_2, hedge_ratio)
        zscore = calculate_zscore(spread)

        import math

        def safe_float(val):
            try:
                f = float(val)
                return None if math.isnan(f) else f
            except Exception:
                return None

        rows = []
        for i in range(len(prices_1)):
            s_val = spread[i] if hasattr(spread, '__getitem__') else None
            z_val = zscore[i] if hasattr(zscore, '__getitem__') else None
            rows.append({
                sym1: prices_1[i],
                sym2: prices_2[i],
                "Spread": safe_float(s_val),
                "ZScore": safe_float(z_val)
            })

        return jsonify({
            "data": rows,
            "columns": [sym1, sym2, "Spread", "ZScore"]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/backtest/pair/live", methods=["GET"])
def get_backtest_pair_live():
    """Fetch LIVE klines from Bybit and compute spread/zscore for charting
    using BOT-EQUIVALENT REPLAY — at each historical point T, we replay
    exactly what the bot's get_latest_zscore() would have computed:
      1. Take kline_limit candles ending at T
      2. Run ONE OLS → ONE hedge_ratio β_T
      3. Compute ALL kline_limit spreads using that single β_T
      4. Compute rolling z-score (window=z_score_window) on those spreads
      5. Take the LAST z-score value = what the bot would have seen at time T
    This ensures the chart accurately represents bot decision points."""
    try:
        import math as _math
        import pandas as pd

        sym1 = request.args.get('sym1', '').strip().upper()
        sym2 = request.args.get('sym2', '').strip().upper()
        if not sym1 or not sym2:
            return jsonify({"error": "Missing sym1 or sym2"}), 400

        exec_cfg = parse_execution_config()
        tf_param = request.args.get('timeframe', '').strip()
        if tf_param and tf_param.isdigit() and int(tf_param) in (1, 5, 15, 30, 60):
            timeframe = int(tf_param)
        else:
            timeframe = exec_cfg.get("timeframe", 60)

        dur_param = request.args.get('duration', '').strip()
        duration_hours = int(dur_param) if dur_param and dur_param.isdigit() and int(dur_param) > 0 else 48
        display_limit = int(duration_hours * 60 / timeframe)

        config_kline_limit = exec_cfg.get("kline_limit", 200)
        z_window = exec_cfg.get("z_score_window", 21)

        # Fetch enough candles: kline_limit for OLS warmup + display candles
        fetch_limit = min(config_kline_limit + display_limit, 1000)

        for p in (str(STRATEGY_DIR), str(EXECUTION_DIR), str(BASE_DIR)):
            if p not in sys.path:
                sys.path.insert(0, p)

        import statsmodels.api as sm

        sess = _get_pub_session()
        r1 = sess.get_mark_price_kline(category="linear", symbol=sym1,
                            interval=str(timeframe), limit=fetch_limit)
        r2 = sess.get_mark_price_kline(category="linear", symbol=sym2,
                            interval=str(timeframe), limit=fetch_limit)

        kl1 = list(reversed(r1.get("result", {}).get("list", [])))
        kl2 = list(reversed(r2.get("result", {}).get("list", [])))
        if not kl1 or not kl2:
            return jsonify({"error": "No kline data returned from Bybit"}), 500

        n = min(len(kl1), len(kl2))
        p1 = [float(kl1[i][4]) for i in range(n)]
        p2 = [float(kl2[i][4]) for i in range(n)]
        ts = [int(kl1[i][0]) for i in range(n)]

        if len(p1) < config_kline_limit + z_window:
            return jsonify({"error": "Insufficient data points"}), 400

        # ── BOT-EQUIVALENT REPLAY ──────────────────────────────────────
        # At each display point T, replay what the bot would have computed:
        #   - Take kline_limit candles ending at T (inclusive)
        #   - OLS on those candles → single β_T (hedge_ratio)
        #   - Compute ALL spreads in the window using β_T
        #   - Rolling z-score on those spreads → take LAST value
        # This matches get_latest_zscore() → calculate_metrics() exactly.
        replay_zscores = []
        replay_spreads = []
        replay_hedge_ratios = []
        valid_p1 = []
        valid_p2 = []
        valid_ts = []

        for T in range(config_kline_limit, len(p1)):
            # Window of kline_limit candles ending at T (same as bot's get_latest_klines)
            window_p1 = p1[T - config_kline_limit + 1:T + 1]
            window_p2 = p2[T - config_kline_limit + 1:T + 1]

            # Single OLS on entire window → one hedge_ratio (same as bot)
            model = sm.OLS(window_p1, window_p2).fit()
            beta_T = float(model.params[0])

            # Compute ALL spreads using this single β_T (same as bot)
            window_spread = [window_p1[j] - beta_T * window_p2[j]
                             for j in range(len(window_p1))]

            # Rolling z-score on those spreads (same as bot's calculate_zscore)
            spread_series = pd.Series(window_spread)
            mean = spread_series.rolling(center=False, window=z_window).mean()
            std = spread_series.rolling(center=False, window=z_window).std()
            z_series = (spread_series - mean) / std

            # Take the LAST z-score = what the bot sees at time T
            z_at_T = float(z_series.iloc[-1])

            replay_zscores.append(z_at_T)
            replay_spreads.append(window_spread[-1])
            replay_hedge_ratios.append(beta_T)
            valid_p1.append(p1[T])
            valid_p2.append(p2[T])
            valid_ts.append(ts[T])

        def safe(val):
            try:
                f = float(val)
                return None if _math.isnan(f) else f
            except Exception:
                return None

        from datetime import datetime, timezone, timedelta
        ict = timezone(timedelta(hours=7))

        full_rows = []
        for i in range(len(valid_p1)):
            dt = datetime.fromtimestamp(valid_ts[i] / 1000, tz=ict)
            full_rows.append({
                sym1: valid_p1[i],
                sym2: valid_p2[i],
                "Spread": safe(replay_spreads[i]),
                "ZScore": safe(replay_zscores[i]),
                "Time": dt.strftime("%m/%d %H:%M"),
            })

        # Trim to requested display duration
        rows = full_rows[-display_limit:] if len(full_rows) > display_limit else full_rows

        return jsonify({
            "data": rows,
            "columns": [sym1, sym2, "Spread", "ZScore", "Time"],
            "method": "bot_equivalent_replay",
        })
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@app.route("/api/pairs", methods=["GET"])
def get_pairs():
    try:
        if not COINTEGRATED_CSV.exists():
            return jsonify({"pairs": []})
        pairs = []
        with open(COINTEGRATED_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                pairs.append(row)
        return jsonify({"pairs": pairs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Z-Score utilities for pairs table ────────────────────────────────────────
_pairs_zscore_cache: dict = {}   # {(sym1, sym2): (zscore_float, ts)}
_pairs_history_cache: dict = {}  # {(sym1, sym2): ([{t,z}, ...], ts)}
_ZSCORE_TTL   = 10   # seconds – current z-score cache
_HISTORY_TTL  = 30   # seconds – 24h history cache
_pub_anon_session = None         # unauthenticated pybit session (created once)


def _get_pub_session():
    """Return a cached unauthenticated pybit session for public kline calls."""
    global _pub_anon_session
    if _pub_anon_session is None:
        from pybit.unified_trading import HTTP as _H
        _pub_anon_session = _H()
    return _pub_anon_session


def _get_hedge_ratio(sym1, sym2):
    """Read hedge_ratio from cached CSV; falls back to None if not found."""
    if not COINTEGRATED_CSV.exists():
        return None
    with open(COINTEGRATED_CSV, newline="", encoding="utf-8") as f:
        import csv as _csv2
        for row in _csv2.DictReader(f):
            if row.get("sym_1", "").upper() == sym1 and row.get("sym_2", "").upper() == sym2:
                try:
                    return float(row["hedge_ratio"])
                except (KeyError, ValueError):
                    return None
    return None


def _compute_pair_zscores(sym1, sym2, kline_limit=None, timeframe_override=None):
    """
    Fetch klines for sym1/sym2 and return (zscore_list, timestamps_ms_list,
    hedge_ratio) using BOT-EQUIVALENT REPLAY.

    At each point T, we replay exactly what the bot's get_latest_zscore()
    would compute:
      1. Take kline_limit candles ending at T
      2. ONE OLS → single hedge_ratio β_T
      3. Compute ALL spreads using β_T
      4. Rolling z-score → take LAST value
    """
    import math as _math
    import statsmodels.api as sm
    import pandas as pd

    # Ensure execution dir is on sys.path for func_stats
    for p in (str(EXECUTION_DIR), str(BASE_DIR)):
        if p not in sys.path:
            sys.path.insert(0, p)

    exec_cfg    = parse_execution_config()
    timeframe   = timeframe_override or exec_cfg.get("timeframe", 60)
    z_window    = exec_cfg.get("z_score_window", 21)
    if kline_limit is None:
        kline_limit = exec_cfg.get("kline_limit", 200)

    # Fetch 2x candles: kline_limit for OLS warmup + kline_limit for display
    fetch_limit = min(kline_limit * 2, 1000)

    sess = _get_pub_session()
    r1 = sess.get_mark_price_kline(category="linear", symbol=sym1,
                        interval=str(timeframe), limit=fetch_limit)
    r2 = sess.get_mark_price_kline(category="linear", symbol=sym2,
                        interval=str(timeframe), limit=fetch_limit)

    kl1 = r1.get("result", {}).get("list", [])
    kl2 = r2.get("result", {}).get("list", [])
    if not kl1 or not kl2:
        raise ValueError("No kline data returned")

    # Bybit: newest-first → reverse to chronological. k[0]=ts_ms, k[4]=close
    kl1 = list(reversed(kl1))
    kl2 = list(reversed(kl2))

    n = min(len(kl1), len(kl2))
    p1 = [float(kl1[i][4]) for i in range(n) if not _math.isnan(float(kl1[i][4]))]
    p2 = [float(kl2[i][4]) for i in range(n) if not _math.isnan(float(kl2[i][4]))]
    ts = [int(kl1[i][0]) for i in range(min(len(p1), len(p2)))]

    n2 = min(len(p1), len(p2))
    if n2 < kline_limit + z_window:
        raise ValueError("Insufficient data for bot-equivalent replay")

    p1, p2, ts = p1[-n2:], p2[-n2:], ts[-n2:]

    # ── BOT-EQUIVALENT REPLAY ──────────────────────────────────────────
    zscores = []
    valid_ts = []
    latest_hr = 0.0
    for T in range(kline_limit, n2):
        # Window of kline_limit candles ending at T (inclusive)
        window_p1 = p1[T - kline_limit + 1:T + 1]
        window_p2 = p2[T - kline_limit + 1:T + 1]

        # Single OLS on entire window → one hedge_ratio (same as bot)
        model = sm.OLS(window_p1, window_p2).fit()
        beta_T = float(model.params[0])

        # Compute ALL spreads using this single β_T
        window_spread = [window_p1[j] - beta_T * window_p2[j]
                         for j in range(len(window_p1))]

        # Rolling z-score (same as bot's calculate_zscore)
        spread_series = pd.Series(window_spread)
        mean = spread_series.rolling(center=False, window=z_window).mean()
        std = spread_series.rolling(center=False, window=z_window).std()
        z_series = (spread_series - mean) / std

        z_at_T = float(z_series.iloc[-1])
        zscores.append(z_at_T)
        valid_ts.append(ts[T])
        latest_hr = beta_T

    return zscores, valid_ts, latest_hr


@app.route("/api/pairs/zscore", methods=["GET"])
def get_pair_zscore():
    """Current z-score for a pair. 10 s server-side cache."""
    sym1 = request.args.get("sym1", "").strip().upper()
    sym2 = request.args.get("sym2", "").strip().upper()
    if not sym1 or not sym2:
        return jsonify({"error": "Missing sym1 or sym2"}), 400

    cached = _pairs_zscore_cache.get((sym1, sym2))
    if cached and (time.time() - cached[1]) < _ZSCORE_TTL:
        return jsonify({"zscore": cached[0], "sym1": sym1, "sym2": sym2, "cached": True})

    try:
        import math as _math
        zscores, _, _ = _compute_pair_zscores(sym1, sym2)
        last_z = next((float(v) for v in reversed(zscores)
                       if not _math.isnan(float(v))), None)
        if last_z is None:
            return jsonify({"error": "Could not compute z-score"}), 500
        _pairs_zscore_cache[(sym1, sym2)] = (last_z, time.time())
        return jsonify({"zscore": last_z, "sym1": sym1, "sym2": sym2, "cached": False})
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@app.route("/api/pairs/zscore-history", methods=["GET"])
def get_pair_zscore_history():
    """
    Return up to 24 h of z-score data points, oldest-first.
    Each entry: { t: epoch_ms, z: float, label: 'HH:MM' }
    30 s server-side cache.
    """
    sym1 = request.args.get("sym1", "").strip().upper()
    sym2 = request.args.get("sym2", "").strip().upper()
    if not sym1 or not sym2:
        return jsonify({"error": "Missing sym1 or sym2"}), 400

    cached = _pairs_history_cache.get((sym1, sym2))
    if cached and (time.time() - cached[1]) < _HISTORY_TTL:
        return jsonify({"history": cached[0], "sym1": sym1, "sym2": sym2, "cached": True})

    try:
        import math as _math
        from datetime import datetime, timezone, timedelta

        # Use execution config timeframe + kline_limit (same as bot)
        zscores, timestamps, _ = _compute_pair_zscores(sym1, sym2)

        # Build output array; skip NaN entries (z-score window warm-up)
        ict = timezone(timedelta(hours=7))
        history = []
        for ts_ms, z in zip(timestamps, zscores):
            try:
                fz = float(z)
                if _math.isnan(fz):
                    continue
                dt = datetime.fromtimestamp(ts_ms / 1000, tz=ict)
                history.append({
                    "t": ts_ms,
                    "z": round(fz, 4),
                    "label": dt.strftime("%H:%M")
                })
            except (TypeError, ValueError):
                continue

        _pairs_history_cache[(sym1, sym2)] = (history, time.time())
        return jsonify({"history": history, "sym1": sym1, "sym2": sym2, "cached": False})
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500



@app.route("/api/backtest", methods=["GET"])
def get_backtest():
    try:
        if not BACKTEST_CSV.exists():
            return jsonify({"data": []})
        rows = []
        with open(BACKTEST_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        return jsonify({"data": rows, "columns": list(rows[0].keys()) if rows else []})
    except Exception as e:
        return jsonify({"error": str(e)}), 500








# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES – Performance (P&L)
# ═══════════════════════════════════════════════════════════════════════════════

# Start time: 20/03/2026 16:00 ICT (UTC+7) = 20/03/2026 09:00 UTC = 1773997200000 ms
# Verification: 2026-01-01 UTC = 1767225600, + 78 days = 1773964800, + 9h = 1773997200
PERF_START_MS = 1773997200000


def _make_session(mode, api_key, api_secret):
    """Create a Bybit HTTP session for the given mode."""
    from pybit.unified_trading import HTTP as _HTTP
    if mode == "test":
        return _HTTP(testnet=True, api_key=api_key, api_secret=api_secret)
    elif mode == "demo":
        return _HTTP(demo=True, api_key=api_key, api_secret=api_secret)
    else:
        return _HTTP(api_key=api_key, api_secret=api_secret)


def _fetch_all_closed_pnl(session, fetch_start_ms):
    """Paginate through Bybit closed-pnl starting from fetch_start_ms.
    Returns ALL rows without client-side filtering (caller applies user filter).
    """
    all_rows = []
    errors = []
    cursor = ""
    while True:
        kwargs = dict(category="linear", startTime=fetch_start_ms, limit=200)
        if cursor:
            kwargs["cursor"] = cursor
        try:
            resp = session.get_closed_pnl(**kwargs)
        except Exception as exc:
            errors.append(f"get_closed_pnl exception: {exc}")
            break
        ret = resp.get("retCode", -1)
        if ret != 0:
            errors.append(f"get_closed_pnl retCode={ret} msg={resp.get('retMsg', '')}")
            break
        result = resp.get("result", {})
        rows = result.get("list", [])
        all_rows.extend(rows)
        cursor = result.get("nextPageCursor", "")
        if not cursor or not rows:
            break
    return all_rows, errors


def _fetch_all_executions(session, fetch_start_ms):
    """Paginate through Bybit execution list starting from fetch_start_ms.
    Returns ALL rows without client-side filtering (caller applies user filter).
    """
    all_rows = []
    errors = []
    cursor = ""
    while True:
        kwargs = dict(category="linear", startTime=fetch_start_ms, limit=200)
        if cursor:
            kwargs["cursor"] = cursor
        try:
            resp = session.get_executions(**kwargs)
        except Exception as exc:
            errors.append(f"get_executions exception: {exc}")
            break
        ret = resp.get("retCode", -1)
        if ret != 0:
            errors.append(f"get_executions retCode={ret} msg={resp.get('retMsg', '')}")
            break
        result = resp.get("result", {})
        rows = result.get("list", [])
        all_rows.extend(rows)
        cursor = result.get("nextPageCursor", "")
        if not cursor or not rows:
            break
    return all_rows, errors


def _fetch_transaction_log(session, fetch_start_ms):
    """Fetch ALL transaction log entries from Bybit starting from fetch_start_ms.
    This includes TRADE P&L, SETTLEMENT (funding fees), FEE, etc.
    Returns the TRUE total P&L that matches what Bybit UI shows.

    NOTE: Bybit limits startTime-endTime to ≤ 7 days, so we split into
    7-day windows and paginate within each window.
    """
    import time as _time
    all_rows = []
    errors = []
    SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000
    now_ms = int(_time.time() * 1000)

    window_start = fetch_start_ms
    while window_start < now_ms:
        window_end = min(window_start + SEVEN_DAYS_MS, now_ms)
        cursor = ""
        while True:
            kwargs = dict(
                accountType="UNIFIED",
                category="linear",
                startTime=window_start,
                endTime=window_end,
                limit=50,
            )
            if cursor:
                kwargs["cursor"] = cursor
            try:
                resp = session.get_transaction_log(**kwargs)
            except Exception as exc:
                errors.append(f"get_transaction_log exception: {exc}")
                return all_rows, errors
            ret = resp.get("retCode", -1)
            if ret != 0:
                errors.append(f"get_transaction_log retCode={ret} msg={resp.get('retMsg', '')}")
                return all_rows, errors
            result = resp.get("result", {})
            rows = result.get("list", [])
            all_rows.extend(rows)
            cursor = result.get("nextPageCursor", "")
            if not cursor or not rows:
                break
        window_start = window_end
    return all_rows, errors


@app.route("/api/performance", methods=["GET"])
def get_performance():
    """
    Return real P&L performance using Bybit's Wallet Balance API directly.
    
    Key fields from get_wallet_balance:
      - walletBalance:  current USDT balance (realized only)
      - unrealisedPnl:  P&L of currently open positions
      - cumRealisedPnl: cumulative realized P&L since account creation
      - equity:         walletBalance + unrealisedPnl
    
    Starting capital = walletBalance - cumRealisedPnl (= initial deposit)
    Total P&L = cumRealisedPnl + unrealisedPnl
    P&L % = total_pnl / starting_capital × 100
    
    This matches Bybit UI exactly — no manual calculation needed.
    """
    try:
        # ── Read execution config ──────────────────────────────────────
        cfg = parse_execution_config()
        mode = cfg.get("mode", "demo")

        # Load .env
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(str(BASE_DIR / ".env"))

        if mode == "test":
            api_key    = os.getenv("API_KEY_TESTNET", "")
            api_secret = os.getenv("API_SECRET_TESTNET", "")
        elif mode == "demo":
            api_key    = os.getenv("API_KEY_DEMO", "")
            api_secret = os.getenv("API_SECRET_DEMO", "")
        else:
            api_key    = os.getenv("API_KEY_MAINNET", "")
            api_secret = os.getenv("API_SECRET_MAINNET", "")

        if not api_key or not api_secret:
            return jsonify({"error": f"No API key configured for mode '{mode}'"}), 400

        session = _make_session(mode, api_key, api_secret)

        # ══════════════════════════════════════════════════════════════
        # 1. WALLET BALANCE — the single source of truth
        # ══════════════════════════════════════════════════════════════
        wallet_resp = session.get_wallet_balance(accountType="UNIFIED")
        wallet_result = wallet_resp.get("result", {})
        accounts = wallet_result.get("list", [])

        wallet_balance = 0.0
        unrealised_pnl = 0.0
        cum_realised_pnl = 0.0
        equity = 0.0

        if accounts:
            acc = accounts[0]
            equity = float(acc.get("totalEquity", 0))
            # Find USDT coin for detailed breakdown
            for coin in acc.get("coin", []):
                if coin.get("coin") == "USDT":
                    wallet_balance   = float(coin.get("walletBalance", 0))
                    unrealised_pnl   = float(coin.get("unrealisedPnl", 0))
                    cum_realised_pnl = float(coin.get("cumRealisedPnl", 0))
                    break

        # Starting capital = what you deposited = current balance minus all P&L
        starting_capital = wallet_balance - cum_realised_pnl
        # Total P&L = realized + unrealized
        total_pnl = cum_realised_pnl + unrealised_pnl
        # P&L percentage based on what you actually deposited
        pnl_pct = (total_pnl / starting_capital * 100) if starting_capital > 0 else 0.0

        # ══════════════════════════════════════════════════════════════
        # 2. TRANSACTION LOG — for funding/fee breakdown (informational)
        # ══════════════════════════════════════════════════════════════
        # Parse time filters
        start_ms_param = request.args.get("startMs")
        start_ms = int(start_ms_param) if start_ms_param else PERF_START_MS
        end_ms_param = request.args.get("endMs")
        end_ms = int(end_ms_param) if end_ms_param else None

        funding_total = 0.0
        fee_total = 0.0
        trade_pnl = 0.0
        period_pnl = 0.0  # net cash change in the filtered period
        try:
            all_txn_rows, _ = _fetch_transaction_log(session, PERF_START_MS)
            # Apply time filter
            if end_ms is not None:
                txn_rows = [r for r in all_txn_rows
                            if start_ms <= int(r.get("transactionTime", 0)) <= end_ms]
            else:
                txn_rows = [r for r in all_txn_rows
                            if int(r.get("transactionTime", 0)) >= start_ms]
            for row in txn_rows:
                txn_type = row.get("type", "")
                # 'change' = net effect on cash balance per transaction
                period_pnl += float(row.get("change", 0))
                if txn_type == "TRADE":
                    trade_pnl += float(row.get("cashFlow", 0))
                    fee_total -= float(row.get("fee", 0))
                elif txn_type == "SETTLEMENT":
                    funding_total += float(row.get("funding", 0))
        except Exception:
            pass  # Non-fatal: breakdown is informational only

        # Period-specific PnL %: when a period filter is used, show PnL for
        # that period only (from transaction log 'change' sums), not all-time.
        is_filtered = start_ms_param is not None
        if is_filtered:
            period_pnl_pct = (period_pnl / starting_capital * 100) if starting_capital > 0 else 0.0
        else:
            # No filter → show all-time from wallet balance (most accurate)
            period_pnl = total_pnl
            period_pnl_pct = pnl_pct

        # ══════════════════════════════════════════════════════════════
        # 3. CLOSED PNL — for pair count
        # ══════════════════════════════════════════════════════════════
        pair_count = 0
        try:
            all_pnl_rows, _ = _fetch_all_closed_pnl(session, PERF_START_MS)
            if end_ms is not None:
                closed_pnl_rows = [r for r in all_pnl_rows
                                   if start_ms <= int(r.get("updatedTime", 0)) <= end_ms]
            else:
                closed_pnl_rows = [r for r in all_pnl_rows
                                   if int(r.get("updatedTime", 0)) >= start_ms]
            groups: dict = {}
            for row in closed_pnl_rows:
                group_key = int(row.get("updatedTime", "0")) // 1000
                groups.setdefault(group_key, []).append(row)
            pair_count = len(groups)
        except Exception:
            pass  # Non-fatal

        # ══════════════════════════════════════════════════════════════
        # 4. RESPONSE
        # ══════════════════════════════════════════════════════════════
        return jsonify({
            "mode":              mode,
            "source":            "wallet_balance_api",
            # Period-specific numbers (what the header shows)
            "period_pnl":        round(period_pnl, 4),
            "period_pnl_pct":    round(period_pnl_pct, 4),
            # All-time numbers (from wallet API — always accurate)
            "total_pnl":         round(total_pnl, 4),
            "pnl_pct":           round(pnl_pct, 4),
            "starting_capital":  round(starting_capital, 4),
            "current_equity":    round(equity, 4),
            "wallet_balance":    round(wallet_balance, 4),
            "unrealised_pnl":    round(unrealised_pnl, 4),
            "cum_realised_pnl":  round(cum_realised_pnl, 4),
            # Breakdown (from transaction log — period-filtered)
            "trade_pnl":         round(trade_pnl, 4),
            "funding_fees":      round(funding_total, 4),
            "trading_fees":      round(fee_total, 4),
            # Pair count (from closedPnl — period-filtered)
            "pair_count":        pair_count,
            "filter_ms":         start_ms,
        })

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES – Serve Frontend
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def serve_index():
    return send_from_directory(str(DASHBOARD_DIR), "index.html")


@app.route("/<path:filename>")
def serve_static(filename):
    return send_from_directory(str(DASHBOARD_DIR), filename)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"🚀 Dashboard server starting...")
    print(f"   Project root: {BASE_DIR}")
    print(f"   Open http://localhost:5000 in your browser")
    app.run(host="0.0.0.0", port=5000, debug=True)
