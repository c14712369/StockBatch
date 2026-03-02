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

    logger.info("抓取法人資料（30日）…")
    inst_df = fetchers.fetch_institutional(universe, days=30)

    logger.info("抓取融資資料（60日）…")
    margin_df = fetchers.fetch_margin(universe, days=60)

    logger.info("抓取月營收（6個月）…")
    rev_df = fetchers.fetch_revenue(universe, months=6)

    logger.info("抓取財務報表（yfinance 損益/資負/現金流，合併一次）…")
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

    # 4. 存入 Supabase
    week_rows = [
        {
            "stock_id": s["stock_id"],
            "week_date": today,
            "profitability_score": s["profitability"],
            "health_score": s["health"],
            "chip_score": s["chip"],
            "momentum_score": s["momentum"],
            "total_score": s["total"],
            "passes_filter": s["passes_filter"],
            "filter_reason": s.get("filter_reason", ""),
        }
        for s in scores
    ]
    db.upsert("weekly_scores", week_rows)
    logger.info("已儲存 %d 筆評分到 Supabase", len(week_rows))

    # 5. 發送週報
    notifier.send_weekly_report(scores, today)
    logger.info("═══ 週報工作完成 ═══")


if __name__ == "__main__":
    run()
