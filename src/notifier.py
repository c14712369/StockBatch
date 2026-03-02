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
