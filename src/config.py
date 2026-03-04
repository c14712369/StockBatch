import os

# 簡單的 .env 解析器，用於本地開發測試 (不依賴 python-dotenv)
env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(env_file):
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

# 支援多把 FinMind Token (以逗號分隔)
FINMIND_TOKENS_STR = os.environ.get("FINMIND_TOKENS", os.environ.get("FINMIND_TOKEN", ""))
FINMIND_TOKENS = [t.strip() for t in FINMIND_TOKENS_STR.split(",")] if FINMIND_TOKENS_STR else []

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")  # service_role key

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

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
