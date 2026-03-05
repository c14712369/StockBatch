"""
晨報工作：平日 08:30 TST 執行（台股開盤前）
完全讀 Supabase，不打任何外部 API，不占 FinMind 額度。
昨日 18:30 日報已將 daily_price / daily_institutional / daily_margin 存入 Supabase。
"""
import logging
from datetime import date
import pandas as pd
from src import notifier, db
from src.config import TOP_N_WATCHLIST

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _get_watchlist() -> list[dict]:
    """從 weekly_scores 取得最新一期 Top 10，附帶評分欄位。"""
    rows = db.select("weekly_scores", columns="*")
    if not rows:
        return []
    df = pd.DataFrame(rows)
    latest_week = df["week_date"].max()
    top = (df[(df["week_date"] == latest_week) & (df["passes_filter"] == True)]
           .sort_values("total_score", ascending=False)
           .head(TOP_N_WATCHLIST))
    if top.empty:
        return []
    universe = {r["stock_id"]: r.get("stock_name", r["stock_id"])
                for r in db.select("stock_universe")}
    return [
        {
            "stock_id":    r["stock_id"],
            "stock_name":  universe.get(r["stock_id"], r["stock_id"]),
            "total_score": float(r.get("total_score", 0) or 0),
        }
        for _, r in top.iterrows()
    ]


def run() -> None:
    today = date.today().strftime("%Y-%m-%d")
    logger.info("═══ 晨報工作開始 %s ═══", today)

    watchlist = _get_watchlist()
    if not watchlist:
        logger.warning("watchlist 為空，跳過晨報")
        return

    stock_ids = {s["stock_id"] for s in watchlist}

    # 直接讀 Supabase（昨日日報已存，無需打 API）
    price_map  = {r["stock_id"]: r for r in db.select("daily_price")
                  if r["stock_id"] in stock_ids}
    inst_map   = {r["stock_id"]: r for r in db.select("daily_institutional")
                  if r["stock_id"] in stock_ids}
    margin_map = {r["stock_id"]: r for r in db.select("daily_margin")
                  if r["stock_id"] in stock_ids}

    morning_data = []
    for s in watchlist:
        sid  = s["stock_id"]
        px   = price_map.get(sid, {})
        inst = inst_map.get(sid, {})
        mg   = margin_map.get(sid, {})

        close  = float(px.get("close",  0) or 0)
        ma5    = float(px.get("ma5",    0) or 0)
        ma20   = float(px.get("ma20",   0) or 0)
        ma60   = float(px.get("ma60",   0) or 0)

        morning_data.append({
            "stock_id":       sid,
            "stock_name":     s["stock_name"],
            "total_score":    s["total_score"],
            "close":          close,
            "high":           float(px.get("high",   0) or 0),
            "low":            float(px.get("low",    0) or 0),
            "volume":         int(px.get("volume", 0) or 0),
            "ma_aligned":     close > ma5 > ma20 > ma60 > 0,
            "foreign_streak": int(inst.get("foreign_streak", 0) or 0),
            "trust_streak":   int(inst.get("trust_streak",   0) or 0),
            "foreign_net":    int(inst.get("foreign_net",    0) or 0),
            "trust_net":      int(inst.get("trust_net",      0) or 0),
            "margin_chg_pct": float(mg.get("margin_chg_pct", 0) or 0),
        })

    notifier.send_morning_briefing(morning_data, today)
    logger.info("═══ 晨報工作完成 ═══")


if __name__ == "__main__":
    run()
