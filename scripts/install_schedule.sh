#!/bin/bash
# 安裝三個 LaunchAgent：
#   local.findash.server   開機後常駐本機 dashboard 伺服器（按鈕要靠它）
#   local.findash.fetch    每天 08:00 抓資料（電腦睡著就在喚醒後補跑）
#   local.findash.morning  每天 10:00 抓資料 + 發通知提醒你看 dashboard
#
# 反安裝：scripts/install_schedule.sh --uninstall

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
PY="/usr/bin/python3"          # 系統內建，不依賴 homebrew
PORT="${FINDASH_PORT:-8787}"

LABELS=(local.findash.server local.findash.fetch local.findash.morning)

unload_all() {
  for l in "${LABELS[@]}"; do
    launchctl bootout "gui/$(id -u)/$l" 2>/dev/null || true
    rm -f "$AGENTS/$l.plist"
  done
}

if [ "${1:-}" = "--uninstall" ]; then
  unload_all
  echo "已移除排程。"
  exit 0
fi

mkdir -p "$AGENTS" "$ROOT/data"
unload_all

write_plist() {
  local label="$1"; shift
  cat > "$AGENTS/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StandardOutPath</key><string>$ROOT/data/$label.log</string>
  <key>StandardErrorPath</key><string>$ROOT/data/$label.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>FINDASH_PORT</key><string>$PORT</string>
  </dict>
$*
</dict>
</plist>
PLIST
}

# --- 常駐伺服器 ---------------------------------------------------------
write_plist local.findash.server "
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$ROOT/scripts/serve.py</string><string>--port</string><string>$PORT</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>"

# --- 08:00 抓資料（錯過會在喚醒後補跑）---------------------------------
write_plist local.findash.fetch "
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$ROOT/scripts/fetch_daily.py</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>"

# --- 10:00 抓資料 + 提醒 ------------------------------------------------
write_plist local.findash.morning "
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/scripts/morning.sh</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>"

for l in "${LABELS[@]}"; do
  launchctl bootstrap "gui/$(id -u)" "$AGENTS/$l.plist"
  echo "已載入 $l"
done

echo
echo "dashboard → http://127.0.0.1:$PORT/"
echo "檢查狀態： launchctl list | grep findash"
