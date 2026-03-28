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
