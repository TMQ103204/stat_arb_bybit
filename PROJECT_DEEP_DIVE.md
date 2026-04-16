# StatArb Bybit – Tài Liệu Kỹ Thuật Toàn Diện

---

## 1. TỔNG QUAN DỰ ÁN

**StatArb Bybit** là một hệ thống giao dịch **Statistical Arbitrage (Thống Kê Chênh Lệch Giá)** chạy trên sàn Bybit. Chiến lược cốt lõi là: tìm hai đồng coin có mối quan hệ thống kê bền vững (cointegration), sau đó khai thác sự lệch tạm thời khỏi trạng thái cân bằng đó để kiếm lợi nhuận.

### Nguyên lý cơ bản (Stat Arb)

Giả sử COIN_A và COIN_B thường di chuyển cùng chiều. Khi spread giữa chúng lệch quá xa khỏi mức bình thường:
- **Nếu spread cao bất thường**: Short COIN_A, Long COIN_B → kỳ vọng spread thu hẹp lại
- **Nếu spread thấp bất thường**: Long COIN_A, Short COIN_B → kỳ vọng spread mở rộng lại

Lợi nhuận đến từ sự hồi quy về trạng thái cân bằng (mean reversion), không phải từ dự đoán hướng thị trường. Đây là chiến lược **market-neutral** về lý thuyết.

### Ba môi trường hoạt động

Dự án hỗ trợ 3 chế độ qua biến `mode`:
- `"demo"`: Môi trường demo Bybit – giá thật, tiền ảo
- `"test"`: Testnet Bybit – giá ảo, tiền ảo
- `"live"`: Mainnet Bybit – tiền thật

---

## 2. KIẾN TRÚC TỔNG THỂ

```
stat-arb-bybit/
│
├── .env                          # API keys (KHÔNG commit lên git)
├── bybit_response.py             # Utility: parse Bybit API response
├── requirements.txt              # Dependencies
│
├── strategy/                     # Module 1: Tìm cặp coin tốt nhất
│   ├── main_strategy.py          # Entry point chiến lược
│   ├── config_strategy_api.py    # Config + Bybit session cho strategy
│   ├── func_get_symbols.py       # Lấy danh sách coin đủ điều kiện
│   ├── func_prices_json.py       # Tải và lưu dữ liệu giá xuống JSON
│   ├── func_price_klines.py      # Lấy kline data từ Bybit
│   ├── func_cointegration.py     # ★ Tìm cặp cointegrated + scoring
│   ├── func_plot_trends.py       # Vẽ biểu đồ backtest
│   ├── custom_plot.py            # Helper vẽ biểu đồ
│   ├── 1_price_list.json         # [OUTPUT] Dữ liệu giá đã tải về
│   ├── 2_cointegrated_pairs.csv  # [OUTPUT] Danh sách cặp đã lọc
│   └── 3_backtest_file.csv       # [OUTPUT] Dữ liệu backtest
│
├── execution/                    # Module 2: Bot giao dịch thực tế
│   ├── main_execution.py         # Entry point bot single-pair
│   ├── main_portfolio.py         # ★ Entry point bot multi-pair
│   ├── portfolio_config.py       # ★ Config multi-pair: danh sách cặp + risk limits
│   ├── portfolio_manager.py      # ★ Điều phối nhiều PairTrader + giám sát drawdown
│   ├── pair_config.py            # ★ PairConfig (cấu hình) + PairState (runtime)
│   ├── pair_trader.py            # ★ Lifecycle 1 cặp: SEEKING → HOLDING → CLOSING
│   ├── pair_rotator.py           # ★ Auto rotation: thay cặp kém bằng cặp tốt hơn
│   ├── config_execution_api.py   # Config + Bybit session + retry wrapper (single-pair)
│   ├── func_trade_management.py  # ★ Logic tìm tín hiệu và đặt lệnh
│   ├── func_stats.py             # Tính spread và z-score
│   ├── func_get_zscore.py        # Lấy z-score live từ Bybit
│   ├── func_price_calls.py       # Lấy kline + liquidity data
│   ├── func_calcultions.py       # Tính PnL, quantity, wallet balance
│   ├── func_execution_calls.py   # Đặt lệnh, set leverage
│   ├── func_close_positions.py   # Đóng vị thế
│   ├── func_position_calls.py    # Kiểm tra vị thế và lệnh đang mở
│   ├── func_order_review.py      # Kiểm tra trạng thái lệnh
│   ├── func_save_status.py       # Ghi status.json
│   ├── logger_setup.py           # Cấu hình logging
│   ├── reset_bot.py              # Hủy lệnh + đóng vị thế (cleanup)
│   ├── bot.log                   # [OUTPUT] Log file của bot
│   ├── status.json               # [OUTPUT] Trạng thái single-pair
│   └── status_portfolio.json     # [OUTPUT] Trạng thái multi-pair
│
├── dashboard/
│   └── dashboard_server.py       # ★ Flask server (~1500 dòng, REST API)
│
├── docs/                         # Frontend dashboard
│   ├── index.html                # Giao diện chính (mode-aware)
│   ├── app.js                    # Logic frontend + multi-pair UI
│   └── styles.css                # Styling + pair card grid
│
└── resources/
    ├── Kelly Criterion.xlsx       # Tài liệu tính toán sizing
    └── Probability Math...xlsx   # Tài liệu phân tích xác suất
```

### Luồng hoạt động tổng quát

```
[Dashboard UI] ──HTTP──> [Flask Server :5000]
                              │
               ┌──────────────┴───────────────┐
               ▼                              ▼
        [Strategy Pipeline]           [Execution Engine]
        main_strategy.py              ┌─────────────────────┐
               │                      │ Single-pair:        │
               ▼                      │  main_execution.py  │
        1. Lấy symbols               │ Multi-pair:         │
        2. Tải giá lịch sử           │  main_portfolio.py  │
        3. Tính cointegration        │  → PortfolioManager │
        4. Lọc và rank pairs         │    → PairTrader ×N  │
        5. Lưu CSV                   │    → PairRotator    │
                                     └─────────────────────┘
```

---

## 3. MODULE STRATEGY – TÌM CẶP COIN

