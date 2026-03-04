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

def send_daily_report(watchlist: list[dict], date_str: str, paper_summary: list[dict] = None) -> None:
    """
    watchlist: 每支股票包含
      stock_id, stock_name, close, pct_change, volume,
      foreign_net, foreign_streak, trust_net, trust_streak,
      margin_balance, margin_chg_pct, ma_aligned (bool)
    paper_summary: (可選) 模擬交易的績效總結，格式 [{week_date, avg_pnl_pct, best_stock:{stock_name, pnl_pct}, count}]
    """
    lines = [f"📡 *今日籌碼快報 {date_str}*", ""]

    # 模擬交易績效追蹤 (Paper Trading)
    if paper_summary:
        lines.append("💼 *模擬投資組合績效 (每週Top10)*")
        for summary in paper_summary:
            week = summary['week_date']
            avg = summary['avg_pnl_pct']
            cnt = summary['count']
            best = summary['best_stock']
            
            avg_sign = "+" if avg >= 0 else ""
            lines.append(f"  • {week} 選股 ({cnt}檔): 均報 {avg_sign}{avg:.1f}%")
            if best:
                best_name = best['stock_name']
                best_pnl = best['pnl_pct']
                best_sign = "+" if best_pnl >= 0 else ""
                lines.append(f"    最佳: {best_name} ({best_sign}{best_pnl:.1f}%)")
        lines.append("")

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
# 晨報（開盤前：直接讀 Supabase，不打外部 API）
# ─────────────────────────────────────────────

def send_morning_briefing(watchlist: list[dict], date_str: str) -> None:
    """
    watchlist: 每支包含
      stock_id, stock_name, total_score, close, high, low, volume,
      ma_aligned, foreign_streak, trust_streak, foreign_net, trust_net,
      margin_chg_pct
    """
    lines = [f"🌅 *開盤前晨報 {date_str}*", ""]

    # 重點關注：多頭排列 + 法人買超，取前 3
    highlights = [
        s for s in watchlist
        if s.get("ma_aligned") and (s.get("foreign_streak", 0) > 0 or s.get("trust_streak", 0) > 0)
    ][:3]

    if highlights:
        lines.append("🔥 *今日重點關注*")
        for s in highlights:
            f_str = _streak_label(s.get("foreign_streak", 0))
            t_str = _streak_label(s.get("trust_streak", 0))
            f_net = s.get("foreign_net", 0)
            lines += [
                f"  *{s['stock_name']} ({s['stock_id']})* 📊{s['total_score']:.0f}分",
                f"  昨收 {s['close']:.1f} ✅多頭 | 外資{f_str}({f_net:+,}張) | 投信{t_str}",
                "",
            ]

    # Watchlist 全覽
    lines.append("📋 *Watchlist 全覽*")
    for s in watchlist:
        ma_tag = "✅" if s.get("ma_aligned") else "📉"
        f_streak = s.get("foreign_streak", 0)
        t_streak = s.get("trust_streak", 0)
        mg = s.get("margin_chg_pct", 0) * 100
        mg_tag = f" ⚠️融資+{mg:.1f}%" if mg > 10 else ""

        chip_parts = []
        if f_streak != 0:
            chip_parts.append(f"外資{_streak_label(f_streak)}")
        if t_streak != 0:
            chip_parts.append(f"投信{_streak_label(t_streak)}")
        chip_str = " | ".join(chip_parts) if chip_parts else "法人持平"

        lines.append(
            f"  {ma_tag} *{s['stock_id']}* {s['stock_name']} "
            f"{s['close']:.1f} | {chip_str}{mg_tag}"
        )

    # 融資警示
    margin_warn = [s for s in watchlist if s.get("margin_chg_pct", 0) * 100 > 10]
    if margin_warn:
        lines += ["", "⚠️ *融資大增（注意風險）*"]
        for s in margin_warn:
            mg = s.get("margin_chg_pct", 0) * 100
            lines.append(f"  • {s['stock_name']} ({s['stock_id']}): 融資較20日前 +{mg:.1f}%")

    lines += ["", "📌 _台股 09:00 開盤_"]

    _send("\n".join(lines))
    logger.info("晨報已發送，%d 支", len(watchlist))


# ─────────────────────────────────────────────
# 盤中快報（有訊號才發）
# ─────────────────────────────────────────────

def send_intraday_alert(alerts: list[dict], time_str: str) -> None:
    """
    alerts: 每支包含
      stock_id, stock_name, price, pct, prev_close, volume, signals (list[str])
    """
    lines = [f"📡 *盤中快報 {time_str}*", ""]

    surge  = [a for a in alerts if a["pct"] >= 2.0]
    plunge = [a for a in alerts if a["pct"] <= -2.0]
    others = [a for a in alerts if -2.0 < a["pct"] < 2.0]

    if surge:
        lines.append("🚀 *強勢上漲*")
        for a in sorted(surge, key=lambda x: x["pct"], reverse=True):
            sig = " | ".join(a["signals"])
            lines.append(
                f"  *{a['stock_name']} ({a['stock_id']})*: "
                f"{a['price']:.1f} ▲{a['pct']:.1f}% | {sig} | 量{a['volume']:,}張"
            )
        lines.append("")

    if plunge:
        lines.append("🔻 *急殺警示*")
        for a in sorted(plunge, key=lambda x: x["pct"]):
            sig = " | ".join(a["signals"])
            lines.append(
                f"  *{a['stock_name']} ({a['stock_id']})*: "
                f"{a['price']:.1f} ▼{abs(a['pct']):.1f}% | {sig} | 量{a['volume']:,}張"
            )
        lines.append("")

    if others:
        lines.append("📌 *其他訊號*")
        for a in others:
            sig = " | ".join(a["signals"])
            arrow = "▲" if a["pct"] >= 0 else "▼"
            lines.append(
                f"  *{a['stock_name']} ({a['stock_id']})*: "
                f"{a['price']:.1f} {arrow}{abs(a['pct']):.1f}% | {sig}"
            )

    _send("\n".join(lines))
    logger.info("盤中快報已發送，%d 支異動", len(alerts))
