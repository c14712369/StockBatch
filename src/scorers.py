"""
評分引擎：兩階段選股
  第一階段 — 硬性門檻過濾（任一不過即淘汰）
  第二階段 — 加權評分 0~100 分
"""
import logging
import pandas as pd
from src.config import WEIGHTS

logger = logging.getLogger(__name__)


def _lerp(value: float, low: float, high: float,
          score_low: float, score_high: float) -> float:
    """在 [low, high] 區間內對 value 做線性插值，回傳 [score_low, score_high] 之間的分數。"""
    if high == low:
        return score_high
    t = max(0.0, min(1.0, (value - low) / (high - low)))
    return score_low + t * (score_high - score_low)


def _filter(df: pd.DataFrame, stock_id: str) -> pd.DataFrame:
    """安全過濾：空 DataFrame 或缺少 stock_id 欄時直接回傳空。"""
    if df.empty or "stock_id" not in df.columns:
        return pd.DataFrame()
    return df[df["stock_id"] == stock_id]


def _safe_sort(df: pd.DataFrame) -> pd.DataFrame:
    """sort_values("date") 的安全版本；空 DataFrame 或無 date 欄時直接回傳。"""
    return df.sort_values("date") if not df.empty and "date" in df.columns else df


# ─────────────────────────────────────────────
# 硬性門檻
# ─────────────────────────────────────────────

def hard_filter(stock_id: str, income: pd.DataFrame, balance: pd.DataFrame,
                cashflow: pd.DataFrame, revenue: pd.DataFrame) -> tuple[bool, str]:
    """
    回傳 (通過, 失敗原因)。
    條件：
      1. 最近一季 OCF > 0
      2. 最近一季負債比 < 60%
      3. 近 3 個月營收 YOY 未連續全部為負
    """
    # --- 1. OCF > 0 ---
    cf = _safe_sort(_filter(cashflow, stock_id)) if not cashflow.empty and "stock_id" in cashflow.columns else pd.DataFrame()
    if not cf.empty:
        if cf.iloc[-1].get("operating_cf", 0) <= 0:
            return False, f"最近一季 OCF = {cf.iloc[-1].get('operating_cf', 0):,.0f}"

    # --- 2. 負債比 < 60% ---
    bs = _safe_sort(_filter(balance, stock_id)) if not balance.empty and "stock_id" in balance.columns else pd.DataFrame()
    if not bs.empty:
        debt_ratio = bs.iloc[-1].get("debt_ratio", 0)
        if debt_ratio > 60:
            return False, f"負債比 {debt_ratio:.1f}% > 60%"

    # --- 3. 近 3 月 YOY 未全負 ---
    rev = _safe_sort(_filter(revenue, stock_id)) if not revenue.empty and "stock_id" in revenue.columns else pd.DataFrame()
    if len(rev) >= 3:
        last3 = rev.tail(3)["revenue_yoy"].dropna().tolist()
        if len(last3) >= 3 and all(v < 0 for v in last3):
            return False, f"近 3 月 YOY 均為負 ({[round(v, 1) for v in last3]})"

    return True, ""


# ─────────────────────────────────────────────
# 獲利動能 (0~100)
# ─────────────────────────────────────────────

def score_profitability(stock_id: str, income: pd.DataFrame,
                        revenue: pd.DataFrame) -> float:
    score = 0.0

    # 近 3 月平均 YOY（40分）—— 線性插值讓評分連續
    rev = _safe_sort(_filter(revenue, stock_id))
    if not rev.empty:
        avg_yoy = rev.tail(3)["revenue_yoy"].mean()
        if pd.isna(avg_yoy):
            logger.debug("%s 月營收 YOY 全為 NaN，獲利動能 YOY 分項得 0", stock_id)
        elif avg_yoy >= 30:
            score += 40
        elif avg_yoy >= 15:
            score += _lerp(avg_yoy, 15, 30, 30, 40)
        elif avg_yoy >= 5:
            score += _lerp(avg_yoy, 5, 15, 20, 30)
        elif avg_yoy > 0:
            score += _lerp(avg_yoy, 0, 5, 10, 20)

    # EPS QoQ 成長（30分）—— 線性插值
    inc = _safe_sort(_filter(income, stock_id))
    if not inc.empty:
        qoq = inc.iloc[-1].get("eps_qoq", 0) or 0
        if qoq >= 20:
            score += 30
        elif qoq >= 10:
            score += _lerp(qoq, 10, 20, 20, 30)
        elif qoq > 0:
            score += _lerp(qoq, 0, 10, 10, 20)

    # 毛利率趨勢：近兩季是否上升（30分）
    if len(inc) >= 2:
        gm_now = inc.iloc[-1].get("gross_margin", 0) or 0
        gm_prev = inc.iloc[-2].get("gross_margin", 0) or 0
        if gm_now > gm_prev + 1:
            score += 30
        elif gm_now > gm_prev:
            score += 15

    return min(score, 100)


