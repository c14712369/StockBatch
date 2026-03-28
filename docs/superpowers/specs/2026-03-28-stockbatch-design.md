# StockBatch 設計規格

**日期：** 2026-03-28
**專案路徑：** `C:\Users\c1471\Desktop\Projects\StockBatch`

---

## 目標

平日早上 08:30，自動將台股開盤前掃描 Prompt 發送給 Gemini API（含 Google Search Grounding），將回傳結果依任務分段推送至指定 Discord Channel。

---

## 架構總覽

```
StockBatch/（本地版控）
├── prompt/
│   └── prompt.md                ← 台股掃描完整 Prompt
├── gas/
│   ├── main.gs                  ← 主流程、觸發器設定、假日判斷
│   ├── gemini.gs                ← Gemini API 呼叫與最新 model 自動選擇
│   └── discord.gs               ← Discord Webhook 分段發送
├── scripts/
│   └── sync-prompt.ts           ← 將 prompt.md 推送至 GAS PropertiesService
├── .env                         ← GAS_WEBHOOK_URL、GAS_SYNC_SECRET（不進版控）
├── .env.example
├── package.json
└── .gitignore

GAS PropertiesService（不進版控）：
  GEMINI_API_KEY
  DISCORD_WEBHOOK_URL
  STOCK_SCAN_PROMPT              ← 由 sync-prompt.ts 推送
```

**執行鏈：**
GAS 時間觸發（平日 ~08:30）→ `main.gs` 判斷週六週日 → 取最新 Gemini model → 呼叫 Gemini API（Search Grounding）→ 解析回應分 5 段 → Discord Webhook 依序發送

---

## GAS 腳本設計

### `main.gs`

- `setupTrigger()`：執行一次，建立每日 `atHour(8).nearMinute(30)` 觸發器
- `runStockScan()`：主流程
  1. 判斷今日 `getDay()` 是否為 1–5，否則 return
  2. 從 PropertiesService 讀取 `GEMINI_API_KEY`、`DISCORD_WEBHOOK_URL`、`STOCK_SCAN_PROMPT`
  3. 呼叫 `getLatestGeminiModel(apiKey)` 取得 model ID
  4. 呼叫 `callGemini(prompt, modelId, apiKey)` 取得回應文字
  5. 呼叫 `parseResponse(text)` 拆成段落陣列
  6. 呼叫 `sendToDiscord(segments, webhookUrl)`
  7. 任何步驟丟出例外 → catch → 發送錯誤通知至 Discord

### `gemini.gs`

- `getLatestGeminiModel(apiKey)`
  - `GET https://generativelanguage.googleapis.com/v1beta/models?key={apiKey}`
  - 篩選：`supportedGenerationMethods` 含 `generateContent`，名稱含 `gemini`
  - 含 preview 版本，按版本號降序排列（`gemini-3.1` > `gemini-3.0` > `gemini-2.5`）
  - 優先選名稱含 `pro` 的最新版
  - 回傳 model name 字串

- `callGemini(prompt, modelId, apiKey)`
  - `POST https://generativelanguage.googleapis.com/v1beta/models/{modelId}:generateContent?key={apiKey}`
  - Payload：
    ```json
    {
      "contents": [{ "parts": [{ "text": "<prompt>" }] }],
      "tools": [{ "google_search": {} }]
    }
    ```
  - 回傳 `candidates[0].content.parts[0].text`

- `parseResponse(text)`
  - 用 regex 偵測以下標題切段：`第一步|環境偵測`、`任務一`、`任務二`、`任務三`、`任務四`、`數據完整度自檢`
  - 若某段 > 2000 字元，按換行符再切割
  - 回傳字串陣列

### `discord.gs`

- `sendToDiscord(segments, webhookUrl)`
  - 第一則固定加 header：
    ```
    📊 台股開盤前掃描｜{今日日期}
    使用模型：{modelId}
    ━━━━━━━━━━━━━━━━━
    ```
  - 每則 `POST` 至 webhook，間隔 500ms 避免 rate limit
  - 每則長度上限 2000 字元

- `sendError(message, webhookUrl)`
  - 發送格式：
    ```
    ❌ StockBatch 執行失敗
    時間：{timestamp}
    錯誤：{message}
    ```

---

## sync-prompt 腳本

**觸發方式：** `npm run sync`

**流程：**
1. 讀取 `prompt/prompt.md`
2. `POST` 至 GAS doPost endpoint，帶 `{ secret, prompt }` JSON body
3. GAS 驗證 secret → 存入 `PropertiesService[STOCK_SCAN_PROMPT]`
4. 成功輸出 `✅ Prompt 已同步`，失敗輸出錯誤

**`package.json` scripts：**
```json
{
  "scripts": {
    "sync": "tsx scripts/sync-prompt.ts"
  }
}
```

---

## 觸發器與時間精度

- GAS `nearMinute(30)` 觸發誤差約 ±15 分鐘（實際約 08:25–08:45）
- 若需精確 08:30，可改用 cron-job.org 呼叫 GAS doGet endpoint（同 FoodBatch / AvBatch 模式）
- 國定假日：GAS 只判斷週六週日，其餘假日交由 Prompt 第一步讓 Gemini 自行判斷並回應「今日休市」

---

## 成本考量

- **Gemini API：** 自動選最新 pro 模型（含 preview），功能優先、成本盡量壓低
- **Google Search Grounding：** 依方案計費，約 22 次/月，用量極低
- **GAS：** 完全免費
- **Discord Webhook：** 完全免費

---

## 環境變數

| 變數 | 存放位置 | 說明 |
|------|---------|------|
| `GEMINI_API_KEY` | GAS PropertiesService | Gemini API 金鑰 |
| `DISCORD_WEBHOOK_URL` | GAS PropertiesService | Discord Webhook URL |
| `STOCK_SCAN_PROMPT` | GAS PropertiesService | 台股掃描 Prompt 內容 |
| `GAS_WEBHOOK_URL` | 本地 `.env` | sync-prompt 目標 GAS URL |
| `GAS_SYNC_SECRET` | 本地 `.env` | sync-prompt 驗證 token |

---

## 初始設定步驟

1. 建立 GAS 專案，新增 `main.gs`、`gemini.gs`、`discord.gs`
2. 在 GAS 設定 PropertiesService（API key、Discord webhook）
3. 執行 `setupTrigger()` 一次
4. 本地 `npm run sync` 推送 prompt
5. 手動執行 `runStockScan()` 驗證全流程
