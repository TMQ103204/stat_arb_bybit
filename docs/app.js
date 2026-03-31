/* ═══════════════════════════════════════════════════════════════════
   STAT-ARB DASHBOARD – Frontend Logic (GitHub Pages Edition)
   Connects to your LOCAL Flask backend via configurable API URL.
   ═══════════════════════════════════════════════════════════════════ */

// ── API URL – connects to your local machine ──────────────────────
const DEFAULT_API = "http://localhost:5000";
let API = localStorage.getItem("statarb_api_url") || DEFAULT_API;

// ── State ──────────────────────────────────────────────────────────
let strategyPolling = null;
let executionPolling = null;
let backtestChart = null;
let connectionOk = false;
let _chartTimeframe = 60;  // selected chart timeframe (minutes)
let _chartDuration  = 48;  // selected chart duration in hours (24h or 48h)
let _lastChartData = null; // store for PnL estimation
let _execScrolledUp = false; // user scrolled up in execution terminal

// ── Selected-pair Z-Score state ─────────────────────────────────────
let _selectedPair      = null;  // {sym1, sym2}
let _zscoreInterval    = null;  // realtime 2s interval
let _zscoreHistoryOpen = false; // is history modal open
let _zscoreHistory     = [];    // [{t, z, label}, ...] accumulated
let _histScrolledUp    = false; // user scrolled up in history list

// ── Helpers ────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  try {
    const res = await fetch(API + path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (!connectionOk) {
      connectionOk = true;
      updateConnectionUI(true);
    }
    return res.json();
  } catch (e) {
    if (connectionOk) {
      connectionOk = false;
      updateConnectionUI(false);
    }
    throw e;
  }
}

function toast(msg, type = "info") {
  const c = document.getElementById("toast-container");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  c.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 300);
  }, 3500);
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function colorLine(text) {
  const t = escHtml(text);
  if (/error|exception|critical/i.test(t))
    return `<span class="line-error">${t}</span>`;
  if (/warn/i.test(t)) return `<span class="line-warn">${t}</span>`;
  if (/info|▶|✓|found|saved|calculating|getting|plotting|setting/i.test(t))
    return `<span class="line-info">${t}</span>`;
  return t;
}

// ── Connection UI ─────────────────────────────────────────────────
function updateConnectionUI(connected) {
  const dot = document.getElementById("conn-dot");
  const label = document.getElementById("conn-label");
  if (connected) {
    dot.className = "status-dot live";
    label.textContent = "CONNECTED";
    label.style.color = "var(--success)";
  } else {
    dot.className = "status-dot error";
    label.textContent = "DISCONNECTED";
    label.style.color = "var(--danger)";
  }
}

function saveApiUrl() {
  const url = document
    .getElementById("api-url-input")
    .value.replace(/\/+$/, "");
  API = url;
  localStorage.setItem("statarb_api_url", url);
  toast("API URL saved: " + url, "success");
  initDashboard();
}

// ── Clock ──────────────────────────────────────────────────────────
function updateClock() {
  const now = new Date();
  document.getElementById("header-time").textContent = now.toLocaleTimeString(
    "en-GB",
    { hour12: false },
  );
}
setInterval(updateClock, 1000);

// ═══════════════════════════════════════════════════════════════════
// CONFIG
// ═══════════════════════════════════════════════════════════════════

function setGlobalModeUI(mode, skipSave = false) {
  const hidden = document.getElementById("global-mode");
  if (!hidden) return;
  hidden.value = mode;

  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });

  if (!skipSave) {
    onGlobalModeChange();
  }
}

async function onGlobalModeChange() {
  const mode = document.getElementById("global-mode").value;
  toast("Switching entire system to " + mode.toUpperCase() + " mode...", "info");

  // Save the new mode in BOTH configuration files
  await saveStrategyConfig();
  await saveExecutionConfig();

  // Reload P&L performance for the newly selected environment
  loadPerformance(currentPerfStartMs, currentPerfEndMs);
}

async function loadStrategyConfig() {
  try {
    const cfg = await api("/api/config/strategy");
    if (cfg.error) return toast(cfg.error, "error");
    setGlobalModeUI(cfg.mode, true);
    document.getElementById("s-timeframe").value = cfg.timeframe;
    document.getElementById("s-kline").value = cfg.kline_limit;
    document.getElementById("s-zscore-win").value = cfg.z_score_window;
    document.getElementById("s-min-zero-cross").value =
      cfg.min_zero_crossings ?? 20;
    document.getElementById("s-liquidity").value =
      cfg.min_turnover_24h || 2000000;
  } catch (e) {
    /* offline */
  }
}

async function saveStrategyConfig() {
  const minZeroCross = parseInt(
    document.getElementById("s-min-zero-cross").value,
  );
  const data = {
    mode: document.getElementById("global-mode").value,
    timeframe: parseInt(document.getElementById("s-timeframe").value),
    kline_limit: parseInt(document.getElementById("s-kline").value),
    z_score_window: parseInt(document.getElementById("s-zscore-win").value),
    min_zero_crossings: Number.isNaN(minZeroCross) ? 20 : minZeroCross,
    min_turnover_24h: parseInt(document.getElementById("s-liquidity").value),
  };
  try {
    const res = await api("/api/config/strategy", {
      method: "POST",
      body: data,
    });
    if (res.error) return toast(res.error, "error");
    toast("Strategy config saved ✓", "success");
  } catch (e) {
    toast("Cannot connect to local server", "error");
  }
}

