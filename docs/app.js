/* ═══════════════════════════════════════════════════════════════════
   STAT-ARB DASHBOARD – Frontend Logic (GitHub Pages Edition)
   Connects to your LOCAL Flask backend via configurable API URL.
   ═══════════════════════════════════════════════════════════════════ */

// ── API URL – connects to your local machine ──────────────────────
const DEFAULT_API = 'http://localhost:5000';
let API = localStorage.getItem('statarb_api_url') || DEFAULT_API;

// ── State ──────────────────────────────────────────────────────────
let strategyPolling = null;
let executionPolling = null;
let backtestChart = null;
let connectionOk = false;

// ── Helpers ────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  try {
    const res = await fetch(API + path, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (!connectionOk) { connectionOk = true; updateConnectionUI(true); }
    return res.json();
  } catch (e) {
    if (connectionOk) { connectionOk = false; updateConnectionUI(false); }
    throw e;
  }
}

function toast(msg, type = 'info') {
  const c = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  c.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 3500);
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function colorLine(text) {
  const t = escHtml(text);
  if (/error|exception|critical/i.test(t)) return `<span class="line-error">${t}</span>`;
  if (/warn/i.test(t)) return `<span class="line-warn">${t}</span>`;
  if (/info|▶|✓|found|saved|calculating|getting|plotting|setting/i.test(t)) return `<span class="line-info">${t}</span>`;
  return t;
}

// ── Connection UI ─────────────────────────────────────────────────
function updateConnectionUI(connected) {
  const dot = document.getElementById('conn-dot');
  const label = document.getElementById('conn-label');
  if (connected) {
    dot.className = 'status-dot live';
    label.textContent = 'CONNECTED';
    label.style.color = 'var(--green)';
  } else {
    dot.className = 'status-dot error';
    label.textContent = 'DISCONNECTED';
    label.style.color = 'var(--red)';
  }
}

function saveApiUrl() {
  const url = document.getElementById('api-url-input').value.replace(/\/+$/, '');
  API = url;
  localStorage.setItem('statarb_api_url', url);
  toast('API URL saved: ' + url, 'success');
  initDashboard();
}

// ── Clock ──────────────────────────────────────────────────────────
function updateClock() {
  const now = new Date();
  document.getElementById('header-time').textContent = now.toLocaleTimeString('en-GB', { hour12: false });
}
setInterval(updateClock, 1000);

// ═══════════════════════════════════════════════════════════════════
// CONFIG
// ═══════════════════════════════════════════════════════════════════

async function loadStrategyConfig() {
  try {
    const cfg = await api('/api/config/strategy');
    if (cfg.error) return toast(cfg.error, 'error');
    document.getElementById('s-mode').value = cfg.mode;
    document.getElementById('s-timeframe').value = cfg.timeframe;
    document.getElementById('s-kline').value = cfg.kline_limit;
    document.getElementById('s-zscore-win').value = cfg.z_score_window;
    document.getElementById('s-liquidity').value = cfg.min_turnover_24h || 2000000;
  } catch(e) { /* offline */ }
}

async function saveStrategyConfig() {
  const data = {
    mode: document.getElementById('s-mode').value,
    timeframe: parseInt(document.getElementById('s-timeframe').value),
    kline_limit: parseInt(document.getElementById('s-kline').value),
    z_score_window: parseInt(document.getElementById('s-zscore-win').value),
    min_turnover_24h: parseInt(document.getElementById('s-liquidity').value),
  };
  try {
    const res = await api('/api/config/strategy', { method: 'POST', body: data });
    if (res.error) return toast(res.error, 'error');
    toast('Strategy config saved ✓', 'success');
  } catch(e) { toast('Cannot connect to local server', 'error'); }
}

