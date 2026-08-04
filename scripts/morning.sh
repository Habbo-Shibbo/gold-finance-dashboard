#!/bin/bash
# 每天早上 10:00 由 launchd 呼叫：抓最新資料 → 發通知提醒看 dashboard。
#
# 這支腳本**不會**去碰 truney。truney 的重新整理只在你按下 dashboard 上那顆
# 按鈕時才發生，理由寫在 README。這裡只負責提醒你去按。

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${FINDASH_PORT:-8787}"
URL="http://127.0.0.1:${PORT}/"

# 設成 1 就會在提醒時直接把 dashboard 開起來
AUTO_OPEN="${FINDASH_AUTO_OPEN:-0}"

cd "$ROOT" || exit 1
/usr/bin/python3 scripts/fetch_daily.py >> "$ROOT/data/fetch.log" 2>&1
FETCH_RC=$?

# truney 上次讀到的數字有多舊？用它決定提醒詞。
STALE_MSG=$(/usr/bin/python3 - <<'PY'
import json, pathlib
from datetime import datetime, timezone

p = pathlib.Path("data/truney.json")
if not p.exists():
    print("truney 還沒讀過，點一下更新")
    raise SystemExit

try:
    d = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    print("truney 讀取狀態不明，點一下更新")
    raise SystemExit

if not d.get("ok"):
    print("truney 上次讀取失敗，點一下更新")
    raise SystemExit

loaded = d.get("page_loaded_at")
try:
    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(loaded)).total_seconds() / 3600
except Exception:
    print("truney 數字時間不明，點一下更新")
    raise SystemExit

if age_h > 20:
    print(f"truney 的數字是 {round(age_h)} 小時前的，點一下更新")
else:
    print(f"truney 數字還新（{round(age_h)} 小時前）")
PY
)

if [ "$FETCH_RC" -eq 0 ]; then
  TITLE="財經 Dashboard 已更新"
else
  TITLE="財經 Dashboard（部分來源抓取失敗）"
fi

/usr/bin/osascript -e "display notification \"${STALE_MSG}\" with title \"${TITLE}\" subtitle \"${URL}\" sound name \"Ping\""

if [ "$AUTO_OPEN" = "1" ]; then
  /usr/bin/open "$URL"
fi
