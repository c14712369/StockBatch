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
from src.config import TOP_N_WATCHLIST

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _get_watchlist() -> list[dict]:
    """從 Supabase 取得最近一期 weekly_scores 的 Top 10 通過股票。
    只拉最新 100 筆（週報 50 支 × 2 週），避免全表掃描。
    """
    rows = db.select("weekly_scores", columns="*",
                     order_by="week_date", desc=True, limit=100)
    if not rows:
        return []

    df = pd.DataFrame(rows)
    latest_week = df["week_date"].max()
    top = (df[(df["week_date"] == latest_week) & (df["passes_filter"] == True)]
           .sort_values("total_score", ascending=False)
           .head(TOP_N_WATCHLIST))

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
    inst_df = fetchers.fetch_institutional(universe, days=60)
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

    # --- 處置模擬倉位 (Paper Trading) ---
    logger.info("結算模擬倉位(Paper Trading)...")
    open_positions = db.select("paper_trading_positions", filters={"status": "open"})
    paper_updates = []
    paper_summary = []

    if open_positions:
        # 為了避免重複抓取價格，如果前面 price_df 已經有了就直接用，沒有再抓
        # 目前抓了 watchlist 的 65 天價格，但舊的 positions 可能不在 watchlist 中
        position_sids = {p["stock_id"] for p in open_positions}
        missing_sids = position_sids - universe
        
        all_prices = price_df.copy()
        if missing_sids:
            logger.info("額外抓取 %d 支非本週 watchlist 的模擬倉位股票價格", len(missing_sids))
            extra_price_df = fetchers.fetch_price(list(missing_sids), days=5)
            all_prices = pd.concat([all_prices, extra_price_df], ignore_index=True)

        # 依據週次分組統計
        positions_by_week = {}
        
        # 取得股票名稱對照表
        universe_rows = db.select("stock_universe")
        stock_name_map = {r["stock_id"]: r.get("stock_name", r["stock_id"]) for r in universe_rows}
        
        for pos in open_positions:
            sid = pos["stock_id"]
            week_date = pos["week_date"]

            px_data = all_prices[all_prices["stock_id"] == sid].sort_values("date")
            if not px_data.empty:
                current_price = float(px_data.iloc[-1].get("close", 0) or 0)
                entry_price = float(pos["entry_price"] or 0)

                # entry_price == 0 表示週報建倉時的哨兵值，以首個交易日收盤確認進場價
                if entry_price == 0.0 and current_price > 0:
                    entry_price = current_price
                    logger.info("Paper Trading %s 首日建倉，進場價確認為 %.2f", sid, entry_price)

                pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0

                # 準備寫回 DB
                paper_updates.append({
                    "week_date": week_date,
                    "stock_id": sid,
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "unrealized_pnl_pct": round(pnl_pct, 2),
                    "status": "open"
                })
                
                # 統計用
                if week_date not in positions_by_week:
                    positions_by_week[week_date] = []
                
                stock_name = stock_name_map.get(sid, sid)
                positions_by_week[week_date].append({
                    "stock_id": sid,
                    "stock_name": stock_name,
                    "pnl_pct": pnl_pct,
                    "current_price": current_price,
                    "entry_price": entry_price
                })

        if paper_updates:
            db.upsert("paper_trading_positions", paper_updates)

        # 整理摘要給 Telegram (只取最近的 4 週)
        for week in sorted(positions_by_week.keys(), reverse=True)[:4]:
            week_pos = positions_by_week[week]
            avg_pnl = sum(p["pnl_pct"] for p in week_pos) / len(week_pos) if week_pos else 0
            best_stock = max(week_pos, key=lambda x: x["pnl_pct"]) if week_pos else None
            paper_summary.append({
                "week_date": week,
                "avg_pnl_pct": round(avg_pnl, 2),
                "best_stock": best_stock,
                "count": len(week_pos)
            })

    # ------------------------------------

    notifier.send_daily_report(daily_data, today, paper_summary=paper_summary)
    logger.info("═══ 日報工作完成 ═══")


if __name__ == "__main__":
    run()