### 3.1. Điểm vào: `strategy/main_strategy.py`

Script đơn giản, 52 dòng, chạy 4 bước tuần tự:

```
STEP 1: get_tradeable_symbols()  → Lấy coins đủ điều kiện thanh khoản
STEP 2: store_price_history()    → Tải klines và lưu vào 1_price_list.json
STEP 3: get_cointegrated_pairs() → Phân tích cointegration, lọc, rank
STEP 4: plot_trends()            → Vẽ biểu đồ cho cặp tốt nhất (số 1)
```

### 3.2. Config Strategy: `config_strategy_api.py`

Các tham số quan trọng:

| Tham số | Mặc định | Ý nghĩa |
|---------|----------|---------|
| `mode` | `"live"` | Môi trường ("test"/"demo"/"live") |
| `timeframe` | `60` | Khung thời gian nến (phút) |
| `kline_limit` | `200` | Số nến dùng để phân tích |
| `z_score_window` | `21` | Rolling window tính z-score |
| `min_zero_crossings` | `30` | Số lần spread cắt qua 0 tối thiểu |
| `max_hurst` | `0.8` | Hurst exponent tối đa (lọc trending) |
| `max_half_life_hours` | `24` | Half-life tối đa (tránh khóa vốn lâu) |
| `max_net_funding_rate` | `0.0005` | Net funding rate tối đa |
| `min_backtest_profit_pct` | `0.0` | Lợi nhuận backtest tối thiểu |

### 3.3. Lọc coin: `func_get_symbols.py`

```python
MIN_TURNOVER_24H = 4_000_000  # $4 triệu USDT
```

Hai bước:
1. Lấy tất cả instruments `category="linear"`, lọc lấy `quoteCoin == "USDT"` và `status == "Trading"`
2. Lấy ticker 24h, lọc những coin có `turnover24h >= 4,000,000 USDT`

Kết quả: danh sách coin với thanh khoản đủ lớn để trade.

### 3.4. Thuật toán Cointegration: `func_cointegration.py` (★ TRỌNG TÂM)

Đây là file phức tạp nhất của module Strategy (~533 dòng). Các hàm chính:

#### `calculate_zscore(spread)` – Tính Z-score

```
Z-score = (current_spread - rolling_mean) / rolling_std
```

Dùng rolling window = `z_score_window` (21 nến). Z-score cao (+) = spread quá cao so với bình thường. Z-score thấp (-) = spread quá thấp.

#### `calculate_spread(series_1, series_2, hedge_ratio)` – Tính Spread

```
spread = series_1 - (series_2 × hedge_ratio)
```

`hedge_ratio` là hệ số được tính bằng OLS regression (series_1 ~ series_2). Nó cho biết cần bao nhiêu đơn vị coin_2 để "hedge" 1 đơn vị coin_1.

#### `calculate_half_life(spread)` – Đo tốc độ mean-reversion

Dùng Ornstein-Uhlenbeck model:
```
ln(2)
half_life = -─────
             gamma
```
Trong đó `gamma` là hệ số hồi quy của `diff(spread) ~ spread_lag`. Kết quả là số nến trung bình để spread hồi quy về 50% giá trị lệch. Nếu `gamma >= 0` → không mean-reverting → trả về 999 (bị filter bỏ).

#### `calculate_hurst_exponent(spread)` – Đo tính chất chuỗi

Dùng Rescaled Range (R/S) Analysis:
- `H < 0.5`: Mean-reverting (tốt cho stat arb)
- `H = 0.5`: Random walk (không dự đoán được)
- `H > 0.5`: Trending (rất xấu cho stat arb)

Thuật toán: chia spread thành nhiều chunk, tính R/S ratio cho mỗi size, fit log-log regression → slope = H.

#### `check_rolling_stability(series_1, series_2)` – Kiểm tra độ bền vững

Chia dữ liệu làm đôi, chạy cointegration test trên từng nửa. Nếu **cả hai** p-value < 0.05 → stable. **Lưu ý**: Bộ lọc này hiện không được dùng để loại bỏ pairs (vì với 100 nến/nửa thì quá noisy), chỉ lưu làm thông tin tham khảo.

#### `calculate_realistic_backtest(...)` – Backtest với chi phí thực tế

Mô phỏng giao dịch thực trên dữ liệu lịch sử:
- Vào khi `|z| > trigger_thresh` (1.1)
- Thoát khi `z` cắt 0
- Tính P&L bao gồm: phí giao dịch (4 × taker_fee) + phí funding
- Nếu vẫn còn vị thế khi hết dữ liệu → tính là lỗ

Trả về: `(avg_net_profit_pct, win_rate, total_trades, profit_factor)`

#### `get_cointegrated_pairs(prices)` – Pipeline chính

**Bước 1: Fast filter (OLS + Cointegration test)**
```python
coint_res = coint(series_1, series_2)  # statsmodels
# Điều kiện pass:
# - p_value < 0.05
# - coint_t < critical_value (5% level)
# - zero_crossings >= min_zero_crossings (30)
```

**Bước 2: Advanced filters** (chỉ chạy cho pairs qua bước 1)
- Tính half_life, hurst, stability, funding rate, backtest

**Bước 3: Hard filters** (loại bỏ bằng threshold cứng)
1. `hedge_ratio`: 0.2 – 5.0 (tránh position cực kỳ mất cân bằng)
2. `half_life`: 1 – 24 giờ (không quá ngắn, không quá dài)
3. `hurst < 0.8` (lọc trending pairs)
4. `net_funding_rate <= 0.0005` (tránh pairs tốn phí funding cao)
5. `total_trades >= 3` (đủ trades trong backtest)
6. `win_rate >= 0.5` (thắng nhiều hơn thua sau phí)
7. `avg_net_profit > 0` (phải có lãi net sau tất cả chi phí)

**Bước 4: Weighted Ranking**
```
composite_score =
    rank_avg_net_profit × 0.35   # Quan trọng nhất: lợi nhuận thực
  + rank_half_life       × 0.25  # Tốc độ revert nhanh
  + rank_hurst           × 0.20  # Tính chất mean-reverting
  + rank_profit_factor   × 0.10  # Tỷ lệ lãi/lỗ
  + rank_p_value         × 0.10  # Độ tin cậy cointegration
```

