/**
 * 設定每日時間觸發器（只需手動執行一次）
 * 觸發時間：每天 8–9 點之間（GAS 內建觸發器誤差約 ±15 分鐘）
 */
function setupTrigger() {
  // 刪除舊觸發器，避免重複
  ScriptApp.getProjectTriggers().forEach(function(t) {
    ScriptApp.deleteTrigger(t);
  });

  ScriptApp.newTrigger('runStockScan')
    .timeBased()
    .everyDays(1)
    .atHour(8)
    .inTimezone('Asia/Taipei')
    .create();

  Logger.log('✅ 觸發器已設定：每天 Asia/Taipei 8–9 點執行 runStockScan');
}

/**
 * cron-job.org 呼叫此 endpoint 觸發每日掃描（備用，可不用）
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