# ─────────────────────────────────────────────
# 財務體質 (0~100)
# ─────────────────────────────────────────────

def score_health(stock_id: str, balance: pd.DataFrame,
                 cashflow: pd.DataFrame) -> float:
    """財務體質評分 0~100（流動比 30 + 負債比 30 + OCF品質 40）。
    PE 調整已移至 compute_all_scores 作後置處理，避免滿分截斷。
    """
    score = 0.0

    bs = _safe_sort(_filter(balance, stock_id))
    if not bs.empty:
        latest = bs.iloc[-1]

        # 流動比率（30分）—— 線性插值
        cur = latest.get("current_ratio", 0) or 0
        if cur >= 2.0:
            score += 30
        elif cur >= 1.5:
            score += _lerp(cur, 1.5, 2.0, 20, 30)
        elif cur >= 1.0:
            score += _lerp(cur, 1.0, 1.5, 10, 20)

        # 負債比（30分）—— 線性插值（負債比越低越好）
        debt = latest.get("debt_ratio", 0) or 0
        if debt < 30:
            score += 30
        elif debt < 45:
            score += _lerp(debt, 30, 45, 30, 20)
        elif debt < 60:
            score += _lerp(debt, 45, 60, 20, 10)

    # OCF 品質 = OCF / 淨利（40分）—— 線性插值
    cf = _safe_sort(_filter(cashflow, stock_id))
    if not cf.empty:
        quality = cf.iloc[-1].get("ocf_quality", 0) or 0
        if quality >= 1.2:
            score += 40
        elif quality >= 0.8:
            score += _lerp(quality, 0.8, 1.2, 30, 40)
        elif quality >= 0.5:
            score += _lerp(quality, 0.5, 0.8, 15, 30)

    return min(score, 100)


# ─────────────────────────────────────────────
# 籌碼集中度 (0~100)
# ─────────────────────────────────────────────

def score_chip(stock_id: str, institutional: pd.DataFrame,
               margin: pd.DataFrame, shareholding: pd.DataFrame) -> float:
    score = 0.0
    max_score = 0.0

    # 外資 + 投信連續買超天數（40分）
    inst = _filter(institutional, stock_id)
    if not inst.empty:
        max_score += 40.0
        latest = inst.sort_values("date").iloc[-1]
        foreign_streak = latest.get("foreign_streak", 0) or 0
        trust_streak = latest.get("trust_streak", 0) or 0

        # 外資（20分）
        if foreign_streak >= 7:
            score += 20
        elif foreign_streak >= 3:
            score += 14
        elif foreign_streak > 0:
            score += 7

        # 投信（20分）
        if trust_streak >= 5:
            score += 20
        elif trust_streak >= 2:
            score += 12
        elif trust_streak > 0:
            score += 6

    # 大戶持股比（30分）—— 比例越高越好
    sh = _filter(shareholding, stock_id)
    if not sh.empty:
        max_score += 30.0
        big_pct = sh.sort_values("date").iloc[-1].get("big_holder_pct", 0) or 0
        if big_pct >= 70:
            score += 30
        elif big_pct >= 55:
            score += 20
        elif big_pct >= 40:
            score += 10

    # 融資水位（30分）—— 增幅越小越好（融資高位=危險）
    mg = _filter(margin, stock_id)
    if not mg.empty:
        max_score += 30.0
        chg = mg.sort_values("date").iloc[-1].get("margin_chg_pct", 0) or 0
        if chg <= -0.05:          # 融資減少 > 5%
            score += 30
        elif chg <= 0:            # 融資持平或微減
            score += 20
        elif chg <= 0.05:         # 融資微增
            score += 10
        # 融資大幅增加 → 0分
        
    # 動態依據取得的資料維度，將總分等比例放大至滿分 100 分
    if max_score > 0:
        score = score * (100.0 / max_score)

    return min(score, 100)


# ─────────────────────────────────────────────
# 市場動能 (0~100)
# ─────────────────────────────────────────────

