# 🎯 精算師預測系統 · sports-prediction-bot

全自動體育賽事預測機器人。每 30 分鐘透過 GitHub Actions 自動執行：抓取賽事 → AI/統計預測 → 推播 Telegram → 賽後驗證命中 → 累積訓練 XGBoost 模型。

![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-自動排程-2088FF?logo=github-actions&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4?logo=telegram&logoColor=white)

---

## ✨ 核心功能

| 功能 | 說明 |
|---|---|
| 🎲 蒙特卡羅模擬 | 每場比賽執行 100 萬次模擬，產生勝率與最可能比分 |
| 📐 去 Vig 真實勝率 | 彙整多家莊家賠率，去除抽水後還原真實機率 |
| 📈 真實數據修正 | NBA 用 `nba_api` 抓近 10 場場均得分；MLB 用 `pybaseball` 抓本季場均得分 |
| 💎 Value Betting | 模型勝率 vs 市場隱含勝率，差距 >5% 標記為有價值下注 |
| 💰 Kelly Criterion | 依真實 edge 計算最佳資金下注比例 |
| 🤖 AI 混合預測 | 歷史數據累積 30 筆後自動訓練 XGBoost，優先使用 AI 預測；模型缺失時自動降級為規則模式 |
| 📋 歷史數據記錄 | 賽前特徵自動寫入 CSV，賽後自動補填實際比分 |
| 🔄 自動賽後驗證 | 比賽結束後自動推播獨贏 / 讓分 / 大小 / 精準比分四項命中報告 |
| ⏰ 強制補漏 | `completed=False` 但開賽已達 4 小時，自動強制觸發賽後推播 |
| 📊 進度條視覺化 | 去Vig市場勝率 vs 蒙特卡羅模擬勝率分開顯示，差異即為 Value 核心依據 |
| 🏅 比分排行榜 | Top 5 最可能比分以 🥇🥈🥉 獎牌格式呈現，附帶全隊名 |
| 🎰 台灣運彩建議 | 依實際 Edge 動態排序主推／次要／備選，附劃位說明，不固定推獨贏 |
| 📊 風控資訊 | Kelly 值與建議下注比例顯示於每則賽前推播 |
| 📈 EV 模型表現 | 賽後自動評估 EV 準確性、Edge 命中、Kelly 策略是否合理 |
| 🏛️ 市場偏差分析 | 賽後顯示模型 vs 市場差異，標記低估／高估程度 |
| 💓 系統心跳 | 無推播時發送心跳通知，確認系統正常運行（靜音） |
| 🚨 推播失敗 Alert | Telegram API 失敗重試 3 次後自動發送錯誤通知 |
| 🏆 世界盃特報 | 冠軍 / 金靴 / 金球 / 金手套賠率即時分析 |
| 📊 系統自學指標 | 週報後自動附帶命中率、Kelly 有效性、Edge 偏差等統計 |
| 📅 週報 | 每週日 21:00 自動推播本週所有賽事的驗證摘要 |

**支援運動：NBA、MLB、FIFA 世界盃、WBC 經典賽、奧運男籃**

---

## 📁 檔案結構

```
sports-prediction-bot/
├── sports_prediction.py      # 主入口，CLI 指令與排程控制
├── notifier.py               # Telegram 推播（唯一 UI 層，格式鎖定）
├── data_fetcher.py           # 資料層：Odds API / nba_api / pybaseball
├── prediction_engine.py      # 預測引擎：Monte Carlo / XGBoost / Kelly
├── data_manager.py           # 狀態層：JSON / CSV / metrics 計算
├── result_verifier.py        # 賽後驗證：四項命中 / verify_all
├── tournament_engine.py      # 世界盃引擎：淘汰偵測 / 四大獎項
├── train.py                  # XGBoost 離線訓練
├── backtester.py             # 回測引擎：ROI / Drawdown / Grid Search
└── .github/workflows/bot.yml # GitHub Actions 排程設定
```

自動產生（勿手動編輯）：

```
flags.json               # 推播狀態與模擬結果快取
weekly_games.json        # 本週賽事快取
team_stats.json          # 球隊統計快取（每日更新一次）
metrics.json             # 系統自學指標
historical_dataset.csv   # 歷史賽前特徵 + 賽後結果
model_A.pkl              # XGBoost 主隊得分模型
model_B.pkl              # XGBoost 客隊得分模型
tournament_state.json    # 世界盃淘汰狀態（世界盃期間）
```

---

## 🚀 部署步驟

### 1. Fork / Clone 此 Repository

```bash
git clone https://github.com/你的帳號/sports-prediction-bot.git
cd sports-prediction-bot
```

### 2. 設定 Secrets

GitHub repo → **Settings → Secrets and variables → Actions**，新增以下三個 Secret：