async function loadExecutionConfig() {
  try {
    const cfg = await api('/api/config/execution');
    if (cfg.error) return toast(cfg.error, 'error');
    document.getElementById('e-mode').value = cfg.mode;
    document.getElementById('e-ticker1').value = cfg.ticker_1;
    document.getElementById('e-ticker2').value = cfg.ticker_2;
    document.getElementById('e-capital').value = cfg.tradeable_capital_usdt;
    document.getElementById('e-trigger').value = cfg.signal_trigger_thresh;
    document.getElementById('e-stoploss').value = cfg.stop_loss_fail_safe;
    document.getElementById('e-zstop').value = cfg.zscore_stop_loss;
    document.getElementById('e-limit').checked = cfg.limit_order_basis;
    document.getElementById('e-timeframe').value = cfg.timeframe;
    document.getElementById('e-kline').value = cfg.kline_limit;
    document.getElementById('e-zscore-win').value = cfg.z_score_window;
  } catch(e) { /* offline */ }
}

async function saveExecutionConfig() {
  const data = {
    mode: document.getElementById('e-mode').value,
    ticker_1: document.getElementById('e-ticker1').value,
    ticker_2: document.getElementById('e-ticker2').value,
    tradeable_capital_usdt: parseFloat(document.getElementById('e-capital').value),
    signal_trigger_thresh: parseFloat(document.getElementById('e-trigger').value),
    stop_loss_fail_safe: parseFloat(document.getElementById('e-stoploss').value),
    zscore_stop_loss: parseFloat(document.getElementById('e-zstop').value),
    limit_order_basis: document.getElementById('e-limit').checked,
    timeframe: parseInt(document.getElementById('e-timeframe').value),
    kline_limit: parseInt(document.getElementById('e-kline').value),
    z_score_window: parseInt(document.getElementById('e-zscore-win').value),
  };
  try {
    const res = await api('/api/config/execution', { method: 'POST', body: data });
    if (res.error) return toast(res.error, 'error');
    toast('Execution config saved ✓', 'success');
  } catch(e) { toast('Cannot connect to local server', 'error'); }
}

// ═══════════════════════════════════════════════════════════════════
// STRATEGY
// ═══════════════════════════════════════════════════════════════════

async function runStrategy() {
  const btn = document.getElementById('btn-run-strategy');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Running...';
  try {
    const res = await api('/api/strategy/run', { method: 'POST' });
    if (res.error) { toast(res.error, 'error'); btn.disabled = false; btn.innerHTML = '▶ Run Strategy'; return; }
    toast('Strategy pipeline started', 'success');
    startStrategyPolling();
  } catch(e) { toast('Cannot connect to local server', 'error'); btn.disabled = false; btn.innerHTML = '▶ Run Strategy'; }
}

