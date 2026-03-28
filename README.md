# StockBatch

平日每天早上自動發送台股開盤前掃描 Prompt 給 Gemini API（含 Google Search Grounding），將分析結果分段推送至指定 Discord 頻道。

---

## 功能

- 每個交易日 08:00–09:00 自動觸發（GAS 時間觸發器）
- 自動選用當前最新的 Gemini Pro 模型（含 preview 版本）
- 透過 Google Search Grounding 搜尋即時市場數據
- 分段發送至 Discord（每則 ≤ 2000 字元，間隔 500ms）
- 週六、週日自動跳過
- 國定假日由 Gemini 自行判斷並回應「今日休市」

---

## 架構

```
StockBatch/
├── gas/
│   ├── main.gs        ← 主流程、觸發器設定、doGet/doPost endpoint
│   ├── gemini.gs      ← Gemini API 呼叫、自動選最新 model
│   └── discord.gs     ← Discord Webhook 分段發送
├── scripts/
│   └── sync-prompt.ts ← 將 prompt.md 推送至 GAS PropertiesService
├── prompt/
│   └── prompt.md      ← 台股掃描完整 Prompt（可修改後 npm run sync 更新）
├── .env               ← 本地環境變數（不進版控）
└── .env.example       ← 環境變數範本
```

---

## 快速開始

### 1. 建立 GAS 專案

1. 前往 [Google Apps Script](https://script.google.com)，建立新專案
2. 將 `gas/main.gs`、`gas/gemini.gs`、`gas/discord.gs` 內容分別貼入對應的 `.gs` 檔案
3. 在 GAS「專案設定」→ 時區設為 `(GMT+08:00) Asia/Taipei`

### 2. 設定 GAS PropertiesService

在 GAS「專案設定」→「指令碼屬性」新增以下四個屬性：

| 屬性名稱 | 說明 |
|---------|------|
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/apikey) 取得 |
| `DISCORD_WEBHOOK_URL` | Discord 頻道設定 → 整合 → Webhook → 複製網址 |
| `CRON_SECRET` | 自訂一個隨機字串作為驗證 token |
| `STOCK_SCAN_PROMPT` | 由 `npm run sync` 自動寫入，初次手動貼入 `prompt/prompt.md` 內容亦可 |

### 3. 部署 GAS 為網頁應用程式

GAS 編輯器 → 部署 → 新增部署 → 類型選「網頁應用程式」
- 執行身分：我（本人）
- 誰可以存取：所有人

記錄部署後的網址（格式：`https://script.google.com/macros/s/YOUR_ID/exec`）

### 4. 本地環境設定

```bash
cp .env.example .env
```

編輯 `.env`：

```
GAS_WEBHOOK_URL=https://script.google.com/macros/s/YOUR_SCRIPT_ID/exec
GAS_SYNC_SECRET=你在第2步設定的 CRON_SECRET 值
```

### 5. 安裝依賴並推送 Prompt

```bash
npm install
npm run sync
```

成功輸出：
```
📤 推送 prompt（XXXX 字）至 GAS...
✅ Prompt 已同步至 GAS PropertiesService
```

### 6. 設定每日觸發器

在 GAS 編輯器，函式下拉選 `setupTrigger`，按執行，授權後完成。
觸發器每天 08:00–09:00（台北時間）自動執行一次。

---

## 日常使用

### 更新 Prompt

修改 `prompt/prompt.md` 後執行：

```bash
npm run sync
```

下次觸發器執行時即自動使用新 Prompt。

### 手動觸發測試

在 GAS 編輯器，函式下拉選 `runStockScan`，按執行即可。

---

## 環境變數

| 變數 | 存放位置 | 說明 |
|------|---------|------|
| `GEMINI_API_KEY` | GAS PropertiesService | Gemini API 金鑰 |
| `DISCORD_WEBHOOK_URL` | GAS PropertiesService | Discord Webhook 網址 |
| `STOCK_SCAN_PROMPT` | GAS PropertiesService | 掃描 Prompt（由 sync 推送） |
| `CRON_SECRET` | GAS PropertiesService | 驗證 token |
| `GAS_WEBHOOK_URL` | 本地 `.env` | GAS 部署網址 |
| `GAS_SYNC_SECRET` | 本地 `.env` | 同上 CRON_SECRET，供 sync-prompt.ts 使用 |

---

## 注意事項

- **GAS 時間觸發器誤差約 ±15 分鐘**，實際觸發時間在 08:00–09:00 之間隨機
- Gemini Search Grounding 每次約耗時 30–60 秒
- Discord 每則訊息上限 2000 字元，超過自動按換行切割
- 免責聲明：本工具輸出僅為技術與籌碼邏輯推演，非投資建議，所有操作風險由使用者自行承擔
