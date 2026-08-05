# 財經 Dashboard

本機執行，資料存成 CSV，網頁讀 CSV 畫圖。沒有雲端、沒有帳號、沒有金鑰。

開啟：<http://127.0.0.1:8787/>

## 看什麼

| 區塊 | 內容 | 更新方式 |
|---|---|---|
| 台加價差 | 同一枚 1oz 楓葉金幣，台灣 vs 加拿大，換算成台幣後差多少 | 兩邊都有數字時自動算 |
| Canada Gold 賣價 | 他們賣給你的價（`Up to 3` 級距），CAD + 台幣並排 | 自動 |
| truney 賣價 | 台灣的賣價，TWD + 加幣並排 | **你按按鈕**，見下 |
| 金價現貨 | LBMA 上午定盤，USD/oz，1M + 3M | 自動 |
| 0050 / VOO | 收盤價，1M + 3M | 自動 |
| CAD/TWD | 匯率，1M + 3M | 自動 |
| 熱力圖 | nstock 與 finviz 的連結，新分頁開啟 | 點擊 |

金幣價格**只顯示當下數字、不畫線**。要看金子的走勢就看金價現貨那張圖。

## 資料來源

| 資料 | 來源 | 備註 |
|---|---|---|
| Canada Gold 賣價 | <https://canadagold.ca/buy-from-us/bullion/> | robots.txt 全開，只要求 crawl-delay 10；我們一天抓一次 |
| 金價 | LBMA `prices.lbma.org.uk/json/gold_am.json` | 官方定盤價，有數十年歷史 |
| 0050 | 臺灣證券交易所 `STOCK_DAY` API | 官方，一次回傳一個月 |
| VOO | stockanalysis.com | |
| CAD/TWD 歷史 | 加拿大央行 Valet `FXCADTWD` | 官方，會落後 2~3 個營業日 |
| CAD/TWD 當日 | open.er-api.com | 補上央行還沒發布的今天 |
| truney | 你自己開的 Chrome 分頁 | 見下 |

抓取邏輯在 `scripts/sources.py`，每個來源一支函式。任何一個掛掉都不影響其他來源，
也不會清空已經存下來的 CSV，只會在 dashboard 頁尾標示哪個失敗了。

## truney 為什麼要手動

truney.com 有兩層拒絕自動抓取的表態：

1. Cloudflare managed challenge — 任何腳本直接抓都是 HTTP 403
2. `robots.txt` 指名封鎖：`User-agent: ClaudeBot` / `Disallow: /`（GPTBot、CCBot 等一併封鎖）

技術上有繞過的手段（stealth browser、代解驗證碼、住宅 proxy），**這個專案不使用**。

實際做法是：**你自己開著那一頁**，`scripts/truney_read.py` 透過 AppleScript 讀取你
Chrome 分頁裡已經載入好的內容。讀取本身不會對 truney 的伺服器發出任何請求。

只有你按下 dashboard 上那顆「重新整理並讀取」時，才會重新載入頁面 —— 那一次請求是
你發動的，跟你自己按 F5 一樣。**排程永遠不會自己重載 truney。**

dashboard 上會顯示那個數字有多舊；超過 20 小時圓點會變黃，提醒你該按一下。

### 抓的是哪個數字

truney 商品頁有一張「批發價格」表，兩個價格欄：

| 數量 | Cash/E-Wallet Discount | Original Price |
|---|---|---|
| **1** | 137,311 | 142,803 |
| 3 | 137,230 | 142,719 |
| 5 | 137,149 | 142,635 |
| 10 | 137,068 | 142,551 |

`Original Price` 固定是 `Cash/E-Wallet Discount` 的 **1.04 倍**。

腳本解析整張表、鎖**數量 1**，兩欄都存進 `data/truney.json`。dashboard 兩個都顯示，
但**台加價差只能用一個**，由 `scripts/truney_read.py` 的 `PRICE_COLUMN` 決定：

```python
PRICE_COLUMN = "cash"      # 現金/電子錢包折扣價（目前設定）
PRICE_COLUMN = "original"  # 原價
```

差別不小 —— 以今天的數字，現金價比加拿大貴 2.4%，原價比加拿大貴 6.5%。

（不直接讀頁面主顯示價 `.oe_price` 是刻意的：它等於現金折扣價，看不出還有另一欄，
而且 truney 改版時解析表格會明確報錯，讀主顯示價則會默默抓到別的數字。）

### 一次性設定

