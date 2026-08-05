"""每個資料源一支 fetch function，全部只用標準函式庫。

每支回傳 (rows, meta):
  rows = [(date_str, value, ...), ...]  由舊到新
  meta = {"source": 顯示名稱, "url": 來源網址, ...}

抓失敗就 raise SourceError，呼叫端會記錄並保留上一次的資料，不會把 CSV 清空。
"""

import gzip
import json
import re
import ssl
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from html import unescape

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# canadagold 的 We Sell 有數量分級，我們要的是最小量那一級（買 1~3 枚的單價）。
CANADAGOLD_URL = "https://canadagold.ca/buy-from-us/bullion/"
CANADAGOLD_PRODUCT = "1 oz Standard Maple Leaf Coin 9999"

LBMA_URL = "https://prices.lbma.org.uk/json/gold_am.json"
TWSE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
TWSE_RT_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
VOO_URL = "https://stockanalysis.com/api/symbol/s/voo/history?range=1Y&period=Daily"
FX_URL = "https://open.er-api.com/v6/latest/CAD"
BOC_URL = "https://www.bankofcanada.ca/valet/observations/FXCADTWD/json"


class SourceError(Exception):
    pass


def _get(url, timeout=25):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise SourceError(f"HTTP {e.code} from {url}") from e
    except Exception as e:
        raise SourceError(f"{type(e).__name__} from {url}: {e}") from e


def _money(s):
    """'$5,827  each' -> 5827.0"""
    m = re.search(r"[\d,]+(?:\.\d+)?", s)
    if not m:
        raise SourceError(f"看不出數字: {s!r}")
    return float(m.group(0).replace(",", ""))


# --------------------------------------------------------------------------
# canadagold：他們賣給我們的價（We Sell，最小數量級距）
# --------------------------------------------------------------------------
def fetch_canadagold():
    html = _get(CANADAGOLD_URL)

    # 用產品名稱切段，再在該段內找 We buy / We Sell，避免依賴表格順序。
    chunks = re.split(r"(?=<h6><strong>)", html)
    for chunk in chunks:
        name_m = re.match(r"<h6><strong>(.*?)</strong>", chunk, re.S)
        if not name_m:
            continue
        name = " ".join(unescape(re.sub(r"<[^>]+>", "", name_m.group(1))).split())
        if name != CANADAGOLD_PRODUCT:
            continue

        buy_m = re.search(r'table-data="We buy">\s*<h6>(.*?)</h6>', chunk, re.S)
        tiers = re.findall(
            r'<span class="text-\w+">(.*?)</span>\s*<div class="pricelabel">(.*?)</div>',
            chunk,
            re.S,
        )
        if not tiers:
            raise SourceError(f"找到產品 {name} 但沒有 We Sell 價格分級")

        tier_label = " ".join(re.sub(r"<[^>]+>", "", tiers[0][0]).split()).rstrip(":")
        sell = _money(re.sub(r"<[^>]+>", "", tiers[0][1]))
        buy = _money(re.sub(r"<[^>]+>", "", buy_m.group(1))) if buy_m else None

        return (
            {"sell_cad": sell, "buy_cad": buy, "tier": tier_label, "product": name},
            {"source": "Canada Gold", "url": CANADAGOLD_URL},
        )

    raise SourceError(
        f"頁面上找不到產品 {CANADAGOLD_PRODUCT!r}（他們可能改了品名，需要人工確認）"
    )


# --------------------------------------------------------------------------
# LBMA 金價（每日定盤，USD/oz）
# --------------------------------------------------------------------------
def fetch_gold():
    data = json.loads(_get(LBMA_URL))
    rows = []
    for item in data:
        vals = item.get("v") or []
        if not vals or vals[0] in (None, 0):
            continue
        rows.append((item["d"], float(vals[0])))
    if not rows:
        raise SourceError("LBMA 回傳沒有可用的價格")
    rows.sort()
    return rows, {"source": "LBMA Gold AM Fix", "url": LBMA_URL, "unit": "USD/oz"}


# --------------------------------------------------------------------------
# 0050（證交所官方，一次一個月，要湊滿三個月得抓四次）
# --------------------------------------------------------------------------
def _roc_to_iso(s):
    """'115/08/04' -> '2026-08-04'"""
    y, m, d = s.strip().split("/")
    return f"{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}"


