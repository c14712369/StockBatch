# StockBatch 系統文件

## 概覽

自動化台股選股系統，每週評分台灣市值前 100 大股票（TW100 = 0050 + 0051 近似成分），推送潛力標的到 Telegram。

- **語言**：Python 3.12
- **排程**：GCP e2-micro 主機 crontab（主力）；GitHub Actions 僅保留 `workflow_dispatch` 手動觸發
- **資料庫**：Supabase（PostgreSQL）
- **通知**：Telegram Bot

---

## 專案結構

```
StockBatch/
├── src/
│   ├── config.py          # 環境變數讀取、WEIGHTS 定義
│   ├── universe.py        # 0050 成分股硬編碼清單（50 支）
│   ├── finmind.py         # FinMind API client（retry + 多 token 輪轉）
│   ├── fetchers.py        # 所有資料抓取函式（同時寫入 Supabase）
│   ├── scorers.py         # 兩階段評分引擎（硬性門檻 + 四維度 + PE 調整）
│   ├── notifier.py        # Telegram 訊息格式化與發送
│   ├── daily_job.py       # 日報入口（18:30 TST）
│   ├── morning_job.py     # 晨報入口（08:30 TST，純讀 Supabase）
│   ├── intraday_job.py    # 盤中快報入口（9:00–13:00 整點，TWSE MIS）
│   └── weekly_job.py      # 週報入口（週日 20:00 TST）
├── scripts/
│   └── run_backtest.py    # 回測腳本
├── .github/workflows/     # 僅 workflow_dispatch，排程已停用
├── supabase/
│   └── schema.sql         # 資料庫建表 SQL
├── requirements.txt
└── SYSTEM.md              # 本文件
```

---

## 環境變數

| 變數名稱            | 說明                                         |
|---------------------|----------------------------------------------|
| `FINMIND_TOKENS`    | FinMind JWT token（多把以逗號分隔，輪轉用）  |
| `FINMIND_TOKEN`     | 單一 token 備用（FINMIND_TOKENS 優先）       |
| `SUPABASE_URL`      | Supabase 專案 URL                            |
| `SUPABASE_KEY`      | Supabase service_role key                    |
| `TELEGRAM_TOKEN`    | Telegram Bot token                           |
| `TELEGRAM_CHAT_ID`  | 接收訊息的 Chat ID                           |

---

## 資料來源

各資料集採**批次優先**策略：優先以不傳 `stock_id` 一次拉全市場再過濾（付費版 ~4 次 API call），失敗時自動 fallback 至逐股（每 10 支為一組暫停）。

| 資料類型            | 來源                  | 說明                                         |
|---------------------|-----------------------|----------------------------------------------|
| 股價、均線          | FinMind `TaiwanStockPrice` | 批次優先；FinMind 無資料時以 yfinance 10支/批備援 |
| 三大法人買賣超      | FinMind `TaiwanStockInstitutionalInvestorsBuySell` | 批次優先；計算連買/賣天數 |
| 融資融券餘額        | FinMind `TaiwanStockMarginPurchaseShortSale` | 批次優先；計算 20 日變化率 |
| 月營收              | FinMind `TaiwanStockMonthRevenue` | 批次優先；需付費，免費版 fallback 後回傳空則跳過 |
| 財務報表（三表）    | FinMind `TaiwanStockFinancialStatements` / `BalanceSheet` / `CashFlowsStatement` | 逐股抓（含 DB 快取，已是最新則跳過）|
| 股權分散（大戶比）  | FinMind `TaiwanStockShareholding` | 需付費；免費版直接跳過，籌碼維度動態縮放 |
| 本益比              | 評分引擎自算（股價 / 近 4 季 EPS TTM） | 無需額外 API |
| 盤中即時報價        | TWSE MIS 公開 API     | 僅盤中快報使用，不占 FinMind 額度            |

---

## 資料庫 Schema（Supabase）

| 資料表                    | 主鍵                        | 說明                              |
|---------------------------|-----------------------------|-----------------------------------|
| `stock_universe`          | stock_id                    | TW100 成分股清單（0050 + 0051 近似）|
| `daily_price`             | (stock_id, date)            | 收盤價 + MA5/20/60                |
| `daily_institutional`     | (stock_id, date)            | 三大法人淨買賣 + 連買/賣天數      |
| `daily_margin`            | (stock_id, date)            | 融資餘額 + 20 日變化率            |
| `monthly_revenue`         | (stock_id, year, month)     | 月營收 + MOM/YOY                  |
| `quarterly_income`        | (stock_id, year, quarter)   | EPS、三率、EPS QoQ                |
| `quarterly_balance`       | (stock_id, year, quarter)   | 負債比、流動比、速動比            |
| `quarterly_cashflow`      | (stock_id, year, quarter)   | OCF、OCF 品質                     |
| `weekly_shareholding`     | (stock_id, date)            | 400 張以上大戶持股比（需付費）    |
| `valuation`               | (stock_id, date)            | PER、PBR（保留欄位，目前跳過）    |
| `weekly_scores`           | (stock_id, week_date)       | 四維度分數 + PE + 總分            |
| `paper_trading_positions` | (week_date, stock_id)       | 模擬倉位，含進場價 / 浮動損益     |