def score_momentum(stock_id: str, price: pd.DataFrame) -> float:
    score = 0.0

    px = _safe_sort(_filter(price, stock_id))
    if px.empty:
        return 0.0

    latest = px.iloc[-1]
    close = latest.get("close", 0) or 0
    ma5 = latest.get("ma5", 0) or 0
    ma20 = latest.get("ma20", 0) or 0
    ma60 = latest.get("ma60", 0) or 0

    # 均線多頭排列（40分）
    if close > ma5 > ma20 > ma60:
        score += 40
    elif close > ma20 > ma60:
        score += 25
    elif close > ma60:
        score += 10

    # 收盤 vs 20MA（30分）—— 線性插值
    if ma20 > 0:
        pct_vs_ma20 = (close - ma20) / ma20 * 100
        if pct_vs_ma20 >= 5:
            score += 30
        elif pct_vs_ma20 >= 2:
            score += _lerp(pct_vs_ma20, 2, 5, 20, 30)
        elif pct_vs_ma20 > 0:
            score += _lerp(pct_vs_ma20, 0, 2, 10, 20)

    # 量能趨勢：近 5 日均量 vs 近 20 日均量（30分）—— 線性插值
    if len(px) >= 20:
        vol_5 = px.tail(5)["volume"].mean()
        vol_20 = px.tail(20)["volume"].mean()
        if vol_20 > 0:
            vol_ratio = vol_5 / vol_20
            if vol_ratio >= 1.5:
                score += 30
            elif vol_ratio >= 1.2:
                score += _lerp(vol_ratio, 1.2, 1.5, 20, 30)
            elif vol_ratio >= 1.0:
                score += _lerp(vol_ratio, 1.0, 1.2, 10, 20)

    return min(score, 100)


# ─────────────────────────────────────────────
# 綜合評分
# ─────────────────────────────────────────────

def compute_all_scores(universe: list[dict],
                       price: pd.DataFrame,
                       institutional: pd.DataFrame,
                       margin: pd.DataFrame,
                       revenue: pd.DataFrame,
                       income: pd.DataFrame,
                       balance: pd.DataFrame,
                       cashflow: pd.DataFrame,
                       shareholding: pd.DataFrame) -> list[dict]:
    """
    對所有 universe 股票打分，回傳包含評分的 list，已排序。
    """
    results = []
    for stock in universe:
        sid = stock["stock_id"]
        name = stock.get("stock_name", sid)

        # 第一階段：硬性過濾
        passed, reason = hard_filter(sid, income, balance, cashflow, revenue)

        p = score_profitability(sid, income, revenue)

        pe_ratio = 0.0
        px = _safe_sort(_filter(price, sid))
        inc = _safe_sort(_filter(income, sid))
        if not px.empty and len(inc) >= 4:
            close_price = px.iloc[-1].get("close", 0) or 0
            last_4_eps = inc.tail(4)["eps"].sum()
            if last_4_eps > 0 and close_price > 0:
                pe_ratio = round(close_price / last_4_eps, 1)

        h = score_health(sid, balance, cashflow)
        c = score_chip(sid, institutional, margin, shareholding)
        m = score_momentum(sid, price)

        total = (
            p * WEIGHTS["profitability"] +
            h * WEIGHTS["health"] +
            c * WEIGHTS["chip"] +
            m * WEIGHTS["momentum"]
        )

        # PE 後置調整（±0~10分）：低估加分、過高扣分
        pe_adj = 0.0
        if pe_ratio > 0:
            if pe_ratio < 15:
                pe_adj = 10.0
            elif pe_ratio < 20:
                pe_adj = 5.0
            elif pe_ratio >= 35:
                pe_adj = -10.0
            elif pe_ratio >= 25:
                pe_adj = -5.0
        total = max(0.0, min(100.0, total + pe_adj))

        results.append({
            "stock_id": sid,
            "stock_name": name,
            "passes_filter": passed,
            "filter_reason": reason,
            "profitability": round(p, 1),
            "health": round(h, 1),
            "chip": round(c, 1),
            "momentum": round(m, 1),
            "pe": pe_ratio,
            "total": round(total, 1),
        })
        logger.debug("%s %s: P=%.0f H=%.0f C=%.0f M=%.0f PE=%.1f(adj%+.0f) → %.1f %s",
                     sid, name, p, h, c, m, pe_ratio, pe_adj, total,
                     "" if passed else f"[篩除: {reason}]")

    results.sort(key=lambda x: (x["passes_filter"], x["total"]), reverse=True)
    return results
