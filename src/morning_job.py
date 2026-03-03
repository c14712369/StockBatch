"""
晨報工作：平日 08:30 TST 執行（台股開盤前）
  1. 抓取美股三大指數昨夜收盤表現
  2. 取得本週 watchlist
  3. 抓取 watchlist 昨日收盤資料
  4. 組合今日開盤前局勢分析並發送 Telegram
"""
import logging
from datetime import date, timedelta
import yfinance as yf
import pandas as pd
from src import fetchers, notifier, db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


US_INDICES = {
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq",
    "^DJI":  "道瓊",
    "^VIX":  "VIX 恐慌指數",
}

TW_PROXY = {
    "EWT": "台灣 ETF (EWT)",
}


def _fetch_us_market() -> list[dict]:
    """抓取美股主要指數昨夜收盤與漲跌幅。"""
    result = []
    all_tickers = {**US_INDICES, **TW_PROXY}
    for ticker, name in all_tickers.items():
        try:
            hist = yf.Ticker(ticker).history(period="2d")
            if len(hist) < 2:
                hist = yf.Ticker(ticker).history(period="5d")
            if len(hist) < 2:
                continue
            latest = hist.iloc[-1]
            prev = hist.iloc[-2]
            close = float(latest["Close"])
            prev_close = float(prev["Close"])
            pct = (close - prev_close) / prev_close * 100 if prev_close else 0
            result.append({
                "name": name,
                "ticker": ticker,
                "close": close,
                "pct": round(pct, 2),
            })
        except Exception as exc:
            logger.warning("抓取 %s 失敗: %s", ticker, exc)
    return result


def _get_watchlist() -> list[dict]:
    """從 Supabase 取得最近一期 weekly_scores 的 Top 10 通過股票。"""
    rows = db.select("weekly_scores", columns="*")
    if not rows:
        return []
    df = pd.DataFrame(rows)
    latest_week = df["week_date"].max()
    top = (df[(df["week_date"] == latest_week) & (df["passes_filter"].astype(str) == "true")]
           .sort_values("total_score", ascending=False)
           .head(10))
    universe = {r["stock_id"]: r.get("stock_name", r["stock_id"])
                for r in db.select("stock_universe")}
    return [
        {"stock_id": r["stock_id"],
         "stock_name": universe.get(r["stock_id"], r["stock_id"])}
        for _, r in top.iterrows()
    ]


def run() -> None:
    today = date.today().strftime("%Y-%m-%d")
    logger.info("═══ 晨報工作開始 %s ═══", today)

    us_data = _fetch_us_market()
    logger.info("取得 %d 個美股指數", len(us_data))

    watchlist = _get_watchlist()
    if not watchlist:
        logger.warning("watchlist 為空，晨報僅含美股概況")

    prev_data = []
    if watchlist:
        universe = {s["stock_id"] for s in watchlist}
        price_df = fetchers.fetch_price(universe, days=5)
        inst_df = fetchers.fetch_institutional(universe, days=5)

        for stock in watchlist:
            sid = stock["stock_id"]
            px = price_df[price_df["stock_id"] == sid].sort_values("date")
            if px.empty:
                continue
            latest_px = px.iloc[-1]
            prev_px = px.iloc[-2] if len(px) >= 2 else latest_px
            close = float(latest_px.get("close", 0) or 0)
            prev_close = float(prev_px.get("close", 0) or 0)
            pct = ((close - prev_close) / prev_close * 100) if prev_close else 0

            inst = inst_df[inst_df["stock_id"] == sid].sort_values("date")
            latest_inst = inst.iloc[-1] if not inst.empty else {}
            foreign_net = int(latest_inst.get("foreign_net", 0) or 0) if not inst.empty else 0
            trust_net = int(latest_inst.get("trust_net", 0) or 0) if not inst.empty else 0

            prev_data.append({
                "stock_id": sid,
                "stock_name": stock["stock_name"],
                "close": close,
                "pct": round(pct, 2),
                "foreign_net": foreign_net,
                "trust_net": trust_net,
            })

    notifier.send_morning_briefing(us_data, prev_data, today)
    logger.info("═══ 晨報工作完成 ═══")


if __name__ == "__main__":
    run()