async function loadExecutionConfig() {
  try {
    const cfg = await api("/api/config/execution");
    if (cfg.error) return toast(cfg.error, "error");
    setGlobalModeUI(cfg.mode, true);
    document.getElementById("e-ticker1").value = cfg.ticker_1;
    document.getElementById("e-ticker2").value = cfg.ticker_2;
    document.getElementById("e-capital").value = cfg.tradeable_capital_usdt;
    document.getElementById("e-trigger").value = cfg.signal_trigger_thresh;
    document.getElementById("e-stoploss").value = cfg.stop_loss_fail_safe;
    document.getElementById("e-zstop").value = cfg.zscore_stop_loss;
    document.getElementById("e-limit").checked = cfg.limit_order_basis;
    const limitToggle = document.getElementById("e-limit").nextElementSibling;
    if (limitToggle) limitToggle.classList.toggle("active", cfg.limit_order_basis);
    const limitLabel = document.getElementById("e-limit-label");
    if (limitLabel) limitLabel.innerText = cfg.limit_order_basis ? "Enabled" : "Disabled";

    const autoTrade = cfg.auto_trade === true;
    document.getElementById("e-autotrade").checked = autoTrade;
    const autoToggle = document.getElementById("e-autotrade").nextElementSibling;
    if (autoToggle) autoToggle.classList.toggle("active", autoTrade);
    const autoLabel = document.getElementById("e-autotrade-label");
    if (autoLabel) autoLabel.innerText = autoTrade ? "Enabled" : "Disabled";

    // Custom thresholds
    const isCustom = cfg.custom_thresholds === true;
    document.getElementById("e-custom-thresholds").checked = isCustom;
    const customToggle = document.getElementById("e-custom-thresholds").nextElementSibling;
    if (customToggle) customToggle.classList.toggle("active", isCustom);
    const customLabel = document.getElementById("e-custom-label");
    if (customLabel) customLabel.innerText = isCustom ? "Enabled" : "Disabled";
    document.getElementById("e-user-custom-panel").style.display = isCustom ? "block" : "none";
    document.getElementById("e-exit-threshold").value = cfg.exit_threshold ?? 0.0;
    document.getElementById("e-leverage").value = cfg.leverage ?? 1;

    document.getElementById("e-market-zscore").value = cfg.market_order_zscore_thresh ?? 2.0;
    document.getElementById("e-min-profit").value = cfg.min_profit_pct ?? 0.5;
    document.getElementById("e-taker-fee").value = cfg.taker_fee_pct ?? 0.055;
    
    // Initial P&L estimation
    estimatePnL();
  } catch (e) {
    /* offline */
  }
}

async function saveExecutionConfig() {
  const data = {
    mode: document.getElementById("global-mode").value,
    ticker_1: document.getElementById("e-ticker1").value,
    ticker_2: document.getElementById("e-ticker2").value,
    tradeable_capital_usdt: parseFloat(
      document.getElementById("e-capital").value,
    ),
    signal_trigger_thresh: parseFloat(
      document.getElementById("e-trigger").value,
    ),
    stop_loss_fail_safe: parseFloat(
      document.getElementById("e-stoploss").value,
    ),
    zscore_stop_loss: parseFloat(document.getElementById("e-zstop").value),
    limit_order_basis: document.getElementById("e-limit").checked,
    auto_trade: document.getElementById("e-autotrade").checked,
    custom_thresholds: document.getElementById("e-custom-thresholds").checked,
    exit_threshold: parseFloat(document.getElementById("e-exit-threshold").value) || 0.0,
    leverage: document.getElementById("e-custom-thresholds").checked
      ? (parseInt(document.getElementById("e-leverage").value) || 1)
      : 1,

    market_order_zscore_thresh: parseFloat(document.getElementById("e-market-zscore").value),
    min_profit_pct: parseFloat(document.getElementById("e-min-profit").value),
    taker_fee_pct: parseFloat(document.getElementById("e-taker-fee").value),
  };
  try {
    const res = await api("/api/config/execution", {
      method: "POST",
      body: data,
    });
    if (res.error) return toast(res.error, "error");
    toast("Execution config saved ✓", "success");

    // Update the chart to visually reflect the new configuration pair
    if (data.ticker_1 && data.ticker_2) {
      fetchDynamicBacktest(data.ticker_1, data.ticker_2);
    }
  } catch (e) {
    toast("Cannot connect to local server", "error");
  }
}