---

## 排程設定（GCP crontab，主力）

| 任務         | 台灣時間               | 執行腳本           |
|--------------|------------------------|--------------------|
| 晨報         | 週一～五 08:30         | `morning_job.py`   |
| 盤中快報     | 週一～五 09:00～13:00 整點 | `intraday_job.py` |
| 日報         | 週一～五 18:30         | `daily_job.py`     |
| 週報         | 週日 20:00             | `weekly_job.py`    |

GitHub Actions（`.github/workflows/`）已停用排程，僅保留手動觸發（`workflow_dispatch`）供緊急補跑使用。

---

## 運作流程

### 週報（weekly_job.py）

```
1.  載入 TW100 清單 → upsert stock_universe
2.  fetch_price()         → FinMind 批次優先，90 天，計算 MA5/20/60；缺漏以 yfinance 10支/批備援
3.  fetch_institutional() → FinMind 批次優先，60 交易日，計算外資/投信連買/賣天數
4.  fetch_margin()        → FinMind 批次優先，60 交易日，計算融資 20 日變化率
5.  fetch_revenue()       → FinMind 批次優先（需付費）；免費版 fallback 後跳過
6.  fetch_financials()    → FinMind，損益/資負/現金流，近 2 年，逐股抓（DB 快取優先）
7.  fetch_shareholding()  → FinMind（需付費），直接跳過
8.  fetch_valuation()     → 跳過（評分引擎自算 P/E）
9.  compute_all_scores()  → 兩階段評分（見下節）
10. upsert weekly_scores  → 存入 Supabase
11. 關閉前週 paper_trading_positions（status → closed，補最終損益）
12. upsert paper_trading_positions → 本週 Top 10，entry_price=0（哨兵值，待首個交易日日報確認）
13. send_weekly_report()  → Telegram 推送 Top 10 詳細版
```

### 日報（daily_job.py）

```
1. 從 weekly_scores 取最新 100 筆（order by week_date DESC）→ 取最新週的 Top 10 watchlist
2. fetch_price()         → FinMind，65 天（夠算 MA60）
3. fetch_institutional() → FinMind，60 日
4. fetch_margin()        → FinMind，30 日
5. 更新 paper_trading_positions 浮動損益：
   - 若 entry_price == 0（週報哨兵值），以本日收盤確認進場價（消除週一跳空偏差）
   - 含非本週 watchlist 但有 open 部位的股票
6. send_daily_report()   → Telegram 推送籌碼快報 + 模擬損益
```

### 晨報（morning_job.py）

```
1. 從 weekly_scores 取最新週的 Top 10 watchlist
2. 直接讀 daily_price / daily_institutional / daily_margin（昨日日報已寫入）
3. send_morning_briefing() → Telegram 推送開盤前摘要
（完全不打外部 API，不占 FinMind 額度）
```

### 盤中快報（intraday_job.py）

```
1. 從 weekly_scores 取最新週的 Top 10 watchlist
2. 讀 daily_price 取昨收 / 昨高 / 昨低
3. _fetch_twse() → TWSE MIS 公開 API 取即時報價
4. 判斷訊號：漲跌 ±2%、突破昨高、跌破昨低
5. 有訊號才 send_intraday_alert()；無訊號靜默
```

---

## 評分邏輯（scorers.py）

### 第一階段：硬性門檻（任一不過即淘汰）

| 條件                         | 說明                          |
|------------------------------|-------------------------------|
| 最近一季 OCF > 0             | 排除燒錢公司                  |
| 最近一季負債比 < 60%         | 排除財務槓桿過高              |
| 近 3 月 YOY 未全部為負       | 排除持續衰退（NaN 視為跳過）  |

### 第二階段：加權評分（0~100 分）

各分項均使用**線性插值**連續評分，消除舊版硬斷層（同一區間內分數相同）的問題。

