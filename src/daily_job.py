"""
日報工作：平日 18:30 TST 執行（台股收盤後）
  1. 從 Supabase 取得本週 watchlist（上週得分 Top 10 且通過篩選）
  2. 抓取今日最新價格 / 法人 / 融資資料
  3. 組合成日報所需格式
  4. 發送 Telegram 日報
"""
import logging
from datetime import date
import pandas as pd
from src import fetchers, notifier, db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _get_watchlist() -> list[dict]:
    """從 Supabase 取得最近一期 weekly_scores 的 Top 10 通過股票。"""
    rows = db.select("weekly_scores", columns="*")
    if not rows:
        return []

    df = pd.DataFrame(rows)
    latest_week = df["week_date"].max()
    top = (df[(df["week_date"] == latest_week) & (df["passes_filter"] == True)]
           .sort_values("total_score", ascending=False)
           .head(10))

    # 補股票名稱
    universe = {r["stock_id"]: r.get("stock_name", r["stock_id"])
                for r in db.select("stock_universe")}

    return [
        {"stock_id": r["stock_id"],
         "stock_name": universe.get(r["stock_id"], r["stock_id"])}
        for _, r in top.iterrows()
    ]


def run() -> None:
    today = date.today().strftime("%Y-%m-%d")
    logger.info("═══ 日報工作開始 %s ═══", today)

    watchlist = _get_watchlist()
    if not watchlist:
        logger.warning("watchlist 為空，跳過日報（可能尚未執行週報）")
        return

    universe = {s["stock_id"] for s in watchlist}
    logger.info("watchlist：%d 支 %s", len(universe), list(universe))

    # 抓取今日資料（只針對 watchlist，減少 API 呼叫）
    price_df = fetchers.fetch_price(universe, days=65)   # 65天夠算 60MA
    inst_df = fetchers.fetch_institutional(universe, days=30)
    margin_df = fetchers.fetch_margin(universe, days=30)

    # 組合日報資料
    daily_data = []
    for stock in watchlist:
        sid = stock["stock_id"]

        # 價格
        px = price_df[price_df["stock_id"] == sid].sort_values("date")
        if px.empty:
            continue
        latest_px = px.iloc[-1]
        prev_px = px.iloc[-2] if len(px) >= 2 else latest_px
        close = float(latest_px.get("close", 0) or 0)
        prev_close = float(prev_px.get("close", 0) or 0)
        pct_change = ((close - prev_close) / prev_close * 100) if prev_close else 0
        ma5 = float(latest_px.get("ma5", 0) or 0)
        ma20 = float(latest_px.get("ma20", 0) or 0)
        ma60 = float(latest_px.get("ma60", 0) or 0)
        ma_aligned = close > ma5 > ma20 > ma60 > 0

        # 法人
        inst = inst_df[inst_df["stock_id"] == sid].sort_values("date")
        latest_inst = inst.iloc[-1] if not inst.empty else {}
        foreign_net = int(latest_inst.get("foreign_net", 0) or 0) if not inst.empty else 0
        trust_net = int(latest_inst.get("trust_net", 0) or 0) if not inst.empty else 0
        foreign_streak = int(latest_inst.get("foreign_streak", 0) or 0) if not inst.empty else 0
        trust_streak = int(latest_inst.get("trust_streak", 0) or 0) if not inst.empty else 0

        # 融資
        mg = margin_df[margin_df["stock_id"] == sid].sort_values("date")
        latest_mg = mg.iloc[-1] if not mg.empty else {}
        margin_chg = float(latest_mg.get("margin_chg_pct", 0) or 0) if not mg.empty else 0

        daily_data.append({
            "stock_id": sid,
            "stock_name": stock["stock_name"],
            "close": close,
            "pct_change": round(pct_change, 2),
            "volume": int(latest_px.get("volume", 0) or 0),
            "ma_aligned": ma_aligned,
            "foreign_net": foreign_net,
            "foreign_streak": foreign_streak,
            "trust_net": trust_net,
            "trust_streak": trust_streak,
            "margin_chg_pct": margin_chg,
        })

    notifier.send_daily_report(daily_data, today)
    logger.info("═══ 日報工作完成 ═══")


if __name__ == "__main__":
    run()
