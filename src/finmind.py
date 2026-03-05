"""FinMind API 客戶端，含 retry 與統一錯誤處理。支援多把 Token 輪轉。"""
import time
import logging
import requests
from src.config import FINMIND_TOKENS

logger = logging.getLogger(__name__)

BASE_URL = "https://api.finmindtrade.com/api/v4/data"

# 用於追蹤當前使用的 Token 索引
_current_token_idx = 0

def get_current_token() -> str:
    global _current_token_idx
    if not FINMIND_TOKENS:
        return ""
    return FINMIND_TOKENS[_current_token_idx % len(FINMIND_TOKENS)]

def switch_to_next_token() -> str:
    global _current_token_idx
    if not FINMIND_TOKENS:
        return ""
    _current_token_idx += 1
    new_token = FINMIND_TOKENS[_current_token_idx % len(FINMIND_TOKENS)]
    logger.info("FinMind 切換至第 %d 把 Token", (_current_token_idx % len(FINMIND_TOKENS)) + 1)
    return new_token

def fetch(dataset: str, start_date: str, end_date: str = "",
          stock_id: str = "") -> list[dict]:
    """
    呼叫 FinMind API，回傳 list of dict。
    若不傳 stock_id 則抓全市場（用於批次處理，減少 API 呼叫次數）。
    支援多把 KEY 自動輪流（Rate Limit 400 registered 時切換）。
    """
    params = {
        "dataset": dataset,
        "start_date": start_date,
    }
    if end_date:
        params["end_date"] = end_date
    if stock_id:
        params["data_id"] = stock_id

    num_tokens = max(1, len(FINMIND_TOKENS))
    max_retries_per_token = 3
    # 每把 Token 最多重試 max_retries_per_token 次，試完所有 Token 後放棄
    tokens_tried = set()

    for attempt in range(max_retries_per_token * num_tokens):
        current_token = get_current_token()
        if current_token:
            params["token"] = current_token

        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") != 200:
                msg = body.get("msg", "unknown error")
                # 判斷是否為「非會員/免費次數用盡」
                if "register" in msg.lower():
                    logger.warning("FinMind %s 需付費訂閱或達上限: %s", dataset, msg)
                    tokens_tried.add(current_token)
                    if len(FINMIND_TOKENS) > 1 and len(tokens_tried) < num_tokens:
                        switch_to_next_token()
                        continue  # 立即切換至下一把 Token 重試
                    return []  # 所有 Token 均已達上限
                logger.warning("FinMind %s 回傳非 200: %s", dataset, msg)
                return []
            return body.get("data", [])
        except requests.RequestException as exc:
            # 402 = 付費功能，直接放棄（換 Token 也無用）
            status_code = exc.response.status_code if exc.response is not None else 0
            if status_code == 402:
                logger.warning("FinMind %s 需付費訂閱(HTTP 402)，跳過", dataset)
                return []

            body_text = ""
            try:
                body_text = exc.response.text[:200] if exc.response is not None else ""
            except Exception:
                pass

            logger.warning("FinMind 第 %d 次請求失敗 (%s): %s | body: %s",
                           attempt + 1, dataset, exc, body_text)

            retry_in_token = attempt % max_retries_per_token
            if retry_in_token < max_retries_per_token - 1:
                time.sleep(2 ** retry_in_token)
            elif len(FINMIND_TOKENS) > 1 and len(tokens_tried) < num_tokens:
                tokens_tried.add(current_token)
                switch_to_next_token()

    return []
