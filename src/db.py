"""Supabase 客戶端封裝，提供 upsert 與查詢操作。"""
import math
import logging
from supabase import create_client, Client
from src.config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def _sanitize(rows: list[dict]) -> list[dict]:
    """將 NaN / Inf 轉成 None，避免 Supabase JSON 解析錯誤。"""
    def clean(v):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v
    return [{k: clean(v) for k, v in row.items()} for row in rows]


def upsert(table: str, rows: list[dict], on_conflict: str = "") -> None:
    """批次 upsert，空資料直接跳過。"""
    if not rows:
        return
    rows = _sanitize(rows)
    try:
        q = get_client().table(table).upsert(rows)
        if on_conflict:
            q = q  # supabase-py 自動依 PRIMARY KEY / UNIQUE 做 upsert
        q.execute()
        logger.info("upsert %s: %d 筆", table, len(rows))
    except Exception as exc:
        logger.error("upsert %s 失敗: %s", table, exc)


def select(table: str, filters: dict | None = None,
           columns: str = "*", limit: int = 0,
           order_by: str = "", desc: bool = False) -> list[dict]:
    """查詢資料表，filters 為 {column: value} 的 eq 條件。
    order_by 指定排序欄位；desc=True 則降冪排列。
    """
    try:
        q = get_client().table(table).select(columns)
        for col, val in (filters or {}).items():
            q = q.eq(col, val)
        if order_by:
            q = q.order(order_by, desc=desc)
        if limit:
            q = q.limit(limit)
        return q.execute().data or []
    except Exception as exc:
        logger.error("select %s 失敗: %s", table, exc)
        return []
