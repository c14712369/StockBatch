""" backtest_engine.py
此腳本保留了核心的歷史回測基礎建設。
功能：
1. 本地快取：避免重複呼叫 YFinance / FinMind API。
2. 歷史回測：模擬過去 n 週的 `weekly_job.py` 邏輯。
"""
import os
import sys
import json
import logging
import hashlib
from datetime import datetime, date, timedelta
import pandas as pd
from typing import Callable, Any

# Ensure src module can be imported even when run from scripts/ folder
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src import fetchers, scorers
from src.universe import get_universe, get_universe_ids

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

CACHE_DIR = "cache/backtest"

def _ensure_cache_dir():
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)

def _get_cache_key(func_name: str, *args, **kwargs) -> str:
    key_str = f"{func_name}_{args}_{kwargs}"
    return hashlib.md5(key_str.encode()).hexdigest()

def cached_fn(func: Callable, cache_days: int = 7) -> Callable:
    """快取裝飾器：將抓取資料的結果存到本地 JSON，避免回測重複發 API。"""
    def wrapper(*args, **kwargs):
        _ensure_cache_dir()
        key = _get_cache_key(func.__name__, *args, **kwargs)
        cache_file = os.path.join(CACHE_DIR, f"{key}.json")
        
        # 檢查快取是否有效
        if os.path.exists(cache_file):
            mtime = os.path.getmtime(cache_file)
            mdate = datetime.fromtimestamp(mtime)
            if (datetime.now() - mdate).days < cache_days:
                with open(cache_file, "r", encoding="utf-8") as f:
                    logger.debug("Hit cache for %s", func.__name__)
                    cache_data = json.load(f)
                    
                    # 將 list of dict 轉回 DataFrame (針對 fetchers)
                    if isinstance(cache_data, list) and not list and cache_data:
                        pass
                    if "price" in func.__name__ or "institutional" in func.__name__ or "margin" in func.__name__ or "revenue" in func.__name__ or "shareholding" in func.__name__:
                        return pd.DataFrame(cache_data)
                    # financials 回傳 tuple of DataFrames
                    if "financials" in func.__name__:
                        return (pd.DataFrame(cache_data[0]), pd.DataFrame(cache_data[1]), pd.DataFrame(cache_data[2]))
                        
                    return cache_data

        # 呼叫原始函式
        logger.info("Executing API fetch for %s", func.__name__)
        result = func(*args, **kwargs)
        
        # 存快取: 使用 pandas 內建的 to_json 來處理 datetime 和 numpy data types
        if isinstance(result, pd.DataFrame):
            cache_data = json.loads(result.to_json(orient="records", date_format="iso"))
        elif isinstance(result, tuple) and "financials" in func.__name__:
            cache_data = [json.loads(df.to_json(orient="records", date_format="iso")) for df in result]
        else:
            cache_data = result

        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False)
            
        return result
    return wrapper


# 替換原先的 API 呼叫為具備快取功能的版本
fetch_price_cached = cached_fn(fetchers.fetch_price, cache_days=1)
fetch_institutional_cached = cached_fn(fetchers.fetch_institutional, cache_days=1)
fetch_margin_cached = cached_fn(fetchers.fetch_margin, cache_days=1)
fetch_revenue_cached = cached_fn(fetchers.fetch_revenue, cache_days=7)
fetch_financials_cached = cached_fn(fetchers.fetch_financials, cache_days=7)
fetch_shareholding_cached = cached_fn(fetchers.fetch_shareholding, cache_days=7)