Sắp xếp ascending → cặp #1 = tốt nhất. Kết quả ghi vào `2_cointegrated_pairs.csv`.

---

## 4. MODULE EXECUTION – BOT GIAO DỊCH

### 4.1. Config Execution: `config_execution_api.py`

Các tham số quan trọng:

| Tham số | Mặc định | Ý nghĩa |
|---------|----------|---------|
| `ticker_1` | `"FARTCOINUSDT"` | Coin thứ nhất |
| `ticker_2` | `"HUSDT"` | Coin thứ hai |
| `signal_positive_ticker` | `ticker_2` | Coin "positive" trong strategy |
| `signal_negative_ticker` | `ticker_1` | Coin "negative" trong strategy |
| `tradeable_capital_usdt` | `11` | Vốn giao dịch tổng (USDT) |
| `leverage` | `2` | Đòn bẩy (1x-50x) |
| `signal_trigger_thresh` | `1.1` | Z-score threshold để vào lệnh |
| `zscore_stop_loss` | `10` | Z-score emergency stop-loss |
| `time_stop_loss_hours` | `48` | Thời gian tối đa giữ vị thế (giờ) |
| `max_session_loss_pct` | `10.0` | % lỗ tối da/session trước khi halt bot |
| `custom_thresholds` | `True` | Dùng exit_threshold tùy chỉnh |
| `exit_threshold` | `0.0` | Z-score exit (0 = mean reversion) |
| `limit_order_basis` | `True` | Dùng limit order (aggressive GTC) |
| `auto_trade` | `True` | Tự tìm trade mới sau khi đóng |
| `market_order_zscore_thresh` | `99` | Threshold để dùng market order (DISABLED) |
| `stop_loss_fail_safe` | `0` | Stop-loss % tính trên giá (0 = tắt) |

**Hai sessions Bybit:**
- `session_public`: Không cần API key, dùng để lấy giá, orderbook, klines
- `session_private`: Cần API key, dùng để đặt lệnh, check vị thế

**`retry_api_call(func, *args, max_retries=3, backoff_factor=1.5, **kwargs)`:**

Wrapper cho mọi Bybit API call, tự retry khi gặp `ConnectionError`, `TimeoutError`, `ProtocolError`. Delay tăng theo: 1s → 1.5s → 2.25s. Xử lý đặc biệt `ConnectionResetError 10054` (Windows TCP reset).

### 4.2. Vòng lặp chính: `main_execution.py`

Bot chạy vòng lặp `while True` với `time.sleep(2)` mỗi tick. Biến quan trọng nhất là **`kill_switch`**:

| `kill_switch` | Ý nghĩa |
|--------------|---------|
| `0` | Không có vị thế, đang tìm tín hiệu |
| `1` | Đang giữ vị thế (HOLDING) |
| `2` | Cần đóng vị thế (CLOSING) |

#### Trạng thái đầu mỗi tick

```python
is_p_ticker_open   = open_position_confirmation(ticker_positive)  # Có size > 0?
is_n_ticker_open   = open_position_confirmation(ticker_negative)
is_p_ticker_active = active_position_confirmation(ticker_positive) # Có lệnh đang chờ?
is_n_ticker_active = active_position_confirmation(ticker_negative)

has_p = is_p_ticker_open or is_p_ticker_active
has_n = is_n_ticker_open or is_n_ticker_active

both_legs_open = has_p and has_n  # Cả 2 legs → an toàn
half_leg_open  = has_p ^ has_n    # XOR: đúng 1 leg → emergency!
no_positions   = not has_p and not has_n  # Sạch → tìm trade mới
```

#### Luồng quyết định

```
1. half_leg_open + kill_switch==0 → EMERGENCY: đóng orphan leg ngay
2. no_positions + kill_switch==0  → manage_new_trades() → tìm tín hiệu
3. both_legs_open + kill_switch==0 → Re-attach: bot vừa restart, có sẵn vị thế
4. kill_switch==1                  → HOLDING: check z-score, PnL, stop-losses
5. kill_switch==2                  → CLOSING: đóng tất cả, circuit breaker
```

#### Frozen parameters (quan trọng!)

Khi vào lệnh, bot **đóng băng** ba tham số:
- `entry_hedge_ratio`: hedge ratio tại thời điểm vào
- `entry_mean`: rolling mean của spread tại thời điểm vào
- `entry_std`: rolling std của spread tại thời điểm vào

Lý do: Nếu dùng rolling z-score trong khi HOLDING, window sẽ "hấp thụ" dần spread hiện tại vào mean, làm z-score tự giảm về 0 dù spread thực tế không hồi. Gọi là "phantom mean-reversion". Với frozen parameters, z-score phản ánh đúng: spread đã di chuyển bao nhiêu so với lúc vào lệnh.

#### Exit rules (4 trigger)

1. **Z-score stop-loss**: `|z| > zscore_stop_loss` (mặc định 10) → Cặp coin đã diverge nghiêm trọng
2. **Time stop-loss**: Giữ > `time_stop_loss_hours` giờ (mặc định 48h) → Vốn bị khóa quá lâu
3. **Take profit (positive side)**: `z < exit_threshold` (mặc định 0) → Spread hồi về
4. **Take profit (negative side)**: `z >= -exit_threshold` → Spread hồi về

#### Circuit breaker (session loss limiter)

Sau mỗi lần đóng vị thế:
```python
session_realized_loss += abs(trade_pnl_if_negative)
session_loss_pct = session_realized_loss / starting_capital × 100
if session_loss_pct >= max_session_loss_pct:
    sys.exit(1)  # Halt bot hoàn toàn
```

Tính `starting_capital` từ wallet API (`walletBalance - cumRealisedPnl`), không dùng config.

#### Cooldown sau close

Sau khi đóng vị thế: `time.sleep(300)` (5 phút) để tránh re-entry ngay khi z-score vẫn extreme.

### 4.3. Logic vào lệnh: `func_trade_management.py`

#### `manage_new_trades(kill_switch)` – Hàm phức tạp nhất execution

