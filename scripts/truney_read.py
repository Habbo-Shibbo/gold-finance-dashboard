#!/usr/bin/env python3
"""從你已經開著的 Chrome 分頁讀出 truney 的金幣價格。

重要：預設**只讀不重載**。頁面是你自己載入的，讀取它不會對 truney 的伺服器
發出任何請求。只有加上 --refresh（也就是你按下 dashboard 上那顆按鈕）才會
重新整理，那一次請求是你發動的。

排程不應該帶 --refresh。

用法:
    python3 scripts/truney_read.py             # 只讀目前分頁上的數字
    python3 scripts/truney_read.py --refresh   # 重新整理後再讀（使用者按鈕觸發）
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "truney.json"
MATCH = "truney.com"

# truney 的商品頁有一張「批發價格」表，欄位是：
#   數量 | Cash/E-Wallet Discount | Original Price
# 我們鎖「數量 1」那一列，兩個價格都抓回來。
#
# 直接讀主顯示價（.oe_price）是行不通的 —— 它等於現金折扣價，會讓人以為
# 那就是唯一的價格。解析表格才看得到完整結構，而且他們改版時會明確壞掉
# 而不是默默抓到別的數字。
JS = r"""
(function () {
  function clean(s) { return String(s || "").replace(/\s+/g, " ").trim(); }
  function num(s) {
    var m = clean(s).replace(/,/g, "").match(/[0-9]+(\.[0-9]+)?/);
    return m ? parseFloat(m[0]) : null;
  }

  var qtyTable = null, headers = [];
  var tables = document.querySelectorAll("table");
  for (var i = 0; i < tables.length; i++) {
    var t = tables[i];
    if (t.querySelectorAll(".oe_currency_value").length < 2) continue;
    var hs = [];
    t.querySelectorAll("th").forEach(function (h) { hs.push(clean(h.textContent)); });
    if (hs.join(" ").indexOf("Original Price") === -1) continue;
    qtyTable = t; headers = hs; break;
  }

  var tiers = [];
  if (qtyTable) {
    qtyTable.querySelectorAll("tr").forEach(function (r) {
      var cells = [];
      r.querySelectorAll("td").forEach(function (c) { cells.push(clean(c.textContent)); });
      if (cells.length < 3) return;
      var q = num(cells[0]);
      if (q === null) return;
      tiers.push({ qty: q, cash: num(cells[1]), original: num(cells[2]) });
    });
  }

  var one = null;
  for (var j = 0; j < tiers.length; j++) if (tiers[j].qty === 1) one = tiers[j];

  var headline = document.querySelector(".oe_price .oe_currency_value");

  return JSON.stringify({
    tiers: tiers,
    qty1: one,
    headers: headers,
    headline: headline ? clean(headline.textContent) : null,
    title: document.title,
    url: location.href,
    loaded_at: new Date(performance.timeOrigin).toISOString()
  });
})()
"""

# HERO 價差要用哪一欄： "cash"（現金/電子錢包折扣價）或 "original"（原價）
PRICE_COLUMN = "cash"


def osa(script):
    r = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "osascript 失敗")
    return r.stdout.strip()


def find_tab():
    """回傳 (window_index, tab_index)，找不到就 raise。"""
    script = f'''
    tell application "Google Chrome"
      set wi to 0
      repeat with w in windows
        set wi to wi + 1
        set ti to 0
        repeat with t in tabs of w
          set ti to ti + 1
          if URL of t contains "{MATCH}" then return (wi as text) & "," & (ti as text)
        end repeat
      end repeat
    end tell
    return "none"
    '''
    out = osa(script)
    if out == "none" or "," not in out:
        raise LookupError(
            f"Chrome 裡找不到網址含 {MATCH} 的分頁。請先開著那一頁再跑。"
        )
    w, t = out.split(",")
    return int(w), int(t)


def reload_tab(w, t, timeout=30):
    osa(f'tell application "Google Chrome" to reload tab {t} of window {w}')
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.6)
        state = osa(
            f'tell application "Google Chrome" to return loading of tab {t} of window {w}'
        )
        if state.lower() == "false":
            time.sleep(1.2)  # 給 Odoo 的 JS 一點時間把價格填進去
            return True
    raise TimeoutError("重新整理後頁面在 30 秒內沒有載入完成")


def read_tab(w, t):
    # ensure_ascii=False 是必要的：AppleScript 不認得 \uXXXX 跳脫，
    # JS 裡只要出現任何非 ASCII 字元（例如不斷行空白）就會變成語法錯誤。
    script = (
        f'tell application "Google Chrome" to return execute tab {t} '
        f'of window {w} javascript {json.dumps(JS, ensure_ascii=False)}'
    )
    try:
        raw = osa(script)
    except RuntimeError as e:
        msg = str(e)
        # Chrome 的這則錯誤會跟著系統語言變，所以比對關鍵字而不是整句。
        hints = ("Allow JavaScript", "Apple 事件", "AppleScript", "-2700")
        if any(h in msg for h in hints) or not msg:
            raise RuntimeError(
                "Chrome 沒開放透過 AppleScript 執行 JavaScript。"
                "請到 Chrome 選單列：檢視 → 開發人員 → 允許 Apple 事件的 JavaScript"
                "（英文介面是 View → Developer → Allow JavaScript from Apple Events），"
                "打勾後重跑一次。"
            ) from e
        raise
    if not raw:
        raise RuntimeError("Chrome 沒有回傳任何東西（分頁可能還在載入）")
    return json.loads(raw)


def to_number(s):
    if not s:
        return None
    cleaned = "".join(c for c in s if c.isdigit() or c == ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def main():
    refresh = "--refresh" in sys.argv
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    try:
        w, t = find_tab()
        if refresh:
            reload_tab(w, t)
        info = read_tab(w, t)
    except Exception as e:
        payload = {"ok": False, "error": str(e), "checked_at": now, "refreshed": refresh}
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"讀取失敗: {e}", file=sys.stderr)
        return 1

    qty1 = info.get("qty1") or {}
    cash = qty1.get("cash")
    original = qty1.get("original")
    value = qty1.get(PRICE_COLUMN)

    payload = {
        "ok": value is not None,
        "sell_twd": value,
        "column": PRICE_COLUMN,
        "qty": 1,
        "cash_twd": cash,
        "original_twd": original,
        "tiers": info.get("tiers"),
        "table_headers": info.get("headers"),
        "headline_on_page": info.get("headline"),
        "page_title": info.get("title"),
        "url": info.get("url"),
        "page_loaded_at": info.get("loaded_at"),
        "checked_at": now,
        "refreshed": refresh,
    }
    if value is None:
        payload["error"] = (
            "找不到「批發價格」表的數量 1 那一列。"
            "truney 可能改了頁面結構，需要人工確認後改 scripts/truney_read.py 的 JS。"
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if value is None:
        print("解析失敗，抓到的級距:", info.get("tiers"), file=sys.stderr)
        return 1
    print(
        f"truney 數量1 → 現金價 NT${cash:,.0f} / 原價 NT${original:,.0f}"
        f"  (採用 {PRICE_COLUMN} = NT${value:,.0f}，頁面載入於 {info.get('loaded_at')})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
