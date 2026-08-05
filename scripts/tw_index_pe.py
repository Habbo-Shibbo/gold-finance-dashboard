"""用證交所官方資料自行計算 0050 的本益比。

證交所只公布個股本益比，不含 ETF；元大官網是 SPA，原始 HTML 拿不到數字。
所以這個數字是**自行計算**的，不是官方公布值 —— dashboard 上必須標明。

方法
----
1. 從 TWSE OpenAPI 取三份資料：每日收盤價、已發行普通股數、個股本益比
2. 市值 = 收盤價 × 已發行普通股數
3. 取市值前 50 大作為 0050 成分股的替代
4. 整體本益比 = Σ市值 ÷ Σ盈餘，其中個股盈餘 = 市值 ÷ 本益比

第 4 步用的是「總市值除以總盈餘」，也就是指數本益比的標準定義，
而不是把 50 個本益比平均。兩者差很多：平均會被高本益比的個股拉高，
總量法才反映「買下整個組合要用幾年盈餘回本」。

已知誤差來源（照實記錄，不粉飾）
--------------------------------
- 成分股用市值排名近似。真正的台灣50指數採**自由流通量調整**後的市值，
  並且每季才調整一次成分股，所以名單會有少數幾檔出入。
- 權重同理，本計算用總市值而非自由流通調整市值。
- 虧損公司的本益比證交所顯示「-」，這些個股被排除在分母外。回傳值裡的
  `coverage` 是納入計算的市值佔前 50 大總市值的比例，低於 100% 就代表
  有成分股被排除。
"""

import json
import urllib.request

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
PRICES = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
BASIC = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
PERATIO = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"

TOP_N = 50


def _get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def load_rows():
    """把三份官方資料併成一張表：代號、名稱、市值、本益比。"""
    prices = {r["Code"]: r for r in _get(PRICES)}
    basics = {r["公司代號"]: r for r in _get(BASIC)}
    pes = {r["Code"]: r for r in _get(PERATIO)}

    rows = []
    for code, b in basics.items():
        px = prices.get(code)
        if not px:
            continue
        close = _num(px.get("ClosingPrice"))
        shares = _num(b.get("已發行普通股數或TDR原股發行股數"))
        if not close or not shares:
            continue
        rows.append({
            "code": code,
            "name": b.get("公司簡稱") or px.get("Name"),
            "cap": close * shares,
            "pe": _num((pes.get(code) or {}).get("PEratio")),
        })
    if not rows:
        raise RuntimeError("沒有取得任何個股資料")
    rows.sort(key=lambda r: r["cap"], reverse=True)
    date = (next(iter(pes.values()), {}) or {}).get("Date")
    return rows, date


def summarize(rows, date, label):
    """總量法：Σ市值 ÷ Σ盈餘，個股盈餘 = 市值 ÷ 本益比。"""
    cap_total = sum(r["cap"] for r in rows)
    priced = [r for r in rows if r["pe"] and r["pe"] > 0]
    cap_priced = sum(r["cap"] for r in priced)
    earnings = sum(r["cap"] / r["pe"] for r in priced)
    if not earnings:
        raise RuntimeError(f"{label}：納入計算的盈餘總和為零")
    return {
        "label": label,
        "pe": cap_priced / earnings,
        "date": date,
        "constituents": len(rows),
        "counted": len(priced),
        "coverage": cap_priced / cap_total,
        "top10": [
            {"code": r["code"], "name": r["name"], "pe": r["pe"],
             "weight": r["cap"] / cap_total}
            for r in rows[:10]
        ],
    }


def compute(top_n=TOP_N):
    rows, date = load_rows()
    return summarize(rows[:top_n], date, f"市值前 {top_n} 大")


def compute_all():
    """一次算出 0050 近似值與全上市市場，只抓一次資料。"""
    rows, date = load_rows()
    return {
        "tw0050": summarize(rows[:TOP_N], date, f"市值前 {TOP_N} 大"),
        "market": summarize(rows, date, "全上市市場"),
    }


if __name__ == "__main__":
    both = compute_all()
    for key in ("tw0050", "market"):
        d = both[key]
        print(f"{d['label']}  本益比 {d['pe']:.2f}")
        print(f"  資料日期 {d['date']}   成分 {d['constituents']} 檔，"
              f"納入 {d['counted']} 檔，市值涵蓋 {d['coverage']*100:.1f}%")
    print("\n  前十大（市值前 50 大之中）:")
    for r in both["tw0050"]["top10"]:
        pe = f"{r['pe']:.2f}" if r["pe"] else "－"
        print(f"    {r['code']} {r['name']:<8} 權重 {r['weight']*100:5.2f}%   PE {pe}")
