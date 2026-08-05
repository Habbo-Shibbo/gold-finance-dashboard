#!/usr/bin/env python3
"""每日抓取。排程在早上跑，也可以隨時手動跑。

設計原則：任何一個來源掛掉都不能影響其他來源，也不能把已經存下來的
歷史資料清空。抓失敗就沿用 CSV 裡既有的資料，並在 dashboard 上標成過期。

用法:
    python3 scripts/fetch_daily.py            # 抓全部
    python3 scripts/fetch_daily.py --rebuild  # 只用既有 CSV 重新產生 web/data.js
"""

import csv
import json
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sources  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WEB = ROOT / "web"
TRUNEY_FILE = DATA / "truney.json"
STATUS_FILE = DATA / "status.json"

# 圖表視窗（日曆天）
WINDOWS = {"1M": 30, "3M": 91, "1Y": 365}


# ---------------------------------------------------------------- CSV 讀寫
def read_series(name):
    path = DATA / f"{name}.csv"
    if not path.exists():
        return {}
    out = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                out[row["date"]] = float(row["value"])
            except (KeyError, TypeError, ValueError):
                continue
    return out


def write_series(name, mapping):
    path = DATA / f"{name}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "value"])
        for d in sorted(mapping):
            w.writerow([d, mapping[d]])
    tmp.replace(path)


def merge_series(name, rows):
    """rows = [(date, value)]，與既有 CSV 合併後寫回，回傳合併結果。"""
    existing = read_series(name)
    existing.update({d: v for d, v in rows})
    write_series(name, existing)
    return existing


def append_dealer_log(row):
    path = DATA / "dealers_log.csv"
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "canadagold_sell_cad", "canadagold_buy_cad", "product", "tier"])
        w.writerow(row)


