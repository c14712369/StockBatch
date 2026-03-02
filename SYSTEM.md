# StockBatch 系統文件

## 概覽

自動化台股選股系統，每週評分 0050 成分股（50 支），推送潛力標的到 Telegram。

- **語言**：Python 3.12
- **排程**：GitHub Actions
- **資料庫**：Supabase（PostgreSQL）
- **通知**：Telegram Bot

---

## 專案結構

```
StockBatch/
├── src/
│   ├── config.py          # 環境變數讀取（所有憑證）
│   ├── universe.py        # 0050 成分股硬編碼清單（50 支）
│   ├── finmind.py         # FinMind API client（僅月營收/籌碼用，免費版）
│   ├── fetchers.py        # 所有資料抓取函式
│   ├── scorers.py         # 四維度評分引擎
│   ├── notifier.py        # Telegram 訊息格式化與發送
│   ├── daily_job.py       # 日報入口
│   └── weekly_job.py      # 週報入口
├── .github/workflows/
│   ├── daily.yml          # 平日 18:30 TST 觸發
│   └── weekly.yml         # 週日 20:00 TST 觸發
├── supabase/
│   └── schema.sql         # 資料庫建表 SQL
├── requirements.txt
└── SYSTEM.md              # 本文件
```

---

## 環境變數（GitHub Secrets）

| Secret 名稱      | 說明                        |
|------------------|-----------------------------|
| `FINMIND_TOKEN`  | FinMind JWT token           |
| `SUPABASE_URL`   | Supabase 專案 URL           |
| `SUPABASE_KEY`   | Supabase service_role key   |
| `TELEGRAM_TOKEN` | Telegram Bot token          |
| `TELEGRAM_CHAT_ID` | 接收訊息的 Chat ID        |

---

## 資料來源

| 資料類型            | 來源              | 說明                              |
|---------------------|-------------------|-----------------------------------|
| 股價、均線          | yfinance          | `.TW` 後綴，分批 10 支下載        |
| 財務報表（三表）    | yfinance          | quarterly_income_stmt 等          |
| 本益比 / 股淨比     | yfinance          | ticker.info                       |
| 三大法人買賣超      | TWSE T86 API      | 逐日抓，最近 30 交易日            |
| 融資融券餘額        | TWSE MI_MARGN API | 逐日抓，最近 45 交易日            |
| 月營收              | FinMind 免費版    | 失敗則跳過（需付費才能使用）      |
| 股權分散（大戶比）  | FinMind 免費版    | 失敗則跳過                        |

---

## 資料庫 Schema（Supabase）

| 資料表                | 主鍵              | 說明                        |
|-----------------------|-------------------|-----------------------------|
| `stock_universe`      | stock_id          | 0050 成分股清單             |
| `daily_price`         | (stock_id, date)  | 收盤價 + MA5/20/60          |
| `daily_institutional` | (stock_id, date)  | 三大法人 + 連買/賣天數      |
| `daily_margin`        | (stock_id, date)  | 融資餘額 + 20 日變化率      |
| `monthly_revenue`     | (stock_id, year, month) | 月營收 + MOM/YOY      |
| `quarterly_income`    | (stock_id, year, quarter) | EPS、三率、QoQ       |
| `quarterly_balance`   | (stock_id, year, quarter) | 負債比、流動比       |
| `quarterly_cashflow`  | (stock_id, year, quarter) | OCF、OCF 品質        |
| `weekly_shareholding` | (stock_id, date)  | 400 張以上大戶持股比        |
| `valuation`           | (stock_id, date)  | PER、PBR                    |
| `weekly_scores`       | (stock_id, week_date) | 四維度分數 + 總分       |

---

## 評分邏輯（scorers.py）

### 第一階段：硬性門檻（任一不過即淘汰）

| 條件                         | 說明                    |
|------------------------------|-------------------------|
| OCF > 0（最近一季）          | 排除燒錢公司            |
| 負債比 < 60%                 | 排除財務槓桿過高        |
| 近 3 月 YOY 未全部為負       | 排除持續衰退            |

