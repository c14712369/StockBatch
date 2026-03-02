"""
所有 FinMind 資料集的抓取函式。
策略：FinMind 免費版需逐支股票查詢（帶 data_id），加小延遲避免限速。
50支 × 9資料集 ≈ 450 次/週，在免費版 600次/日 內。
"""
import time
import logging
from datetime import date, timedelta
import pandas as pd
from src import finmind, db

logger = logging.getLogger(__name__)

_API_DELAY = 0.3  # 每次 API 呼叫間隔（秒）


def _date(days_ago: int = 0) -> str:
    return (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _fetch_for_universe(dataset: str, universe: set[str],
                        start_date: str, end_date: str = "") -> list[dict]:
    """逐支股票查詢並合併結果，避免免費版 400 錯誤。"""
    all_rows = []
    for stock_id in sorted(universe):
        rows = finmind.fetch(dataset, start_date=start_date,
                             end_date=end_date, stock_id=stock_id)
        all_rows.extend(rows)
        time.sleep(_API_DELAY)
    logger.info("%s：共取得 %d 筆（%d 支股票）", dataset, len(all_rows), len(universe))
    return all_rows


# ─────────────────────────────────────────────
# 1. 還原股價（每日）
# ─────────────────────────────────────────────

def fetch_price(universe: set[str], days: int = 90) -> pd.DataFrame:
    """抓取還原股價，計算 5/20/60 MA。"""
    rows = _fetch_for_universe("TaiwanStockPriceAdj", universe, _date(days))
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    # FinMind 欄位名：Trading Volume
    vol_col = next((c for c in df.columns if "Volume" in c or c == "volume"), None)
    df["volume"] = pd.to_numeric(df[vol_col], errors="coerce") if vol_col else 0

    df = df.sort_values(["stock_id", "date"])
    for ma, win in [("ma5", 5), ("ma20", 20), ("ma60", 60)]:
        df[ma] = df.groupby("stock_id")["close"].transform(
            lambda x, w=win: x.rolling(w, min_periods=1).mean()
        )

    latest = df[df["date"] == df["date"].max()].copy()
    db.upsert("daily_price", [
        {
            "stock_id": r["stock_id"],
            "date": r["date"].strftime("%Y-%m-%d"),
            "open": float(r.get("open", 0) or 0),
            "high": float(r.get("max", 0) or 0),
            "low": float(r.get("min", 0) or 0),
            "close": float(r["close"] or 0),
            "volume": int(r["volume"] or 0),
            "ma5": round(float(r["ma5"] or 0), 2),
            "ma20": round(float(r["ma20"] or 0), 2),
            "ma60": round(float(r["ma60"] or 0), 2),
        }
        for _, r in latest.iterrows()
    ])
    return df


# ─────────────────────────────────────────────
# 2. 三大法人（每日）
# ─────────────────────────────────────────────

def fetch_institutional(universe: set[str], days: int = 30) -> pd.DataFrame:
    rows = _fetch_for_universe("TaiwanStockInstitutionalInvestorsBuySell",
                               universe, _date(days))
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["buy"] = pd.to_numeric(df["buy"], errors="coerce").fillna(0)
    df["sell"] = pd.to_numeric(df["sell"], errors="coerce").fillna(0)
    df["net"] = pd.to_numeric(df["net"], errors="coerce").fillna(0)

    pivot = df.pivot_table(index=["date", "stock_id"], columns="name",
                           values="net", aggfunc="sum").reset_index()
    pivot.columns.name = None

    col_map = {"外資及陸資": "foreign_net", "外資": "foreign_net",
               "投信": "trust_net", "自營商": "dealer_net"}
    pivot = pivot.rename(columns={k: v for k, v in col_map.items() if k in pivot.columns})
    for col in ["foreign_net", "trust_net", "dealer_net"]:
        if col not in pivot.columns:
            pivot[col] = 0
    pivot["total_net"] = pivot["foreign_net"] + pivot["trust_net"] + pivot["dealer_net"]
    pivot = pivot.sort_values(["stock_id", "date"])

    def calc_streak(series: pd.Series) -> pd.Series:
        streak = [0] * len(series)
        vals = series.tolist()
        for i in range(len(vals)):
            if vals[i] > 0:
                streak[i] = (streak[i - 1] + 1) if i > 0 and streak[i - 1] > 0 else 1
            elif vals[i] < 0:
                streak[i] = (streak[i - 1] - 1) if i > 0 and streak[i - 1] < 0 else -1
        return pd.Series(streak, index=series.index)

    for col, streak_col in [("foreign_net", "foreign_streak"), ("trust_net", "trust_streak")]:
        pivot[streak_col] = pivot.groupby("stock_id")[col].transform(calc_streak)

    latest = pivot[pivot["date"] == pivot["date"].max()].copy()
    db.upsert("daily_institutional", [
        {
            "stock_id": r["stock_id"],
            "date": r["date"].strftime("%Y-%m-%d"),
            "foreign_net": int(r.get("foreign_net", 0) or 0),
            "trust_net": int(r.get("trust_net", 0) or 0),
            "dealer_net": int(r.get("dealer_net", 0) or 0),
            "total_net": int(r.get("total_net", 0) or 0),
            "foreign_streak": int(r.get("foreign_streak", 0) or 0),
            "trust_streak": int(r.get("trust_streak", 0) or 0),
        }
        for _, r in latest.iterrows()
    ])
    return pivot


# ─────────────────────────────────────────────
# 3. 融資融券（每日）
# ─────────────────────────────────────────────

def fetch_margin(universe: set[str], days: int = 60) -> pd.DataFrame:
    rows = _fetch_for_universe("TaiwanStockMarginPurchaseShortSale",
                               universe, _date(days))
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    for col in ["MarginPurchaseBalance", "ShortSaleBalance"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df = df.sort_values(["stock_id", "date"])
    df["margin_balance_prev20"] = df.groupby("stock_id")["MarginPurchaseBalance"].transform(
        lambda x: x.shift(20)
    )
    df["margin_chg_pct"] = (
        (df["MarginPurchaseBalance"] - df["margin_balance_prev20"])
        / df["margin_balance_prev20"].replace(0, float("nan"))
    ).fillna(0)

    latest = df[df["date"] == df["date"].max()].copy()
    db.upsert("daily_margin", [
        {
            "stock_id": r["stock_id"],
            "date": r["date"].strftime("%Y-%m-%d"),
            "margin_balance": int(r.get("MarginPurchaseBalance", 0) or 0),
            "short_balance": int(r.get("ShortSaleBalance", 0) or 0),
            "margin_chg_pct": round(float(r.get("margin_chg_pct", 0) or 0), 4),
        }
        for _, r in latest.iterrows()
    ])
    return df


# ─────────────────────────────────────────────
# 4. 月營收（每週）
# ─────────────────────────────────────────────

def fetch_revenue(universe: set[str], months: int = 6) -> pd.DataFrame:
    rows = _fetch_for_universe("TaiwanStockMonthRevenue",
                               universe, _date(months * 31))
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce").fillna(0)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["stock_id", "date"])
    df["revenue_mom"] = df.groupby("stock_id")["revenue"].pct_change() * 100
    df["revenue_yoy"] = df.groupby("stock_id")["revenue"].pct_change(12) * 100

    db.upsert("monthly_revenue", [
        {
            "stock_id": r["stock_id"],
            "year": int(r["date"].year),
            "month": int(r["date"].month),
            "revenue": int(r["revenue"]),
            "revenue_mom": round(float(r.get("revenue_mom", 0) or 0), 2),
            "revenue_yoy": round(float(r.get("revenue_yoy", 0) or 0), 2),
        }
        for _, r in df.iterrows()
    ])
    return df


# ─────────────────────────────────────────────
# 5. 損益表（季度）
# ─────────────────────────────────────────────

def fetch_income(universe: set[str], days: int = 400) -> pd.DataFrame:
    rows = _fetch_for_universe("TaiwanStockFinancialStatements",
                               universe, _date(days))
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    wanted = {"EPS", "毛利率", "營業利益率", "稅後淨利率", "每股盈餘"}
    df = df[df["type"].isin(wanted)]

    pivot = df.pivot_table(index=["date", "stock_id"], columns="type",
                           values="value", aggfunc="last").reset_index()
    pivot.columns.name = None
    pivot["date"] = pd.to_datetime(pivot["date"])
    if "每股盈餘" in pivot.columns and "EPS" not in pivot.columns:
        pivot["EPS"] = pivot["每股盈餘"]

    pivot = pivot.sort_values(["stock_id", "date"])
    pivot["eps_qoq"] = pivot.groupby("stock_id")["EPS"].pct_change() * 100

    db.upsert("quarterly_income", [
        {
            "stock_id": r["stock_id"],
            "year": int(r["date"].year),
            "quarter": (int(r["date"].month) - 1) // 3 + 1,
            "eps": float(r.get("EPS", 0) or 0),
            "gross_margin": float(r.get("毛利率", 0) or 0),
            "operating_margin": float(r.get("營業利益率", 0) or 0),
            "net_margin": float(r.get("稅後淨利率", 0) or 0),
            "eps_qoq": round(float(r.get("eps_qoq", 0) or 0), 2),
        }
        for _, r in pivot.iterrows()
    ])
    return pivot


# ─────────────────────────────────────────────
# 6. 資產負債表（季度）
# ─────────────────────────────────────────────

def fetch_balance_sheet(universe: set[str], days: int = 400) -> pd.DataFrame:
    rows = _fetch_for_universe("TaiwanStockBalanceSheet", universe, _date(days))
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    wanted = {"負債占資產比率", "流動比率", "速動比率", "負債比率"}
    df = df[df["type"].isin(wanted)]

    pivot = df.pivot_table(index=["date", "stock_id"], columns="type",
                           values="value", aggfunc="last").reset_index()
    pivot.columns.name = None
    pivot["date"] = pd.to_datetime(pivot["date"])
    for col in ["負債占資產比率", "負債比率"]:
        if col in pivot.columns:
            pivot["debt_ratio"] = pivot[col]
            break

    db.upsert("quarterly_balance", [
        {
            "stock_id": r["stock_id"],
            "year": int(r["date"].year),
            "quarter": (int(r["date"].month) - 1) // 3 + 1,
            "debt_ratio": float(r.get("debt_ratio", 0) or 0),
            "current_ratio": float(r.get("流動比率", 0) or 0),
            "quick_ratio": float(r.get("速動比率", 0) or 0),
        }
        for _, r in pivot.iterrows()
    ])
    return pivot


# ─────────────────────────────────────────────
# 7. 現金流量表（季度）
# ─────────────────────────────────────────────

def fetch_cashflow(universe: set[str], days: int = 400) -> pd.DataFrame:
    rows = _fetch_for_universe("TaiwanStockCashFlowsStatement",
                               universe, _date(days))
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    wanted = {"營業活動之現金流量", "本期淨利（淨損）", "稅後淨利"}
    df = df[df["type"].isin(wanted)]

    pivot = df.pivot_table(index=["date", "stock_id"], columns="type",
                           values="value", aggfunc="last").reset_index()
    pivot.columns.name = None
    pivot["date"] = pd.to_datetime(pivot["date"])
    for col in ["本期淨利（淨損）", "稅後淨利"]:
        if col in pivot.columns:
            pivot["net_income"] = pivot[col]
            break
    pivot["ocf"] = pivot.get("營業活動之現金流量", pd.Series(0, index=pivot.index))
    pivot["ocf_quality"] = (
        pivot["ocf"] / pivot["net_income"].replace(0, float("nan"))
    ).fillna(0)

    db.upsert("quarterly_cashflow", [
        {
            "stock_id": r["stock_id"],
            "year": int(r["date"].year),
            "quarter": (int(r["date"].month) - 1) // 3 + 1,
            "operating_cf": float(r.get("ocf", 0) or 0),
            "net_income": float(r.get("net_income", 0) or 0),
            "ocf_quality": round(float(r.get("ocf_quality", 0) or 0), 4),
        }
        for _, r in pivot.iterrows()
    ])
    return pivot


# ─────────────────────────────────────────────
# 8. 股權分散表（每週）
# ─────────────────────────────────────────────

def fetch_shareholding(universe: set[str], days: int = 30) -> pd.DataFrame:
    rows = _fetch_for_universe("TaiwanStockShareholding", universe, _date(days))
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["percent"] = pd.to_numeric(df["percent"], errors="coerce").fillna(0)
    df["date"] = pd.to_datetime(df["date"])

    # 400張以上視為大戶（level 欄位含 400001 以上或 over 字樣）
    big_levels = [l for l in df["HoldingSharesLevel"].unique()
                  if any(x in str(l) for x in
                         ["400001", "600001", "800001", "1000001", "over"])]
    df_big = df[df["HoldingSharesLevel"].isin(big_levels)]
    big_pct = (df_big.groupby(["date", "stock_id"])["percent"]
               .sum().reset_index()
               .rename(columns={"percent": "big_holder_pct"}))

    latest = big_pct[big_pct["date"] == big_pct["date"].max()]
    db.upsert("weekly_shareholding", [
        {
            "stock_id": r["stock_id"],
            "date": r["date"].strftime("%Y-%m-%d"),
            "big_holder_pct": round(float(r["big_holder_pct"]), 4),
        }
        for _, r in latest.iterrows()
    ])
    return big_pct


# ─────────────────────────────────────────────
# 9. 本益比 / 股淨比（每週）
# ─────────────────────────────────────────────

def fetch_valuation(universe: set[str], days: int = 30) -> pd.DataFrame:
    rows = _fetch_for_universe("TaiwanStockPER", universe, _date(days))
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    for col in ["PER", "PBR"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    latest = df[df["date"] == df["date"].max()]
    db.upsert("valuation", [
        {
            "stock_id": r["stock_id"],
            "date": r["date"].strftime("%Y-%m-%d"),
            "per": float(r.get("PER", 0) or 0),
            "pbr": float(r.get("PBR", 0) or 0),
        }
        for _, r in latest.iterrows()
    ])
    return df
