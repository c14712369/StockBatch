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