# ---------------------------------------------------------------- 抓取
def run_source(status, key, fn):
    """跑一個來源，錯誤收進 status 而不是往上炸。"""
    try:
        result = fn()
        status[key] = {"ok": True, "at": now_iso()}
        return result
    except Exception as e:
        status[key] = {
            "ok": False,
            "at": now_iso(),
            "error": f"{type(e).__name__}: {e}",
        }
        print(f"  [!] {key} 失敗: {e}", file=sys.stderr)
        if "--debug" in sys.argv:
            traceback.print_exc()
        return None


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def fetch_all():
    status = {}
    print("抓取中…")

    # --- 幣商現價（不畫線，只留當下數字 + 一份 log）
    cg = run_source(status, "canadagold", sources.fetch_canadagold)
    if cg:
        payload, meta = cg
        payload["ts"] = now_iso()
        payload["url"] = meta["url"]
        (DATA / "canadagold.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        append_dealer_log(
            [payload["ts"], payload["sell_cad"], payload["buy_cad"],
             payload["product"], payload["tier"]]
        )
        print(f"  canadagold  {payload['product']} → C${payload['sell_cad']:,.0f}")

    # --- 有歷史的四條線
    gold = run_source(status, "gold", sources.fetch_gold)
    if gold:
        rows, _ = gold
        merge_series("gold", rows)
        print(f"  金價        {rows[-1][0]}  US${rows[-1][1]:,.2f}/oz  ({len(rows)} 筆)")

    # 過去月份的收盤價不會再變，把已經齊全的月份告訴抓取端，讓它跳過
    existing_tw = read_series("tw0050")
    counts = {}
    for d in existing_tw:
        counts[d[:7]] = counts.get(d[:7], 0) + 1
    complete = frozenset(m for m, n in counts.items() if n >= 15)
    tw = run_source(status, "tw0050", lambda: sources.fetch_0050(have_months=complete))
    if tw:
        rows, _ = tw
        merged = merge_series("tw0050", rows)
        print(f"  0050        新增 {len(rows)} 筆，累計 {len(merged)} 筆")

    # 盤中報價：台股開盤時間看 dashboard，盤後資料還停在昨天
    rt = run_source(status, "tw0050_intraday", sources.fetch_0050_intraday)
    if rt:
        quote, _ = rt
        quote["fetched_at"] = now_iso()
        (DATA / "tw0050_intraday.json").write_text(
            json.dumps(quote, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  0050 盤中   {quote['date']} {quote['time']}  "
              f"NT${quote['price']:,.2f}  ({quote['change_pct']:+.2f}%)")

    voo = run_source(status, "voo", sources.fetch_voo)
    if voo:
        rows, _ = voo
        merge_series("voo", rows)
        print(f"  VOO         {rows[-1][0]}  US${rows[-1][1]:,.2f}  ({len(rows)} 筆)")

    # 本益比：兩邊都沒有公開的歷史序列，只能從今天開始累積
    sp = run_source(status, "sp500_pe", sources.fetch_sp500_pe)
    if sp:
        d, _ = sp
        merge_series("pe_sp500", [(today_str(), d["pe"])])
        (DATA / "sp500_pe.json").write_text(
            json.dumps({**d, "ts": now_iso()}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  S&P500 PE   {d['pe']:.2f}  ({d.get('as_of') or ''})")

    tw = run_source(status, "tw0050_pe", sources.fetch_tw0050_pe)
    if tw:
        d, _ = tw
        merge_series("pe_tw0050", [(today_str(), round(d["pe"], 4))])
        (DATA / "tw0050_pe.json").write_text(
            json.dumps({**d, "ts": now_iso()}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  0050 PE     {d['pe']:.2f}  （自行計算，涵蓋率 {d['coverage']*100:.1f}%）")

    # 匯率：歷史用加拿大央行，今天的即時值另外補
    fxh = run_source(status, "fx_history", sources.fetch_fx_history)
    if fxh:
        rows, _ = fxh
        merge_series("fx_cadtwd", rows)
        print(f"  CAD/TWD 史  {rows[0][0]} ~ {rows[-1][0]}  ({len(rows)} 筆)")

    fx = run_source(status, "fx", sources.fetch_fx)
    if fx:
        rate, _ = fx
        merge_series("fx_cadtwd", [(today_str(), rate)])
        print(f"  CAD/TWD 今  {rate:.4f}")

    STATUS_FILE.write_text(
        json.dumps({"sources": status, "at": now_iso()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return status


# ---------------------------------------------------------------- 產生 data.js
def series_for_web(name):
    """視窗用「日曆天」切，不是用「筆數」切。

    金價、股價都只有交易日有資料，用筆數切的話「近一個月」會變成
    近 31 個交易日 ≈ 六週，跟標題對不上。
    """
    data = read_series(name)
    dates = sorted(data)
    out = {"points": [{"d": d, "v": data[d]} for d in dates]}
    if dates:
        latest = datetime.strptime(dates[-1], "%Y-%m-%d").date()
        for label, days in WINDOWS.items():
            cutoff = (latest - timedelta(days=days)).isoformat()
            out[label] = [{"d": d, "v": data[d]} for d in dates if d >= cutoff]
    else:
        for label in WINDOWS:
            out[label] = []
    return out


def load_json(path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def build_data_js():
    canadagold = load_json(DATA / "canadagold.json")
    truney = load_json(TRUNEY_FILE)
    status = load_json(STATUS_FILE, {})

    fx = read_series("fx_cadtwd")
    fx_rate = fx[max(fx)] if fx else None

    payload = {
        "generated_at": now_iso(),
        "fx_cadtwd": fx_rate,
        "canadagold": canadagold,
        "tw0050_intraday": load_json(DATA / "tw0050_intraday.json"),
        "truney": truney,
        "pe": {
            "tw0050": load_json(DATA / "tw0050_pe.json"),
            "sp500": load_json(DATA / "sp500_pe.json"),
        },
        "series": {
            "gold": series_for_web("gold"),
            "tw0050": series_for_web("tw0050"),
            "voo": series_for_web("voo"),
            "fx_cadtwd": series_for_web("fx_cadtwd"),
            "pe_tw0050": series_for_web("pe_tw0050"),
            "pe_sp500": series_for_web("pe_sp500"),
        },
        "status": status,
        "heatmaps": [
            {
                "label": "0050 成分股熱力圖",
                "sub": "nstock",
                "url": "https://www.nstock.tw/market_index/heatmap?t1=0&t2=0&t3=0&t4=1&t5=0&iid&nh=0",
            },
            {
                "label": "S&P 500 熱力圖",
                "sub": "finviz",
                "url": "https://finviz.com/map.ashx?t=sec",
            },
        ],
    }

    WEB.mkdir(parents=True, exist_ok=True)
    (WEB / "data.js").write_text(
        "window.DASH = " + json.dumps(payload, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    return payload


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    if "--rebuild" not in sys.argv:
        fetch_all()
    build_data_js()
    print(f"\ndata.js 已更新 → {WEB / 'data.js'}")


if __name__ == "__main__":
    main()