**Điều kiện vào lệnh:**
1. Z-score tính fresh (OLS + rolling window)
2. `|z| >= zscore_stop_loss` → Skip (tránh vào trong vùng danger)
3. `|z| > signal_trigger_thresh` (1.1) → Hot = True → Bắt đầu đặt lệnh

**Sizing:**
```python
available_capital = min(tradeable_capital_usdt, wallet_balance)  # Không vượt ví
capital_long = available_capital × 0.5
capital_short = available_capital × 0.5

# Limit liquidity: dùng min(avg_liquidity_long, avg_liquidity_short) × price
# không vượt quá capital_long
initial_capital_usdt = min(fill_target, capital_long)
```

**Đặt lệnh song song (cả 2 legs cùng lúc):**
```python
with concurrent.futures.ThreadPoolExecutor() as executor:
    future_long = executor.submit(initialise_order_execution, ...)
    future_short = executor.submit(initialise_order_execution, ...)
```

Lý do dùng ThreadPoolExecutor: minimize time gap giữa 2 leg, tránh một leg bị fill ở giá xa hơn nhiều.

**Retry logic với escalation:**
- `max_retries = 5` lần thử tổng cộng
- `FORCE_MARKET_AFTER_RETRY = 3`: Sau 3 lần limit fail → escalate sang market order
- Nếu một leg đã fill trước → force market cho leg còn lại ngay lập tức

**Trạng thái lệnh (từ `check_order`):**
- `"Order Active"`: Lệnh đang chờ fill (Created/New)
- `"Partial Fill"`: Đã fill một phần
- `"Position Filled"`: Lệnh đã fill hoàn toàn
- `"Trade Complete"`: Vị thế đã đạt target capital
- `"Try Again"`: Lệnh bị cancel/reject → thử lại

**Half-position guards (nhiều điểm):**
Bot kiểm tra tại nhiều điểm: khi một leg hết retry mà leg kia đã fill, khi z-score drop và một leg đã fill, khi bot restart và phát hiện một leg. Trong mọi trường hợp: `close_all_positions()` ngay lập tức.

### 4.4. Tính Z-score live: `func_get_zscore.py`

#### `get_latest_zscore()` – Phiên bản đơn giản (không dùng frozen)

```
1. Lấy orderbook ticker_1 → mid_price_1 (best ask cho Long)
2. sleep(0.5)
3. Lấy orderbook ticker_2 → mid_price_2
4. sleep(0.5)
5. Lấy klines lịch sử ticker_1 và ticker_2
6. Thay kline cuối = mid_price hiện tại (giá "live")
7. calculate_metrics(series_1, series_2) → OLS fresh → zscore
```

#### `get_latest_zscore_with_hedge(frozen_hedge_ratio, frozen_mean, frozen_std)` – Phiên bản có frozen

Tương tự nhưng dùng `calculate_metrics_with_hedge()`:
- Nếu `frozen_hedge_ratio is None` → SEEKING mode: tính OLS fresh, capture mean/std
- Nếu có frozen → HOLDING mode: skip OLS, dùng `(current_spread - frozen_mean) / frozen_std`

Trả về: `(zscore, signal_sign_positive, hedge_ratio, entry_mean, entry_std)`

### 4.5. Statistics: `func_stats.py`

#### `calculate_metrics_with_hedge()` – Hàm quan trọng nhất

```python
# SEEKING mode (không có frozen)
hedge_ratio = OLS(series_1 ~ series_2).params[0]
spread = series_1 - hedge_ratio × series_2
zscore_list = rolling_zscore(spread, window=z_score_window)
entry_mean = spread.rolling(z_score_window).mean().iloc[-1]
entry_std  = spread.rolling(z_score_window).std().iloc[-1]

# HOLDING mode (có frozen)
hedge_ratio = frozen_hedge_ratio  # Không recompute OLS!
spread = series_1 - hedge_ratio × series_2
current_spread = spread.iloc[-1]
zscore = (current_spread - frozen_mean) / frozen_std  # Một giá trị duy nhất
```

### 4.6. Đặt lệnh: `func_execution_calls.py`

#### `place_order(ticker, price, quantity, direction, stop_loss, force_market)`

Loại lệnh:
- **Limit GTC (Aggressive)**: Giá = best ask (Long) hoặc best bid (Short) → fill ngay như taker nhưng có giới hạn slippage
- **Market**: Dùng khi `force_market=True` hoặc `limit_order_basis=False` hoặc z-score đủ cao

#### `should_use_market(z_score)`

Logic tự động upgrade limit → market (hiện đã DISABLED với `market_order_zscore_thresh=99`):
```
expected_move_pct = |z| / window × 100
net_profit = expected_move - round_trip_fees
use_market = net_profit >= min_profit_pct
```

#### `initialise_order_execution(ticker, direction, capital, force_market, z_score)`

1. Lấy orderbook → tính `entry_price`, `quantity` (capital / price, rounded to qty_step)
2. Quyết định loại lệnh
3. Gọi `place_order()`
4. Nếu `retCode == 0` → trả về `orderId`
5. Nếu lỗi `110007` (insufficient balance) → trả về `-1` (sentinel)

### 4.7. Tính PnL: `func_calcultions.py`

#### `calculate_exact_live_profit(long_ticker, short_ticker, baseline_long, baseline_short)`

```python
# Từ Bybit API get_positions():
unrealised_long  = pos_long["unrealisedPnl"]
realised_long    = pos_long["cumRealisedPnl"] - baseline_realised_long
pnl_long = unrealised_long + realised_long

unrealised_short = ...
realised_short   = pos_short["cumRealisedPnl"] - baseline_realised_short
pnl_short = unrealised_short + realised_short

total_pnl = pnl_long + pnl_short
pnl_pct = total_pnl / (avg_price_long × size_long + avg_price_short × size_short) × 100
```

`baseline_*` là snapshot `cumRealisedPnl` tại thời điểm vào lệnh, đảm bảo chỉ tính PnL của trade này không bao gồm carry-over từ trade trước.

#### `snapshot_cumrealised_pnl(long_ticker, short_ticker)`

Gọi ngay sau khi confirm vào lệnh, lưu `cumRealisedPnl` cả 2 legs làm baseline.

#### `get_wallet_equity()`