async function testLeverage() {
  const btn = document.getElementById("btn-test-leverage");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Testing...';
  try {
    // Save config first so the leverage value is up to date
    await saveExecutionConfig();
    const res = await api("/api/execution/test-leverage", { method: "POST" });
    if (res.error) {
      toast("❌ Leverage test failed: " + res.error, "error");
    } else if (res.status === "ok") {
      toast(`✅ Leverage ${res.leverage}x set successfully on Bybit!`, "success");
    } else {
      const failed = (res.results || []).filter(r => !r.success);
      const msgs = failed.map(r => `${r.symbol}: ${r.error || r.retMsg}`).join(", ");
      toast(`⚠️ Partial fail: ${msgs}`, "error");
    }
  } catch (e) {
    toast("Cannot connect to local server", "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "🧪 Test Leverage";
  }
}

// ═══════════════════════════════════════════════════════════════════
// STRATEGY
// ═══════════════════════════════════════════════════════════════════

async function runStrategy() {
  const btn = document.getElementById("btn-run-strategy");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Running...';
  try {
    const res = await api("/api/strategy/run", { method: "POST" });
    if (res.error) {
      toast(res.error, "error");
      btn.disabled = false;
      btn.innerHTML = "▶ Run Strategy";
      return;
    }
    toast("Strategy pipeline started", "success");
    startStrategyPolling();
  } catch (e) {
    toast("Cannot connect to local server", "error");
    btn.disabled = false;
    btn.innerHTML = "▶ Run Strategy";
  }
}

function startStrategyPolling() {
  if (strategyPolling) clearInterval(strategyPolling);
  strategyPolling = setInterval(async () => {
    try {
      const s = await api("/api/strategy/status");
      const term = document.getElementById("strategy-terminal");
      term.innerHTML = s.output.map(colorLine).join("\n");
      term.scrollTop = term.scrollHeight;
      if (!s.running) {
        clearInterval(strategyPolling);
        strategyPolling = null;
        const btn = document.getElementById("btn-run-strategy");
        btn.disabled = false;
        btn.innerHTML = "▶ Run Strategy";
        toast("Strategy pipeline finished ✓ Auto-syncing...", "success");
        // Auto-sync: reload pairs, backtest, and auto-fill top pair
        await syncAfterStrategy();
      }
    } catch (e) {
      /* offline */
    }
  }, 1500);
}

// ═══════════════════════════════════════════════════════════════════
// PAIRS TABLE
// ═══════════════════════════════════════════════════════════════════

// Auto-sync after strategy: load pairs + backtest + fill top pair into exec config
async function syncAfterStrategy() {
  await loadPairs();
  await loadBacktest();
  // Auto-fill top pair into execution config
  const displayedPairs = getSortedPairs();
  if (displayedPairs.length > 0) {
    const top = displayedPairs[0];
    const sym1 = top.sym_1;
    const sym2 = top.sym_2;
    document.getElementById("e-ticker1").value = sym1;
    document.getElementById("e-ticker2").value = sym2;
    toast(
      `🎯 Top pair auto-filled: ${sym1} / ${sym2} (${top.zero_crossings} crossings)`,
      "success",
    );
    // Auto-save execution config so the file is updated too
    await saveExecutionConfig();
  }
}

let pairsData = [];
let pairsSort = { col: null, asc: false };
const _zscoreCache = {}; // key: "SYM1|SYM2", value: { z, color, text }

async function loadPairs() {
  try {
    const res = await api("/api/pairs");
    if (res.error) return toast(res.error, "error");
    pairsData = res.pairs || [];
    document.getElementById("pairs-count").textContent = pairsData.length;
    renderPairs();
  } catch (e) {
    /* offline */
  }
}

function renderPairs() {
  const tbody = document.getElementById("pairs-tbody");
  const sorted = getSortedPairs();
  tbody.innerHTML = sorted
    .slice(0, 100)
    .map((p) => {
      const s1 = escHtml(p.sym_1), s2 = escHtml(p.sym_2);
      const zid = `z-${s1}|${s2}`;
      const cached = _zscoreCache[`${s1}|${s2}`];
      const zClass = cached ? (cached.z >= 0 ? 'zscore-pos' : 'zscore-neg') : '';
      const zText  = cached ? cached.text : "—";
      const isSelected = _selectedPair &&
        _selectedPair.sym1 === p.sym_1 && _selectedPair.sym2 === p.sym_2;
      return `
    <tr class="pair-row${isSelected ? ' pair-row-selected' : ''}" id="row-${s1}|${s2}">
      <td>${s1}</td>
      <td>${s2}</td>
      <td>${p.p_value}</td>
      <td>${p.t_value}</td>
      <td>${p.c_value}</td>
      <td>${p.hedge_ratio}</td>
      <td><strong>${p.zero_crossings}</strong></td>
      <td id="${zid}" class="zscore-cell ${zClass}"
          onclick="openZscoreModal('${s1}','${s2}')">${zText}</td>
      <td><button class="btn-select-pair" onclick="selectPair('${s1}','${s2}')">Select</button></td>
    </tr>`;
    })
    .join("");
}

// ── Selected-pair realtime Z-Score ──────────────────────────────────────────
function _startSelectedZscore(sym1, sym2) {
  if (_zscoreInterval) clearInterval(_zscoreInterval);

  const updateCell = async () => {
    try {
      const res = await api(`/api/pairs/zscore?sym1=${sym1}&sym2=${sym2}`);
      if (res.zscore === undefined) return;
      const z = Number(res.zscore);
      const colorCls = z >= 0 ? 'zscore-pos' : 'zscore-neg';
      const text  = z.toFixed(3);

      // Store in cache so renderPairs can bake it in on next re-render
      _zscoreCache[`${sym1}|${sym2}`] = { z, text };

      // Update the cell directly (fast path, no full re-render)
      const cell = document.getElementById(`z-${sym1}|${sym2}`);
      if (cell) {
        cell.textContent = text;
        cell.classList.remove('zscore-pos', 'zscore-neg');
        cell.classList.add(colorCls);
      }

      // If history modal is open, append new point and update live badge
      if (_zscoreHistoryOpen) {
        const now = new Date();
        const label = now.getHours().toString().padStart(2,'0') + ':' +
                      now.getMinutes().toString().padStart(2,'0');
        const entry = { t: Date.now(), z, label };
        const lastT = _zscoreHistory.length
          ? _zscoreHistory[_zscoreHistory.length - 1].t : 0;
        if (entry.t - lastT > 1000) {
          _zscoreHistory.push(entry);
          appendHistoryEntry(entry);
        }
        const liveVal = document.getElementById("zscore-live-val");
        if (liveVal) {
          liveVal.textContent = z.toFixed(4);
          liveVal.style.color = color;
        }
      }
    } catch (_) { /* offline */ }
  };

  updateCell();
  _zscoreInterval = setInterval(updateCell, 2000);
}

function selectPair(sym1, sym2) {
  // Update form fields
  document.getElementById("e-ticker1").value = sym1;
  document.getElementById("e-ticker2").value = sym2;
  toast(`Selected: ${sym1} / ${sym2}`, "success");
  document.getElementById("exec-config-card").scrollIntoView({ behavior: "smooth" });

  // Remove previous highlight
  if (_selectedPair) {
    const old = document.getElementById(`row-${_selectedPair.sym1}|${_selectedPair.sym2}`);
    if (old) old.classList.remove("pair-row-selected");
    const oldCell = document.getElementById(`z-${_selectedPair.sym1}|${_selectedPair.sym2}`);
    if (oldCell) { oldCell.textContent = "—"; oldCell.style.color = ""; }
  }

  _selectedPair = { sym1, sym2 };

  // Highlight new row
  const row = document.getElementById(`row-${sym1}|${sym2}`);
  if (row) row.classList.add("pair-row-selected");

  // Start realtime z-score for this pair
  _startSelectedZscore(sym1, sym2);

  // Auto-save execution config (this will also load the chart)
  saveExecutionConfig();
}

// ── Z-Score History Modal ───────────────────────────────────────────────────
async function openZscoreModal(sym1, sym2) {
  // Ensure this pair is selected & realtime
  if (!_selectedPair || _selectedPair.sym1 !== sym1 || _selectedPair.sym2 !== sym2) {
    selectPair(sym1, sym2);
  }

  const modal   = document.getElementById("zscore-modal");
  const list    = document.getElementById("zscore-history-list");
  const titleEl = document.getElementById("zscore-modal-title");
  const subEl   = document.getElementById("zscore-modal-sub");

  titleEl.textContent = `${sym1} / ${sym2}`;
  subEl.textContent   = "Last 24 hours – Z-Score History";
  list.innerHTML      = '<div class="zscore-loading">Loading…</div>';
  modal.style.display = "flex";
  _zscoreHistoryOpen  = true;
  _histScrolledUp     = false;

  // Smart-scroll tracking
  list.onscroll = () => {
    const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 30;
    _histScrolledUp = !atBottom;
  };

  try {
    const res = await api(`/api/pairs/zscore-history?sym1=${sym1}&sym2=${sym2}`);
    if (res.error) { list.innerHTML = `<div class="zscore-loading zscore-err">${res.error}</div>`; return; }
    _zscoreHistory = res.history || [];
    list.innerHTML = "";
    _zscoreHistory.forEach(entry => appendHistoryEntry(entry, false));
    // Scroll to bottom on initial load
    list.scrollTop = list.scrollHeight;
  } catch (e) {
    list.innerHTML = '<div class="zscore-loading zscore-err">Failed to load history</div>';
  }
}

function appendHistoryEntry(entry, autoScroll = true) {
  const list = document.getElementById("zscore-history-list");
  if (!list) return;
  const z = Number(entry.z);
  const color = z >= 0 ? "var(--success)" : "var(--danger)";
  const bar = Math.min(Math.abs(z) / 3, 1); // normalize bar width to |z|=3
  const row = document.createElement("div");
  row.className = "zhist-row";
  row.innerHTML = `
    <span class="zhist-time">${entry.label}</span>
    <span class="zhist-bar-wrap">
      <span class="zhist-bar" style="width:${(bar*100).toFixed(1)}%;background:${color}"></span>
    </span>
    <span class="zhist-val" style="color:${color}">${z.toFixed(4)}</span>`;
  list.appendChild(row);

  if (autoScroll && !_histScrolledUp) {
    list.scrollTop = list.scrollHeight;
  }
}

function closeZscoreModal(event, force = false) {
  if (!force && event && event.target !== document.getElementById("zscore-modal")) return;
  document.getElementById("zscore-modal").style.display = "none";
  _zscoreHistoryOpen = false;
}

function getSortedPairs() {
  const sorted = [...pairsData];
  if (!pairsSort.col) return sorted;

  sorted.sort((a, b) => {
    let va = a[pairsSort.col],
      vb = b[pairsSort.col];
    const na = parseFloat(va),
      nb = parseFloat(vb);
    if (!isNaN(na) && !isNaN(nb)) {
      va = na;
      vb = nb;
    }
    if (va < vb) return pairsSort.asc ? -1 : 1;
    if (va > vb) return pairsSort.asc ? 1 : -1;
    return 0;
  });

  return sorted;
}

function sortPairs(col) {
  if (pairsSort.col === col) pairsSort.asc = !pairsSort.asc;
  else {
    pairsSort.col = col;
    pairsSort.asc = false;
  }
  renderPairs();
}


function setChartTimeframe(tf) {
  _chartTimeframe = tf;
  // Update active button style
  document.querySelectorAll(".chart-tf-btn").forEach((b) =>
    b.classList.toggle("active", parseInt(b.dataset.tf) === tf)
  );
  // Re-fetch chart with new timeframe
  refreshBacktestChart();
}

function setChartDuration(hours) {
  _chartDuration = hours;
  document.querySelectorAll(".chart-dur-btn").forEach((b) =>
    b.classList.toggle("active", parseInt(b.dataset.dur) === hours)
  );
  refreshBacktestChart();
}

async function fetchDynamicBacktest(sym1, sym2) {
  toast(`Loading live chart for ${sym1} / ${sym2}...`, "info");
  try {
    // Try live endpoint first (fetches fresh klines from Bybit) with selected timeframe + duration
    let res = await api(`/api/backtest/pair/live?sym1=${sym1}&sym2=${sym2}&timeframe=${_chartTimeframe}&duration=${_chartDuration}`);
    // Fallback to cached endpoint if live fails
    if (res.error) {
      res = await api(`/api/backtest/pair?sym1=${sym1}&sym2=${sym2}`);
    }
    if (res.error) return toast(res.error, "error");
    if (!res.data || res.data.length === 0) return;
    const data = res.data;
    const cols = res.columns.filter(
      (c) => c !== "" && !c.toLowerCase().includes("unnamed"),
    );
    renderBacktestChart(data, cols);
    toast(`Chart updated to now for ${sym1} / ${sym2}`, "success");
  } catch (e) {
    console.error(e);
    toast("Error loading pair data for chart: " + e.message, "error");
  }
}

// ═══════════════════════════════════════════════════════════════════
// BACKTEST CHART
// ═══════════════════════════════════════════════════════════════════

let priceChart = null;

async function loadBacktest() {
  try {
    const res = await api("/api/backtest");
    if (res.error || !res.data || res.data.length === 0) return;
    const data = res.data;
    const cols = res.columns.filter(
      (c) => c !== "" && !c.toLowerCase().includes("unnamed"),
    );
    renderBacktestChart(data, cols);
  } catch (e) {
    /* offline */
  }
}

function refreshBacktestChart() {
  const sym1 = document.getElementById("e-ticker1").value.trim();
  const sym2 = document.getElementById("e-ticker2").value.trim();
  if (sym1 && sym2) {
    fetchDynamicBacktest(sym1, sym2);
  } else {
    loadBacktest();
  }
}


function renderBacktestChart(data, cols) {
  // Use timestamps from API if available, fall back to index
  const timeLabels = data.map((r) => r["Time"] || "");
  const labels = timeLabels.some(t => t) ? timeLabels : data.map((_, i) => i);
  const symCols = cols.filter(
    (c) => c !== "Spread" && c !== "ZScore" && c !== "Date" && c !== "Time",
  );

  // Parse Backtest Chart data
  const spreadData = data.map((r) => parseFloat(r["Spread"]) || 0);
  const zscoreData = data.map((r) => parseFloat(r["ZScore"]) || null);

  // --- Price Chart Logic ---
  if (symCols.length >= 2) {
    const sym1 = symCols[0];
    const sym2 = symCols[1];
    const p1 = data.map((r) => parseFloat(r[sym1]) || 0);
    const p2 = data.map((r) => parseFloat(r[sym2]) || 0);

    // Save for PnL estimation
    _lastChartData = { sym1, sym2, p1, p2, spreadData };

    // Normalize prices to z-scores for visual comparison (so they start at same scale)
    const mean1 = p1.reduce((a, b) => a + b, 0) / p1.length;
    const std1 = Math.sqrt(
      p1.reduce((sq, n) => sq + Math.pow(n - mean1, 2), 0) / (p1.length - 1),
    );
    const norm1 = p1.map((v) => (std1 === 0 ? 0 : (v - mean1) / std1));

    const mean2 = p2.reduce((a, b) => a + b, 0) / p2.length;
    const std2 = Math.sqrt(
      p2.reduce((sq, n) => sq + Math.pow(n - mean2, 2), 0) / (p2.length - 1),
    );
    const norm2 = p2.map((v) => (std2 === 0 ? 0 : (v - mean2) / std2));

    if (priceChart) priceChart.destroy();
    const pctx = document.getElementById("price-canvas").getContext("2d");
    priceChart = new Chart(pctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: sym1 + " (Normalized)",
            data: norm1,
            borderColor: "#f59e0b",
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.1,
            fill: "-1",
            backgroundColor: "rgba(245, 158, 11, 0.1)",
          },
          {
            label: sym2 + " (Normalized)",
            data: norm2,
            borderColor: "#10b981",
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.1,
          },
          {
            label: "Mean (0)",
            data: data.map(() => 0),
            borderColor: "#ffffff",
            borderWidth: 2,
            pointRadius: 0,
            borderDash: [4, 4],
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            labels: { color: "#94a3b8", font: { family: "Outfit", size: 12 } },
          },
          title: {
            display: true,
            text: "Normalized Prices",
            color: "#f8fafc",
            font: { family: "Outfit", size: 14 },
          },
        },
        scales: {
          x: {
            display: true,
            ticks: {
              color: "#64748b",
              font: { family: "JetBrains Mono", size: 9 },
              maxTicksLimit: 8,
              maxRotation: 0,
            },
            grid: { display: false },
          },
          y: {
            grid: { color: "rgba(255,255,255,0.05)" },
            ticks: {
              color: "#64748b",
              font: { family: "JetBrains Mono", size: 10 },
            },
          },
        },
      },
    });
  }
  // --- End Price Chart ---

  // Read current threshold values from the Execution Config form
  const entryThresh = parseFloat(document.getElementById("e-trigger").value) || 1.1;
  const isCustom = document.getElementById("e-custom-thresholds").checked;
  const exitThresh = isCustom ? (parseFloat(document.getElementById("e-exit-threshold").value) || 0) : 0;

  // Build z-score chart datasets
  const zscoreDatasets = [
    {
      label: "Z-Score",
      data: zscoreData,
      borderColor: "#06b6d4",
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.1,
    },
    {
      label: "Mean (0)",
      data: data.map(() => 0),
      borderColor: "rgba(255,255,255,0.3)",
      borderWidth: 1,
      pointRadius: 0,
      borderDash: [5, 5],
    },
    {
      label: `Entry (+${entryThresh})`,
      data: data.map(() => entryThresh),
      borderColor: "rgba(239,68,68,0.8)",
      borderWidth: 1.5,
      pointRadius: 0,
      borderDash: [6, 3],
    },
    {
      label: `Entry (-${entryThresh})`,
      data: data.map(() => -entryThresh),
      borderColor: "rgba(239,68,68,0.8)",
      borderWidth: 1.5,
      pointRadius: 0,
      borderDash: [6, 3],
    },
    {
      label: `Exit (+${exitThresh})`,
      data: data.map(() => exitThresh),
      borderColor: "rgba(16,185,129,0.8)",
      borderWidth: 1.5,
      pointRadius: 0,
      borderDash: [3, 4],
    },
    {
      label: `Exit (-${exitThresh})`,
      data: data.map(() => -exitThresh),
      borderColor: "rgba(16,185,129,0.8)",
      borderWidth: 1.5,
      pointRadius: 0,
      borderDash: [3, 4],
    },
  ];

  if (backtestChart) backtestChart.destroy();
  const ctx = document.getElementById("backtest-canvas").getContext("2d");
  backtestChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: zscoreDatasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          labels: { color: "#94a3b8", font: { family: "Inter", size: 11 } },
        },
        title: {
          display: true,
          text: "Z-Score",
          color: "#f8fafc",
          font: { family: "Outfit", size: 14 },
        },
        tooltip: {
          callbacks: {
            title: function(items) {
              if (!items.length) return '';
              const idx = items[0].dataIndex;
              return timeLabels[idx] || `Point ${idx}`;
            }
          }
        },
      },
      scales: {
        x: {
          display: true,
          ticks: {
            color: "#64748b",
            font: { family: "JetBrains Mono", size: 9 },
            maxTicksLimit: 8,
            maxRotation: 0,
          },
          grid: { display: false },
        },
        y: {
          grid: { color: "rgba(6,182,212,0.08)" },
          ticks: {
            color: "#06b6d4",
            font: { family: "JetBrains Mono", size: 10 },
          },
        },
      },
    },
  });

  if (symCols.length >= 2) {
    document.getElementById("metric-sym1").textContent = symCols[0];
    document.getElementById("metric-sym2").textContent = symCols[1];
  }
  document.getElementById("metric-datapoints").textContent = data.length;

  estimatePnL();
}

