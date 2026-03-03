"""FinMind API 客戶端，含 retry 與統一錯誤處理。"""
import time
import logging
import requests
from src.config import FINMIND_TOKEN

logger = logging.getLogger(__name__)

BASE_URL = "https://api.finmindtrade.com/api/v4/data"


def fetch(dataset: str, start_date: str, end_date: str = "",
          stock_id: str = "") -> list[dict]:
    """
    呼叫 FinMind API，回傳 list of dict。
    若不傳 stock_id 則抓全市場（用於批次處理，減少 API 呼叫次數）。
    """
    params = {
        "dataset": dataset,
        "start_date": start_date,
        "token": FINMIND_TOKEN,
    }
    if end_date:
        params["end_date"] = end_date
    if stock_id:
        params["data_id"] = stock_id

    for attempt in range(3):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") != 200:
                msg = body.get("msg", "unknown error")
                if "register" in msg.lower():
                    logger.warning("FinMind %s 需付費訂閱，跳過", dataset)
                    return []  # 不重試
                logger.warning("FinMind %s 回傳非 200: %s", dataset, msg)
                return []
            return body.get("data", [])
        except requests.RequestException as exc:
            # 402 = 付費功能，直接跳過不重試
            if exc.response is not None and exc.response.status_code == 402:
                logger.warning("FinMind %s 需付費訂閱，跳過", dataset)
                return []
            body = ""
            try:
                body = exc.response.text[:200] if exc.response is not None else ""
            except Exception:
                pass
            logger.warning("FinMind 第 %d 次請求失敗 (%s): %s | body: %s",
                           attempt + 1, dataset, exc, body)
            if attempt < 2:
                time.sleep(2 ** attempt)
    return []
