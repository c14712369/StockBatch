"""
週報工作：每週日 20:00 TST 執行
  1. 更新 0050 成分股
  2. 抓取所有基本面 / 籌碼資料
  3. 計算四維度評分
  4. 存入 Supabase
  5. 發送 Telegram 週報
"""
import logging
from datetime import date
import pandas as pd
from src import fetchers, scorers, notifier, db
from src.universe import get_universe, get_universe_ids

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def run() -> None:
    today = date.today().strftime("%Y-%m-%d")
    logger.info("═══ 週報工作開始 %s ═══", today)

    # 1. 載入股票宇宙（硬編碼清單，FinMind 免費版不支援 ETF API）
    universe_list = get_universe()
    universe = get_universe_ids()
    db.upsert("stock_universe", universe_list)
    logger.info("股票宇宙：%d 支", len(universe))

    # 2. 抓取所有資料（批次，減少 API 呼叫）
    logger.info("抓取價格資料（90日）…")
    price_df = fetchers.fetch_price(universe, days=90)

    logger.info("抓取法人資料（60日）…")
    inst_df = fetchers.fetch_institutional(universe, days=60)

    logger.info("抓取融資資料（60日）…")
    margin_df = fetchers.fetch_margin(universe, days=60)

    logger.info("抓取月營收（15個月）…")
    rev_df = fetchers.fetch_revenue(universe, months=15)

    logger.info("抓取財務報表（FinMind 損益/資負/現金流，逐股抓取）…")
    income_df, balance_df, cashflow_df = fetchers.fetch_financials(universe)

    logger.info("抓取股權分散表（30日）…")
    sh_df = fetchers.fetch_shareholding(universe, days=30)

    logger.info("抓取本益比（yfinance）…")
    fetchers.fetch_valuation(universe)

    # 3. 計算評分
    logger.info("計算評分…")
    scores = scorers.compute_all_scores(
        universe=universe_list,
        price=price_df,
        institutional=inst_df,
        margin=margin_df,
        revenue=rev_df,
        income=income_df,
        balance=balance_df,
        cashflow=cashflow_df,
        shareholding=sh_df,
    )

    # 4. 存入 Supabase (weekly_scores)
    week_rows = [
        {
            "stock_id": s["stock_id"],
            "week_date": today,
            "profitability_score": s["profitability"],
            "health_score": s["health"],
            "chip_score": s["chip"],
            "momentum_score": s["momentum"],
            "pe": s.get("pe", 0),
            "total_score": s["total"],
            "passes_filter": s["passes_filter"],
            "filter_reason": s.get("filter_reason", ""),
        }
        for s in scores
    ]
    db.upsert("weekly_scores", week_rows)
    logger.info("已儲存 %d 筆評分到 Supabase", len(week_rows))

    # 4.5 模擬倉位 (Paper Trading) - 關閉前週倉位，記錄本週進場價
    # 先將所有前週 open 倉位標記為 closed（含最終損益）
    old_open = db.select("paper_trading_positions", filters={"status": "open"})
    old_to_close = [p for p in old_open if p["week_date"] != today]
    if old_to_close:
        close_rows = []
        for pos in old_to_close:
            sid = pos["stock_id"]
            px = price_df[price_df["stock_id"] == sid].sort_values("date")
            current_price = float(px.iloc[-1].get("close", 0)) if not px.empty else float(pos.get("current_price") or 0)
            entry_price = float(pos.get("entry_price") or 0)
            pnl = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
            close_rows.append({
                "week_date":          pos["week_date"],
                "stock_id":           sid,
                "entry_price":        entry_price,
                "current_price":      current_price,
                "unrealized_pnl_pct": round(pnl, 2),
                "status":             "closed",
            })
        db.upsert("paper_trading_positions", close_rows)
        logger.info("已關閉 %d 筆前週模擬倉位（最終損益已計算）", len(close_rows))

    # 找出前 10 名且通過門檻的股票作為本週模擬投資組合
    top_10 = [s for s in scores if s["passes_filter"]][:10]
    paper_rows = []
    for s in top_10:
        sid = s["stock_id"]
        # entry_price 設為 0（哨兵值），等週一日報第一次更新時以當日收盤確認，
        # 避免用週五收盤當進場價而忽略週一開盤跳空的誤差。
        px = price_df[price_df["stock_id"] == sid].sort_values("date")
        ref_price = float(px.iloc[-1].get("close", 0)) if not px.empty else 0.0

        paper_rows.append({
            "week_date": today,
            "stock_id": sid,
            "entry_price": 0.0,       # 待首個交易日收盤確認
            "current_price": ref_price,
            "unrealized_pnl_pct": 0.0,
            "status": "open"
        })
    
    if paper_rows:
        db.upsert("paper_trading_positions", paper_rows)
        logger.info("已更新 %d 筆模擬倉位 (Paper Trading)", len(paper_rows))

    # 5. 發送週報
    notifier.send_weekly_report(scores, today)
    logger.info("═══ 週報工作完成 ═══")


if __name__ == "__main__":
    run()
