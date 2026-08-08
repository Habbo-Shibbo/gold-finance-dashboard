"""台股交易日曆。

跟美股不同，台灣的休市日有農曆假期（春節、端午、中秋）和補假，用規則算
不可靠。所幸證交所有官方行事曆，直接用它，不自己推算。

    https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?response=json

那份清單混了兩種條目：真正的休市日，以及「開始交易日」這種純公告性質的
（例如「農曆春節後開始交易日」）。名稱含「交易」但不含「無交易」的是後者。

行事曆一年才變一次，所以抓下來存成快取，過期才重抓。
"""

import json
import urllib.request
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TPE = ZoneInfo("Asia/Taipei")
OPEN = time(9, 0)
CLOSE = time(13, 30)

SCHEDULE_URL = "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?response=json"
CACHE = Path(__file__).resolve().parent.parent / "data" / "tw_holidays.json"
CACHE_DAYS = 30

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _fetch():
    req = urllib.request.Request(SCHEDULE_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        payload = json.loads(r.read().decode("utf-8", "replace"))
    closures = []
    for row in payload.get("data") or []:
        day, name = row[0], row[1]
        # 「農曆春節後開始交易日」這類是公告用的交易日，不是休市
        if "交易" in name and "無交易" not in name:
            continue
        closures.append({"date": day, "name": name})
    if not closures:
        raise RuntimeError("證交所行事曆沒有回傳任何休市日")
    return closures


def holidays(force=False):
    """休市日 {date: 名稱}。行事曆一年才變一次，快取 30 天。"""
    if not force and CACHE.exists():
        try:
            cached = json.loads(CACHE.read_text(encoding="utf-8"))
            fetched = date.fromisoformat(cached["fetched"])
            if (date.today() - fetched).days < CACHE_DAYS:
                return {c["date"]: c["name"] for c in cached["closures"]}
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            pass

    closures = _fetch()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(
        json.dumps({"fetched": date.today().isoformat(), "closures": closures},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {c["date"]: c["name"] for c in closures}


def is_trading_day(d, hol=None):
    hol = holidays() if hol is None else hol
    return d.weekday() < 5 and d.isoformat() not in hol


def session_state(now=None):
    """regular（盤中）或 closed，以及下一次開盤的台北時間。"""
    now = now or datetime.now(TPE)
    today, t = now.date(), now.time()
    hol = holidays()

    state = "regular" if (is_trading_day(today, hol) and OPEN <= t < CLOSE) else "closed"

    next_open = None
    if state != "regular":
        d = today if (is_trading_day(today, hol) and t < OPEN) else today + timedelta(days=1)
        # 農曆春節最長可以連休九天，抓寬一點
        for _ in range(20):
            if is_trading_day(d, hol):
                break
            d += timedelta(days=1)
        else:
            d = None
        if d:
            next_open = datetime.combine(d, OPEN, tzinfo=TPE)

    return {
        "state": state,
        "next_open_label": f"{next_open.month}/{next_open.day} {next_open:%H:%M}" if next_open else None,
        "closed_reason": hol.get(today.isoformat()),
    }


if __name__ == "__main__":
    hol = holidays(force=True)
    print(f"證交所公告的休市日 {len(hol)} 天:")
    for d in sorted(hol):
        wd = "一二三四五六日"[date.fromisoformat(d).weekday()]
        print(f"  {d} 週{wd}  {hol[d]}")
    print()
    now = datetime.now(TPE)
    print(f"台北現在 {now:%Y-%m-%d %H:%M}（週{'一二三四五六日'[now.weekday()]}）")
    for k, v in session_state().items():
        print(f"  {k}: {v}")