// ── P&L Estimation ──────────────────────────────────────────────────
function estimatePnL() {
  const panel = document.getElementById("pnl-estimate-panel");
  if (!panel) return;
  
  if (!_lastChartData) {
    panel.style.display = "none";
    return;
  }
  
  const capital = parseFloat(document.getElementById("e-capital").value) || 0;
  const entryZ = parseFloat(document.getElementById("e-trigger").value) || 0;
  const isCustom = document.getElementById("e-custom-thresholds").checked;
  const exitZ = isCustom ? (parseFloat(document.getElementById("e-exit-threshold").value) || 0) : 0;
  const takerFeePct = parseFloat(document.getElementById("e-taker-fee").value) || 0.055;
  const leverage = isCustom ? (parseFloat(document.getElementById("e-leverage").value) || 1) : 1;

  if (capital <= 0 || entryZ <= 0) {
    panel.style.display = "none";
    return;
  }

  const { p1, p2, spreadData, sym1, sym2 } = _lastChartData;
  if (!p1 || !p2 || !spreadData || p1.length === 0 || spreadData.length === 0) return;

  // Calculate spread standard deviation
  const meanSpread = spreadData.reduce((a, b) => a + b, 0) / spreadData.length;
  const varSpread = spreadData.reduce((sq, n) => sq + Math.pow(n - meanSpread, 2), 0) / (spreadData.length - 1);
  const stdSpread = Math.sqrt(varSpread);

  // Average prices for both legs
  const avgP1 = p1.reduce((a, b) => a + b, 0) / p1.length;
  const avgP2 = p2.reduce((a, b) => a + b, 0) / p2.length;

  // Stat-arb P&L estimation:
  // - Capital is split 50/50 across two legs
  // - Each leg's notional = (capital / 2) * leverage
  // - When z-score moves from entryZ to exitZ, spread moves by zDelta * stdSpread
  // - P&L on leg1 ≈ notional_leg1 * (spread_dollar_move / avgP1)
  // - This is an approximation; real P&L depends on actual price movements
  // - Total fees = 4 legs (open+close × 2 symbols) × notional × fee%
  
  const zDelta = Math.abs(entryZ - exitZ);
  const notionalPerLeg = (capital / 2) * leverage;
  const spreadDollarMove = zDelta * stdSpread;
  
  // Gross P&L: spread movement relative to reference price × notional
  const grossPnL = notionalPerLeg * (spreadDollarMove / avgP1);
  
  // Fees: 4 order fills (open leg1, open leg2, close leg1, close leg2)
  const totalFees = 4 * notionalPerLeg * (takerFeePct / 100);
  const netPnL = grossPnL - totalFees;
  const returnPct = (netPnL / capital) * 100;

  document.getElementById("pnl-est-pair").textContent = `${sym1} / ${sym2}`;
  document.getElementById("pnl-est-gross").textContent = `$${grossPnL.toFixed(3)}`;
  document.getElementById("pnl-est-fees").textContent = `$${totalFees.toFixed(3)}`;
  
  const netEl = document.getElementById("pnl-est-net");
  const pctEl = document.getElementById("pnl-est-pct");
  
  netEl.textContent = `$${netPnL.toFixed(3)}`;
  const leverageLabel = leverage > 1 ? ` (${leverage}x)` : '';
  pctEl.textContent = `${returnPct > 0 ? '+' : ''}${returnPct.toFixed(2)}%${leverageLabel}`;
  
  const color = netPnL >= 0 ? "var(--green)" : "var(--red)";
  netEl.style.color = color;
  pctEl.style.color = color;
  
  if (netPnL < 0) {
    panel.style.background = "linear-gradient(135deg, rgba(239,68,68,.06) 0%, rgba(220,38,38,.04) 100%)";
    panel.style.borderColor = "rgba(239,68,68,.2)";
  } else {
    panel.style.background = "linear-gradient(135deg,rgba(34,211,238,.06) 0%,rgba(16,185,129,.04) 100%)";
    panel.style.borderColor = "rgba(34,211,238,.15)";
  }
  
  panel.style.display = "block";
}

