#!/usr/bin/env python3
"""本機 dashboard 伺服器。

只聽 127.0.0.1，不對外開放。提供兩件事：
  1. web/ 底下的靜態檔（這樣頁面能正常 fetch data.js，不會被 file:// 的 CORS 擋掉）
  2. 兩個 API：重新整理 truney、重跑每日抓取

用法:
    python3 scripts/serve.py            # http://127.0.0.1:8787
    python3 scripts/serve.py --port 9000
"""

import json
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
PY = sys.executable or "python3"

# 同一時間只准跑一個外部腳本，避免連點按鈕開出一堆 Chrome reload。
_lock = threading.Lock()


def run_script(name, extra=()):
    if not _lock.acquire(blocking=False):
        return 429, {"ok": False, "error": "上一個動作還在跑，稍等一下再點。"}
    try:
        r = subprocess.run(
            [PY, str(ROOT / "scripts" / name), *extra],
            capture_output=True,
            text=True,
            timeout=180,
        )
        ok = r.returncode == 0
        return (200 if ok else 500), {
            "ok": ok,
            "stdout": r.stdout.strip(),
            "stderr": r.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return 504, {"ok": False, "error": "腳本執行超過 180 秒"}
    finally:
        _lock.release()


class Handler(SimpleHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/api/refresh-truney":
            # --refresh 只在這裡出現：使用者按了按鈕才會重載 truney 分頁。
            code, payload = run_script("truney_read.py", ["--refresh"])
            if payload.get("ok"):
                run_script("fetch_daily.py", ["--rebuild"])
            self._json(code, payload)
        elif self.path == "/api/refresh-all":
            code, payload = run_script("fetch_daily.py")
            self._json(code, payload)
        else:
            self._json(404, {"ok": False, "error": "no such endpoint"})

    def end_headers(self):
        # data.js 會一直被改寫，不要讓瀏覽器快取。
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if "--verbose" in sys.argv:
            super().log_message(fmt, *args)


def main():
    port = 8787
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    handler = partial(Handler, directory=str(WEB))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"dashboard → http://127.0.0.1:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n收工")


if __name__ == "__main__":
    main()