def fetch_0050(months=13, have_months=frozenset()):
    """證交所一次只回一個月，所以要湊一年份得跑十三次。

    過去月份的收盤價不會再變，所以 have_months 裡（CSV 已經有完整資料的月份）
    直接跳過，只抓缺的月份加上當月。第一次跑會打十三次，之後每天通常只打一次。
    """
    today = date.today()
    this_month = f"{today:%Y-%m}"
    seen = {}
    fetched = 0
    first = today.replace(day=1)

    for i in range(months):
        target = first
        for _ in range(i):
            target = (target - timedelta(days=1)).replace(day=1)
        key = f"{target:%Y-%m}"
        # 當月一定要重抓（每天都在長），其他月份有了就跳過
        if key != this_month and key in have_months:
            continue

        if fetched:
            time.sleep(1.2)  # 證交所沒有明訂速率，保守一點
        url = f"{TWSE_URL}?date={target:%Y%m%d}&stockNo=0050&response=json"
        fetched += 1
        try:
            payload = json.loads(_get(url))
        except SourceError:
            continue  # 單月失敗不致命，其他月份還是有資料
        if payload.get("stat") != "OK":
            continue
        for row in payload.get("data", []):
            close = row[6].replace(",", "")
            if not close or close == "--":
                continue
            seen[_roc_to_iso(row[0])] = float(close)

    if not seen and not have_months:
        raise SourceError("證交所沒有回傳任何 0050 資料")
    return sorted(seen.items()), {
        "source": "臺灣證券交易所",
        "url": f"{TWSE_URL}?stockNo=0050",
        "unit": "TWD",
        "months_fetched": fetched,
    }


def fetch_0050_intraday():
    """證交所盤中即時報價。

    STOCK_DAY 是盤後資料，台股 09:00-13:30 期間它給的還是昨天的收盤，
    早上看 dashboard 會誤以為那是「現在」。這支補上當下的成交價。

    收盤後或非交易日，這裡回的是最後一盤的價格，日期會是那一天，
    呼叫端要自己判斷是不是今天。
    """
    req = urllib.request.Request(
        f"{TWSE_RT_URL}?ex_ch=tse_0050.tw&json=1&delay=0",
        headers={
            "User-Agent": UA,
            "Referer": "https://mis.twse.com.tw/stock/fibest.jsp",  # 少了這個會被擋
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception as e:
        raise SourceError(f"{type(e).__name__} from {TWSE_RT_URL}: {e}") from e

    if payload.get("rtcode") != "0000":
        raise SourceError(f"盤中報價回傳 rtcode={payload.get('rtcode')}")
    arr = payload.get("msgArray") or []
    if not arr:
        raise SourceError("盤中報價沒有回傳任何標的")
    m = arr[0]

    def f(key):
        """單一數值。無成交時證交所會回 '-'。"""
        try:
            return float(m.get(key))
        except (TypeError, ValueError):
            return None

    def first_of_list(key):
        """五檔報價是底線串接的，例如 '104.3500_104.3000_...'，取第一檔。"""
        raw = m.get(key) or ""
        for part in str(raw).split("_"):
            try:
                return float(part)
            except ValueError:
                continue
        return None

    price, prev_close = f("z"), f("y")
    if price is None:
        # z 在兩筆撮合之間、以及開盤前會是 '-'，改用最佳買賣價的中間值
        bid, ask = first_of_list("b"), first_of_list("a")
        if bid and ask:
            price = (bid + ask) / 2
        else:
            price = bid or ask
    if price is None:
        # 完全沒有報價（非交易日）就退回昨收，呼叫端會看日期自己判斷
        price = prev_close
    if price is None or prev_close is None:
        raise SourceError("盤中報價缺成交價與昨收，兩者都沒有")

    raw = str(m.get("d") or "")
    iso = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 else None

    return (
        {
            "price": price,
            "prev_close": prev_close,
            "change": price - prev_close,
            "change_pct": (price - prev_close) / prev_close * 100,
            "date": iso,
            "time": m.get("t"),
            "name": m.get("n"),
        },
        {"source": "證交所盤中報價", "url": TWSE_RT_URL},
    )


# --------------------------------------------------------------------------
# VOO
# --------------------------------------------------------------------------
def fetch_voo():
    payload = json.loads(_get(VOO_URL))
    rows = [(d["t"], float(d["c"])) for d in payload.get("data", []) if d.get("c")]
    if not rows:
        raise SourceError("VOO 沒有回傳資料")
    rows.sort()
    return rows, {"source": "stockanalysis.com", "url": "https://stockanalysis.com/etf/voo/", "unit": "USD"}


# --------------------------------------------------------------------------
# CAD/TWD
#   歷史用加拿大央行（官方、免金鑰），但它會落後兩三個營業日；
#   今天的即時值另外用 open.er-api 補上。
# --------------------------------------------------------------------------
def fetch_fx_history(recent=400):
    url = f"{BOC_URL}?recent={recent}"
    payload = json.loads(_get(url))
    rows = []
    for obs in payload.get("observations", []):
        v = (obs.get("FXCADTWD") or {}).get("v")
        if v in (None, ""):
            continue
        rows.append((obs["d"], float(v)))
    if not rows:
        raise SourceError("加拿大央行沒有回傳 CAD/TWD 觀測值")
    rows.sort()
    return rows, {"source": "Bank of Canada Valet", "url": url, "pair": "CAD/TWD"}


def fetch_fx():
    payload = json.loads(_get(FX_URL))
    if payload.get("result") != "success":
        raise SourceError(f"匯率 API 回傳 {payload.get('result')}")
    rate = payload["rates"].get("TWD")
    if not rate:
        raise SourceError("匯率回傳裡沒有 TWD")
    day = payload.get("time_last_update_utc", "")
    return (
        float(rate),
        {"source": "open.er-api.com", "url": FX_URL, "as_of": day, "pair": "CAD/TWD"},
    )
