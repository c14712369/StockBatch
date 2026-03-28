# StockBatch 系統架構文件

**版本：** 1.0.0
**日期：** 2026-03-28

---

## 系統概覽

StockBatch 是一個純 Google Apps Script 驅動的自動化系統，不需要伺服器、不需要 CI/CD。每個交易日早上，由 GAS 時間觸發器自動啟動，向 Gemini API 發出台股掃描請求，並將結果推送至 Discord。

```
GAS 時間觸發器（每天 08:00–09:00）
        │
        ▼
   main.gs：runStockScan()
        │
        ├─ 週末判斷（週六日 return）
        │
        ├─ gemini.gs：getLatestGeminiModel()
        │       └─ GET /v1beta/models → 篩選最新 gemini-*-pro 版本
        │
        ├─ gemini.gs：callGemini()
        │       └─ POST /v1beta/models/{id}:generateContent
        │              └─ tools: [{ google_search: {} }]（Search Grounding）
        │
        ├─ gemini.gs：parseResponse()
        │       └─ 按段落標題 regex 切割成陣列
        │
        └─ discord.gs：sendToDiscord()
                └─ 逐則 POST Discord Webhook（間隔 500ms）
```

---

## 元件說明

### `gas/main.gs`

**職責：** 系統入口、流程控制、觸發器管理

| 函式 | 說明 |
|------|------|
| `setupTrigger()` | 設定每日時間觸發器（手動執行一次） |
| `doGet(e)` | HTTP GET endpoint，驗證 secret 後呼叫 runStockScan（備用） |
| `doPost(e)` | HTTP POST endpoint，接收 sync-prompt.ts 推送的 prompt 並存入 PropertiesService |
| `runStockScan()` | 主流程：週末判斷 → 取模型 → 呼叫 Gemini → 解析 → 發送 Discord |

**錯誤處理：** 任何例外都由 catch 攔截，透過 `sendError()` 發送錯誤訊息至 Discord。

---

### `gas/gemini.gs`

**職責：** Gemini API 整合

| 函式 | 說明 |
|------|------|
| `getLatestGeminiModel(apiKey)` | 呼叫 List Models API，自動選最新的 gemini-*-pro（含 preview） |
| `extractVersion(name)` | 從 model 名稱解析版本號（major × 100 + minor），用於排序 |
| `callGemini(prompt, modelId, apiKey)` | 送出 generateContent 請求，啟用 google_search grounding |
| `parseResponse(text)` | 以 regex 按段落標題切割回應文字，超過 2000 字元再按換行切割 |

**模型選擇邏輯：**
1. 取得所有支援 `generateContent` 的 gemini 模型
2. 篩選名稱含 `pro` 的版本
3. 按版本號降序排列（`gemini-2.5` > `gemini-2.0` > `gemini-1.5`）
4. 回傳最新版本的 model name

---

### `gas/discord.gs`

**職責：** Discord Webhook 發送

| 函式 | 說明 |
|------|------|
| `sendToDiscord(segments, webhookUrl, modelId)` | 發送標頭 + 所有段落，每則間隔 500ms |
| `sendError(errorMessage, webhookUrl)` | 發送格式化錯誤通知 |
| `splitMessage(text, maxLength)` | 超過字元限制時按換行切割 |

**Discord 限制：** 每則訊息最多 2000 字元。標頭固定格式：
```
📊 台股開盤前掃描｜{日期}
使用模型：{modelId}
━━━━━━━━━━━━━━━━━
```

---

### `scripts/sync-prompt.ts`

**職責：** 本地 Prompt 推送工具

讀取 `prompt/prompt.md`，透過 HTTP POST 發送至 GAS `doPost` endpoint，GAS 驗證 secret 後將 prompt 存入 `PropertiesService['STOCK_SCAN_PROMPT']`。

**執行：** `npm run sync`

---

### `prompt/prompt.md`

**職責：** 台股掃描 Prompt 內容

四步結構：
1. **環境偵測** — 日期確認、主流題材偵測、大盤基調、國際指標對應
2. **數據獲取** — 三大法人盤後數據、個股技術數據、族群強度
3. **分析任務** — 強勢鎖碼掃描（任務一）、全市場潛力標的掃描（任務二）、攻擊盤掃描（任務三）、明日前五強（任務四）
4. **數據完整度自檢** — 輸出完整度百分比與警示

---

## 資料流

```
本地                          GAS PropertiesService            外部服務
──────                        ─────────────────────            ────────
prompt/prompt.md
    │ npm run sync
    ▼
scripts/sync-prompt.ts ──POST──▶ STOCK_SCAN_PROMPT
.env (GAS_WEBHOOK_URL)
.env (GAS_SYNC_SECRET)

                              GEMINI_API_KEY ──────────────▶ Gemini API
                              STOCK_SCAN_PROMPT ───────────▶ Gemini API
                              DISCORD_WEBHOOK_URL ─────────▶ Discord Webhook
                              CRON_SECRET（驗證 doGet）
```

---

## 機密管理

| 機密 | 存放位置 | 是否進版控 |
|------|---------|----------|
| `GEMINI_API_KEY` | GAS PropertiesService | ❌ |
| `DISCORD_WEBHOOK_URL` | GAS PropertiesService | ❌ |
| `CRON_SECRET` | GAS PropertiesService | ❌ |
| `STOCK_SCAN_PROMPT` | GAS PropertiesService | ✅（原始檔在 prompt/prompt.md） |
| `GAS_WEBHOOK_URL` | 本地 `.env` | ❌ |
| `GAS_SYNC_SECRET` | 本地 `.env` | ❌ |

---

## 成本估算

| 服務 | 用量 | 費用 |
|------|------|------|
| Gemini API | ~22 次/月（每個交易日 1 次） | 依方案，免費額度通常足夠 |
| Google Search Grounding | ~22 次/月 | 依 Google AI 方案計費 |
| Google Apps Script | 全部 | 免費 |
| Discord Webhook | 全部 | 免費 |

---

## 限制與注意事項

- GAS 時間觸發器誤差約 ±15 分鐘，觸發時間落在 08:00–09:00 之間
- GAS 單次執行上限 6 分鐘，Gemini Search Grounding 通常 30–60 秒內完成
- Discord Webhook rate limit：每秒約 5 次，透過 500ms 間隔規避
- 國定假日由 Prompt 第一步讓 Gemini 自行判斷，GAS 只做週六日過濾
