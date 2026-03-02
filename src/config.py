import os

FINMIND_TOKEN = os.environ["FINMIND_TOKEN"]

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]  # service_role key

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# 台灣 Top 50 ETF
ETF_ID = "0050"

# 評分權重
WEIGHTS = {
    "profitability": 0.30,
    "health":        0.20,
    "chip":          0.30,
    "momentum":      0.20,
}

# 每週推送數量
TOP_N = 10

# GitHub Actions 執行時間為 UTC，台灣時間 = UTC+8
# Daily job: 平日 18:30 TST = 10:30 UTC
# Weekly job: 週日 20:00 TST = 12:00 UTC
