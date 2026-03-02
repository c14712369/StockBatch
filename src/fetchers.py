"""
資料抓取模組 v2（完全免費方案）
  股價 + 財務報表 : yfinance
  三大法人 + 融資  : TWSE Open API
  月營收          : FinMind 免費嘗試（失敗則跳過）
"""
import time
import logging
from datetime import date, timedelta
import requests
import pandas as pd
import yfinance as yf
from src import db, finmind

logger = logging.getLogger(__name__)

# ─── 設定 ───────────────────────────────────────
_TWSE_DELAY  = 0.6   # TWSE API 每次請求間隔（秒）
_YF_DELAY    = 0.3   # yfinance 個股財報間隔
_YF_SUFFIX   = ".TW" # 台灣上市股票後綴


def _date(days_ago: int = 0) -> str:
    return (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _trading_dates(n: int) -> list[str]:
    """取最近 n 個「可能的交易日」（週一~五），格式 YYYYMMDD。"""
    result, d = [], date.today() - timedelta(days=1)
    while len(result) < n:
        if d.weekday() < 5:
            result.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return result


def _clean_num(s) -> float:
    """移除千分位逗號並轉 float。"""
    try:
        return float(str(s).replace(",", "").replace("--", "0").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


# ────────────────────────────────────────────────
# TWSE API 基礎函式
# ────────────────────────────────────────────────

def _twse_get(url: str) -> dict:
    try:
        r = requests.get(url, timeout=20,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.debug("TWSE 請求失敗: %s | %s", url, e)
        return {}


# ────────────────────────────────────────────────
# 1. 股價（yfinance 分批下載，避免限速）
# ────────────────────────────────────────────────

_YF_BATCH     = 10   # 每批股票數
_YF_BATCH_DLY = 3.0  # 批次間隔（秒）

def fetch_price(universe: set[str], days: int = 90) -> pd.DataFrame:
    tickers = [f"{sid}{_YF_SUFFIX}" for sid in sorted(universe)]
    start   = _date(days)
    logger.info("yfinance 下載 %d 支股票價格（分批 %d 支）…",
                len(tickers), _YF_BATCH)

    # 分批下載，避免 YFRateLimitError
    frames = []
    for i in range(0, len(tickers), _YF_BATCH):
        batch = tickers[i:i + _YF_BATCH]
        try:
            raw = yf.download(batch, start=start, auto_adjust=True,
                              group_by="ticker", threads=False, progress=False)
        except Exception as e:
            logger.warning("yfinance 批次 %d-%d 失敗: %s", i, i + _YF_BATCH, e)
            time.sleep(_YF_BATCH_DLY)
            continue

        if raw.empty:
            time.sleep(_YF_BATCH_DLY)
            continue

        for ticker in batch:
            sid = ticker.replace(_YF_SUFFIX, "")
            try:
                # 單支或多支下載時 DataFrame 結構不同
                if len(batch) == 1:
                    df = raw.copy()
                else:
                    df = raw[ticker].copy()
                df = df.dropna(subset=["Close"])
            except KeyError:
                continue
            df.index = pd.to_datetime(df.index)
            df = df.rename(columns={"Open": "open", "High": "high",
                                    "Low": "low", "Close": "close",
                                    "Volume": "volume"})
            df["stock_id"] = sid
            df["date"] = df.index
            frames.append(df[["stock_id", "date", "open", "high", "low",
                               "close", "volume"]])

        logger.info("價格進度：%d / %d", min(i + _YF_BATCH, len(tickers)), len(tickers))
        time.sleep(_YF_BATCH_DLY)

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
# 2. 三大法人（TWSE T86，逐日抓）
# ────────────────────────────────────────────────

def _fetch_twse_institutional_one(date_str: str) -> list[dict]:
    """抓單一交易日三大法人（date_str = YYYYMMDD）。"""
    url  = (f"https://www.twse.com.tw/fund/T86"
            f"?date={date_str}&selectType=ALL&response=json")
    body = _twse_get(url)
    if body.get("stat") != "OK" or not body.get("data"):
        return []

    fields = body["fields"]
    rows   = []
    for item in body["data"]:
        d = dict(zip(fields, item))
        rows.append(d)
    return rows


def _find_col(cols: list[str], *keywords: str) -> str:
    """在欄位名稱中找含所有關鍵字的第一個。"""
    for c in cols:
        if all(k in c for k in keywords):
            return c
    return ""


def fetch_institutional(universe: set[str], days: int = 30) -> pd.DataFrame:
    """TWSE 三大法人：抓最近 days 個交易日，計算連買/賣天數。"""
    all_rows, trade_days = [], _trading_dates(days)
    logger.info("TWSE 法人資料：嘗試 %d 個交易日…", len(trade_days))

    for ds in trade_days:
        raw = _fetch_twse_institutional_one(ds)
        if not raw:
            time.sleep(_TWSE_DELAY)
            continue
        cols = list(raw[0].keys())
        f_net_col  = _find_col(cols, "外資及陸資", "買賣超") or _find_col(cols, "外資", "買賣超")
        t_net_col  = _find_col(cols, "投信", "買賣超")
        d_net_col  = _find_col(cols, "自營商", "買賣超")
        id_col     = cols[0]  # 第一欄固定是代號

        for r in raw:
            sid = str(r.get(id_col, "")).strip()
            if sid not in universe:
                continue
            all_rows.append({
                "stock_id":  sid,
                "date":      f"{ds[:4]}-{ds[4:6]}-{ds[6:]}",
                "foreign_net": _clean_num(r.get(f_net_col, 0)),
                "trust_net":   _clean_num(r.get(t_net_col, 0)),
                "dealer_net":  _clean_num(r.get(d_net_col, 0)),
            })
        time.sleep(_TWSE_DELAY)

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
    logger.info("法人資料：%d 筆（%d 交易日）",
                len(df), df["date"].nunique())
    return df


# ────────────────────────────────────────────────
# 3. 融資融券（TWSE MI_MARGN，逐日抓）
# ────────────────────────────────────────────────

def fetch_margin(universe: set[str], days: int = 45) -> pd.DataFrame:
    """TWSE 融資融券：計算與 20 交易日前的餘額變化率。"""
    all_rows, trade_days = [], _trading_dates(days)
    logger.info("TWSE 融資資料：嘗試 %d 個交易日…", len(trade_days))

    for ds in trade_days:
        url  = (f"https://www.twse.com.tw/exchangeReport/MI_MARGN"
                f"?date={ds}&selectType=ALL&response=json")
        body = _twse_get(url)
        if body.get("stat") != "OK" or not body.get("data"):
            time.sleep(_TWSE_DELAY)
            continue
        fields = body["fields"]
        id_col = fields[0]

        margin_bal_col = _find_col(fields, "融資", "今日餘額")
        short_bal_col  = _find_col(fields, "融券", "今日餘額")

        for item in body["data"]:
            d = dict(zip(fields, item))
            sid = str(d.get(id_col, "")).strip()
            if sid not in universe:
                continue
            all_rows.append({
                "stock_id":       sid,
                "date":           f"{ds[:4]}-{ds[4:6]}-{ds[6:]}",
                "margin_balance": _clean_num(d.get(margin_bal_col, 0)),
                "short_balance":  _clean_num(d.get(short_bal_col, 0)),
            })
        time.sleep(_TWSE_DELAY)

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
# 5–7. 財務報表（yfinance 逐股）
# ────────────────────────────────────────────────

def _safe_row(stmt, *candidates) -> pd.Series:
    """從 yfinance 財報 DataFrame 取第一個找到的指標行。"""
    if stmt is None or stmt.empty:
        return pd.Series(dtype=float)
    for name in candidates:
        for idx in stmt.index:
            if name.lower() in str(idx).lower():
                return pd.to_numeric(stmt.loc[idx], errors="coerce")
    return pd.Series(dtype=float)


def _yf_financials(universe: set[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """一次抓全部 50 支的季度損益/資負/現金流，回傳三個 DataFrame。"""
    inc_rows, bal_rows, cf_rows = [], [], []

    for sid in sorted(universe):
        try:
            tkr = yf.Ticker(f"{sid}{_YF_SUFFIX}")

            # ── 損益表 ──
            inc = tkr.quarterly_income_stmt
            rev  = _safe_row(inc, "Total Revenue", "Operating Revenue")
            gp   = _safe_row(inc, "Gross Profit")
            oi   = _safe_row(inc, "Operating Income", "EBIT")
            ni   = _safe_row(inc, "Net Income")
            eps  = _safe_row(inc, "Basic EPS", "Diluted EPS")

            for col in (rev.index if not rev.empty else pd.Index([])):
                rv = rev.get(col, 0) or 0
                gm = (gp.get(col, 0) / rv * 100) if rv else 0
                om = (oi.get(col, 0) / rv * 100) if rv else 0
                nm = (ni.get(col, 0) / rv * 100) if rv else 0
                ep = float(eps.get(col, 0) or 0)
                inc_rows.append({
                    "stock_id": sid, "date": pd.to_datetime(col),
                    "eps": ep, "gross_margin": round(gm, 2),
                    "operating_margin": round(om, 2), "net_margin": round(nm, 2),
                })

            # ── 資產負債表 ──
            bs  = tkr.quarterly_balance_sheet
            ta  = _safe_row(bs, "Total Assets")
            tl  = _safe_row(bs, "Total Liabilities Net Minority Interest", "Total Liabilities")
            ca  = _safe_row(bs, "Current Assets")
            cl  = _safe_row(bs, "Current Liabilities")

            for col in (ta.index if not ta.empty else pd.Index([])):
                ta_ = ta.get(col, 0) or 0
                tl_ = tl.get(col, 0) or 0
                ca_ = ca.get(col, 0) or 0
                cl_ = cl.get(col, 0) or 0
                dr  = (tl_ / ta_ * 100) if ta_ else 0
                cr  = (ca_ / cl_) if cl_ else 0
                bal_rows.append({
                    "stock_id": sid, "date": pd.to_datetime(col),
                    "debt_ratio": round(dr, 2), "current_ratio": round(cr, 2),
                    "quick_ratio": round(cr, 2),  # 簡化：速動比 ≈ 流動比
                })

            # ── 現金流量表 ──
            cfst = tkr.quarterly_cashflow
            ocf  = _safe_row(cfst, "Operating Cash Flow")
            ni2  = _safe_row(cfst, "Net Income", "Net Income From Continuing Operations")

            for col in (ocf.index if not ocf.empty else pd.Index([])):
                o = float(ocf.get(col, 0) or 0)
                n = float(ni2.get(col, 0) or 0)
                q = (o / n) if n else 0
                cf_rows.append({
                    "stock_id": sid, "date": pd.to_datetime(col),
                    "operating_cf": o, "net_income": n,
                    "ocf_quality": round(q, 4),
                })

        except Exception as e:
            logger.debug("yfinance 財報失敗 %s: %s", sid, e)
        time.sleep(_YF_DELAY)

    def _build(rows: list[dict]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values(["stock_id", "date"])
        return df

    return _build(inc_rows), _build(bal_rows), _build(cf_rows)


def fetch_income(universe: set[str], **_) -> pd.DataFrame:
    inc, _, _ = _yf_financials(universe)
    if not inc.empty:
        inc["eps_qoq"] = inc.groupby("stock_id")["eps"].pct_change() * 100
        db.upsert("quarterly_income", [
            {
                "stock_id":         r["stock_id"],
                "year":             int(r["date"].year),
                "quarter":          (int(r["date"].month) - 1) // 3 + 1,
                "eps":              float(r.get("eps", 0) or 0),
                "gross_margin":     float(r.get("gross_margin", 0) or 0),
                "operating_margin": float(r.get("operating_margin", 0) or 0),
                "net_margin":       float(r.get("net_margin", 0) or 0),
                "eps_qoq":          round(float(r.get("eps_qoq", 0) or 0), 2),
            }
            for _, r in inc.iterrows()
        ])
    return inc


def fetch_balance_sheet(universe: set[str], **_) -> pd.DataFrame:
    _, bal, _ = _yf_financials(universe)
    if not bal.empty:
        db.upsert("quarterly_balance", [
            {
                "stock_id":     r["stock_id"],
                "year":         int(r["date"].year),
                "quarter":      (int(r["date"].month) - 1) // 3 + 1,
                "debt_ratio":   float(r.get("debt_ratio", 0) or 0),
                "current_ratio": float(r.get("current_ratio", 0) or 0),
                "quick_ratio":  float(r.get("quick_ratio", 0) or 0),
            }
            for _, r in bal.iterrows()
        ])
    return bal


def fetch_cashflow(universe: set[str], **_) -> pd.DataFrame:
    _, _, cf = _yf_financials(universe)
    if not cf.empty:
        db.upsert("quarterly_cashflow", [
            {
                "stock_id":   r["stock_id"],
                "year":       int(r["date"].year),
                "quarter":    (int(r["date"].month) - 1) // 3 + 1,
                "operating_cf": float(r.get("operating_cf", 0) or 0),
                "net_income": float(r.get("net_income", 0) or 0),
                "ocf_quality": round(float(r.get("ocf_quality", 0) or 0), 4),
            }
            for _, r in cf.iterrows()
        ])
    return cf


# ────────────────────────────────────────────────
# 8. 股權分散（FinMind 免費嘗試）
# ────────────────────────────────────────────────

def fetch_shareholding(universe: set[str], days: int = 30) -> pd.DataFrame:
    all_rows = []
    start = _date(days)
    for sid in sorted(universe):
        rows = finmind.fetch("TaiwanStockShareholding",
                             start_date=start, stock_id=sid)
        all_rows.extend(rows)
        if rows:
            time.sleep(0.2)

    if not all_rows:
        logger.warning("股權分散：FinMind 免費版無資料，此維度跳過")
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    logger.debug("股權分散欄位: %s", list(df.columns))

    # 自動找持股比例欄與分級欄（欄位名稱視 FinMind 版本而異）
    pct_col   = next((c for c in df.columns if "percent" in c.lower()), None)
    level_col = next((c for c in df.columns
                      if "level" in c.lower() or "Level" in c), None)

    if pct_col is None or level_col is None:
        logger.warning("股權分散：欄位不符預期 %s，跳過", list(df.columns))
        return pd.DataFrame()

    df["percent"] = pd.to_numeric(df[pct_col], errors="coerce").fillna(0)
    df["date"]    = pd.to_datetime(df["date"])

    big_levels = [l for l in df[level_col].unique()
                  if any(x in str(l) for x in
                         ["400001", "600001", "800001", "1000001", "over"])]
    big_pct = (df[df[level_col].isin(big_levels)]
               .groupby(["date", "stock_id"])["percent"]
               .sum().reset_index()
               .rename(columns={"percent": "big_holder_pct"}))

    latest = big_pct[big_pct["date"] == big_pct["date"].max()]
    db.upsert("weekly_shareholding", [
        {
            "stock_id": r["stock_id"],
            "date":     r["date"].strftime("%Y-%m-%d"),
            "big_holder_pct": round(float(r["big_holder_pct"]), 4),
        }
        for _, r in latest.iterrows()
    ])
    return big_pct


# ────────────────────────────────────────────────
# 9. 本益比 / 股淨比（yfinance info）
# ────────────────────────────────────────────────

def fetch_valuation(universe: set[str], **_) -> pd.DataFrame:
    rows = []
    today = date.today().strftime("%Y-%m-%d")
    for sid in sorted(universe):
        try:
            info = yf.Ticker(f"{sid}{_YF_SUFFIX}").info
            rows.append({
                "stock_id": sid,
                "date":     today,
                "per":      float(info.get("trailingPE", 0) or 0),
                "pbr":      float(info.get("priceToBook", 0) or 0),
            })
        except Exception:
            pass
        time.sleep(_YF_DELAY)

    if rows:
        db.upsert("valuation", rows)
    logger.info("估值資料：%d 支", len(rows))
    return pd.DataFrame(rows)