// Attach auto-save listeners to config inputs on blur/change
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("#strategy-config-card input").forEach(el => {
    el.addEventListener(el.type === 'checkbox' || el.type === 'radio' ? 'change' : 'blur', saveStrategyConfig);
  });
  document.querySelectorAll("#exec-config-card input").forEach(el => {
    el.addEventListener(el.type === 'checkbox' || el.type === 'radio' ? 'change' : 'blur', saveExecutionConfig);
  });
});

// ═══════════════════════════════════════════════════════════════════
// EXECUTION BOT
// ═══════════════════════════════════════════════════════════════════

async function startExecution() {
  try {
    // Always save config to disk BEFORE spawning the bot subprocess,
    // so the new process reads the latest values (e.g. zscore_stop_loss).
    await saveExecutionConfig();
    const res = await api("/api/execution/start", { method: "POST" });
    if (res.error) return toast(res.error, "error");
    toast("Execution bot started", "success");
    startExecutionPolling();
    updateBotUI(true);
  } catch (e) {
    toast("Cannot connect to local server", "error");
  }
}

async function stopExecution() {
  try {
    await api("/api/execution/stop", { method: "POST" });
    toast("Bot stop signal sent", "success");
    updateBotUI(false);
  } catch (e) {
    toast("Cannot connect to local server", "error");
  }
}

