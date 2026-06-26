# 台股期貨籌碼快報 — 自動更新儀表板

每個交易日盤後自動從 **臺灣證券交易所(TWSE)** 與 **臺灣期貨交易所(TAIFEX)** 的官方公開資料
抓取加權指數、三大法人買賣超、台指期、大額交易人未平倉、P/C Ratio 等籌碼資訊，整理成網頁儀表板。

設定好之後完全不用管，GitHub 會每天自動幫你更新資料。

---

## 這份 v1 包含的資料

- 加權指數收盤 / 漲跌 / 成交金額
- 台指期（近月）價格 / 漲跌 / 價差(期－現)
- 三大法人現貨買賣超：外資 / 投信 / 自營商
- 三大法人－臺股期貨留倉：外資 / 投信 / 自營商
- 外資－小型臺指期貨留倉
- 十大交易人 / 十大特定法人 淨未平倉
- 賣買權未平倉比 P/C Ratio（VIX 若資料來源中有提供也會一併顯示）
- 以上各項的歷史走勢圖（每天自動累積）

> 選擇權各履約價未平倉分布圖、小台/微台散戶指標明細、VIX 30/50/100日百分位，
> 這些屬於更精細的計算，先不在 v1 內，等這套基礎架構穩定運作後，我們再加進來。

---

## 設定步驟（第一次設定，大約15分鐘）

### 第 1 步：申請 GitHub 帳號（如果還沒有）

前往 [github.com](https://github.com)，點右上角「Sign up」，用 email 申請一個免費帳號。

### 第 2 步：建立新的 Repository（倉庫）

1. 登入後，點右上角 **+** → **New repository**
2. Repository name 填：`taifex-dashboard`（或你喜歡的名字）
3. 選擇 **Public**（要用免費的 GitHub Pages 必須是 Public）
4. 不要勾選 "Add a README file"
5. 點 **Create repository**

### 第 3 步：把這個資料夾的檔案上傳上去

1. 在剛建立的空 repository 頁面，點 **uploading an existing file**
2. 把這次下載到電腦的整個資料夾**裡面的所有檔案和資料夾**（包含 `.github` 這個隱藏資料夾）拖曳到上傳區
   - ⚠️ `.github` 資料夾名稱前面有一個點，在某些電腦上是隱藏的，記得要連同它一起上傳，
     不然每天自動更新的功能不會生效。如果用網頁拖曳上傳整個資料夾有問題，
     建議改用下方「進階：用 GitHub Desktop」的方法，會更穩。
3. 下方填寫 commit message，例如「first upload」，點 **Commit changes**

### 第 4 步：開啟 Actions 的寫入權限（很重要，不開的話自動更新會失敗）

1. 進入 repository → 上方 **Settings**
2. 左側選單找到 **Actions** → **General**
3. 往下捲到 **Workflow permissions**
4. 選擇 **Read and write permissions**
5. 點 **Save**

### 第 5 步：啟用 GitHub Pages（讓網站有網址可以看）

1. 還在 **Settings** 裡，左側選單找 **Pages**
2. **Source** 選擇 **Deploy from a branch**
3. **Branch** 選擇 `main`，資料夾選 `/ (root)`，點 **Save**
4. 等 1～2 分鐘，重新整理這個頁面，上面會出現你的網站網址，
   通常是：`https://你的帳號.github.io/taifex-dashboard/`

### 第 6 步：手動跑一次資料抓取，確認有沒有問題

1. 進入 repository 上方 **Actions** 分頁
2. 左側點 **每日更新台股期貨籌碼資料**
3. 右邊點 **Run workflow** → 綠色 **Run workflow** 按鈕
4. 等約 30 秒～1 分鐘，重新整理頁面，點進剛剛跑的那筆紀錄看是不是綠色勾勾（成功）

如果是綠色勾勾：太棒了，打開你的網站網址，應該已經看到今天的最新資料。

如果是紅色叉叉（失敗）：點進去看 log，**把錯誤訊息複製貼給我**，
我可以幫你看是哪個資料來源的欄位名稱跟我猜的不一樣，馬上修。
（這是完全正常的情況，因為政府開放資料的欄位名稱我沒辦法百分之百在沒有真實連線的狀況下肉眼確認，
第一次上線通常需要 1～2 次小調整。）

---

## 之後完全不用管

設定完之後，GitHub 會在**每個週一到週五**台北時間 **15:40 與 17:00** 自動執行兩次抓資料
（兩次是為了保險，萬一第一次跑的時候政府網站資料還沒更新完）。
資料會自動 commit 進 repository，你的網站會自動顯示最新內容，不需要你做任何事。

想要看某一天有沒有抓成功，可以到 **Actions** 分頁查看執行紀錄。

---

## 想自己手動檢查資料

每次抓取後，會在 `data/` 資料夾留下：

- `data/latest.json` — 最新一天的整理後資料
- `data/history.json` — 歷史資料（自動累積，最多保留約180個交易日）
- `data/fetch_log.txt` — 這次抓取過程的詳細紀錄（包含抓到的原始欄位名稱，方便除錯）
- `data/raw/*.csv` — 各資料來源當天抓到的原始 CSV

如果之後要除錯，把 `data/fetch_log.txt` 的內容貼給我，我就能知道哪一段解析錯誤。

---

## 進階：用 GitHub Desktop 上傳（如果網頁拖曳上傳不順）

1. 下載安裝 [GitHub Desktop](https://desktop.github.com/)
2. 登入你的 GitHub 帳號
3. File → Clone repository → 選你剛建立的 `taifex-dashboard`
4. 把這次拿到的所有檔案複製到 clone 下來的資料夾裡（覆蓋掉空資料夾）
5. 回到 GitHub Desktop，下方填 commit message，點 **Commit to main**
6. 點 **Push origin**

---

## 之後想加新功能怎麼辦

直接回來這個對話跟我說想加什麼（例如：VIX歷史百分位、各履約價選擇權OI分布圖），
我會幫你改 `scripts/fetch_data.py` 和前端頁面，你只要把改好的檔案重新上傳覆蓋即可，
不需要重新設定 GitHub Pages 或 Actions。

---

## 資料來源清單

| 項目 | 來源 |
|---|---|
| 加權指數、成交金額 | TWSE OpenAPI `exchangeReport/FMTQIK` |
| 三大法人現貨買賣超 | TWSE OpenAPI `exchangeReport/BFIAUU` |
| 台指期每日行情 | TAIFEX 政府開放資料 `DailyMarketReportFut` |
| 三大法人期貨留倉 | TAIFEX 政府開放資料 `MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate` |
| 大額交易人未沖銷部位 | TAIFEX 政府開放資料 `OpenInterestOfLargeTradersFutures` |
| 賣買權未平倉比 | TAIFEX 政府開放資料 `PutCallRatio` |

本網站資料僅供參考，不保證完整性與正確性，不作為投資建議。
