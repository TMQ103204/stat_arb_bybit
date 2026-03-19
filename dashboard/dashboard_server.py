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
BOT_LOG = EXECUTION_DIR / "bot.log"

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
    m = re.search(r'^tradeable_capital_usdt\s*=\s*([\d.]+)', content, re.MULTILINE)
    config["tradeable_capital_usdt"] = float(m.group(1)) if m else 10000
    m = re.search(r'^stop_loss_fail_safe\s*=\s*([\d.]+)', content, re.MULTILINE)
    config["stop_loss_fail_safe"] = float(m.group(1)) if m else 0.15
    m = re.search(r'^signal_trigger_thresh\s*=\s*([\d.]+)', content, re.MULTILINE)
    config["signal_trigger_thresh"] = float(m.group(1)) if m else 1.1
    m = re.search(r'^zscore_stop_loss\s*=\s*([\d.]+)', content, re.MULTILINE)
    config["zscore_stop_loss"] = float(m.group(1)) if m else 3.0
    m = re.search(r'^timeframe\s*=\s*(\d+)', content, re.MULTILINE)
    config["timeframe"] = int(m.group(1)) if m else 60
    m = re.search(r'^kline_limit\s*=\s*(\d+)', content, re.MULTILINE)
    config["kline_limit"] = int(m.group(1)) if m else 200
    m = re.search(r'^z_score_window\s*=\s*(\d+)', content, re.MULTILINE)
    config["z_score_window"] = int(m.group(1)) if m else 21
    return config


def write_execution_config(config):
    """Rewrite config_execution_api.py with updated values."""
    content = EXECUTION_CONFIG.read_text(encoding="utf-8")
    content = re.sub(r'^mode\s*=\s*["\'](\w+)["\']', f'mode = "{config["mode"]}"', content, flags=re.MULTILINE)
    content = re.sub(r'^ticker_1\s*=\s*["\'][^"\']+["\']', f'ticker_1 = "{config["ticker_1"]}"', content, flags=re.MULTILINE)
    content = re.sub(r'^ticker_2\s*=\s*["\'][^"\']+["\']', f'ticker_2 = "{config["ticker_2"]}"', content, flags=re.MULTILINE)
    content = re.sub(r'^limit_order_basis\s*=\s*(True|False)',
                     f'limit_order_basis = {config["limit_order_basis"]}', content, flags=re.MULTILINE)
    content = re.sub(r'^tradeable_capital_usdt\s*=\s*[\d.]+',
                     f'tradeable_capital_usdt = {config["tradeable_capital_usdt"]}', content, flags=re.MULTILINE)
    content = re.sub(r'^stop_loss_fail_safe\s*=\s*[\d.]+',
                     f'stop_loss_fail_safe = {config["stop_loss_fail_safe"]}', content, flags=re.MULTILINE)
    content = re.sub(r'^signal_trigger_thresh\s*=\s*[\d.]+',
                     f'signal_trigger_thresh = {config["signal_trigger_thresh"]}', content, flags=re.MULTILINE)
    content = re.sub(r'^zscore_stop_loss\s*=\s*[\d.]+',
                     f'zscore_stop_loss = {config["zscore_stop_loss"]}', content, flags=re.MULTILINE)
    content = re.sub(r'^timeframe\s*=\s*\d+', f'timeframe = {config["timeframe"]}', content, flags=re.MULTILINE)
    content = re.sub(r'^kline_limit\s*=\s*\d+', f'kline_limit = {config["kline_limit"]}', content, flags=re.MULTILINE)
    content = re.sub(r'^z_score_window\s*=\s*\d+', f'z_score_window = {config["z_score_window"]}', content, flags=re.MULTILINE)
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
    with execution_lock:
        if execution_process and execution_process.poll() is None:
            return jsonify({"error": "Execution bot is already running"}), 409
        execution_output = ["▶ Starting execution bot..."]
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
    return jsonify({"status": "started", "pid": proc.pid})


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

        # Build response rows
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


@app.route("/api/logs", methods=["GET"])
def get_logs():
    try:
        if not BOT_LOG.exists():
            return jsonify({"lines": []})
        lines = BOT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        return jsonify({"lines": lines[-200:]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES – Git
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/git/status", methods=["GET"])
def git_status():
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, cwd=str(BASE_DIR),
        )
        return jsonify({"output": result.stdout, "error": result.stderr})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/git/push", methods=["POST"])
def git_push():
    try:
        msg = (request.json or {}).get("message", "Dashboard update")
        # Add all
        subprocess.run(["git", "add", "."], cwd=str(BASE_DIR), capture_output=True)
        # Commit
        commit = subprocess.run(
            ["git", "commit", "-m", msg],
            capture_output=True, text=True, cwd=str(BASE_DIR),
        )
        # Push
        push = subprocess.run(
            ["git", "push"],
            capture_output=True, text=True, cwd=str(BASE_DIR),
        )
        return jsonify({
            "commit_output": commit.stdout + commit.stderr,
            "push_output": push.stdout + push.stderr,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