async function resetBot() {
  const btn = document.getElementById("btn-reset-bot");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Resetting...';
  const term = document.getElementById("execution-terminal");
  term.innerHTML = colorLine("🔄 Resetting bot — cancelling orders and closing positions...");
  term.scrollTop = term.scrollHeight;
  try {
    const res = await api("/api/execution/reset", { method: "POST" });
    if (res.error) {
      toast("Reset error: " + res.error, "error");
      term.innerHTML += "\n" + colorLine("❌ " + res.error);
    } else {
      // Show output lines in terminal
      if (res.output && res.output.length) {
        term.innerHTML = res.output.map(colorLine).join("\n");
      }
      if (res.clean) {
        toast("✅ Account is CLEAN — ready for new pair", "success");
        term.innerHTML += "\n" + colorLine("✅ Reset complete — account is CLEAN.");
      } else {
        toast("⚠️ Reset finished with warnings. Check the terminal.", "error");
      }
    }
    term.scrollTop = term.scrollHeight;
  } catch (e) {
    toast("Cannot connect to local server", "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "🔄 Reset";
  }
}

function updateBotUI(running) {
  const dot = document.getElementById("bot-status-dot");
  const label = document.getElementById("bot-status-label");
  const startBtn = document.getElementById("btn-start-bot");
  const stopBtn = document.getElementById("btn-stop-bot");
  if (running) {
    dot.className = "status-dot live";
    label.textContent = "RUNNING";
    label.style.color = "var(--success)";
    startBtn.disabled = true;
    stopBtn.disabled = false;
  } else {
    dot.className = "status-dot";
    label.textContent = "STOPPED";
    label.style.color = "var(--text-muted)";
    startBtn.disabled = false;
    stopBtn.disabled = true;
  }
}

function startExecutionPolling() {
  if (executionPolling) clearInterval(executionPolling);

  // Smart-scroll tracking: only auto-scroll if user is near the bottom
  const term = document.getElementById("execution-terminal");
  _execScrolledUp = false;
  term.onscroll = () => {
    const atBottom = term.scrollHeight - term.scrollTop - term.clientHeight < 40;
    _execScrolledUp = !atBottom;
  };

  executionPolling = setInterval(async () => {
    try {
      const s = await api("/api/execution/status");
      const term = document.getElementById("execution-terminal");
      term.innerHTML = s.output.map(colorLine).join("\n");
      // Only auto-scroll if user hasn't scrolled up
      if (!_execScrolledUp) {
        term.scrollTop = term.scrollHeight;
      }
      if (s.status && s.status.message)
        document.getElementById("bot-message").textContent = s.status.message;
      if (!s.running) {
        updateBotUI(false);
        clearInterval(executionPolling);
        executionPolling = null;
      }
    } catch (e) {
      /* offline */
    }
  }, 2000);
}

async function checkBotStatus() {
  try {
    const s = await api("/api/execution/status");
    updateBotUI(s.running);
    if (s.running) startExecutionPolling();
    if (s.status && s.status.message)
      document.getElementById("bot-message").textContent = s.status.message;
  } catch (e) {
    /* offline */
  }
}

// ═══════════════════════════════════════════════════════════════════
// PERFORMANCE (P&L)
// ═══════════════════════════════════════════════════════════════════

// Duration in ms for each relative preset
const PERF_PERIODS = {
  "1D":  1   * 24 * 60 * 60 * 1000,
  "7D":  7   * 24 * 60 * 60 * 1000,
  "30D": 30  * 24 * 60 * 60 * 1000,
  "6M":  180 * 24 * 60 * 60 * 1000,
  "1Y":  365 * 24 * 60 * 60 * 1000,
};

let currentPerfStartMs = null; // null → backend uses PERF_DEFAULT_START_MS
let currentPerfEndMs   = null; // null → no upper bound (preset buttons)
let _activePerfPeriod  = "ALL";

// ── Helpers ────────────────────────────────────────────────────────
function _fmtUsdt(val) {
  const v = Number(val);
  if (isNaN(v)) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}$${v.toFixed(4)}`;
}

function _pnlColor(val) {
  const v = Number(val);
  if (isNaN(v) || v === 0) return "";
  return v >= 0 ? "pd-positive" : "pd-negative";
}

// ── Data loader ────────────────────────────────────────────────────
async function loadPerformance(startMs = null, endMs = null) {
  // ── Reset display so stale values never linger ─────────────────────
  const pctEl  = document.getElementById("perf-pct");
  if (pctEl) { pctEl.textContent = "—"; pctEl.className = "perf-value"; }

  try {
    const params = [];
    if (startMs != null) params.push(`startMs=${startMs}`);
    if (endMs   != null) params.push(`endMs=${endMs}`);
    const url = "/api/performance" + (params.length ? "?" + params.join("&") : "");
    const res = await api(url);
    if (res.error) return;   // leaves "—" which is intentional

    const modeMap = { demo: "Demo", live: "Live", test: "Test" };
    document.getElementById("perf-mode").textContent =
      modeMap[res.mode] || res.mode;

    // ── Header: period-specific % ──────────────────────────────────
    const pct = Number(res.period_pnl_pct) || 0;
    pctEl.textContent = (pct >= 0 ? "+" : "") + pct.toFixed(3) + "%";
    pctEl.className = "perf-value " + (pct >= 0 ? "perf-positive" : "perf-negative");

    // ── Detail popup data ──────────────────────────────────────────
    // Account section
    const scEl = document.getElementById("pd-starting-capital");
    if (scEl) scEl.textContent = "$" + (Number(res.starting_capital) || 0).toFixed(4);
    const wbEl = document.getElementById("pd-wallet-balance");
    if (wbEl) wbEl.textContent = "$" + (Number(res.wallet_balance) || 0).toFixed(4);
    const eqEl = document.getElementById("pd-equity");
    if (eqEl) eqEl.textContent = "$" + (Number(res.current_equity) || 0).toFixed(4);

    // PnL section (always all-time from wallet)
    const allPct = Number(res.pnl_pct) || 0;
    const tpEl = document.getElementById("pd-total-pnl");
    if (tpEl) {
      tpEl.textContent = _fmtUsdt(res.total_pnl) + ` (${allPct >= 0 ? "+" : ""}${allPct.toFixed(3)}%)`;
      tpEl.className = "perf-detail-val perf-detail-big " + _pnlColor(res.total_pnl);
    }
    const rlEl = document.getElementById("pd-realized");
    if (rlEl) {
      rlEl.textContent = _fmtUsdt(res.cum_realised_pnl);
      rlEl.className = "perf-detail-val " + _pnlColor(res.cum_realised_pnl);
    }
    const ulEl = document.getElementById("pd-unrealized");
    if (ulEl) {
      ulEl.textContent = _fmtUsdt(res.unrealised_pnl);
      ulEl.className = "perf-detail-val " + _pnlColor(res.unrealised_pnl);
    }

    // Period breakdown section
    const plEl = document.getElementById("pd-period-label");
    if (plEl) plEl.textContent = _activePerfPeriod;

    const trEl = document.getElementById("pd-trade-pnl");
    if (trEl) {
      trEl.textContent = _fmtUsdt(res.trade_pnl);
      trEl.className = "perf-detail-val " + _pnlColor(res.trade_pnl);
    }
    const fdEl = document.getElementById("pd-funding");
    if (fdEl) {
      fdEl.textContent = _fmtUsdt(res.funding_fees);
      fdEl.className = "perf-detail-val " + _pnlColor(res.funding_fees);
    }
    const feEl = document.getElementById("pd-fees");
    if (feEl) {
      feEl.textContent = _fmtUsdt(res.trading_fees);
      feEl.className = "perf-detail-val pd-warning";  // fees always amber
    }
  } catch (e) { console.error("loadPerformance error:", e); /* "—" already shown */ }
}

// ── Detail popup toggle ───────────────────────────────────────────
function togglePerfDetail() {
  const popup = document.getElementById("perf-detail-popup");
  if (!popup) return;
  popup.classList.toggle("open");
}

// Close detail popup when clicking outside
document.addEventListener("click", (e) => {
  const popup = document.getElementById("perf-detail-popup");
  const headerPerf = document.getElementById("header-perf");
  if (popup && popup.classList.contains("open") &&
      headerPerf && !headerPerf.contains(e.target)) {
    popup.classList.remove("open");
  }
});

// ── Period preset buttons ─────────────────────────────────────────
function setPerfPeriod(period) {
  _activePerfPeriod = period;
  document.querySelectorAll(".perf-period-btn").forEach((b) =>
    b.classList.remove("active")
  );
  const btn = document.querySelector(`.perf-period-btn[data-period="${period}"]`);
  if (btn) btn.classList.add("active");

  // Reset calendar trigger label
  document.getElementById("perf-cal-label").textContent = "Date";
  document.getElementById("perf-cal-trigger").classList.remove("active");

  if (period === "ALL") {
    currentPerfStartMs = null;
    currentPerfEndMs   = null;
    loadPerformance(null, null);
  } else if (period === "YTD") {
    const yr = new Date().getFullYear();
    currentPerfStartMs = new Date(`${yr}-01-01T00:00:00+07:00`).getTime();
    currentPerfEndMs   = null;
    loadPerformance(currentPerfStartMs, null);
  } else {
    currentPerfStartMs = Date.now() - PERF_PERIODS[period];
    currentPerfEndMs   = null;
    loadPerformance(currentPerfStartMs, null);
  }
}

// ── Custom Calendar Picker ────────────────────────────────────────
const CAL_MONTHS = [
  "January","February","March","April","May","June",
  "July","August","September","October","November","December"
];
let dpYear  = new Date().getFullYear();
let dpMonth = new Date().getMonth();
let dpSelected = null; // { year, month (0-based), day }

function toggleCal(e) {
  e.stopPropagation();
  const panel = document.getElementById("perf-cal-panel");
  const open  = panel.classList.toggle("open");
  if (open) renderCalendar();
}

function dpNav(e, dir) {
  e.stopPropagation();
  dpMonth += dir;
  if (dpMonth < 0)  { dpMonth = 11; dpYear--; }
  if (dpMonth > 11) { dpMonth = 0;  dpYear++; }
  renderCalendar();
}

function renderCalendar() {
  document.getElementById("perf-cal-title").textContent =
    `${CAL_MONTHS[dpMonth]} ${dpYear}`;

  const grid = document.getElementById("perf-cal-days");
  const today = new Date();
  const daysInMonth = new Date(dpYear, dpMonth + 1, 0).getDate();
  // Monday-first offset: getDay() returns 0=Sun,1=Mon...6=Sat
  let firstDow = new Date(dpYear, dpMonth, 1).getDay();
  const offset = (firstDow === 0) ? 6 : firstDow - 1; // blanks before day 1

  let html = "";
  for (let i = 0; i < offset; i++) html += '<span class="perf-cal-blank"></span>';
  for (let d = 1; d <= daysInMonth; d++) {
    const isToday = (d === today.getDate() && dpMonth === today.getMonth() && dpYear === today.getFullYear());
    const isSel   = dpSelected && (d === dpSelected.day && dpMonth === dpSelected.month && dpYear === dpSelected.year);
    const cls = ["perf-cal-day", isToday ? "today" : "", isSel ? "selected" : ""].filter(Boolean).join(" ");
    html += `<button class="${cls}" onclick="selectCalDate(${dpYear},${dpMonth+1},${d})">${d}</button>`;
  }
  grid.innerHTML = html;
}

function selectCalDate(year, month, day) {
  dpSelected = { year, month: month - 1, day };
  renderCalendar();

  // Update trigger label (DD/MM)
  const dd = String(day).padStart(2, "0");
  const mm = String(month).padStart(2, "0");
  const dateLabel = `${dd}/${mm}/${year}`;
  document.getElementById("perf-cal-label").textContent = dateLabel;
  _activePerfPeriod = dateLabel;

  // Close panel
  document.getElementById("perf-cal-panel").classList.remove("open");

  // Deactivate preset buttons, activate calendar trigger
  document.querySelectorAll(".perf-period-btn").forEach((b) => b.classList.remove("active"));
  document.getElementById("perf-cal-trigger").classList.add("active");

  // Midnight ICT = start of selected day
  const iso    = `${year}-${mm}-${dd}T00:00:00+07:00`;
  const startMs = new Date(iso).getTime();
  // End of selected day (23:59:59.999 ICT)
  const endMs   = new Date(`${year}-${mm}-${dd}T23:59:59.999+07:00`).getTime();
  currentPerfStartMs = startMs;
  currentPerfEndMs   = endMs;
  loadPerformance(startMs, endMs);
}

// Close calendar when clicking outside
document.addEventListener("click", (e) => {
  const cal = document.getElementById("perf-cal");
  if (cal && !cal.contains(e.target)) {
    document.getElementById("perf-cal-panel")?.classList.remove("open");
  }
});

// ═══════════════════════════════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════════════════════════════

let perfPolling = null;

async function initDashboard() {
  updateConnectionUI(false);
  try {
    await api("/api/config/strategy"); // test connection
    connectionOk = true;
    updateConnectionUI(true);
  } catch (e) {
    connectionOk = false;
    updateConnectionUI(false);
    toast(
      "⚠ Cannot reach local server. Start it with: python dashboard/dashboard_server.py",
      "error",
    );
  }
  loadStrategyConfig();
  await loadExecutionConfig();
  loadPairs();
  refreshBacktestChart();
  checkBotStatus();
  loadPerformance(); // uses default PERF_DEFAULT_START_MS

  if (perfPolling) clearInterval(perfPolling);
  perfPolling = setInterval(() => loadPerformance(currentPerfStartMs, currentPerfEndMs), 60000);
}

document.addEventListener("DOMContentLoaded", () => {
  updateClock();
  document.getElementById("api-url-input").value = API;
  initDashboard();
});
