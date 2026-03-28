# StockBatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 平日 08:30 自動呼叫 Gemini API（含 Search Grounding），將台股開盤前掃描結果分段推送至 Discord Channel。

**Architecture:** 純 Google Apps Script 執行，cron-job.org 在 08:30 整觸發 GAS doGet endpoint。本地 StockBatch 專案僅做版控與 sync-prompt 工具腳本。

**Tech Stack:** Google Apps Script (GAS)、Gemini Generative Language API v1beta、Discord Webhook、TypeScript (tsx)、dotenv

---

## 檔案結構

| 檔案 | 職責 |
|------|------|
| `prompt/prompt.md` | 台股開盤前掃描完整 Prompt |
| `gas/discord.gs` | Discord Webhook 發送（sendToDiscord、sendError） |
| `gas/gemini.gs` | Gemini model 自動選擇、API 呼叫、回應解析 |
| `gas/main.gs` | 主流程（doGet、doPost、runStockScan） |
| `scripts/sync-prompt.ts` | 讀取 prompt.md 並 POST 至 GAS PropertiesService |
| `.env.example` | 環境變數範本 |
| `package.json` | 只含 tsx + dotenv |

---

## Task 1: 專案初始化

**Files:**
- Create: `package.json`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `prompt/prompt.md`

- [ ] **Step 1: 建立 package.json**

```json
{
  "name": "stockbatch",
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "sync": "tsx scripts/sync-prompt.ts"
  },
  "dependencies": {
    "dotenv": "^16.0.0"
  },
  "devDependencies": {
    "tsx": "^4.0.0",
    "@types/node": "^20.0.0"
  }
}
```

- [ ] **Step 2: 建立 .gitignore**

```
.env
node_modules/
```

- [ ] **Step 3: 建立 .env.example**

```
GAS_WEBHOOK_URL=https://script.google.com/macros/s/YOUR_SCRIPT_ID/exec
GAS_SYNC_SECRET=your_sync_secret_here
```

- [ ] **Step 4: 複製 prompt 檔案**

將 `C:\Users\c1471\Downloads\台股開盤前掃描_完整Prompt.md` 複製到 `prompt/prompt.md`。

- [ ] **Step 5: 安裝相依套件**

```bash
cd C:\Users\c1471\Desktop\Projects\StockBatch
npm install
```

Expected: `node_modules/` 建立，無錯誤

- [ ] **Step 6: Commit**

```bash
git add package.json .gitignore .env.example prompt/prompt.md
git commit -m "chore: 初始化專案結構與 Prompt"
```

---

## Task 2: discord.gs

**Files:**
- Create: `gas/discord.gs`

- [ ] **Step 1: 建立 gas/discord.gs**

```javascript
/**
 * 將文字陣列依序發送到 Discord Webhook
 * @param {string[]} segments - 要發送的訊息段落陣列
 * @param {string} webhookUrl - Discord Webhook URL
 * @param {string} modelId - 使用的 Gemini model 名稱（顯示在 header）
 */
function sendToDiscord(segments, webhookUrl, modelId) {
  const today = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy-MM-dd');
  const header = `📊 台股開盤前掃描｜${today}\n使用模型：${modelId}\n━━━━━━━━━━━━━━━━━`;

  const allMessages = [header, ...segments];

  for (const message of allMessages) {
    const chunks = splitMessage(message, 2000);
    for (const chunk of chunks) {
      UrlFetchApp.fetch(webhookUrl, {
        method: 'post',
        contentType: 'application/json',
        payload: JSON.stringify({ content: chunk }),
        muteHttpExceptions: true,
      });
      Utilities.sleep(500);
    }
  }
}

/**
 * 發送錯誤通知到 Discord
 * @param {string} errorMessage - 錯誤訊息
 * @param {string} webhookUrl - Discord Webhook URL
 */
function sendError(errorMessage, webhookUrl) {
  const timestamp = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy-MM-dd HH:mm');
  const content = `❌ StockBatch 執行失敗\n時間：${timestamp}\n錯誤：${errorMessage}`;
  UrlFetchApp.fetch(webhookUrl, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({ content }),
    muteHttpExceptions: true,
  });
}

/**
 * 將超過 maxLength 的字串按換行符切割
 * @param {string} text
 * @param {number} maxLength
 * @returns {string[]}
 */
function splitMessage(text, maxLength) {
  if (text.length <= maxLength) return [text];

  const lines = text.split('\n');
  const chunks = [];
  let current = '';

  for (const line of lines) {
    if ((current + '\n' + line).length > maxLength) {
      if (current) chunks.push(current.trim());
      current = line;
    } else {
      current = current ? current + '\n' + line : line;
    }
  }
  if (current) chunks.push(current.trim());
  return chunks;
}
```

