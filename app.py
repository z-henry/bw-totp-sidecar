#!/usr/bin/env python3
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.getenv("PORT", "8080"))
BW_SERVER = (os.getenv("BW_SERVER") or "").rstrip("/")
DEFAULT_ITEM_NAME = (os.getenv("BW_ITEM_NAME") or "").strip()  # 可选：默认条目名
BW_MASTER_PASSWORD = os.getenv("BW_MASTER_PASSWORD") or ""
AUTH_TOKEN = os.getenv("BWHELPER_TOKEN") or ""

SESSION_LOCK = threading.Lock()
BW_SESSION = None
BW_SESSION_TS = 0.0

def run(cmd: list[str], env: dict | None = None) -> str:
    r = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "").strip() or f"command failed: {' '.join(cmd)}")
    return (r.stdout or "").strip()

def bw_config_server() -> None:
    if not BW_SERVER:
        return
    run(["bw", "config", "server", BW_SERVER])

def bw_login_apikey() -> None:
    try:
        run(["bw", "login", "--apikey"])
    except Exception:
        # 已登录 / 暂时网络问题都不硬崩
        pass

def bw_refresh() -> None:
    try:
        run(["bw", "refresh"])
    except Exception:
        pass

def bw_status() -> dict:
    out = run(["bw", "status"])
    return json.loads(out)

def bw_unlock_get_session() -> str:
    if not BW_MASTER_PASSWORD:
        raise RuntimeError("BW_MASTER_PASSWORD not set, cannot unlock automatically")
    env = os.environ.copy()
    env["BW_MASTER_PASSWORD"] = BW_MASTER_PASSWORD
    return run(["bw", "unlock", "--raw", "--passwordenv", "BW_MASTER_PASSWORD"], env=env)

def get_cached_session() -> str:
    global BW_SESSION, BW_SESSION_TS
    with SESSION_LOCK:
        if BW_SESSION and (time.time() - BW_SESSION_TS) < 600:
            return BW_SESSION
        BW_SESSION = bw_unlock_get_session()
        BW_SESSION_TS = time.time()
        return BW_SESSION

def find_item_id(session: str, name: str) -> str:
    out = run(["bw", "list", "items", "--session", session])
    items = json.loads(out)
    for it in items:
        if it.get("name") == name:
            return it.get("id", "")
    return ""

def get_totp_by_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise RuntimeError("Item name is empty")

    session = get_cached_session()
    item_id = find_item_id(session, name)

    if not item_id:
        # 刷新一次再找
        bw_refresh()
        item_id = find_item_id(session, name)
        if not item_id:
            raise RuntimeError(f"Item not found: {name}")

    try:
        return run(["bw", "get", "totp", item_id, "--session", session])
    except Exception:
        # session 可能过期，清掉缓存重来
        global BW_SESSION, BW_SESSION_TS
        with SESSION_LOCK:
            BW_SESSION = None
            BW_SESSION_TS = 0.0
        session = get_cached_session()
        return run(["bw", "get", "totp", item_id, "--session", session])

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            parsed = urlparse(self.path)

            if parsed.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"ok\n")
                return

            if parsed.path != "/otp":
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"not found\n")
                return

            # Header 鉴权（可选）
            if AUTH_TOKEN:
                got = self.headers.get("X-Auth", "")
                if got != AUTH_TOKEN:
                    self.send_response(401)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"unauthorized\n")
                    return

            # 支持 /otp?name=MoviePilot
            qs = parse_qs(parsed.query)
            name = qs.get("name", [""])[0].strip()
            if not name:
                name = DEFAULT_ITEM_NAME

            if not name:
                raise RuntimeError('Missing item name. Use /otp?name=XXX or set BW_ITEM_NAME')

            otp = get_totp_by_name(name)

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write((otp + "\n").encode("utf-8"))

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write((str(e) + "\n").encode("utf-8"))

    def log_message(self, format, *args):
        return

def main():
    bw_config_server()
    bw_login_apikey()
    bw_refresh()

    # 可选：快速校验登录态（不强制崩）
    try:
        st = bw_status()
        if st.get("status") == "unauthenticated":
            pass
    except Exception:
        pass

    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

if __name__ == "__main__":
    main()