function startStrategyPolling() {
  if (strategyPolling) clearInterval(strategyPolling);
  strategyPolling = setInterval(async () => {
    try {
      const s = await api('/api/strategy/status');
      const term = document.getElementById('strategy-terminal');
      term.innerHTML = s.output.map(colorLine).join('\n');
      term.scrollTop = term.scrollHeight;
      if (!s.running) {
        clearInterval(strategyPolling); strategyPolling = null;
        const btn = document.getElementById('btn-run-strategy');
        btn.disabled = false; btn.innerHTML = '▶ Run Strategy';
        toast('Strategy pipeline finished ✓ Auto-syncing...', 'success');
        // Auto-sync: reload pairs, backtest, and auto-fill top pair
        await syncAfterStrategy();
      }
    } catch(e) { /* offline */ }
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
  if (pairsData.length > 0) {
    const top = pairsData[0]; // already sorted by zero_crossings desc from loadPairs
    const sym1 = top.sym_1;
    const sym2 = top.sym_2;
    document.getElementById('e-ticker1').value = sym1;
    document.getElementById('e-ticker2').value = sym2;
    toast(`🎯 Top pair auto-filled: ${sym1} / ${sym2} (${top.zero_crossings} crossings)`, 'success');
    // Auto-save execution config so the file is updated too
    await saveExecutionConfig();
  }
}

let pairsData = [];
let pairsSort = { col: 'zero_crossings', asc: false };

async function loadPairs() {
  try {
    const res = await api('/api/pairs');
    if (res.error) return toast(res.error, 'error');
    pairsData = res.pairs || [];
    document.getElementById('pairs-count').textContent = pairsData.length;
    renderPairs();
  } catch(e) { /* offline */ }
}

function renderPairs() {
  const tbody = document.getElementById('pairs-tbody');
  let sorted = [...pairsData];
  sorted.sort((a, b) => {
    let va = a[pairsSort.col], vb = b[pairsSort.col];
    const na = parseFloat(va), nb = parseFloat(vb);
    if (!isNaN(na) && !isNaN(nb)) { va = na; vb = nb; }
    if (va < vb) return pairsSort.asc ? -1 : 1;
    if (va > vb) return pairsSort.asc ? 1 : -1;
    return 0;
  });
  tbody.innerHTML = sorted.slice(0, 100).map(p => `
    <tr>
      <td>${escHtml(p.sym_1)}</td>
      <td>${escHtml(p.sym_2)}</td>
      <td>${p.p_value}</td>
      <td>${p.t_value}</td>
      <td>${p.c_value}</td>
      <td>${p.hedge_ratio}</td>
      <td><strong>${p.zero_crossings}</strong></td>
      <td><button class="btn-select-pair" onclick="selectPair('${escHtml(p.sym_1)}','${escHtml(p.sym_2)}')">Select</button></td>
    </tr>
  `).join('');
}

function sortPairs(col) {
  if (pairsSort.col === col) pairsSort.asc = !pairsSort.asc;
  else { pairsSort.col = col; pairsSort.asc = false; }
  renderPairs();
}

function selectPair(sym1, sym2) {
  document.getElementById('e-ticker1').value = sym1;
  document.getElementById('e-ticker2').value = sym2;
  toast(`Selected: ${sym1} / ${sym2}`, 'success');
  document.getElementById('exec-config-card').scrollIntoView({ behavior: 'smooth' });
}

// ═══════════════════════════════════════════════════════════════════
// BACKTEST CHART
// ═══════════════════════════════════════════════════════════════════

let priceChart = null;

async function loadBacktest() {
  try {
    const res = await api('/api/backtest');
    if (res.error || !res.data || res.data.length === 0) return;
    const data = res.data;
    const cols = res.columns.filter(c => c !== '');
    const labels = data.map((_, i) => i);
    const symCols = cols.filter(c => c !== 'Spread' && c !== 'ZScore' && c !== 'Date' && c !== 'Time');
    
    // Parse Backtest Chart data
    const spreadData = data.map(r => parseFloat(r['Spread']) || 0);
    const zscoreData = data.map(r => parseFloat(r['ZScore']) || null);

    // --- Price Chart Logic ---
    if (symCols.length >= 2) {
      const sym1 = symCols[0];
      const sym2 = symCols[1];
      const p1 = data.map(r => parseFloat(r[sym1]) || 0);
      const p2 = data.map(r => parseFloat(r[sym2]) || 0);

      // Normalize prices to z-scores for visual comparison (so they start at same scale)
      const mean1 = p1.reduce((a,b)=>a+b,0)/p1.length;
      const std1 = Math.sqrt(p1.reduce((sq, n)=>sq+Math.pow(n-mean1,2),0)/(p1.length-1));
      const norm1 = p1.map(v => std1 === 0 ? 0 : (v - mean1) / std1);

      const mean2 = p2.reduce((a,b)=>a+b,0)/p2.length;
      const std2 = Math.sqrt(p2.reduce((sq, n)=>sq+Math.pow(n-mean2,2),0)/(p2.length-1));
      const norm2 = p2.map(v => std2 === 0 ? 0 : (v - mean2) / std2);

      if (priceChart) priceChart.destroy();
      const pctx = document.getElementById('price-canvas').getContext('2d');
      priceChart = new Chart(pctx, {
        type: 'line',
        data: {
          labels,
          datasets: [
            { label: sym1 + ' (Normalized)', data: norm1, borderColor: '#f59e0b', borderWidth: 1.5, pointRadius: 0, tension: 0.1 },
            { label: sym2 + ' (Normalized)', data: norm2, borderColor: '#10b981', borderWidth: 1.5, pointRadius: 0, tension: 0.1 },
          ]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          plugins: { 
            legend: { labels: { color: '#94a3b8', font: { family: 'Outfit', size: 12 } } },
            title: { display: true, text: 'Normalized Prices (Spread Visualization)', color: '#f8fafc', font: {family: 'Outfit', size: 14} }
          },
          scales: {
            x: { display: false },
            y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#64748b', font: { family: 'JetBrains Mono', size: 10 } } }
          }
        }
      });
    }
    // --- End Price Chart ---

    if (backtestChart) backtestChart.destroy();
    const ctx = document.getElementById('backtest-canvas').getContext('2d');
    backtestChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'Spread', data: spreadData, borderColor: '#6366f1', backgroundColor: 'rgba(99,102,241,0.1)', borderWidth: 1.5, pointRadius: 0, fill: true, yAxisID: 'y' },
          { label: 'Z-Score', data: zscoreData, borderColor: '#06b6d4', borderWidth: 2, pointRadius: 0, yAxisID: 'y1', tension: 0.1 },
          { label: 'Mean (0)', data: data.map(() => 0), borderColor: 'rgba(255,255,255,0.3)', borderWidth: 1, pointRadius: 0, borderDash: [5, 5], yAxisID: 'y1' },
          { label: 'Upper Band (+2)', data: data.map(() => 2), borderColor: 'rgba(239,68,68,0.5)', borderWidth: 1, pointRadius: 0, borderDash: [3, 4], yAxisID: 'y1' },
          { label: 'Lower Band (-2)', data: data.map(() => -2), borderColor: 'rgba(16,185,129,0.5)', borderWidth: 1, pointRadius: 0, borderDash: [3, 4], yAxisID: 'y1' },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { labels: { color: '#94a3b8', font: { family: 'Inter', size: 11 } } } },
        scales: {
          x: { display: false },
          y: { position: 'left', grid: { color: 'rgba(99,102,241,0.08)' }, ticks: { color: '#64748b', font: { family: 'JetBrains Mono', size: 10 } } },
          y1: { position: 'right', grid: { drawOnChartArea: false }, ticks: { color: '#06b6d4', font: { family: 'JetBrains Mono', size: 10 } } },
        },
      },
    });

    const lastZ = zscoreData.filter(v => v !== null);
    if (lastZ.length > 0) {
      const z = lastZ[lastZ.length - 1];
      const el = document.getElementById('metric-zscore');
      el.textContent = z.toFixed(4);
      el.className = 'metric-value ' + (z >= 0 ? 'positive' : 'negative');
    }
    if (symCols.length >= 2) {
      document.getElementById('metric-sym1').textContent = symCols[0];
      document.getElementById('metric-sym2').textContent = symCols[1];
    }
    document.getElementById('metric-datapoints').textContent = data.length;
  } catch(e) { /* offline */ }
}