```python
wallet_balance    = USDT coin walletBalance (chỉ realized)
unrealised_pnl    = USDT coin unrealisedPnl
cum_realised_pnl  = USDT coin cumRealisedPnl
equity            = totalEquity (account level)
starting_capital  = wallet_balance - cum_realised_pnl  # = số tiền ban đầu nạp vào
```

#### `get_trade_details(orderbook, direction, capital)`

```python
nearest_ask = float(orderbook["a"][0][0])  # Best ask (Long entry)
nearest_bid = float(orderbook["b"][0][0])  # Best bid (Short entry)
mid_price = nearest_ask if direction == "Long" else nearest_bid

quantity = floor(capital / mid_price, decimals=qty_step_decimals)
```

Instrument info (tick_size, qty_step) được cache để tránh gọi API lặp lại.

### 4.8. Đóng vị thế: `func_close_positions.py`

#### `close_all_positions(kill_switch)`

1. Cancel tất cả open orders cả 2 tickers
2. `time.sleep(1)` cho API settle
3. Lấy `(side, size)` của từng vị thế
4. Đặt market order `reduceOnly=True` với side ngược lại
5. Nếu thành công → `kill_switch = 0`, ngược lại giữ nguyên

#### `get_position_info(ticker, max_retries=3)`

Lấy side và size với retry. Nếu sau 3 lần vẫn fail → raise Exception (không trả về 0 fake).

### 4.9. Kiểm tra vị thế: `func_position_calls.py`

#### `open_position_confirmation(ticker, max_retries=3)`

- Gọi `get_positions` → Nếu có `size > 0` → `True`
- Nếu `retCode != 0` (API error) → retry, KHÔNG return False (sẽ gây phantom close)
- Nếu hết retry → return False với critical log

#### `active_position_confirmation(ticker, max_retries=3)`

Tương tự nhưng check `get_open_orders` (lệnh đang chờ fill).

#### `query_existing_order(ticker, order_id, direction)`

Thử 2 nguồn theo thứ tự:
1. `get_open_orders` (lệnh chưa fill)
2. `get_order_history` (lệnh đã fill/cancel)

Trả về `(price, qty, orderStatus)`.

### 4.10. Review lệnh: `func_order_review.py`

#### `check_order(ticker, order_id, remaining_capital, direction)`

Logic quyết định trạng thái:
```
position_value >= remaining_capital → "Trade Complete" (đã đủ capital)
orderStatus == "Filled"             → "Position Filled"
orderStatus in ["Created", "New"]   → "Order Active"
orderStatus == "PartiallyFilled"    → "Partial Fill"
orderStatus in ["Cancelled", "Rejected", "PendingCancel"] → "Try Again"
unhandled case                      → "Try Again"
```

### 4.11. Reset bot: `reset_bot.py`

Chạy độc lập hoặc qua CLI `python main_execution.py --reset`. Chức năng:
1. Cancel tất cả orders cho cả 2 tickers
2. Đóng tất cả vị thế bằng market order (IOC)
3. Wait 2 giây, verify lại
4. Print trạng thái rõ ràng: CLEAN hoặc cần check thủ công

---

## 5. MODULE DASHBOARD

### 5.1. Backend: `dashboard/dashboard_server.py` (~1307 dòng)

Flask app chạy tại `http://localhost:5000`. Phục vụ 2 việc:
1. Chạy các Python subprocess (strategy, execution) và stream output
2. Serve static files từ `docs/` (HTML/CSS/JS)

#### Quản lý subprocess

Bot được tracking bằng:
```python
strategy_process = None   # subprocess.Popen object
strategy_output = []      # Rolling buffer 500 dòng log
strategy_lock = threading.Lock()
# Tương tự cho execution
```

Output được đọc bằng thread riêng biệt qua `stream_process_output()` (blocking read line-by-line).

#### Đọc/ghi config

Dashboard đọc và ghi config bằng **regex trực tiếp trên file .py**, không dùng file JSON riêng. Tức là khi user bấm Save, code dùng `re.sub()` để thay đổi giá trị trong `config_execution_api.py`. Điều này cho phép config luôn đồng bộ giữa dashboard và code chạy trực tiếp.

#### `kill_all_bot_processes()`

Trước khi start execution bot, server dùng WMIC (Windows) hoặc `pgrep` (Unix) để tìm và kill tất cả process `main_execution.py` đang chạy. Tránh tình huống có 2 bot cùng chạy ghi vào cùng 1 log file.

### 5.2. API Endpoints

#### Config
- `GET /api/config/strategy` – Đọc strategy config
- `POST /api/config/strategy` – Ghi strategy config
- `GET /api/config/execution` – Đọc execution config
- `POST /api/config/execution` – Ghi execution config

#### Strategy Pipeline
- `POST /api/strategy/run` – Start strategy subprocess
- `GET /api/strategy/status` – Trạng thái + log output

#### Execution Bot
- `POST /api/execution/start` – Start bot, kill stray processes trước
- `POST /api/execution/stop` – Stop bot
- `POST /api/execution/reset` – Chạy reset_bot.py (cancel orders + close positions)
- `GET /api/execution/status` – Running status + log output + status.json
- `GET /api/execution/zscore-live` – Z-score live của cặp đang cấu hình
- `POST /api/execution/test-leverage` – Test set leverage lên Bybit

#### Dữ liệu & Biểu đồ
- `GET /api/pairs` – Danh sách pairs từ `2_cointegrated_pairs.csv`
- `GET /api/pairs/zscore?sym1=X&sym2=Y` – Z-score hiện tại cho pair (cache 10s)
- `GET /api/pairs/zscore-history?sym1=X&sym2=Y` – Lịch sử z-score (cache 30s)
- `GET /api/backtest?sym1=X&sym2=Y` – Backtest từ dữ liệu JSON giá
- `GET /api/backtest/live?sym1=X&sym2=Y&timeframe=60&duration=48` – Backtest từ live Bybit klines

#### Performance / P&L
- `GET /api/performance` – P&L từ Bybit Wallet API (nguồn chính xác nhất)

#### Serve Frontend
- `GET /` → `docs/index.html`
- `GET /<filename>` → `docs/<filename>`