| 維度       | 權重 | 評分項目（滿分）                                                        |
|------------|------|-------------------------------------------------------------------------|
| 獲利動能   | 30%  | 近3月營收YOY均值（40pt）、EPS QoQ（30pt）、毛利率趨勢（30pt）         |
| 財務體質   | 20%  | 流動比率（30pt）、負債比（30pt）、OCF品質（40pt）；合計滿分恰好 100pt  |
| 籌碼集中   | 30%  | 外資連買天數（20pt）、投信連買天數（20pt）、大戶持股比（30pt）、融資水位（30pt）；**缺失維度動態縮放，不歸零** |
| 市場動能   | 20%  | 均線多頭排列（40pt）、收盤 vs MA20（30pt）、量能趨勢（30pt）           |

> **注意**：PE 評分已從財務體質移出，改為第三階段後置調整，避免舊版「health 最大 120pt 被 min(100) 截斷，PE 永遠無效」的問題。

### 第三階段：PE 後置調整（±0~10 分）

自算年化 PE = 最新收盤 / 近 4 季 EPS TTM（需 ≥ 4 季資料，否則不調整）

| PE 範圍         | 調整      |
|----------------|-----------|
| 0 < PE < 15    | +10 分    |
| 15 ≤ PE < 20   | +5 分     |
| 20 ≤ PE < 25   | 不調整    |
| 25 ≤ PE < 35   | -5 分     |
| PE ≥ 35        | -10 分    |
| PE = 0（無效）  | 不調整    |

最終總分 = 加權總分 + PE 調整，並 clamp 至 [0, 100]。

**最終輸出**：通過門檻的股票依總分排序。

| 用途 | 常數 | 預設值 |
|------|------|--------|
| 週報推送數 | `TOP_N_WEEKLY` | 10 |
| 日報/晨報/盤中快報追蹤數 | `TOP_N_WATCHLIST` | 20 |
| Paper Trading 建倉數 | `TOP_N_PAPER` | 10 |

---

## Telegram 訊息格式

### 週報（詳細版）
```
📊 台股潛力週報 2026-03-02
#1 台積電 (2330) — 綜合 87/100
┌ 獲利動能 ████████░░ 85/100
├ 財務體質 ██████████ 90/100
├ 籌碼集中 ████████░░ 80/100
└ 市場動能 ██████████ 95/100
```

### 日報（籌碼快報）
```
📡 今日籌碼快報 2026-03-03
💼 模擬投資組合績效 (每週Top10)
  • 2026-02-23 選股 (10檔): 均報 +2.3%
    最佳: 台積電 (+5.1%)
🔥 外資 + 投信同步買超
  • 台積電 (2330): 外資 +8,500張（連買7日）
📈 今日收盤
  ✅ 多頭 2330 920.0 ▲1.8% | 量 28,500K張
```

---

## 常見問題與解法

| 問題                   | 原因                        | 解法                                          |
|------------------------|-----------------------------|-----------------------------------------------|
| FinMind 400 "register" | 需付費訂閱或免費次數用盡    | 自動切換下一把 Token；全部耗盡則跳過          |
| FinMind HTTP 402       | 付費功能限制                | 自動跳過，籌碼/月營收維度動態縮放或得 0       |
| watchlist 為空         | 尚未執行過週報              | 手動觸發 GitHub Actions weekly workflow        |
| Telegram 未收到訊息    | TELEGRAM_CHAT_ID 未設或錯誤 | 確認 GCP 環境變數 / GitHub Secret 設定        |
| 盤中快報無資料         | 休市或盤前執行              | TWSE MIS API 無資料則靜默不發                 |
| 晨報資料是舊的         | 日報尚未寫入 Supabase       | 確認前一日 18:30 日報是否正常執行             |

---

## 股票宇宙更新方式

TW100 每季調整。分別維護 `src/universe.py` 的 `TAIWAN_50`（0050）與 `TAIWAN_51_100`（0051 近似），兩者合併為 `TAIWAN_100`：

```python
TAIWAN_50: list[tuple[str, str]] = [("2330", "台積電"), ...]     # Top 1–50
TAIWAN_51_100: list[tuple[str, str]] = [("3231", "緯創"), ...]   # Top 51–100
TAIWAN_100 = TAIWAN_50 + TAIWAN_51_100                            # 合併，自動生效
```

官方參考來源：
- 0050：https://www.yuantaetfs.com/product/detail/0050/composition
- 0051：https://www.fubon.com/finance/etf/etf_detail.htm?etfid=0051

---

## 評分權重調整

修改 `src/config.py`：

```python
WEIGHTS = {
    "profitability": 0.30,  # 獲利動能
    "health":        0.20,  # 財務體質
    "chip":          0.30,  # 籌碼集中
    "momentum":      0.20,  # 市場動能
}
```

---

## 推送數量調整

修改 `src/config.py`：

```python
TOP_N_WEEKLY    = 10   # 週報推送數
TOP_N_WATCHLIST = 20   # 日報/晨報/盤中快報追蹤數
TOP_N_PAPER     = 10   # 模擬建倉數
```
