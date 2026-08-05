"""美股（NYSE / Nasdaq）交易日曆，只用標準函式庫。

用途：休市時要告訴使用者「下次開盤是台北時間幾點」，所以必須算得出
下一個交易日 —— 週末要跳過，國定假日也要跳過。

規則來源是 NYSE 公告的固定假日清單：元旦、馬丁路德金恩日、總統日、
受難日、陣亡將士紀念日、六月節、國慶日、勞動節、感恩節、聖誕節。
落在週六提前到週五休市，落在週日順延到週一休市。

受難日每年日期不同（復活節前的週五），用 Gregorian 演算法算。
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
TPE = ZoneInfo("Asia/Taipei")

OPEN = time(9, 30)
CLOSE = time(16, 0)
EXTENDED_CLOSE = time(20, 0)


def _easter(year):
    """Gregorian 復活節（匿名演算法）。"""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year, month, weekday, n):
    """該月第 n 個星期幾。weekday: 0=週一。"""
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year, month, weekday):
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d):
    """假日落在週六提前一天、落在週日順延一天。"""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def holidays(year):
    """該年度的休市日。感恩節隔天與平安夜是半日交易，仍有開盤，不列入。"""
    out = {
        _observed(date(year, 1, 1)),                    # 元旦
        _nth_weekday(year, 1, 0, 3),                    # 馬丁路德金恩日
        _nth_weekday(year, 2, 0, 3),                    # 總統日
        _easter(year) - timedelta(days=2),              # 受難日
        _last_weekday(year, 5, 0),                      # 陣亡將士紀念日
        _observed(date(year, 6, 19)),                   # 六月節
        _observed(date(year, 7, 4)),                    # 國慶日
        _nth_weekday(year, 9, 0, 1),                    # 勞動節
        _nth_weekday(year, 11, 3, 4),                   # 感恩節
        _observed(date(year, 12, 25)),                  # 聖誕節
    }
    return out


def is_trading_day(d):
    return d.weekday() < 5 and d not in holidays(d.year)


def session_state(now_ny=None):
    """回傳目前的市場狀態與下一次開盤時間。

    state: regular（一般時段）/ pre（盤前）/ after（盤後延長）/ closed（完全休市）
    next_open_tpe: 下一次一般時段開盤，換算成台北時間；已在盤中則為 None
    """
    now = now_ny or datetime.now(NY)
    today = now.date()
    t = now.time()

    state = "closed"
    if is_trading_day(today):
        if OPEN <= t < CLOSE:
            state = "regular"
        elif time(4, 0) <= t < OPEN:
            state = "pre"
        elif CLOSE <= t < EXTENDED_CLOSE:
            state = "after"

    next_open = None
    if state != "regular":
        # 今天還沒開盤就是今天，否則往後找第一個交易日
        d = today if (is_trading_day(today) and t < OPEN) else today + timedelta(days=1)
        for _ in range(15):  # 連假最長也不會超過兩週
            if is_trading_day(d):
                break
            d += timedelta(days=1)
        next_open = datetime.combine(d, OPEN, tzinfo=NY).astimezone(TPE)

    return {
        "state": state,
        "next_open_tpe": next_open.isoformat() if next_open else None,
        "next_open_label": (
            f"{next_open.month}/{next_open.day} {next_open:%H:%M}" if next_open else None
        ),
    }


if __name__ == "__main__":
    y = date.today().year
    print(f"=== {y} 年休市日 ===")
    for d in sorted(holidays(y)):
        print(f"  {d}  週{'一二三四五六日'[d.weekday()]}")
    print()
    print("=== 目前狀態 ===")
    now = datetime.now(NY)
    print(f"  紐約 {now:%Y-%m-%d %H:%M}")
    for k, v in session_state().items():
        print(f"  {k}: {v}")