### 5.3. Bot-Equivalent Replay (trong dashboard)

Để chart dashboard khớp với quyết định thực của bot, dashboard tính z-score theo cách **replay** lại từng time point T:

```
Với mỗi điểm T:
  1. Lấy kline_limit nến kết thúc tại T (e.g., 200 nến)
  2. OLS trên window đó → beta_T (hedge_ratio)
  3. Tính ALL spreads trong window dùng beta_T
  4. Rolling z-score → lấy giá trị cuối = z tại T
  5. Plot z tại T trên chart
```

Đây là cách duy nhất để chart phản ánh đúng "bot thấy gì tại thời điểm đó".

### 5.4. Performance API

Dùng `get_wallet_balance` (không phải closed PnL):
```
starting_capital = wallet_balance - cum_realised_pnl
total_pnl = cum_realised_pnl + unrealised_pnl
pnl_pct = total_pnl / starting_capital × 100
```

Bổ sung: Fetch `get_transaction_log` theo batches 7 ngày (giới hạn của Bybit) để breakdown phí funding và phí giao dịch.

---

## 6. UTILITY & HELPERS

### 6.1. `bybit_response.py`

3 helper functions để parse chuẩn Bybit V5 API response:

```python
get_ret_code(response) → int  # Lấy retCode (0 = success)
get_result_dict(response) → dict  # Lấy response["result"]
get_result_list(response) → list  # Lấy response["result"]["list"]
```

### 6.2. `execution/logger_setup.py`

Tạo logger với 2 handler:
- **Console**: Level INFO trở lên, format: `timestamp [LEVEL] name: message`
- **File**: Level DEBUG trở lên, ghi vào `execution/bot.log` (UTF-8)

### 6.3. `execution/func_save_status.py`

```python
def save_status(status_dict):
    with open("status.json", "w") as f:
        json.dump(status_dict, f)
```

Dashboard polling `status.json` mỗi vài giây để hiển thị trạng thái bot.

---

## 7. DATA FILES

### `strategy/1_price_list.json`

```json
{
  "BTCUSDT": [
    {"open": 30000, "high": 30500, "low": 29800, "close": 30200},
    ...
  ],
  "ETHUSDT": [...],
  ...
}
```

Mỗi coin có 200 nến (kline_limit). Được tạo bởi `func_prices_json.py`.

### `strategy/2_cointegrated_pairs.csv`

```csv
sym_1,sym_2,p_value,t_value,c_value,hedge_ratio,zero_crossings,half_life,hurst,win_rate,total_trades,avg_net_profit,profit_factor,net_funding_rate,composite_score
```

Sắp xếp theo `composite_score` ascending (thấp nhất = tốt nhất). Được đọc bởi dashboard để hiển thị pairs table.

### `execution/status.json`

```json
{"message": "HOLDING | Z: 1.2345 | ...", "checks": "[True, True, False, False]"}
```

### `execution/bot.log`

Log chi tiết của bot, format: `timestamp [LEVEL] module: message`. Được stream realtime lên dashboard.

---

## 8. LUỒNG DỮ LIỆU CHI TIẾT

### Strategy Pipeline (chạy một lần để tìm cặp)

```
Bybit API (get_instruments_info + get_tickers)
    ↓ [func_get_symbols.py]
Danh sách coin lỏng khoản ($4M+/24h)
    ↓ [func_price_klines.py + func_prices_json.py]
1_price_list.json (200 nến × N coins)
    ↓ [func_cointegration.py: get_cointegrated_pairs()]
    ├── Brute-force: N×(N-1)/2 pairs
    ├── Fast filter: OLS + statsmodels.coint
    ├── Advanced: half_life + hurst + backtest + funding
    └── Hard filters + ranking
2_cointegrated_pairs.csv (ranked pairs)
    ↓ [func_plot_trends.py]
3_backtest_file.csv + biểu đồ
```

### Execution Bot (chạy liên tục)

```
Tick mỗi 2 giây:
    ↓
Kiểm tra positions/orders (4 API calls)
    │
    ├── no_positions → get_latest_zscore_with_hedge()
    │       ↓
    │   Bybit Orderbook (giá live mid_price)
    │       ↓
    │   Mark price klines (200 nến lịch sử)
    │       ↓
    │   OLS → hedge_ratio → spread → rolling z-score
    │       ↓
    │   |z| > 1.1? → HOT → đặt lệnh song song
    │       ↓
    │   ThreadPool: Long leg + Short leg đồng thời
    │       ↓
    │   Monitor: check_order() mỗi 0.5s
    │       ↓
    │   Both filled → kill_switch = 1, freeze params
    │
    ├── kill_switch==1 → get_latest_zscore_with_hedge(frozen_params)
    │       ↓
    │   current_spread → (current - frozen_mean) / frozen_std = z
    │       ↓
    │   calculate_exact_live_profit() → PnL từ Bybit positions API
    │       ↓
    │   Log: "HOLDING | Z: x.xxxx | Side: positive | Net PnL: y.yyy USDT"
    │       ↓
    │   Check exit conditions:
    │   ├── |z| > 10 → kill_switch = 2
    │   ├── held > 48h → kill_switch = 2
    │   └── z crossed exit_threshold → kill_switch = 2
    │
    └── kill_switch==2 → close_all_positions()
            ↓
        cancel_all_orders() + market close both legs
            ↓
        Verify closed (wait 2s, re-check)
            ↓
        Circuit breaker check
            ↓
        sleep(300) cooldown
            ↓
        auto_trade=False? → sys.exit(0)
        auto_trade=True? → kill_switch = 0, seek new trades
```

---

## 9. CÁC BUG ĐÃ XỬ LÝ VÀ QUYẾT ĐỊNH THIẾT KẾ

### Bug #1: Half-position (naked leg)

**Vấn đề**: Bot vào một leg nhưng leg kia không fill → nắm giữ vị thế một chiều (unhedged), thua lỗ lớn. Trường hợp thực tế: 54 phút nắm POPCAT naked.

**Giải pháp**: 
- Dùng XOR check (`has_p ^ has_n`) thay vì `any()` để phân biệt half-position vs full
- 4 điểm guard trong `func_trade_management.py`
- Startup check trong `main_execution.py`
- Khi phát hiện: gọi `close_all_positions()` ngay lập tức