// ═══════════════════════════════════════════════════════════════════
// EXECUTION BOT
// ═══════════════════════════════════════════════════════════════════

async function startExecution() {
  try {
    const res = await api('/api/execution/start', { method: 'POST' });
    if (res.error) return toast(res.error, 'error');
    toast('Execution bot started', 'success');
    startExecutionPolling(); updateBotUI(true);
  } catch(e) { toast('Cannot connect to local server', 'error'); }
}

async function stopExecution() {
  try {
    await api('/api/execution/stop', { method: 'POST' });
    toast('Bot stop signal sent', 'success'); updateBotUI(false);
  } catch(e) { toast('Cannot connect to local server', 'error'); }
}

function updateBotUI(running) {
  const dot = document.getElementById('bot-status-dot');
  const label = document.getElementById('bot-status-label');
  const startBtn = document.getElementById('btn-start-bot');
  const stopBtn = document.getElementById('btn-stop-bot');
  if (running) {
    dot.className = 'status-dot live'; label.textContent = 'RUNNING'; label.style.color = 'var(--green)';
    startBtn.disabled = true; stopBtn.disabled = false;
  } else {
    dot.className = 'status-dot'; label.textContent = 'STOPPED'; label.style.color = 'var(--text-muted)';
    startBtn.disabled = false; stopBtn.disabled = true;
  }
}

