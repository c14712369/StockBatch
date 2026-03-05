# AGENTS.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

Automated Taiwan stock screening system targeting 0050 ETF constituents (50 stocks). Runs on GitHub Actions, stores data in Supabase (PostgreSQL), and pushes alerts to Telegram. All code and comments are in Traditional Chinese (繁體中文).

- **Language**: Python 3.12
- **Data sources**: FinMind API (primary), TWSE MIS API (intraday realtime)
- **Database**: Supabase (service_role key, not anon key)
- **Notifications**: Telegram Bot API with Markdown formatting

## Build and Run Commands

```
# Install dependencies
pip install -r requirements.txt

# Run jobs (require all 5 env vars: FINMIND_TOKEN, SUPABASE_URL, SUPABASE_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
python -m src.weekly_job      # Full weekly pipeline: fetch all data → score → push Top 10
python -m src.daily_job       # Post-market daily report for watchlist
python -m src.morning_job     # Pre-market briefing (reads Supabase only, no external API calls)
python -m src.intraday_job    # Intraday alerts via TWSE MIS API (signal-based, silent if no alerts)
```

There is no test suite, linter, or type checker configured. The only test file is `test_price.py` (a manual FinMind connectivity check).

## Architecture

### Data Pipeline (4 scheduled jobs)

The system follows a strict dependency chain:

1. **weekly_job** (Sun 20:00 TST) — The foundational job. Fetches all fundamental/technical data for all 50 stocks via FinMind, computes 4-dimension scores with hard filters, upserts to Supabase, sends weekly Telegram report. Must run before any daily/morning/intraday job has data.

2. **daily_job** (Weekdays 18:30 TST) — Post-market. Reads watchlist from `weekly_scores`, fetches fresh price/institutional/margin data for Top 10 only, upserts latest day to Supabase, sends daily Telegram report.

3. **morning_job** (Weekdays 07:30 TST) — Pre-market. Reads everything from Supabase (zero external API calls). Depends on daily_job having run the previous evening.

4. **intraday_job** (Weekdays 9:00–13:00 TST, hourly) — Uses TWSE MIS realtime API (not FinMind). Signal-based: only sends Telegram alert if ±2% move, breakout above yesterday's high, or breakdown below yesterday's low. Silent otherwise.

### Scoring Engine (scorers.py)

Two-phase selection:
- **Phase 1 — Hard filters** (any failure = elimination): OCF > 0, debt ratio < 60%, recent 3-month YOY revenue not all negative (NaN values are skipped via `.dropna()`, not treated as negative).
- **Phase 2 — Weighted scoring** (0–100): profitability 30%, health 20%, chip concentration 30%, momentum 20%. Weights are in `config.py::WEIGHTS`.
- **Phase 3 — PE adjustment** (post-score, ±0–10 pts): self-calculated trailing P/E using latest close price / sum of last 4 quarters EPS. Requires ≥4 quarters of data. PE ≤15 → +5, 15–25 → 0, 25–40 → −5, >40 → −10. Final total is clamped to [0, 100].

### Key Module Roles

- `config.py` — All env vars, scoring weights (`WEIGHTS`), and `TOP_N` (how many stocks to push).
- `universe.py` — Hardcoded 0050 constituent list (`TAIWAN_50`). Updated quarterly by hand.
- `finmind.py` — FinMind REST client with 3-retry logic. Auto-skips paid-only datasets (402 or "register" in response).
- `fetchers.py` — All data fetching + MA calculation + Supabase upsert. Each `fetch_*` function both returns a DataFrame and writes to Supabase as a side effect.
- `db.py` — Thin Supabase wrapper. `_sanitize()` converts NaN/Inf to None before upsert. Singleton client via `get_client()`.
- `notifier.py` — Telegram message formatting and sending. Auto-chunks messages >4000 chars. Contains formatters for all 4 report types (weekly, daily, morning, intraday).

### FinMind Free-Tier Limitations

Several FinMind datasets require a paid subscription. The code handles this gracefully:
- `fetch_shareholding()` and `fetch_valuation()` immediately return empty DataFrames (hardcoded skip).
- `fetch_revenue()` attempts the call but returns empty if FinMind responds with 402 or "register".
- Chip score (`score_chip`) dynamically rescales to 100 based on which data sources are actually available (`max_score` tracking), rather than assigning 0 for missing dimensions.
- PE is self-calculated from existing price + income data — no extra API call needed.

## Important Patterns

- **Side-effect fetchers**: Every `fetchers.fetch_*` function writes to Supabase AND returns a DataFrame. Both outputs are used downstream.
- **FinMind rate limiting**: A `_FM_DELAY = 0.3s` pause between per-stock API calls. Stocks are fetched one-by-one (not batch) for most datasets.
- **Safe numeric handling**: `_clean_num()`, `_to_int()`, and `db._sanitize()` are used throughout to prevent NaN/Inf from reaching Supabase JSON serialization.
- **Streak calculation**: `calc_streak()` in `fetchers.py` computes consecutive buy/sell days as positive/negative integers. Used for institutional investor analysis.
- **DataFrame filtering convention**: `scorers.py` uses `_filter(df, stock_id)` and `_safe_sort(df)` helpers — always check for empty DataFrames and missing columns before accessing `.iloc[-1]`.

## Scheduling

**Primary: GCP e2-micro crontab** (on Linux host at `/home/c14712369/StockBatch/.env`):
- 08:30 TST weekdays → `morning_job`
- 09:00–13:00 TST weekdays (hourly) → `intraday_job`
- 18:30 TST weekdays → `daily_job`
- 20:00 TST Sunday → `weekly_job`

**GitHub Actions** (`.github/workflows/`): All `schedule:` triggers have been removed. Only `workflow_dispatch` is retained for emergency manual runs. Do NOT re-add cron triggers — this would cause duplicate Telegram pushes.

## Database Schema

Schema is defined in `supabase/schema.sql`. All tables use composite primary keys for natural upsert behavior. The `weekly_scores` table (PK: `stock_id, week_date`) is the central join point that connects the weekly scoring pipeline to all downstream daily/morning/intraday jobs.