### 第二階段：加權評分（0~100 分）

| 維度       | 權重 | 評分項目                                    |
|------------|------|---------------------------------------------|
| 獲利動能   | 30%  | 近3月營收YOY均值（40pt）、EPS QoQ（30pt）、毛利率趨勢（30pt） |
| 財務體質   | 20%  | 流動比率（30pt）、負債比（30pt）、OCF品質（40pt） |
| 籌碼集中   | 30%  | 外資連買天數（20pt）、投信連買天數（20pt）、大戶持股比（30pt）、融資水位（30pt） |
| 市場動能   | 20%  | 均線多頭排列（40pt）、收盤 vs MA20（30pt）、量能趨勢（30pt） |

**最終輸出**：通過門檻的股票依總分排序，取 Top 10。

---

## 排程設定（GitHub Actions）

| Workflow   | Cron（UTC）       | 台灣時間        | 觸發條件           |
|------------|-------------------|-----------------|--------------------|
| weekly.yml | `0 12 * * 0`      | 週日 20:00 TST  | 每週日 + 手動觸發  |
| daily.yml  | `30 10 * * 1-5`   | 平日 18:30 TST  | 週一～五 + 手動觸發 |

---

## 運作流程

### 週報（weekly_job.py）

```
1. 載入 0050 清單 → 寫入 stock_universe
2. fetch_price()         → yfinance，90 天，計算 MA5/20/60
3. fetch_institutional() → TWSE，30 交易日，計算連買/賣天數
4. fetch_margin()        → TWSE，45 交易日，計算融資變化率
5. fetch_revenue()       → FinMind（免費嘗試），失敗跳過
6. fetch_income()        → yfinance quarterly_income_stmt
7. fetch_balance_sheet() → yfinance quarterly_balance_sheet
8. fetch_cashflow()      → yfinance quarterly_cashflow
9. fetch_shareholding()  → FinMind（免費嘗試），失敗跳過
10. fetch_valuation()    → yfinance ticker.info
11. compute_all_scores() → 硬性門檻 + 四維度評分
12. upsert weekly_scores → 存入 Supabase
13. send_weekly_report() → Telegram 推送 Top 10 詳細版
```

### 日報（daily_job.py）

```
1. 從 weekly_scores 取最新週的 Top 10 watchlist
2. fetch_price()         → yfinance，65 天（夠算 MA60）
3. fetch_institutional() → TWSE，30 交易日
4. fetch_margin()        → TWSE，30 交易日
5. 組合各股今日資料
6. send_daily_report()   → Telegram 推送籌碼快報
```

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
🔥 外資 + 投信同步買超
  • 台積電 (2330): 外資 +8,500張（連買7日）...
📈 今日收盤
  ✅ 多頭 2330 920.0 ▲1.8% | 量 28,500K張
```

---

## 常見問題與解法

| 問題 | 原因 | 解法 |
|------|------|------|
| FinMind 400 "register" | 需付費訂閱 | 已自動跳過，不影響主流程 |
| yfinance YFRateLimitError | 批次太大 | 已改為分批 10 支，間隔 3 秒 |
| TWSE API 空回應 | 假日或非交易日 | 自動跳過該日，繼續下一天 |
| watchlist 為空 | 尚未執行過週報 | 先手動觸發週報 workflow |
| Telegram 未收到訊息 | TELEGRAM_CHAT_ID 未設或錯誤 | 確認 Secret 設定 |

---

## 股票宇宙更新方式

0050 成分股每季調整。如需更新，修改 `src/universe.py` 的 `TAIWAN_50` 清單：

```python
TAIWAN_50: list[tuple[str, str]] = [
    ("2330", "台積電"),
    ("2317", "鴻海"),
    # ...
]
```

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
TOP_N = 10  # 每週推送幾支
```