- [ ] **Step 2: 手動測試（在 GAS 編輯器執行）**

在 GAS 編輯器新增並執行此測試函式，確認 Discord 有收到訊息：

```javascript
function testDiscord() {
  const props = PropertiesService.getScriptProperties();
  const webhookUrl = props.getProperty('DISCORD_WEBHOOK_URL');
  sendToDiscord(['測試訊息：Task 2 discord.gs 正常運作'], webhookUrl, 'gemini-test');
}
```

Expected: Discord channel 出現兩則訊息（header + 測試訊息）

- [ ] **Step 3: Commit**

```bash
git add gas/discord.gs
git commit -m "feat: 新增 discord.gs（sendToDiscord、sendError、splitMessage）"
```

---

## Task 3: gemini.gs — model 選擇與 API 呼叫

**Files:**
- Create: `gas/gemini.gs`

- [ ] **Step 1: 建立 gas/gemini.gs**

```javascript
/**
 * 從 Gemini List Models API 取得最新支援 generateContent 的 pro 模型名稱
 * 含 preview 版本，按版本號降序排列
 * @param {string} apiKey
 * @returns {string} model name (e.g. "gemini-3.1-pro-preview-05-06")
 */
function getLatestGeminiModel(apiKey) {
  const url = `https://generativelanguage.googleapis.com/v1beta/models?key=${apiKey}`;
  const response = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  const data = JSON.parse(response.getContentText());

  if (!data.models) throw new Error('List Models API 無回應：' + response.getContentText());

  const candidates = data.models.filter(m =>
    m.name.includes('gemini') &&
    m.name.includes('pro') &&
    m.supportedGenerationMethods &&
    m.supportedGenerationMethods.includes('generateContent')
  );

  if (candidates.length === 0) throw new Error('找不到支援 generateContent 的 gemini pro 模型');

  candidates.sort((a, b) => extractVersion(b.name) - extractVersion(a.name));

  // 回傳 name 去掉 "models/" 前綴
  return candidates[0].name.replace('models/', '');
}

/**
 * 從 model name 提取版本號（用於排序）
 * gemini-3.1-pro-preview → 3.1 → 310
 * gemini-2.5-pro → 2.5 → 250
 * @param {string} name
 * @returns {number}
 */
function extractVersion(name) {
  const match = name.match(/gemini-(\d+)\.(\d+)/);
  if (!match) return 0;
  return parseInt(match[1]) * 100 + parseInt(match[2]);
}

/**
 * 呼叫 Gemini API（含 Google Search Grounding）
 * @param {string} prompt
 * @param {string} modelId
 * @param {string} apiKey
 * @returns {string} Gemini 回傳的文字內容
 */
