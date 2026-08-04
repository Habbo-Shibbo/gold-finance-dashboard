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


NOTES = ROOT / "data" / "notes.jsonl"


def read_notes():
    if not NOTES.exists():
        return []
    out = []
    for line in NOTES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def add_note(target, text, ts):
    NOTES.parent.mkdir(parents=True, exist_ok=True)
    existing = read_notes()
    note = {
        "id": max([n.get("id", 0) for n in existing] or [0]) + 1,
        "ts": ts,
        "target": target,
        "text": text,
        "status": "open",
    }
    with NOTES.open("a", encoding="utf-8") as f:
        f.write(json.dumps(note, ensure_ascii=False) + "\n")
    return note


def set_status(note_id, status):
    notes = read_notes()
    hit = False
    for n in notes:
        if n.get("id") == note_id:
            n["status"] = status
            hit = True
    if hit:
        NOTES.write_text(
            "".join(json.dumps(n, ensure_ascii=False) + "\n" for n in notes),
            encoding="utf-8",
        )
    return hit


class Handler(SimpleHTTPRequestHandler):
    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/notes":
            notes = read_notes()
            self._json(200, {
                "ok": True,
                "open": [n for n in notes if n.get("status") == "open"],
                "all": notes,
            })
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/api/note":
            b = self._body()
            text = (b.get("text") or "").strip()
            target = (b.get("target") or "?").strip()
            if not text:
                self._json(400, {"ok": False, "error": "意見是空的"})
                return
            note = add_note(target, text, b.get("ts") or "")
            print(f"[意見 #{note['id']}] {target}: {text}", flush=True)
            self._json(200, {"ok": True, "note": note})
            return

        if self.path == "/api/note-done":
            b = self._body()
            ok = set_status(b.get("id"), "done")
            self._json(200 if ok else 404, {"ok": ok})
            return

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