def run_backtest(weeks: int = 12):
    """
    執行近 n 週的回測。
    由於 YFinance 不支援精確的 Point-in-Time 財報時間點限制，我們假設目前抓下來的資料
    （例如三表）套用到過去每一週來進行「簡化的回測」。
    （更嚴謹的做法需建立專門的歷史 Dataset）
    """
    logger.info("═══ 歷史回測啟動 (過去 %d 週) ═══", weeks)
    
    universe_list = get_universe()
    universe_ids = get_universe_ids()
    
    # 預先抓好這 n 週所需的區間資料（一次抓大範圍並快取）
    days_to_fetch = weeks * 7 + 100 # 保留算均線的 buffer
    
    logger.info("預載巨量歷史資料...")
    price_df = fetch_price_cached(universe_ids, days=days_to_fetch)
    inst_df = fetch_institutional_cached(universe_ids, days=days_to_fetch)
    margin_df = fetch_margin_cached(universe_ids, days=days_to_fetch)
    rev_df = fetch_revenue_cached(universe_ids, months=(weeks // 4) + 15)
    income_df, balance_df, cashflow_df = fetch_financials_cached(universe_ids)
    sh_df = fetch_shareholding_cached(universe_ids, days=days_to_fetch)
    
    if price_df.empty:
        logger.error("無法取得歷史價格資料，回測中止。")
        return

    price_df['date'] = pd.to_datetime(price_df['date'])
    if not inst_df.empty and 'date' in inst_df.columns:
        inst_df['date'] = pd.to_datetime(inst_df['date'])
    if not margin_df.empty and 'date' in margin_df.columns:
        margin_df['date'] = pd.to_datetime(margin_df['date'])
    if not sh_df.empty and 'date' in sh_df.columns:
        sh_df['date'] = pd.to_datetime(sh_df['date'])

    # 模擬每週日的評分
    today = datetime.now()
    results_by_week = []

    for w in range(weeks - 1, -1, -1):
        target_date = (today - timedelta(weeks=w)).date()
        target_dt = pd.to_datetime(target_date)
        
        logger.info("模擬週次: %s", target_date)
        
        # 建立時間切片 (Time-Slice) -> 將未來資料截斷
        sim_price = price_df[price_df['date'] <= target_dt].copy()
        sim_inst = inst_df[inst_df['date'] <= target_dt].copy()
        sim_margin = margin_df[margin_df['date'] <= target_dt].copy()
        sim_sh = sh_df[sh_df['date'] <= target_dt].copy()
        
        # 由於歷史財報無法切片，使用當前財報資料模擬 (簡化回測)
        scores = scorers.compute_all_scores(
            universe=universe_list,
            price=sim_price,
            institutional=sim_inst,
            margin=sim_margin,
            revenue=rev_df,
            income=income_df,
            balance=balance_df,
            cashflow=cashflow_df,
            shareholding=sim_sh
        )
        
        target_picks = [s["stock_id"] for s in scores if s["passes_filter"]][:10]
        results_by_week.append({
            "week_date": target_date.strftime("%Y-%m-%d"),
            "picks": target_picks,
        })
        
    logger.info("=== 回測績效結算 ===")
    total_avg_ret = 0.0
    for result in results_by_week:
        picks = result["picks"]
        if not picks:
            logger.info("[週次 %s] 無符合條件選股", result["week_date"])
            continue
            
        w_date = pd.to_datetime(result["week_date"])
        portfolio_return = 0.0
        details = []
        
        for sid in picks:
            # 取進場點和結束點 (今日)
            sid_px = price_df[(price_df["stock_id"] == sid) & (price_df["date"] >= w_date)].sort_values("date")
            if sid_px.empty:
                continue
                
            entry_p = sid_px.iloc[0]["close"]
            current_p = sid_px.iloc[-1]["close"]
            if pd.isna(entry_p) or entry_p == 0:
                continue
                
            ret = ((current_p - entry_p) / entry_p) * 100
            portfolio_return += ret
            details.append(f"{sid}: {ret:+.1f}%")
            
        avg_ret = portfolio_return / len(picks) if picks else 0
        total_avg_ret += avg_ret
        
        logger.info("[週次 %s] 選股%d檔 | 累積至今回報: %+5.1f%% | %s", 
                    result["week_date"], len(picks), avg_ret, ", ".join(details))
                    
    final_score = total_avg_ret / len(results_by_week) if results_by_week else 0.0
    logger.info("所有週次平均報酬率: %+5.1f%%", final_score)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="StockBatch 回測引擎")
    parser.add_argument("--weeks", type=int, default=12, help="回測過去 N 週的選股與計算績效")
    args = parser.parse_args()
    
    run_backtest(weeks=args.weeks)
