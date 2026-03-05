"""
盤中快報：平日 9:00–13:00 TST，每小時執行
- Supabase 取得 watchlist + 昨收 / 昨高 / 昨低
- TWSE MIS 公開 API 取得即時報價（不占 FinMind 額度）
- 有訊號才發通知，無訊號靜默
訊號條件：漲跌 ±2%、突破昨高、跌破昨低
"""
import logging
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from src import notifier, db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

TWSE_URL  = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
TST       = timezone(timedelta(hours=8))
PCT_ALERT = 2.0   # 漲跌超過 ±2% 觸發


def _get_watchlist() -> list[dict]:
    """從 Supabase 讀 watchlist，附帶昨收 / 昨高 / 昨低。"""
    rows = db.select("weekly_scores", columns="*")
    if not rows:
        return []
    df = pd.DataFrame(rows)
    latest_week = df["week_date"].max()
    top = (df[(df["week_date"] == latest_week) & (df["passes_filter"] == True)]
           .sort_values("total_score", ascending=False)
           .head(10))
    if top.empty:
        return []

    stock_ids = set(top["stock_id"].tolist())
    universe  = {r["stock_id"]: r.get("stock_name", r["stock_id"])
                 for r in db.select("stock_universe")}
    price_map = {r["stock_id"]: r for r in db.select("daily_price")
                 if r["stock_id"] in stock_ids}

    result = []
    for _, r in top.iterrows():
        sid = r["stock_id"]
        px  = price_map.get(sid, {})
        result.append({
            "stock_id":   sid,
            "stock_name": universe.get(sid, sid),
            "prev_close": float(px.get("close", 0) or 0),
            "prev_high":  float(px.get("high",  0) or 0),
            "prev_low":   float(px.get("low",   0) or 0),
        })
    return result


def _safe_float(v, default: float = 0.0) -> float:
    """將 TWSE API 欄位安全轉為 float；"-" 或空值回傳 default。"""
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _fetch_twse(stock_ids: list[str]) -> dict[str, dict]:
    """
    呼叫 TWSE MIS 即時 API。
    先訪問首頁取得 session cookie，再呼叫 API。
    回傳 {stock_id: {price, open, high, low, volume}}
    若股票尚無成交（z="-"）則略過。
    """
    ex_ch = "|".join(f"tse_{sid}.tw" for sid in stock_ids)
    try:
        session = requests.Session()
        # 先訪問首頁取得 JSESSIONID 等 cookies
        session.get(
            "https://mis.twse.com.tw/stock/index.jsp",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )

        resp = session.get(
            TWSE_URL,
            params={"ex_ch": ex_ch, "json": "1", "delay": "0"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("TWSE API 失敗: %s", exc)
        return {}

    result = {}
    for item in data.get("msgArray", []):
        sid = item.get("c", "").strip()
        z   = item.get("z", "-")
        if not z or z == "-":
            continue
        price = _safe_float(z)
        if not price:
            continue
        result[sid] = {
            "price":  price,
            "open":   _safe_float(item.get("o")),
            "high":   _safe_float(item.get("h")),
            "low":    _safe_float(item.get("l")),
            "volume": int(_safe_float(item.get("v"))),
        }
    return result


def run() -> None:
    now_tst  = datetime.now(TST)
    time_str = now_tst.strftime("%H:%M")
    logger.info("═══ 盤中快報 %s ═══", time_str)

    watchlist = _get_watchlist()
    if not watchlist:
        logger.warning("watchlist 為空，跳過盤中快報")
        return

    realtime = _fetch_twse([s["stock_id"] for s in watchlist])
    if not realtime:
        logger.warning("TWSE API 無即時資料（可能休市或盤前）")
        return

    logger.info("TWSE API 回傳 %d 支即時報價", len(realtime))

    alerts = []
    for s in watchlist:
        sid  = s["stock_id"]
        rt   = realtime.get(sid)
        if not rt:
            continue
        price      = rt["price"]
        prev_close = s["prev_close"]
        if not prev_close:
            continue

        pct     = (price - prev_close) / prev_close * 100
        signals = []

        if pct >= PCT_ALERT:
            signals.append(f"強勢 ▲{pct:.1f}%")
        elif pct <= -PCT_ALERT:
            signals.append(f"急殺 ▼{abs(pct):.1f}%")

        if s["prev_high"] and price > s["prev_high"]:
            signals.append("突破昨高")
        elif s["prev_low"] and price < s["prev_low"]:
            signals.append("跌破昨低")

        if signals:
            alerts.append({
                "stock_id":   sid,
                "stock_name": s["stock_name"],
                "price":      price,
                "pct":        round(pct, 2),
                "prev_close": prev_close,
                "volume":     rt["volume"],
                "signals":    signals,
            })

    if alerts:
        notifier.send_intraday_alert(alerts, time_str)
        logger.info("盤中快報已發送，%d 支異動", len(alerts))
    else:
        logger.info("無異動訊號，靜默不發")


if __name__ == "__main__":
    run()