Chrome 預設不允許 AppleScript 執行 JavaScript，要手動打開：

**Chrome 選單列 → 檢視 → 開發人員 → 允許 Apple 事件的 JavaScript**
（英文介面：View → Developer → Allow JavaScript from Apple Events）

第一次跑的時候 macOS 還會跳一次「終端機想要控制 Google Chrome」的權限請求，按允許。

## 排程

`scripts/install_schedule.sh` 會裝三個 LaunchAgent：

| Label | 時間 | 做什麼 |
|---|---|---|
| `local.findash.server` | 開機常駐 | 本機伺服器，dashboard 的按鈕靠它 |
| `local.findash.fetch` | 每天 08:00 | 抓資料 |
| `local.findash.morning` | 每天 10:00 | 抓資料 + 發通知提醒你看 dashboard |

電腦在排程時間睡著的話，launchd 會在喚醒後補跑，不會整天空白。

```bash
bash scripts/install_schedule.sh              # 安裝
bash scripts/install_schedule.sh --uninstall  # 移除
launchctl list | grep findash                 # 看狀態
```

10:00 的通知會告訴你 truney 的數字有多舊。想讓它同時自動把 dashboard 開起來，
在 `scripts/morning.sh` 裡把 `FINDASH_AUTO_OPEN` 預設值改成 `1`。

## 手動操作

```bash
python3 scripts/fetch_daily.py             # 抓全部
python3 scripts/fetch_daily.py --rebuild   # 不抓，只用既有 CSV 重產 web/data.js
python3 scripts/truney_read.py             # 只讀 Chrome 分頁上現有的數字
python3 scripts/truney_read.py --refresh   # 重新整理分頁後再讀（等同按按鈕）
python3 scripts/serve.py                   # 手動開伺服器
```

## 檔案

```
data/           CSV 與狀態檔（gold, tw0050, voo, fx_cadtwd, canadagold.json, truney.json）
scripts/        抓取、讀取、伺服器、排程
web/            index.html + data.js（data.js 由 fetch_daily.py 產生，不要手改）
```

## 要改東西的時候

- **換 Canada Gold 的產品**：改 `scripts/sources.py` 的 `CANADAGOLD_PRODUCT`。
  現在鎖的是 `1 oz Standard Maple Leaf Coin 9999`；同一頁還有 `1oz DNA Maple Leaf Coin 9999`。
  用產品名稱定位，他們改表格順序不會抓錯；改品名才會，那時腳本會明確報錯而不是默默抓到別的。
- **換數量級距**：現在取 `We Sell` 的第一級（`Up to 3`）。要改成 `4+` 就取 `tiers[1]`。
- **加一條新的線**：在 `sources.py` 寫一支回傳 `[(date, value)]` 的函式，
  在 `fetch_daily.py` 的 `fetch_all()` 裡 `merge_series()`，再到 `build_data_js()` 的
  `series` 加一行、`index.html` 的 `render()` 加一個 `metricBlock()`。
- **圖表視窗**：`fetch_daily.py` 的 `WINDOWS`，單位是日曆天（不是筆數）。

## 指點模式（要改版面的時候用）

在 dashboard 上按 **`D`**，畫面進入指點模式：滑過任何區塊會描邊並顯示它的代號，
點下去就把代號複製到剪貼簿。再按一次 `D` 離開。

目前可指點的 19 個區塊：

```
header                              頁首（標題、時間戳、重新抓取按鈕）
hero                                台加價差
card:canadagold  card:truney        兩張幣商卡
metric:gold      chart:gold:1M      chart:gold:3M
metric:tw0050    chart:tw0050:1M    chart:tw0050:3M
metric:voo       chart:voo:1M       chart:voo:3M
metric:fx        chart:fx:1M        chart:fx:3M
heatmap:nstock   heatmap:finviz     兩張熱力圖連結卡
footer                              頁尾來源列
```

代號直接貼給 AI 就能精確指定要改哪一塊，不用截圖。

**意見的涵蓋範圍**：指著某一個區塊寫的樣式意見（顏色、字級、間距、格式），
預設要套用到**所有同類的區塊**。例如在 `metric:tw0050` 上寫「商品名改亮黃色」，
指的是四個指標的名稱全部要改，不是只改 0050。要縮小範圍會明講。

在 `~/Claude/finance-dashboard` 底下開 Claude Code session 的話，`.claude/launch.json`
會讓預覽面板直接連上 <http://127.0.0.1:8787>。