function callGemini(prompt, modelId, apiKey) {
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${modelId}:generateContent?key=${apiKey}`;
  const payload = {
    contents: [{ parts: [{ text: prompt }] }],
    tools: [{ google_search: {} }],
  };

  const response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  });

  const data = JSON.parse(response.getContentText());

  if (data.error) throw new Error(`Gemini API 錯誤：${data.error.message}`);
  if (!data.candidates || !data.candidates[0]) throw new Error('Gemini 回應格式異常：' + response.getContentText());

  return data.candidates[0].content.parts[0].text;
}

/**
 * 將 Gemini 回應文字依任務段落切割
 * @param {string} text
 * @returns {string[]} 最多 6 段（環境偵測、任務一~四、自檢表）
 */
function parseResponse(text) {
  // 偵測段落分界點的 regex
  const sectionPattern = /(第[一二三四]步|環境偵測|任務[一二三四]|數據完整度自檢)/;

  const lines = text.split('\n');
  const segments = [];
  let current = [];

  for (const line of lines) {
    if (sectionPattern.test(line) && current.length > 0) {
      segments.push(current.join('\n').trim());
      current = [line];
    } else {
      current.push(line);
    }
  }
  if (current.length > 0) segments.push(current.join('\n').trim());

  // 過濾空段落
  return segments.filter(s => s.length > 0);
}
```

- [ ] **Step 2: 手動測試 getLatestGeminiModel（在 GAS 編輯器執行）**

```javascript
function testGetLatestModel() {
  const apiKey = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY');
  const model = getLatestGeminiModel(apiKey);
  Logger.log('最新模型：' + model);
}
```

Expected: 執行記錄顯示 `最新模型：gemini-X.X-pro-...`，無錯誤

- [ ] **Step 3: 手動測試 parseResponse（在 GAS 編輯器執行）**

```javascript
function testParseResponse() {
  const sampleText = `## 第一步：環境偵測
今日為交易日。

### 任務一｜強勢鎖碼掃描
標的 A：投信連買 5 日。

### 任務二｜題材族群輪動分析
AI 伺服器族群領漲。

### 任務三｜當日攻擊盤掃描
今日無攻擊盤標的。

### 任務四｜明日開盤前五強
排名第一：標的 B。

數據完整度自檢
整體完整度：85%`;

  const segments = parseResponse(sampleText);
  Logger.log('段落數：' + segments.length);
  segments.forEach((s, i) => Logger.log(`段落 ${i + 1}（${s.length} 字）：${s.substring(0, 50)}...`));
}
```

Expected: 執行記錄顯示 6 個段落，每段開頭符合預期

- [ ] **Step 4: Commit**

```bash
git add gas/gemini.gs
git commit -m "feat: 新增 gemini.gs（自動選 model、callGemini、parseResponse）"
```

---

## Task 4: main.gs — 主流程

**Files:**
- Create: `gas/main.gs`

- [ ] **Step 1: 建立 gas/main.gs**

```javascript
/**
 * cron-job.org 呼叫此 endpoint 觸發每日掃描
 * URL 帶上 ?secret=YOUR_CRON_SECRET
 */
function doGet(e) {
  const props = PropertiesService.getScriptProperties();
  const expectedSecret = props.getProperty('CRON_SECRET');
  const receivedSecret = e && e.parameter && e.parameter.secret;

  if (receivedSecret !== expectedSecret) {
    return ContentService.createTextOutput('Unauthorized').setMimeType(ContentService.MimeType.TEXT);
  }

  runStockScan();
  return ContentService.createTextOutput('OK').setMimeType(ContentService.MimeType.TEXT);
}

/**
 * sync-prompt.ts 呼叫此 endpoint 更新 PropertiesService 中的 prompt
 * Body: { secret: string, prompt: string }
 */
function doPost(e) {
  const data = JSON.parse(e.postData.contents);
  const props = PropertiesService.getScriptProperties();
  const expectedSecret = props.getProperty('CRON_SECRET');

  if (data.secret !== expectedSecret) {
    return ContentService.createTextOutput('Unauthorized').setMimeType(ContentService.MimeType.TEXT);
  }

  props.setProperty('STOCK_SCAN_PROMPT', data.prompt);
  return ContentService.createTextOutput('OK').setMimeType(ContentService.MimeType.TEXT);
}

/**
 * 主流程：取得最新 model → 呼叫 Gemini → 解析 → 發送 Discord
 */
function runStockScan() {
  const today = new Date();
  const dayOfWeek = today.getDay(); // 0=日, 6=六

  // 週六、週日不執行
  if (dayOfWeek === 0 || dayOfWeek === 6) {
    Logger.log('今日為週末，跳過執行');
    return;
  }

  const props = PropertiesService.getScriptProperties();
  const apiKey = props.getProperty('GEMINI_API_KEY');
  const webhookUrl = props.getProperty('DISCORD_WEBHOOK_URL');
  const prompt = props.getProperty('STOCK_SCAN_PROMPT');

  if (!apiKey || !webhookUrl || !prompt) {
    const missing = [!apiKey && 'GEMINI_API_KEY', !webhookUrl && 'DISCORD_WEBHOOK_URL', !prompt && 'STOCK_SCAN_PROMPT'].filter(Boolean).join(', ');
    Logger.log('缺少必要設定：' + missing);
    if (webhookUrl) sendError('缺少必要設定：' + missing, webhookUrl);
    return;
  }

  try {
    Logger.log('取得最新 Gemini model...');
    const modelId = getLatestGeminiModel(apiKey);
    Logger.log('使用模型：' + modelId);

    Logger.log('呼叫 Gemini API...');
    const responseText = callGemini(prompt, modelId, apiKey);
    Logger.log('Gemini 回應長度：' + responseText.length);

    Logger.log('解析回應...');
    const segments = parseResponse(responseText);
    Logger.log('段落數：' + segments.length);

    Logger.log('發送至 Discord...');
    sendToDiscord(segments, webhookUrl, modelId);
    Logger.log('完成！');
  } catch (err) {
    Logger.log('執行失敗：' + err.message);
    sendError(err.message, webhookUrl);
  }
}
```

- [ ] **Step 2: 手動測試 runStockScan（在 GAS 編輯器執行）**

確認 PropertiesService 已設定（見 Task 5 Step 1），然後執行：

```javascript
// 直接在 GAS 編輯器選擇 runStockScan 並點「執行」
```

Expected:
- 執行記錄依序出現：`取得最新 Gemini model...` → `使用模型：gemini-X.X-pro-...` → `呼叫 Gemini API...` → `Gemini 回應長度：XXXX` → `段落數：X` → `發送至 Discord...` → `完成！`
- Discord channel 出現 header 訊息 + 各任務段落訊息

- [ ] **Step 3: Commit**

```bash
git add gas/main.gs
git commit -m "feat: 新增 main.gs（doGet、doPost、runStockScan）"
```

---

## Task 5: GAS 部署與 PropertiesService 設定

**Files:** 無（GAS 線上操作）

- [ ] **Step 1: 將 gas/*.gs 內容貼入 GAS 編輯器**

前往 [script.google.com](https://script.google.com) → 新增專案，命名為 `StockBatch`。

依序建立三個檔案，將本地 `gas/` 目錄對應檔案的**完整內容**貼入：
- 將預設的 `Code.gs` 重新命名為 `main` → 貼入 `gas/main.gs` 內容
- 新增檔案 `gemini` → 貼入 `gas/gemini.gs` 內容
- 新增檔案 `discord` → 貼入 `gas/discord.gs` 內容

- [ ] **Step 2: 在 GAS 設定 PropertiesService**

在 GAS 編輯器 → 專案設定 → 指令碼屬性，新增以下屬性：

| 屬性名稱 | 值 |
|---------|---|
| `GEMINI_API_KEY` | 你的 Gemini API Key |
| `DISCORD_WEBHOOK_URL` | 你的 Discord Webhook URL |
| `CRON_SECRET` | 自訂一個隨機字串（例如 `stockbatch_secret_2026`） |
| `STOCK_SCAN_PROMPT` | （先留空，Step 3 用 sync 推入） |

- [ ] **Step 3: 部署為 Web App**

GAS 編輯器 → 部署 → 新增部署作業：
- 類型：**網路應用程式**
- 執行身分：**我**
- 誰可以存取：**所有人**
- 點「部署」，複製 Web App URL

- [ ] **Step 4: 複製 Web App URL 到本地 .env**

```bash
# 建立 .env 檔（不進版控）
GAS_WEBHOOK_URL=https://script.google.com/macros/s/YOUR_SCRIPT_ID/exec
GAS_SYNC_SECRET=stockbatch_secret_2026
```

---

## Task 6: sync-prompt.ts

**Files:**
- Create: `scripts/sync-prompt.ts`

- [ ] **Step 1: 建立 scripts/sync-prompt.ts**

```typescript
import 'dotenv/config';
import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

async function syncPrompt() {
  const webhookUrl = process.env.GAS_WEBHOOK_URL;
  const secret = process.env.GAS_SYNC_SECRET;

  if (!webhookUrl || !secret) {
    console.error('❌ 缺少環境變數：GAS_WEBHOOK_URL 或 GAS_SYNC_SECRET');
    process.exit(1);
  }

  const promptPath = join(__dirname, '../prompt/prompt.md');
  const prompt = readFileSync(promptPath, 'utf-8');

  console.log(`📤 推送 prompt（${prompt.length} 字）至 GAS...`);

  const response = await fetch(webhookUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ secret, prompt }),
    redirect: 'follow',
  });

  const text = await response.text();

  if (text.trim() === 'OK') {
    console.log('✅ Prompt 已同步至 GAS PropertiesService');
  } else if (text.trim() === 'Unauthorized') {
    console.error('❌ Secret 驗證失敗，請確認 GAS_SYNC_SECRET 與 GAS PropertiesService 的 CRON_SECRET 一致');
    process.exit(1);
  } else {
    console.error('❌ 未預期的回應：', text);
    process.exit(1);
  }
}

syncPrompt();
```

- [ ] **Step 2: 執行 sync 測試**

```bash
npm run sync
```

Expected:
```
📤 推送 prompt（XXXX 字）至 GAS...
✅ Prompt 已同步至 GAS PropertiesService
```

確認 GAS PropertiesService 的 `STOCK_SCAN_PROMPT` 已有內容。

- [ ] **Step 3: Commit**

```bash
git add scripts/sync-prompt.ts
git commit -m "feat: 新增 sync-prompt.ts（推送 prompt 至 GAS PropertiesService）"
```

---

## Task 7: cron-job.org 設定

**Files:** 無（線上操作）

- [ ] **Step 1: 建立 cron-job.org 排程**

前往 [cron-job.org](https://cron-job.org) → 建立新 Cronjob：

| 設定項目 | 值 |
|---------|---|
| URL | `{GAS Web App URL}?secret={CRON_SECRET}` |
| Execution schedule | Custom: `30 8 * * 1,2,3,4,5` |
| Timezone | `Asia/Taipei` |
| Request method | GET |

- [ ] **Step 2: 手動觸發測試**

在 cron-job.org 頁面點「Run now」，確認：
- HTTP response code: 200
- Response body: `OK`
- Discord channel 出現完整掃描報告

- [ ] **Step 3: Commit 最終狀態**

```bash
git add .
git commit -m "docs: 完成 StockBatch 全部實作，cron-job.org 已設定"
```

---

## 初始設定順序總結

1. Task 1：本地專案初始化
2. Task 2–4：撰寫 GAS 腳本（gas/ 目錄）
3. Task 5：到 GAS 線上新增三個 `.gs` 檔案內容 → 部署 Web App
4. Task 5 Step 1：設定 PropertiesService（除 STOCK_SCAN_PROMPT 外）
5. Task 6：`npm run sync` 推送 prompt
6. Task 7：設定 cron-job.org

---

## Prompt 更新流程（日後維護）

```bash
# 1. 編輯 prompt/prompt.md
# 2. 推送至 GAS
npm run sync
# 3. 版控
git add prompt/prompt.md
git commit -m "prompt: 更新台股掃描 Prompt"
```
