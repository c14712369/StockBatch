"""Supabase 客戶端封裝，提供 upsert 與查詢操作。"""
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


def upsert(table: str, rows: list[dict], on_conflict: str = "") -> None:
    """批次 upsert，空資料直接跳過。"""
    if not rows:
        return
    try:
        q = get_client().table(table).upsert(rows)
        if on_conflict:
            q = q  # supabase-py 自動依 PRIMARY KEY / UNIQUE 做 upsert
        q.execute()
        logger.info("upsert %s: %d 筆", table, len(rows))
    except Exception as exc:
        logger.error("upsert %s 失敗: %s", table, exc)


def select(table: str, filters: dict | None = None,
           columns: str = "*", limit: int = 0) -> list[dict]:
    """查詢資料表，filters 為 {column: value} 的 eq 條件。"""
    try:
        q = get_client().table(table).select(columns)
        for col, val in (filters or {}).items():
            q = q.eq(col, val)
        if limit:
            q = q.limit(limit)
        return q.execute().data or []
    except Exception as exc:
        logger.error("select %s 失敗: %s", table, exc)
        return []