### Bug #2: Bybit API trả về size=0 ngay sau fill

**Vấn đề**: Sau khi lệnh fill, gọi `get_positions()` ngay thì trả về `size=0` vì API chưa cập nhật. `close_all_positions()` bị bỏ qua vì nghĩ không có vị thế.

**Giải pháp**: `time.sleep(3)` trước khi gọi close sau fill.

### Bug #3: `any()` nguy hiểm khi detect positions

**Vấn đề**: `any([is_p_open, is_n_open, is_p_active, is_n_active])` trả về True cho cả half-position và full-position, không phân biệt được.

**Giải pháp**: Logic XOR chính xác (xem Bug #1).

### Bug #4: Close verification

**Vấn đề**: `close_all_positions()` có thể thành công về mặt API nhưng vị thế thực tế chưa đóng (race condition).

**Giải pháp**: Sau khi `kill_switch = 0`, wait 2s rồi check lại positions.

### Bug #5: Phantom mean-reversion

**Vấn đề**: TRong HOLDING, rolling z-score dần về 0 dù spread thực không thay đổi → bot đóng lệnh sớm hoặc z-score không phản ánh thực tế P&L.

**Giải pháp**: Frozen parameters (entry_hedge_ratio, entry_mean, entry_std). HOLDING mode dùng `z = (current_spread - frozen_mean) / frozen_std`.

### Bug #6: Insufficient balance

**Vấn đề**: Lỗi 110007 (insufficient balance) bị retry vô ích, gây loop.

**Giải pháp**: `initialise_order_execution()` trả về sentinel `-1` khi gặp lỗi này. `func_trade_management.py` detect và abort toàn bộ trade entry.

### Bug #7: Unicode crash trên Windows

**Vấn đề**: `subprocess.Popen` với `text=True` dùng encoding mặc định của Windows (không phải UTF-8), crash khi bot log có emoji/dấu đặc biệt.

**Giải pháp**: `encoding="utf-8"` và `errors="replace"` trong tất cả subprocess calls trong `dashboard_server.py`.

### Bug #8: Dual bot

**Vấn đề**: User start bot từ terminal, sau đó start lại từ dashboard → 2 bot cùng chạy, cùng ghi log, cùng đặt lệnh.

**Giải pháp**: `kill_all_bot_processes()` dùng WMIC/pgrep tìm và kill **TẤT CẢ** `main_execution.py` process trước khi start.

---

## 10. MULTI-PAIR PORTFOLIO SYSTEM

### 10.1. Kiến trúc tổng quan

Khi chạy ở **Demo mode**, hệ thống sử dụng multi-pair engine thay vì single-pair. Kiến trúc:

```
main_portfolio.py
    └── PortfolioManager
            ├── PairTrader (AXS_IP)     ← Thread 1
            ├── PairTrader (KAS_POPCAT) ← Thread 2
            ├── PairTrader (...)        ← Thread N
            ├── MonitorThread           ← Giám sát drawdown
            └── PairRotator             ← Tự động xoay cặp (optional)
```

Mỗi `PairTrader` chạy trong thread riêng, có lifecycle độc lập: SEEKING → HOLDING → CLOSING.

### 10.2. Cấu hình: `portfolio_config.py`

```python
ACTIVE_PAIRS = [
    PairConfig(
        pair_id="AXS_IP",
        ticker_1="AXSUSDT",
        ticker_2="IPUSDT",
        signal_positive_ticker="IPUSDT",
        signal_negative_ticker="AXSUSDT",
        allocated_capital=50,
        leverage=2,
        signal_trigger_thresh=1.1,
        # ... các tham số khác
    ),
    # Thêm cặp khác...
]
```

Các tham số portfolio-level:

| Tham số | Mặc định | Ý nghĩa |
|---------|----------|---------|
| `MAX_TOTAL_EXPOSURE_USDT` | 500 | Notional tối đa toàn portfolio |
| `MAX_PAIRS_SIMULTANEOUS` | 5 | Số cặp tối đa cùng lúc |
| `MAX_PORTFOLIO_DRAWDOWN_PCT` | 15.0 | Halt tất cả nếu drawdown > 15% |
| `POST_CLOSE_COOLDOWN_SEC` | 300 | Cooldown giữa các trade/cặp |

### 10.3. PairConfig & PairState (`pair_config.py`)

- **PairConfig**: Cấu hình bất biến cho mỗi cặp (ticker, capital, threshold)
- **PairState**: Trạng thái runtime có thể thay đổi (kill_switch, zscore, PnL)

Mỗi cặp có `PairState.reset_for_new_trade()` để reset state sạch khi bắt đầu trade mới.

### 10.4. PairTrader (`pair_trader.py`)

Thread lifecycle cho mỗi cặp:

```
_tick() mỗi 2 giây:
    │
    ├── Kiểm tra positions (orphan detection)
    ├── SEEKING (kill_switch=0): gọi manage_new_trades()
    ├── RE-ATTACH: phát hiện vị thế sẵn, tự đồng bộ
    ├── HOLDING (kill_switch=1): _tick_holding()
    │       ├── Z-score monitoring (frozen params)
    │       ├── PnL tracking
    │       └── Exit conditions (z-stop, time-stop, TP)
    └── CLOSING (kill_switch=2): close_all_positions()
```
### 10.6. Auto Pair Rotation (`pair_rotator.py`)

Engine tự động thay cặp kém bằng cặp tốt hơn:

```
Cứ 6 giờ:
  1. Chạy strategy scan → cập nhật 2_cointegrated_pairs.csv
  2. Normalize composite_score → [0, 1]
  3. So sánh cặp đang SEEKING (worst) vs cặp mới (best)
  4. Nếu cặp mới tốt hơn ≥ 20% (buffer) → thay thế
```

Quy tắc an toàn:
- **KHÔNG BAO GIỜ** thay cặp đang HOLDING (có vị thế mở)
- Chỉ thay cặp đang SEEKING
- Cooldown 30 phút giữa các lần xoay
- Tối đa 1 cặp/lần quét

Cấu hình:
```python
AUTO_ROTATION_ENABLED = True
SCAN_INTERVAL_HOURS = 6
ROTATION_BUFFER = 0.2          # Cải thiện tối thiểu 20%
MAX_ROTATIONS_PER_CYCLE = 1
ROTATION_COOLDOWN_MIN = 30
```

### 10.7. Portfolio Manager (`portfolio_manager.py`)

Điều phối tất cả PairTrader + giám sát rủi ro portfolio:

- `traders: dict[str, PairTrader]` — O(1) lookup theo pair_id
- `add_pair(config)` — Tạo + start PairTrader thread mới
- `stop_pair(pair_id)` — Dừng + xóa PairTrader
- Background monitor: check wallet equity mỗi 10s → tính drawdown
- Nếu drawdown ≥ 15% → **HALT TẤT CẢ**
- Ghi `status_portfolio.json` với trạng thái từng cặp

### 10.8. Dashboard Multi-Pair UI

**Mode toggle**: Chuyển đổi giữa 🎯 Single và 📦 Multi bằng toggle trên header (độc lập với environment Test/Demo/Live).

| Tính năng | Mô tả |
|-----------|--------|
| Pair Cards Grid | Card cho mỗi cặp: z-score, PnL, hold time |
| Status Colors | Amber (SEEKING), Green (HOLDING), Red (CLOSING), Gray (HALTED) |
| Add Pair | Click Select trên bảng Pairs → dialog tự fill → tùy chỉnh → Add |
| Remove Pair | Click Remove trên card → xóa khỏi config |
| Close Pair | ⏹ Close: đóng vị thế trực tiếp trên Bybit + dừng trader |
| Pause Pair | ⏸ Pause: dừng tìm trade mới (không đóng vị thế) |
| Ticker Overlap Protection | Backend chặn thêm cặp chung token (HTTP 409) |
| Config + Status Merge | Luôn hiện cặp từ config, overlay live status khi bot chạy |

**API endpoints mới:**

| Endpoint | Method | Mô tả |
|----------|--------|--------|
| `/api/portfolio/status` | GET | Trạng thái portfolio + per-pair status |
| `/api/portfolio/pairs` | GET | Danh sách cặp từ portfolio_config.py |
| `/api/portfolio/add-pair` | POST | Thêm cặp (có check overlap + duplicate) |
| `/api/portfolio/remove-pair` | POST | Xóa cặp (bracket-matching, xử lý \r\n) |

### 10.9. Ticker Overlap Protection

Khi 2 cặp chung 1 token (VD: AXS/IP + AXS/SNX → chung AXSUSDT):

**Vấn đề:**
- Position check nhầm: `open_position_confirmation("AXSUSDT")` trả về chung cho cả 2 cặp
- RE-ATTACH nhầm hoặc orphan detection sai
- Vị thế tự triệt tiêu (cặp 1 Long + cặp 2 Short cùng token)

**Giải pháp (3 lớp):**
1. Backend `/api/portfolio/add-pair` kiểm tra và reject (HTTP 409)
2. `main_portfolio.py` log WARNING khi startup
3. Frontend hiển thị thông báo lỗi khi bị reject

---

## 11. DEPENDENCIES

```
pybit >= 5.8.0       # Bybit Python SDK (V5 API)
numpy >= 2.0.0        # Numerical computing
pandas >= 2.2.0       # DataFrames, timeseries
matplotlib >= 3.9.0   # Plotting
scipy >= 1.14.0       # Statistics (coint test)
statsmodels >= 0.14.0 # OLS regression, cointegration
requests >= 2.31.0    # HTTP
python-dotenv >= 1.0.0 # Load .env file
flask >= 3.0.0        # Web server
flask-cors >= 4.0.0   # CORS headers
```

---

## 12. LÀM THẾ NÀO ĐỂ SỬ DỤNG

### Single-Pair (Test/Live mode)

1. Chạy Strategy pipeline từ dashboard để tìm cặp
2. Chọn cặp → Click Select → tự fill vào Execution Config
3. Start Bot → chạy `main_execution.py`

### Multi-Pair (Demo mode)

1. Chạy Strategy pipeline (Run Strategy)
2. Chuyển mode sang **DEMO**
3. Click **Select** trên bảng Cointegrated Pairs → Dialog tự fill
4. Tùy chỉnh Capital, Leverage, Entry Z → **Add to Portfolio**
5. Lặp lại cho các cặp khác (tránh chung token!)
6. Click **▶ Start** → chạy `main_portfolio.py`
7. Theo dõi pair cards cập nhật real-time

**Lưu ý:**
- `signal_positive_ticker` là ticker "long khi z-score âm"
- Khi z-score dương: short `signal_positive_ticker`, long `signal_negative_ticker`
- Khi z-score âm: long `signal_positive_ticker`, short `signal_negative_ticker`

---

## 13. GIỚI HẠN VÀ CẢI THIỆN CÓ THỂ

1. ~~**Chỉ 1 cặp/thời điểm**~~: ✅ Đã giải quyết với Multi-Pair Portfolio System.

2. **OLS không có intercept**: `sm.OLS(series_1, series_2)` không có hằng số. Kết quả là hedge_ratio không có bias correction.

3. **Không có paper trading riêng**: Demo mode dùng Bybit demo accounts (real prices, virtual money), không phải local simulation.

4. **Kline limit cố định**: Bot dùng đúng `kline_limit=200` nến để tính CÙNG hedge_ratio mỗi tick. Nếu thị trường thay đổi cấu trúc, hedge_ratio sẽ drift dần giữa các tick (được xử lý bằng frozen params khi HOLDING).

5. **Không có WebSocket**: Bot dùng REST API polling. Latency ~1-2s/tick.

6. **Rate limit**: Bot dùng `time.sleep(0.5)` giữa các orderbook calls, `time.sleep(2)` mỗi vòng lặp chính. Multi-pair cần delay stagger giữa các thread để tránh rate limit.

7. **Ticker overlap**: Multi-pair không hỗ trợ 2 cặp chung token. Backend chặn khi Add Pair nhưng không chặn nếu edit trực tiếp file config.

---

*Tài liệu này phản ánh trạng thái code tính đến ngày 14/04/2026.*
