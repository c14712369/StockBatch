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
