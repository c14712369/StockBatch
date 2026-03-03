"""Telegram 通知模組：格式化訊息並發送。"""
import logging
import requests
from src.config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TOP_N

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"


def _send(text: str) -> None:
    """發送 Markdown 訊息，超過 4096 字自動分段。"""
    chunk_size = 4000
    chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
    for chunk in chunks:
        try:
            resp = requests.post(TELEGRAM_API, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "Markdown",
            }, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Telegram 發送失敗: %s", exc)


def _bar(score: float, max_score: float = 100, width: int = 10) -> str:
    filled = round(score / max_score * width)
    return "█" * filled + "░" * (width - filled)


def _streak_label(days: int) -> str:
    if days > 0:
        return f"連買 {days} 日"
    elif days < 0:
        return f"連賣 {abs(days)} 日"
    return "持平"


# ─────────────────────────────────────────────
# 週報（詳細版 Top N）
# ─────────────────────────────────────────────

def send_weekly_report(scores: list[dict], date_str: str) -> None:
    """
    scores: compute_all_scores 的回傳值，已排序
    只發 passes_filter=True 的前 TOP_N 支
    """
    candidates = [s for s in scores if s["passes_filter"]][:TOP_N]

    lines = [f"📊 *台股潛力週報 {date_str}*\n"
             f"評分範圍：Top {TOP_N} / {len(scores)} 支成分股",
             ""]

    for i, s in enumerate(candidates, 1):
        p_bar = _bar(s["profitability"])
        h_bar = _bar(s["health"])
        c_bar = _bar(s["chip"])
        m_bar = _bar(s["momentum"])

        lines += [
            f"*#{i} {s['stock_name']} ({s['stock_id']})* — 綜合 {s['total']}/100",
            f"┌ 獲利動能 {p_bar} {s['profitability']:.0f}/100",
            f"├ 財務體質 {h_bar} {s['health']:.0f}/100",
            f"├ 籌碼集中 {c_bar} {s['chip']:.0f}/100",
            f"└ 市場動能 {m_bar} {s['momentum']:.0f}/100",
            "",
        ]

    # 附上被篩除的股票
    failed = [s for s in scores if not s["passes_filter"]]
    if failed:
        lines.append("⚠️ *硬性門檻淘汰*")
        for s in failed:
            lines.append(f"  • {s['stock_name']} ({s['stock_id']}): {s['filter_reason']}")

    _send("\n".join(lines))
    logger.info("週報已發送，共 %d 支候選", len(candidates))


# ─────────────────────────────────────────────
# 日報（籌碼 + 動能快報）
# ─────────────────────────────────────────────

def send_daily_report(watchlist: list[dict], date_str: str) -> None:
    """
    watchlist: 每支股票包含
      stock_id, stock_name, close, pct_change, volume,
      foreign_net, foreign_streak, trust_net, trust_streak,
      margin_balance, margin_chg_pct, ma_aligned (bool)
    """
    lines = [f"📡 *今日籌碼快報 {date_str}*", ""]

    # 法人同步買超
    both_buy = [s for s in watchlist
                if s.get("foreign_streak", 0) > 0 and s.get("trust_streak", 0) > 0]
    if both_buy:
        lines.append("🔥 *外資 + 投信同步買超*")
        for s in sorted(both_buy, key=lambda x: x.get("foreign_net", 0), reverse=True):
            f_net = s.get("foreign_net", 0)
            t_net = s.get("trust_net", 0)
            lines.append(
                f"  • *{s['stock_name']} ({s['stock_id']})*: "
                f"外資 {'+' if f_net>=0 else ''}{f_net:,}張 ({_streak_label(s['foreign_streak'])}) | "
                f"投信 {'+' if t_net>=0 else ''}{t_net:,}張 ({_streak_label(s['trust_streak'])})"
            )
        lines.append("")

    # 融資警示
    margin_warn = [s for s in watchlist if s.get("margin_chg_pct", 0) > 0.1]
    if margin_warn:
        lines.append("⚠️ *融資大幅增加（注意風險）*")
        for s in margin_warn:
            chg = s.get("margin_chg_pct", 0) * 100
            lines.append(f"  • {s['stock_name']} ({s['stock_id']}): 融資 +{chg:.1f}%（20日前比）")
        lines.append("")

    # 今日收盤概覽
    lines.append("📈 *今日收盤*")
    for s in sorted(watchlist, key=lambda x: x.get("pct_change", 0), reverse=True):
        close = s.get("close", 0)
        pct = s.get("pct_change", 0)
        vol = s.get("volume", 0)
        ma_tag = "✅ 多頭" if s.get("ma_aligned") else "📉 空頭"
        arrow = "▲" if pct >= 0 else "▼"
        lines.append(
            f"  {ma_tag} *{s['stock_id']}* {close:.1f} "
            f"{arrow}{abs(pct):.1f}% | 量 {vol//1000:,}K張"
        )

    _send("\n".join(lines))
    logger.info("日報已發送，共 %d 支", len(watchlist))