### 直接在 dashboard 上寫意見

按 `D` 進指點模式後，**點任一區塊會就地跳出輸入框**，直接把想改的地方打進去，
`⌘↵` 送出、`Esc` 取消。不用切回聊天視窗、不用截圖、不用自己描述是哪一塊。

意見存在 `data/notes.jsonl`，右上角會顯示「N 則意見待處理」，點開可以看清單。

累積幾則之後跟 AI 說一聲，它讀 `data/notes.jsonl` 就知道要改哪裡。改完把該筆
`status` 設成 `done`：

```bash
curl -X POST http://127.0.0.1:8787/api/note-done -H 'Content-Type: application/json' -d '{"id":3}'
```

API：`POST /api/note`（新增）、`GET /api/notes`（列出）、`POST /api/note-done`（標記完成）。

## 即時報價

三個指標的走勢資料本質上都是「收盤／定盤」，看的當下已經落後：

| 指標 | 走勢圖用 | 落後多少 | 即時來源 |
|---|---|---|---|
| 0050 | 證交所盤後 `STOCK_DAY` | 台股交易時間內停在**昨天**收盤 | 證交所 MIS 盤中報價 |
| 金價 | LBMA 每日定盤 | 實測跟現貨差到 **2%** | `api.gold-api.com/price/XAU` |
| CAD/TWD | 加拿大央行日資料 | 落後 2~3 個營業日 | `api.fxratesapi.com`（每分鐘） |

頁面每 30 秒打一次 `GET /api/live`（分頁在背景時暫停），拿到的值會：

1. 更新標題數字與漲跌
2. **接成折線圖的最後一點**，末端畫一個 `circle.livedot` 表示尚未定案
3. 匯率還會回頭重算 HERO 的台加價差

VOO 與 Canada Gold 也接了：

| 指標 | 即時來源 | 備註 |
|---|---|---|
| VOO | `stockanalysis.com/api/quotes/s/voo` | 有延長時段價（`ep`）就優先用，標成「延長時段」 |
| Canada Gold | 重抓 `/buy-from-us/bullion/` | 伺服器端快取 120 秒；他們 robots 要求 Crawl-delay 10 |

truney 不在即時之列，原因見上面「truney 為什麼要手動」。

「是不是今天」用**該市場自己的時區**判斷（`LIVE_TZ`）。美股 16:00 EDT 已經是台灣的
隔天凌晨，用台灣日期比對會把盤中的 VOO 誤判成舊資料。

### 為什麼匯率不用日更 API 的直接報價

實測 `exchangerate-api` 直接報 CAD/TWD = 23.04，但用它**自己的** USD 匯率
交叉計算是 22.9433，自相矛盾 0.4%。三家日更 API 交叉算出來都落在 22.94–23.01，
`fxratesapi` 的即時值也在這個區間，且在 EUR/JPY/TWD/CAD 等高流動性貨幣對上
與其他來源差距都在 0.2% 以內。

這件事會影響 HERO：0.4% 的匯率誤差在一枚 NT$137,000 的金幣上就是約 NT$550。

### 舊的 0050 盤中細節

證交所的 `STOCK_DAY` 是**盤後**資料 —— 台股 09:00–13:30 交易時間內，它給的還是
昨天的收盤價。早上看 dashboard 會誤以為那是「現在」。

所以 0050 另外接了證交所的**盤中即時報價**：

```
https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_0050.tw&json=1&delay=0
```

這是證交所自家 MIS 行情系統的公開端點（`mis.twse.com.tw` 就是「基本市況報導網站」
背後那支 API）。要帶 `Referer: https://mis.twse.com.tw/stock/fibest.jsp`，不然會被擋。

頁面每 30 秒輪詢一次（分頁在背景時暫停），所以打開 dashboard 看到的就是當下的價格。
標題旁邊會標「盤中 HH:MM:SS」並轉成綠色；非交易時間則顯示「YYYY-MM-DD 收盤」。

兩個實作細節：

- 成交價欄位 `z` 在**兩筆撮合之間會回傳 `-`**。這時改用最佳買賣價（`b`/`a`，
  底線串接的五檔）的中間值。所以極少數情況下看到的是買賣中價而不是成交價。
- 非交易日完全沒有報價時退回昨收，並照實顯示那天的日期。

圖表用的仍然是盤後收盤價，只有標題那個大數字是即時的 —— 當天的 K 線要等收盤才進 CSV。