function startExecutionPolling() {
  if (executionPolling) clearInterval(executionPolling);
  executionPolling = setInterval(async () => {
    try {
      const s = await api('/api/execution/status');
      const term = document.getElementById('execution-terminal');
      term.innerHTML = s.output.map(colorLine).join('\n');
      term.scrollTop = term.scrollHeight;
      if (s.status && s.status.message) document.getElementById('bot-message').textContent = s.status.message;
      if (!s.running) { updateBotUI(false); clearInterval(executionPolling); executionPolling = null; }
    } catch(e) { /* offline */ }
  }, 2000);
}

async function checkBotStatus() {
  try {
    const s = await api('/api/execution/status');
    updateBotUI(s.running);
    if (s.running) startExecutionPolling();
    if (s.status && s.status.message) document.getElementById('bot-message').textContent = s.status.message;
  } catch(e) { /* offline */ }
}

// ═══════════════════════════════════════════════════════════════════
// GIT
// ═══════════════════════════════════════════════════════════════════

async function loadGitStatus() {
  try {
    const res = await api('/api/git/status');
    document.getElementById('git-status-box').textContent = res.output || res.error || 'Clean';
  } catch(e) { document.getElementById('git-status-box').textContent = 'Cannot connect to server'; }
}

async function gitPush() {
  const msg = document.getElementById('git-message').value || 'Dashboard update';
  const btn = document.getElementById('btn-git-push');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Pushing...';
  try {
    const res = await api('/api/git/push', { method: 'POST', body: { message: msg } });
    btn.disabled = false; btn.innerHTML = '⬆ Push to GitHub';
    if (res.error) return toast(res.error, 'error');
    toast('Pushed to GitHub ✓', 'success');
    document.getElementById('git-status-box').textContent = res.commit_output + '\n' + res.push_output;
    loadGitStatus();
  } catch(e) { btn.disabled = false; btn.innerHTML = '⬆ Push to GitHub'; toast('Cannot connect', 'error'); }
}

// ═══════════════════════════════════════════════════════════════════
// LOGS
// ═══════════════════════════════════════════════════════════════════

async function loadBotLogs() {
  try {
    const res = await api('/api/logs');
    if (res.lines) {
      const term = document.getElementById('logs-terminal');
      term.innerHTML = res.lines.map(colorLine).join('\n');
      term.scrollTop = term.scrollHeight;
    }
  } catch(e) { /* offline */ }
}

// ═══════════════════════════════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════════════════════════════

async function initDashboard() {
  updateConnectionUI(false);
  try {
    await api('/api/config/strategy');  // test connection
    connectionOk = true;
    updateConnectionUI(true);
  } catch(e) {
    connectionOk = false;
    updateConnectionUI(false);
    toast('⚠ Cannot reach local server. Start it with: python dashboard/dashboard_server.py', 'error');
  }
  loadStrategyConfig();
  loadExecutionConfig();
  loadPairs();
  loadBacktest();
  checkBotStatus();
  loadGitStatus();
  loadBotLogs();
}

document.addEventListener('DOMContentLoaded', () => {
  updateClock();
  document.getElementById('api-url-input').value = API;
  initDashboard();
  setInterval(loadBotLogs, 10000);
});