# ─────────────────────────────────────────────
# 晨報（開盤前局勢分析）
# ─────────────────────────────────────────────

def send_morning_briefing(us_data: list[dict], watchlist: list[dict], date_str: str) -> None:
    """
    us_data:  [{"name", "ticker", "close", "pct"}, ...]
    watchlist: [{"stock_id", "stock_name", "close", "pct", "foreign_net", "trust_net"}, ...]
    """
    lines = [f"🌅 *今日開盤前局勢分析 {date_str}*", ""]

    # 美股隔夜表現
    if us_data:
        lines.append("🌏 *美股隔夜收盤*")
        for idx in us_data:
            pct = idx["pct"]
            arrow = "▲" if pct >= 0 else "▼"
            sign = "+" if pct >= 0 else ""
            lines.append(
                f"  {arrow} *{idx['name']}*: {idx['close']:,.2f} ({sign}{pct:.2f}%)"
            )

        # 整體氛圍研判
        sp = next((x for x in us_data if x["ticker"] == "^GSPC"), None)
        vix = next((x for x in us_data if x["ticker"] == "^VIX"), None)
        ewt = next((x for x in us_data if x["ticker"] == "EWT"), None)

        mood = []
        if sp:
            if sp["pct"] >= 1.0:
                mood.append("美股強勁上漲，市場風險偏好高")
            elif sp["pct"] >= 0:
                mood.append("美股小幅收紅，偏多格局")
            elif sp["pct"] >= -1.0:
                mood.append("美股小幅收黑，需留意")
            else:
                mood.append("美股重挫，市場風險偏好低")
        if vix:
            if vix["close"] >= 30:
                mood.append(f"VIX={vix['close']:.1f} 恐慌偏高，操作謹慎")
            elif vix["close"] >= 20:
                mood.append(f"VIX={vix['close']:.1f} 適中")
            else:
                mood.append(f"VIX={vix['close']:.1f} 偏低，情緒穩定")
        if ewt:
            arrow = "▲" if ewt["pct"] >= 0 else "▼"
            mood.append(f"台灣 EWT {arrow}{ewt['pct']:+.2f}%")

        if mood:
            lines.append("")
            lines.append("🧭 *開盤情緒研判*")
            for m in mood:
                lines.append(f"  • {m}")
        lines.append("")

    # Watchlist 昨日回顧
    if watchlist:
        lines.append("📋 *Watchlist 昨收回顧*")
        for s in sorted(watchlist, key=lambda x: x.get("pct", 0), reverse=True):
            pct = s.get("pct", 0)
            arrow = "▲" if pct >= 0 else "▼"
            f_net = s.get("foreign_net", 0)
            t_net = s.get("trust_net", 0)
            chip_tag = ""
            if f_net > 0 and t_net > 0:
                chip_tag = " 🔥外資投信同買"
            elif f_net > 0:
                chip_tag = " 💹外資買超"
            elif t_net > 0:
                chip_tag = " 💹投信買超"
            lines.append(
                f"  {arrow} *{s['stock_id']}* {s['stock_name']} "
                f"{s['close']:.1f} ({pct:+.1f}%){chip_tag}"
            )
        lines.append("")

    lines.append("📌 _台股 09:00 開盤，注意量能與法人動向_")

    _send("\n".join(lines))
    logger.info("晨報已發送")
