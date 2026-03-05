"""
資料抓取模組（全 FinMind 方案）
  股價 / 財務報表 / 三大法人 / 融資 / 月營收 / 股權分散 : FinMind API
"""
import math
import time
import logging
from datetime import date, timedelta
import pandas as pd
from src import db, finmind

logger = logging.getLogger(__name__)

# ─── 設定 ───────────────────────────────────────
_FM_DELAY = 0.3   # FinMind API 每次請求間隔（秒）


def _date(days_ago: int = 0) -> str:
    return (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _clean_num(s) -> float:
    """移除千分位逗號並轉 float。"""
    try:
        return float(str(s).replace(",", "").replace("--", "0").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


def _to_int(v) -> int:
    """安全地將值轉為 int；NaN / Inf / None 均回傳 0。"""
    try:
        f = float(v)
        return 0 if (math.isnan(f) or math.isinf(f)) else int(round(f))
    except (TypeError, ValueError):
        return 0


# ────────────────────────────────────────────────
# 1. 股價（FinMind TaiwanStockPrice，逐股抓）
# ────────────────────────────────────────────────

def fetch_price(universe: set[str], days: int = 90) -> pd.DataFrame:
    start = _date(days)
    logger.info("FinMind 下載 %d 支股票價格（start=%s）…", len(universe), start)

    frames = []
    failed_sids = set()
    for i, sid in enumerate(sorted(universe)):
        rows = finmind.fetch("TaiwanStockPrice", start_date=start, stock_id=sid)
        if not rows:
            logger.debug("股價無資料: %s，準備以 yfinance 備援", sid)
            failed_sids.add(sid)
            time.sleep(_FM_DELAY)
            continue
        df = pd.DataFrame(rows)
        df = df.rename(columns={"max": "high", "min": "low",
                                "Trading_Volume": "volume"})
        df["date"] = pd.to_datetime(df["date"])
        df = df[["stock_id", "date", "open", "high", "low", "close", "volume"]].copy()
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)
        frames.append(df)
        if (i + 1) % 10 == 0:
            logger.info("價格進度：%d / %d", i + 1, len(universe))
        time.sleep(_FM_DELAY)

    # 備援機制：如果 FinMind 返回空，使用 yfinance 補齊
    if failed_sids:
        try:
            import yfinance as yf
            logger.info("FinMind 無法取得部分股價 (共 %d 檔)，啟動 yfinance 分段備援下載...", len(failed_sids))
            
            # 分成每 10 檔一組，避免被 Yahoo Finance 阻擋
            sid_list = sorted(list(failed_sids))
            chunk_size = 10
            for i in range(0, len(sid_list), chunk_size):
                chunk = sid_list[i : i + chunk_size]
                tickers = [f"{sid}.TW" for sid in chunk]
                logger.info("  正在下載組別 %d/%d: %s", (i // chunk_size) + 1, (len(sid_list)-1)//chunk_size + 1, ", ".join(chunk))
                
                try:
                    yf_df = yf.download(tickers, start=start, progress=False, auto_adjust=False)
                    if not yf_df.empty:
                        # 處理單檔或多檔回傳格式
                        if len(chunk) == 1:
                            df_p = yf_df.reset_index()
                            df_p["stock_id"] = chunk[0]
                        else:
                            df_p = yf_df.stack(level=1, future_stack=True).reset_index()
                            df_p = df_p.rename(columns={"Ticker": "stock_id"})
                        
                        df_p = df_p.rename(columns={
                            "Date": "date", "Open": "open", "High": "high", 
                            "Low": "low", "Close": "close", "Volume": "volume"
                        })
                        df_p["stock_id"] = df_p["stock_id"].str.replace(".TW", "", regex=False)
                        df_p["date"] = pd.to_datetime(df_p["date"])
                        
                        # 過濾欄位並轉換型別
                        keep_cols = ["stock_id", "date", "open", "high", "low", "close", "volume"]
                        df_p = df_p[[c for c in keep_cols if c in df_p.columns]].copy()
                        for col in ["open", "high", "low", "close"]:
                            if col in df_p.columns:
                                df_p[col] = pd.to_numeric(df_p[col], errors="coerce")
                        if "volume" in df_p.columns:
                            df_p["volume"] = pd.to_numeric(df_p["volume"], errors="coerce").fillna(0).astype(int)
                        
                        frames.append(df_p)
                except Exception as e:
                    logger.warning("yfinance 該組備援失敗: %s", e)
                
                time.sleep(1.5) # 稍微停頓
        except ImportError:
            logger.warning("yfinance 未安裝，無法進行股價備援")
    if not frames:
        logger.error("無法取得任何股價資料 (FinMind 與 yfinance 皆失敗)，回測中止。")
        return pd.DataFrame()

    combined = pd.concat(frames).sort_values(["stock_id", "date"])
    combined["close"] = pd.to_numeric(combined["close"], errors="coerce")

    if combined.empty or "date" not in combined.columns:
        return pd.DataFrame()

    for ma, win in [("ma5", 5), ("ma20", 20), ("ma60", 60)]:
        combined[ma] = combined.groupby("stock_id")["close"].transform(
            lambda x, w=win: x.rolling(w, min_periods=1).mean()
        )

    latest = combined[combined["date"] == combined["date"].max()].copy()
    db.upsert("daily_price", [
        {
            "stock_id": r["stock_id"],
            "date": r["date"].strftime("%Y-%m-%d"),
            "open": round(float(r.get("open", 0) or 0), 2),
            "high": round(float(r.get("high", 0) or 0), 2),
            "low": round(float(r.get("low", 0) or 0), 2),
            "close": round(float(r["close"] or 0), 2),
            "volume": int(r.get("volume", 0) or 0),
            "ma5": round(float(r["ma5"] or 0), 2),
            "ma20": round(float(r["ma20"] or 0), 2),
            "ma60": round(float(r["ma60"] or 0), 2),
        }
        for _, r in latest.iterrows()
    ])
    logger.info("股價：%d 支，最新日期 %s", combined["stock_id"].nunique(),
                combined["date"].max().strftime("%Y-%m-%d"))
    return combined


# ────────────────────────────────────────────────
# 2. 三大法人（FinMind TaiwanStockInstitutionalInvestors，逐股抓）
# ────────────────────────────────────────────────

def fetch_institutional(universe: set[str], days: int = 60) -> pd.DataFrame:
    """FinMind 三大法人：計算連買/賣天數。"""
    start = _date(days)
    logger.info("FinMind 法人資料：%d 支股票（start=%s）…", len(universe), start)

    all_rows = []
    for i, sid in enumerate(sorted(universe)):
        rows = finmind.fetch("TaiwanStockInstitutionalInvestorsBuySell",
                             start_date=start, stock_id=sid)
        # 回傳格式：每個機構一筆 (long format)，需聚合成每日一筆
        by_date: dict[str, dict] = {}
        for r in rows:
            d = r["date"]
            if d not in by_date:
                by_date[d] = {"foreign_net": 0.0, "trust_net": 0.0, "dealer_net": 0.0}
            net = float(r.get("buy", 0) or 0) - float(r.get("sell", 0) or 0)
            name = r.get("name", "")
            if name == "Foreign_Investor":
                by_date[d]["foreign_net"] += net
            elif name == "Investment_Trust":
                by_date[d]["trust_net"] += net
            elif name in ("Dealer_self", "Dealer_Hedging"):
                by_date[d]["dealer_net"] += net
        for d, vals in by_date.items():
            all_rows.append({"stock_id": sid, "date": d, **vals})
        if (i + 1) % 10 == 0:
            logger.info("法人進度：%d / %d", i + 1, len(universe))
        time.sleep(_FM_DELAY)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["date"] = pd.to_datetime(df["date"])
    df["total_net"] = df["foreign_net"] + df["trust_net"] + df["dealer_net"]
    df = df.sort_values(["stock_id", "date"])

    def calc_streak(s: pd.Series) -> pd.Series:
        streak = [0] * len(s)
        v = s.tolist()
        for i in range(len(v)):
            if v[i] > 0:
                streak[i] = (streak[i-1] + 1) if i > 0 and streak[i-1] > 0 else 1
            elif v[i] < 0:
                streak[i] = (streak[i-1] - 1) if i > 0 and streak[i-1] < 0 else -1
        return pd.Series(streak, index=s.index)

    df["foreign_streak"] = df.groupby("stock_id")["foreign_net"].transform(calc_streak)
    df["trust_streak"]   = df.groupby("stock_id")["trust_net"].transform(calc_streak)

    latest = df[df["date"] == df["date"].max()].copy()
    db.upsert("daily_institutional", [
        {
            "stock_id":       r["stock_id"],
            "date":           r["date"].strftime("%Y-%m-%d"),
            "foreign_net":    int(r["foreign_net"]),
            "trust_net":      int(r["trust_net"]),
            "dealer_net":     int(r["dealer_net"]),
            "total_net":      int(r["total_net"]),
            "foreign_streak": int(r["foreign_streak"]),
            "trust_streak":   int(r["trust_streak"]),
        }
        for _, r in latest.iterrows()
    ])
    logger.info("法人資料：%d 筆（%d 交易日）", len(df), df["date"].nunique())
    return df


# ────────────────────────────────────────────────
# 3. 融資融券（FinMind TaiwanStockMarginPurchaseShortSale，逐股抓）
# ────────────────────────────────────────────────

def fetch_margin(universe: set[str], days: int = 45) -> pd.DataFrame:
    """FinMind 融資融券：計算與 20 交易日前的餘額變化率。"""
    start = _date(days)
    logger.info("FinMind 融資資料：%d 支股票（start=%s）…", len(universe), start)

    all_rows = []
    for i, sid in enumerate(sorted(universe)):
        rows = finmind.fetch("TaiwanStockMarginPurchaseShortSale",
                             start_date=start, stock_id=sid)
        for r in rows:
            all_rows.append({
                "stock_id":       sid,
                "date":           r["date"],
                "margin_balance": float(r.get("MarginPurchaseTodayBalance", 0) or 0),
                "short_balance":  float(r.get("ShortSaleTodayBalance", 0) or 0),
            })
        if (i + 1) % 10 == 0:
            logger.info("融資進度：%d / %d", i + 1, len(universe))
        time.sleep(_FM_DELAY)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["stock_id", "date"])

    df["margin_prev20"] = df.groupby("stock_id")["margin_balance"].transform(
        lambda x: x.shift(20)
    )
    df["margin_chg_pct"] = (
        (df["margin_balance"] - df["margin_prev20"])
        / df["margin_prev20"].replace(0, float("nan"))
    ).fillna(0)

    latest = df[df["date"] == df["date"].max()].copy()
    db.upsert("daily_margin", [
        {
            "stock_id":       r["stock_id"],
            "date":           r["date"].strftime("%Y-%m-%d"),
            "margin_balance": int(r["margin_balance"]),
            "short_balance":  int(r["short_balance"]),
            "margin_chg_pct": round(float(r["margin_chg_pct"]), 4),
        }
        for _, r in latest.iterrows()
    ])
    logger.info("融資資料：%d 筆", len(df))
    return df


# ────────────────────────────────────────────────
# 4. 月營收（FinMind 免費嘗試）
# ────────────────────────────────────────────────

def fetch_revenue(universe: set[str], months: int = 15) -> pd.DataFrame:
    """嘗試用 FinMind 抓月營收；若需付費則回傳空 DataFrame。"""
    all_rows = []
    start = _date(months * 31)
    for sid in sorted(universe):
        rows = finmind.fetch("TaiwanStockMonthRevenue",
                             start_date=start, stock_id=sid)
        all_rows.extend(rows)
        if rows:
            time.sleep(0.2)

    if not all_rows:
        logger.warning("月營收：FinMind 免費版無資料，此維度評分將跳過")
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce").fillna(0)
    df["date"]    = pd.to_datetime(df["date"])
    df = df.sort_values(["stock_id", "date"])
    df["revenue_mom"] = df.groupby("stock_id")["revenue"].pct_change() * 100
    df["revenue_yoy"] = df.groupby("stock_id")["revenue"].pct_change(12) * 100

    db.upsert("monthly_revenue", [
        {
            "stock_id":    r["stock_id"],
            "year":        int(r["date"].year),
            "month":       int(r["date"].month),
            "revenue":     int(r["revenue"]),
            "revenue_mom": round(float(r.get("revenue_mom", 0) or 0), 2),
            "revenue_yoy": round(float(r.get("revenue_yoy", 0) or 0), 2),
        }
        for _, r in df.iterrows()
    ])
    logger.info("月營收：%d 筆", len(df))
    return df


# ────────────────────────────────────────────────
# 5–7. 財務報表（FinMind 逐股）
# ────────────────────────────────────────────────

def fetch_financials(universe: set[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """FinMind 損益/資負/現金流，逐股抓，並寫入 Supabase。"""
    # Check DB first
    db_inc = pd.DataFrame(db.select("quarterly_income"))
    if not db_inc.empty:
        # Check FinMind for ONE stock to see the latest available date
        first_sid = sorted(list(universe))[0]
        fm_test = finmind.fetch("TaiwanStockFinancialStatements", start_date=_date(180), stock_id=first_sid)
        needs_update = True
        if fm_test:
            # Get latest year and quarter from FinMind
            fm_df = pd.DataFrame(fm_test)
            fm_df["date"] = pd.to_datetime(fm_df["date"])
            max_date = fm_df["date"].max()
            fm_max_year = max_date.year
            fm_max_q = (max_date.month - 1) // 3 + 1
            
            # Get latest year and quarter from DB for this stock
            stock_db = db_inc[db_inc["stock_id"] == first_sid]
            if not stock_db.empty:
                db_max_year = stock_db["year"].max()
                db_max_q = stock_db[stock_db["year"] == db_max_year]["quarter"].max()
                if db_max_year >= fm_max_year and db_max_q >= fm_max_q:
                    needs_update = False
                    
        if not needs_update:
            logger.info("財務報表：資料庫已是最新，直接讀取快取，跳過 API 抓取")
            db_bal = pd.DataFrame(db.select("quarterly_balance"))
            db_cf = pd.DataFrame(db.select("quarterly_cashflow"))
            
            db_inc["date"] = pd.to_datetime(db_inc["year"].astype(str) + "-" + (db_inc["quarter"] * 3).astype(str).str.zfill(2) + "-01")
            db_bal["date"] = pd.to_datetime(db_bal["year"].astype(str) + "-" + (db_bal["quarter"] * 3).astype(str).str.zfill(2) + "-01")
            db_cf["date"] = pd.to_datetime(db_cf["year"].astype(str) + "-" + (db_cf["quarter"] * 3).astype(str).str.zfill(2) + "-01")
            
            return db_inc, db_bal, db_cf

    start = _date(365 * 2)
    logger.info("FinMind 財務報表：%d 支股票（start=%s）…", len(universe), start)

    inc_rows, bal_rows, cf_rows = [], [], []

    for i, sid in enumerate(sorted(universe)):
        # ── 損益表 ──
        for r in finmind.fetch("TaiwanStockFinancialStatements",
                               start_date=start, stock_id=sid):
            inc_rows.append({"stock_id": sid, "date": r["date"],
                             "type": r["type"], "value": r["value"]})
        time.sleep(_FM_DELAY)

        # ── 資產負債表 ──
        for r in finmind.fetch("TaiwanStockBalanceSheet",
                               start_date=start, stock_id=sid):
            if not r["type"].endswith("_per"):
                bal_rows.append({"stock_id": sid, "date": r["date"],
                                 "type": r["type"], "value": r["value"]})
        time.sleep(_FM_DELAY)

        # ── 現金流量表 ──
        for r in finmind.fetch("TaiwanStockCashFlowsStatement",
                               start_date=start, stock_id=sid):
            cf_rows.append({"stock_id": sid, "date": r["date"],
                            "type": r["type"], "value": r["value"]})
        time.sleep(_FM_DELAY)

        if (i + 1) % 10 == 0:
            logger.info("財報進度：%d / %d", i + 1, len(universe))

    def _pivot(rows: list[dict]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        return df.pivot_table(index=["stock_id", "date"],
                              columns="type", values="value",
                              aggfunc="first").reset_index()

    # ── 損益 ──
    inc = pd.DataFrame()
    if inc_rows:
        pv = _pivot(inc_rows)
        get = lambda c: pd.to_numeric(pv.get(c, 0), errors="coerce").fillna(0)
        rev = get("Revenue")
        inc = pv[["stock_id", "date"]].copy()
        inc["eps"]              = get("EPS")
        inc["gross_margin"]     = (get("GrossProfit") / rev.replace(0, float("nan")) * 100).fillna(0).round(2)
        inc["operating_margin"] = (get("OperatingIncome") / rev.replace(0, float("nan")) * 100).fillna(0).round(2)
        inc["net_income"]       = get("IncomeAfterTaxes")
        inc["net_margin"]       = (inc["net_income"] / rev.replace(0, float("nan")) * 100).fillna(0).round(2)
        inc = inc.sort_values(["stock_id", "date"])
        eps_grp = inc.groupby("stock_id")["eps"]
        inc["eps_qoq"] = (eps_grp.diff() / eps_grp.shift().abs().replace(0, float("nan"))) * 100
        db.upsert("quarterly_income", [
            {"stock_id": r["stock_id"],
             "year": int(r["date"].year), "quarter": (int(r["date"].month) - 1) // 3 + 1,
             "eps": float(r["eps"] or 0), "gross_margin": float(r["gross_margin"] or 0),
             "operating_margin": float(r["operating_margin"] or 0),
             "net_margin": float(r["net_margin"] or 0),
             "eps_qoq": round(float(r.get("eps_qoq") or 0), 2)}
            for _, r in inc.iterrows()
        ])

    # ── 資負 ──
    bal = pd.DataFrame()
    if bal_rows:
        pv = _pivot(bal_rows)
        get = lambda c: pd.to_numeric(pv.get(c, 0), errors="coerce").fillna(0)
        ta = get("TotalAssets"); tl = get("Liabilities")
        ca = get("CurrentAssets"); cl = get("CurrentLiabilities")
        inv = get("Inventories")
        bal = pv[["stock_id", "date"]].copy()
        bal["debt_ratio"]    = (tl / ta.replace(0, float("nan")) * 100).fillna(0).round(2)
        bal["current_ratio"] = (ca / cl.replace(0, float("nan"))).fillna(0).round(2)
        bal["quick_ratio"]   = ((ca - inv) / cl.replace(0, float("nan"))).fillna(0).round(2)
        bal = bal.sort_values(["stock_id", "date"])
        db.upsert("quarterly_balance", [
            {"stock_id": r["stock_id"],
             "year": int(r["date"].year), "quarter": (int(r["date"].month) - 1) // 3 + 1,
             "debt_ratio": float(r["debt_ratio"] or 0),
             "current_ratio": float(r["current_ratio"] or 0),
             "quick_ratio": float(r["quick_ratio"] or 0)}
            for _, r in bal.iterrows()
        ])

    # ── 現金流 ──
    cf = pd.DataFrame()
    if cf_rows:
        pv = _pivot(cf_rows)
        get = lambda c: pd.to_numeric(pv.get(c, 0), errors="coerce").fillna(0)
        ocf = get("CashFlowsFromOperatingActivities")
        cf = pv[["stock_id", "date"]].copy()
        cf["operating_cf"] = ocf
        # 從損益表補 net_income 以計算 OCF 品質
        if not inc.empty:
            ni_map = inc[["stock_id", "date", "net_income"]].copy()
            cf = cf.merge(ni_map, on=["stock_id", "date"], how="left")
            cf["net_income"] = cf["net_income"].fillna(0)
        else:
            cf["net_income"] = 0.0
        cf["ocf_quality"] = cf.apply(
            lambda r: round(r["operating_cf"] / r["net_income"], 4)
            if r["net_income"] != 0 else 0.0, axis=1)
        cf = cf.sort_values(["stock_id", "date"])
        db.upsert("quarterly_cashflow", [
            {"stock_id": r["stock_id"],
             "year": int(r["date"].year), "quarter": (int(r["date"].month) - 1) // 3 + 1,
             "operating_cf": _to_int(r["operating_cf"]),
             "net_income": _to_int(r["net_income"]),
             "ocf_quality": float(r["ocf_quality"] or 0)}
            for _, r in cf.iterrows()
        ])

    logger.info("財務報表：損益 %d 筆 / 資負 %d 筆 / 現金流 %d 筆",
                len(inc), len(bal), len(cf))
    return inc, bal, cf


# ────────────────────────────────────────────────
# 8. 股權分散（FinMind 免費嘗試）
# ────────────────────────────────────────────────

def fetch_shareholding(universe: set[str], days: int = 30) -> pd.DataFrame:
    """TaiwanStockShareholding 為付費功能，免費版跳過。籌碼集中度此維度將得 0 分。"""
    logger.warning("股權分散：TaiwanStockShareholding 需付費，跳過")
    return pd.DataFrame()


# ────────────────────────────────────────────────
# 9. 本益比（TaiwanStockPER 需付費，跳過）
# ────────────────────────────────────────────────

def fetch_valuation(universe: set[str], **_) -> pd.DataFrame:
    """TaiwanStockPER 需付費，免費版跳過。改由評分引擎自算。"""
    logger.warning("估值：TaiwanStockPER 需付費，跳過。將於評分時自算 P/E。")
    return pd.DataFrame()
