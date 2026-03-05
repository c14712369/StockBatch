# GCP 雲端主機操作手冊 (StockBatch)

這份文件記錄了如何管理與維護跑在 Google Cloud Platform (GCP) e2-micro 主機上的 StockBatch 自動化排程。

## 1. 如何連線到主機 (SSH)
1. 登入 [Google Cloud Console](https://console.cloud.google.com/)。
2. 左上角確認專案已切換到 `StockBatch-Bot` (或你建立的專案名稱)。
3. 左側選單點擊 **Compute Engine** -> **VM 執行個體**。
4. 找到 `stockbatch-vm`，點擊最右側的 **「SSH」** 按鈕。
5. *(如果因為權限問題進不去，點擊 SSH 旁邊的下拉箭頭 `▼`，選擇「在瀏覽器視窗中開啟 (使用自訂通訊埠提供)」，然後將使用者切換為 `root`，進去後再輸入 `su - c14712369` 切換回你的帳號)*

---

## 2. 常見操作指令 (在 SSH 終端機內輸入)

### A. 更新程式碼 (最常用)
當你在本地端修改了程式碼並 push 到 GitHub 後，你需要讓 GCP 主機也拉取最新版本：
```bash
# 1. 進入專案資料夾
cd /home/c14712369/StockBatch

# 2. 拉取最新程式碼
git pull

# 3. (選擇性) 如果你有修改 requirements.txt，需要重新安裝套件：
source venv/bin/activate
pip install -r requirements.txt
deactivate
```

### B. 手動執行特定報表測試
如果你想立刻手動發送一份報表來測試程式碼有沒有寫錯：
```bash
# 先進入專案資料夾
cd /home/c14712369/StockBatch

# 測試盤中快報 (會檢查是否有異動，無異動則只會印出 Log 不會發 Telegram)
/home/c14712369/StockBatch/venv/bin/python -m src.intraday_job

# 測試收盤日報
/home/c14712369/StockBatch/venv/bin/python -m src.daily_job

# 測試週末週報 (警告：這會消耗很多 FinMind 額度，請謹慎使用)
/home/c14712369/StockBatch/venv/bin/python -m src.weekly_job
```

### C. 管理環境變數金鑰 (.env)
如果你更換了 Telegram Token 或 Supabase 金鑰：
```bash
cd /home/c14712369/StockBatch
nano .env
```
*編輯完成後，按 `Ctrl+O` 存檔 -> `Enter` 確認 -> `Ctrl+X` 離開。*

---

## 3. 管理定時排程 (Crontab)

主機是透過 Linux 內建的 `crontab` 來控制報表發送時間的。

### A. 查看目前的排程設定
```bash
crontab -l
```

### B. 修改排程時間
```bash
crontab -e
```
這會打開編輯器（如果問你選哪個，輸入 `1` 選 nano）。
目前的設定檔內容如下參考：
```bash
# 晨報 (週一到週五 早上 08:30)
30 8 * * 1-5 cd /home/c14712369/StockBatch && /home/c14712369/StockBatch/venv/bin/python -m src.morning_job >> /tmp/stockbatch_morning.log 2>&1

# 盤中快報 (週一到週五 09:00~13:00 每整點)
0 9,10,11,12,13 * * 1-5 cd /home/c14712369/StockBatch && /home/c14712369/StockBatch/venv/bin/python -m src.intraday_job >> /tmp/stockbatch_intraday.log 2>&1

# 收盤日報 (週一到週五 晚上 18:30)
30 18 * * 1-5 cd /home/c14712369/StockBatch && /home/c14712369/StockBatch/venv/bin/python -m src.daily_job >> /tmp/stockbatch_daily.log 2>&1

# 週末週報 (週日 晚上 20:00)
0 20 * * 0 cd /home/c14712369/StockBatch && /home/c14712369/StockBatch/venv/bin/python -m src.weekly_job >> /tmp/stockbatch_weekly.log 2>&1
```

---

## 4. 查看報錯與日誌 (Log)
如果你發現時間到了但沒有收到 Telegram，你可以去檢查主機把錯誤訊息寫在哪裡：
```bash
# 查看晨報的紀錄
cat /tmp/stockbatch_morning.log

# 查看盤中快報的紀錄 (可以看它有沒有抓到股價)
cat /tmp/stockbatch_intraday.log

# 查看日報的紀錄
cat /tmp/stockbatch_daily.log

# 如果日誌太長，可以使用 tail 只看最後 50 行：
tail -n 50 /tmp/stockbatch_daily.log
```
每次重開機後，`/tmp/` 裡的日誌會被清空，這是正常的。