| Secret 名稱 | 說明 | 取得方式 |
|---|---|---|
| `TG_TOKEN` | Telegram Bot Token | 向 [@BotFather](https://t.me/BotFather) 建立 Bot |
| `TG_CHAT` | Telegram Chat ID | 傳訊給 Bot 後查詢 `getUpdates` |
| `ODDS_API_KEY` | The Odds API 金鑰 | [the-odds-api.com](https://the-odds-api.com) 免費註冊 |

> ⚠️ 三個 Secrets 缺一不可，任何一個缺失 Actions 會立即顯示紅燈並停止執行。

### 3. 初始化 flags.json

首次部署前，在 repo 根目錄建立空的 `flags.json`：

```bash
echo '{}' > flags.json
git add flags.json
git commit -m "init flags"
git push
```

### 4. 啟用 GitHub Actions

確認 repo → **Actions** → 已啟用。Actions 每 30 分鐘自動執行，**無需任何手動操作**。

---

## 💬 Telegram 推播格式

### 賽前預測

```
🎯 精算師預測系統
⚡ 量化預測模型（賽前 30 分鐘）

━━━━━━━━━━━━━━━━
📅 台灣時間 06/05 09:41
⚾ MLB
Arizona Diamondbacks 🆚 Los Angeles Dodgers
━━━━━━━━━━━━━━━━

📐 去Vig真實勝率
Arizona Diamondbacks  ████░░░░░░ 43.8%
Los Angeles Dodgers   █████░░░░░ 56.2%

蒙特卡羅模擬勝率
Arizona Diamondbacks  ███░░░░░░░ 32.5%
Los Angeles Dodgers   ██████░░░░ 67.5%

📈 Value分析
Arizona Diamondbacks 優勢：-2.5%
Los Angeles Dodgers 優勢：-1.4%

━━━━━━━━━━━━━━━━
🏆 最可能出現的比分
🥇 Los Angeles Dodgers 6–4 Arizona Diamondbacks（2.7%）
🥈 Los Angeles Dodgers 5–4 Arizona Diamondbacks（2.6%）
🥉 Los Angeles Dodgers 6–3 Arizona Diamondbacks（2.4%）
4️⃣ Los Angeles Dodgers 6–5 Arizona Diamondbacks（2.4%）
5️⃣ Los Angeles Dodgers 5–3 Arizona Diamondbacks（2.4%）

━━━━━━━━━━━━━━━━
📊 盤口深度分析
讓分盤口     Los Angeles Dodgers 1.5
總分大小     9.5（大分 🔼）
獨贏賠率     Arizona Diamondbacks:+116｜Los Angeles Dodgers:-136

━━━━━━━━━━━━━━━━
💰 台灣運彩實戰建議
🔮【主推】獨贏盤 → Los Angeles Dodgers 勝出
💎【次要】總分大小 → 大分(9.5)
⭐【備選】讓分盤 → Los Angeles Dodgers(+1.5)

━━━━━━━━━━━━━━━━
📊 風控資訊
- Kelly：0.42
- 建議下注比例：4.2%

━━━━━━━━━━━━━━━━
📡 數據來源：AI模型+真實數據+賠率

⚠️ 請理性投注。
```

### 賽後結果

```
📊 賽後結果
📅 台灣時間 06/05

⚾ Arizona Diamondbacks vs Los Angeles Dodgers

━━━━━━━━━━━━━━━
命中結果：3 / 4（75%）
━━━━━━━━━━━━━━━

獨贏：✅
精準比分：❌
讓分：✅
大小分：✅

────────────────

📊 模型表現
- EV預測準確性：✔ 正向
- Edge命中：✔ 有效
- Kelly策略：✔ 合理

📊 模型 vs 市場
模型優勢：Los Angeles Dodgers +4.8%
市場偏差：中度低估 4.8%

────────────────

📌 預測模式：量化分析
```

### 週報 + 自學指標（每週日 21:00）

```
📅 本週預測週報 05/22 ～ 05/28
━━━━━━━━━━━━━━━━
總場次：12 場｜已驗證：10 場
🎯 獨贏命中：7/10（70%）
📐 讓分命中：6/10（60%）
📊 大小命中：6/10（60%）
🎯 精準比分：1/10（10%）
...

📈 系統自學指標（10 場樣本）
━━━━━━━━━━━━━━━━
獨贏命中率：70.0%
大小盤命中：60.0%
讓分命中：60.0%
Kelly命中：75.0%
Edge偏差：+2.1%（+偏高估，-偏低估）
各運動命中：
  🏀 75.0%
  ⚾ 60.0%
━━━━━━━━━━━━━━━━
⚠️ 數據分析，請理性投注。
```

---

## 🖥️ 指令速查

```bash
# GitHub Actions 自動執行
python sports_prediction.py push        # 推今天賽事 + 自動賽後驗證

# 手動執行（本機或 workflow_dispatch）
python sports_prediction.py fetch       # 只抓賽事存檔
python sports_prediction.py weekly      # 抓賽事 + 推週報 + 自學指標
python sports_prediction.py wc          # 手動推世足特報
python sports_prediction.py results     # 只跑賽後比分推播
python sports_prediction.py verify_all  # 強制補漏，遍歷所有未推播賽後場次
python sports_prediction.py metrics     # 手動觸發自學指標計算並推播
python sports_prediction.py train       # 離線訓練 XGBoost 模型
python sports_prediction.py backtest    # 回測（預設參數）
python sports_prediction.py --debug     # Dry-run：列出時間視窗判斷，強制模擬前兩場

# 回測進階
python backtester.py                    # 單次回測
python backtester.py 0.07 0.25          # 指定 threshold / kelly_fraction
python backtester.py grid               # Grid Search 掃描最佳參數
python backtester.py wc                 # 世界盃 Brier Score
```

---

## ⚙️ 排程邏輯

```
每 30 分鐘執行 push_today()
  ├─ 快取過期或所有比賽已結束 → 重新抓取最新賽程
  ├─ API 失敗 → 重試 3 次 → 回退快取（CI 不中斷）
  ├─ 賽事在「賽後6小時 ~ 未來16小時」視窗內，且今日未推 → 模擬並推播
  ├─ 同一場賽事每天只推一次（跨日自動重置）
  ├─ 已推播且比賽結束 → 抓比分 → 四項驗證 → 推播賽後報告
  ├─ completed=False 但開賽逾 4 小時 → 強制觸發賽後推播
  └─ 無推播條件 → 發送靜音待機通知

每週日 20:00 → 重新抓取下週賽程
每週日 21:00 → 推播週報 + 系統自學指標
每次 CI 結束 → 嘗試訓練 XGBoost（資料不足時靜默跳過）

**🔕 靜音模式（台灣時間 23:00 ~ 08:00）**

| 推播類型 | 靜音期間行為 |
|---|---|
| 賽前預測 | 暫停推播，等靜音結束後下一輪補推 |
| 系統待機通知 | 靜音發送（訊息送出但不響鈴） |
| 賽後驗證報告 | **不受靜音限制**，比賽結束即推 |
```

---

## 🤖 AI 模型說明

| 項目 | 說明 |
|---|---|
| 演算法 | XGBoost 回歸（`reg:squarederror`） |
| 預測目標 | 主隊得分（`model_A.pkl`）、客隊得分（`model_B.pkl`） |
| 輸入特徵 | 賠率、讓分、大小盤、去 Vig 勝率、場均得分、標準差等共 13 項 |
| 最少樣本 | 30 筆完整歷史數據才啟動訓練 |
| AI 安全閥 | 預測偏離規則模式 >50% 自動降回規則，並印出警告 |

推播訊息中 `⚙️ 預測模式` 欄位會明確標示 `🤖 AI預測` 或 `📐 規則模擬`。

---

## 🛡️ Fail-safe 機制

| 情況 | 結果 |
|---|---|
| Secrets 缺失 | 立即印出錯誤，Actions 顯示**紅燈** |
| Odds API 失敗（重試 3 次後） | Warning + 回退快取，CI **不中斷** |
| `tournament_engine` 模組缺失 | 靜默跳過世界盃功能，推播**不中斷** |
| `thefuzz` 未安裝 | 降級內建字串比對，功能**不中斷** |
| AI 模型載入異常 | 靜默降回規則模式，推播**不中斷** |
| AI 預測偏離 >50% | 自動降回規則模式 + 印出警告 |
| `completed=False` 但逾 4 小時 | 強制觸發賽後推播（保護機制） |
| 訓練資料不足 | 靜默跳過，CI **不顯示紅燈** |
| git commit 無變更 | 靜默跳過，CI **不顯示紅燈** |
| 推播 API 失敗（retry 3 次） | 自動發送 🚨 Alert 通知，CI **不中斷** |
| 本輪無推播條件 | 發送心跳通知（靜音），確認系統存活 |

> 原則：寧可不準，也不能掛掉。

---

## 📦 安裝套件（本機執行用）

```bash
pip install requests numpy schedule \
            nba_api pybaseball \
            scikit-learn xgboost joblib pandas \
            thefuzz python-Levenshtein
```

GitHub Actions 會自動安裝，**不需要** `requirements.txt`。

---

## 🔑 Secrets 設定位置

```
GitHub Repo
└── Settings
    └── Secrets and variables
        └── Actions
            ├── TG_TOKEN      ← Telegram Bot Token
            ├── TG_CHAT       ← Telegram Chat ID
            └── ODDS_API_KEY  ← The Odds API 金鑰
```

---

> ⚠️ 本系統僅供數據分析參考，請理性投注。
