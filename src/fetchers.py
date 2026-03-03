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
    for i, sid in enumerate(sorted(universe)):
        rows = finmind.fetch("TaiwanStockPrice", start_date=start, stock_id=sid)
        if not rows:
            logger.debug("股價無資料: %s", sid)
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

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames).sort_values(["stock_id", "date"])
    combined["close"] = pd.to_numeric(combined["close"], errors="coerce")

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

def fetch_institutional(universe: set[str], days: int = 30) -> pd.DataFrame:
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

def fetch_revenue(universe: set[str], months: int = 6) -> pd.DataFrame:
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
        inc["eps_qoq"] = inc.groupby("stock_id")["eps"].pct_change(fill_method=None) * 100
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
        bal = pv[["stock_id", "date"]].copy()
        bal["debt_ratio"]    = (tl / ta.replace(0, float("nan")) * 100).fillna(0).round(2)
        bal["current_ratio"] = (ca / cl.replace(0, float("nan"))).fillna(0).round(2)
        bal["quick_ratio"]   = bal["current_ratio"]
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
    """
    FinMind TaiwanStockShareholding 實際回傳的是外資持股狀況。
    使用 ForeignInvestmentSharesRatio（外資持股比）作為大戶集中度指標。
    """
    all_rows = []
    start = _date(days)
    for sid in sorted(universe):
        rows = finmind.fetch("TaiwanStockShareholding",
                             start_date=start, stock_id=sid)
        all_rows.extend(rows)
        if rows:
            time.sleep(0.2)

    if not all_rows:
        logger.warning("股權分散：FinMind 無資料，此維度跳過")
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    logger.debug("股權分散欄位: %s", list(df.columns))

    # 用外資持股比（ForeignInvestmentSharesRatio）作為大戶集中度代理指標
    ratio_col = next((c for c in df.columns
                      if "ForeignInvestmentSharesRatio" in c
                      or ("Foreign" in c and "Ratio" in c and "Upper" not in c
                          and "Remaining" not in c)), None)
    if ratio_col is None:
        logger.warning("股權分散：找不到外資持股比欄位 %s，跳過", list(df.columns))
        return pd.DataFrame()

    df["big_holder_pct"] = pd.to_numeric(df[ratio_col], errors="coerce").fillna(0)
    df["date"] = pd.to_datetime(df["date"])
    df["stock_id"] = df["stock_id"].astype(str)

    latest = df[df["date"] == df["date"].max()][["stock_id", "date", "big_holder_pct"]].copy()
    db.upsert("weekly_shareholding", [
        {
            "stock_id": r["stock_id"],
            "date":     r["date"].strftime("%Y-%m-%d"),
            "big_holder_pct": round(float(r["big_holder_pct"]), 4),
        }
        for _, r in latest.iterrows()
    ])
    return df


# ────────────────────────────────────────────────
# 9. 本益比（TaiwanStockPER 需付費，跳過）
# ────────────────────────────────────────────────

def fetch_valuation(universe: set[str], **_) -> pd.DataFrame:
    """TaiwanStockPER 為付費功能，免費版跳過。估值不影響評分。"""
    logger.info("估值資料：TaiwanStockPER 需付費，跳過")
    return pd.DataFrame()
