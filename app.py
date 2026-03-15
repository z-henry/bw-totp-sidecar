#!/usr/bin/env python3
import json
import logging
import os
from pathlib import Path
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
LOG_LEVEL = (os.getenv("LOG_LEVEL") or "INFO").upper()
VERSION_FILE = Path(__file__).with_name("VERSION")

def load_app_version() -> str:
    try:
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
        return version or "dev"
    except OSError:
        return "dev"

APP_VERSION = load_app_version()

SESSION_LOCK = threading.Lock()
BW_SESSION = None
BW_SESSION_TS = 0.0

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

def describe_cmd(cmd: list[str]) -> str:
    return " ".join(cmd[:3])

def normalize_server_url(url: str) -> str:
    return (url or "").rstrip("/")

def run(cmd: list[str], env: dict | None = None) -> str:
    logger.debug("Running command: %s", describe_cmd(cmd))
    r = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env
    )
    if r.returncode != 0:
        err = (r.stderr or "").strip() or f"command failed: {describe_cmd(cmd)}"
        logger.error("Command failed: %s - %s", describe_cmd(cmd), err)
        raise RuntimeError(err)
    logger.debug("Command finished: %s", describe_cmd(cmd))
    return (r.stdout or "").strip()

def bw_config_server() -> None:
    if not BW_SERVER:
        logger.info("BW_SERVER not set, using default Bitwarden server")
        return

    current_server = ""
    current_status = ""
    try:
        st = bw_status()
        current_server = normalize_server_url(st.get("serverUrl", ""))
        current_status = st.get("status", "")
    except Exception as e:
        logger.warning("Unable to read Bitwarden status before server config: %s", e)

    target_server = normalize_server_url(BW_SERVER)
    if current_server == target_server:
        logger.info("Bitwarden server already configured: %s", target_server)
        return

    if current_server:
        logger.info("Bitwarden server change detected: %s -> %s", current_server, target_server)
    else:
        logger.info("Configuring Bitwarden server: %s", target_server)

    if current_status and current_status != "unauthenticated":
        logger.warning("Bitwarden status is %s, logging out before updating server", current_status)
        run(["bw", "logout"])

    run(["bw", "config", "server", target_server])

def bw_login_apikey() -> None:
    try:
        logger.info("Attempting Bitwarden API key login")
        run(["bw", "login", "--apikey"])
    except Exception as e:
        # 已登录 / 暂时网络问题都不硬崩
        logger.warning("Bitwarden API key login skipped or failed: %s", e)

def bw_refresh() -> None:
    try:
        logger.debug("Refreshing Bitwarden vault cache")
        run(["bw", "refresh"])
    except Exception as e:
        logger.warning("Bitwarden refresh failed: %s", e)

def bw_status() -> dict:
    logger.debug("Checking Bitwarden status")
    out = run(["bw", "status"])
    return json.loads(out)

def bw_unlock_get_session() -> str:
    if not BW_MASTER_PASSWORD:
        raise RuntimeError("BW_MASTER_PASSWORD not set, cannot unlock automatically")
    logger.info("Unlocking Bitwarden vault to get a fresh session")
    env = os.environ.copy()
    env["BW_MASTER_PASSWORD"] = BW_MASTER_PASSWORD
    return run(["bw", "unlock", "--raw", "--passwordenv", "BW_MASTER_PASSWORD"], env=env)

def get_cached_session() -> str:
    global BW_SESSION, BW_SESSION_TS
    with SESSION_LOCK:
        if BW_SESSION and (time.time() - BW_SESSION_TS) < 600:
            logger.debug("Reusing cached Bitwarden session")
            return BW_SESSION
        logger.info("Cached Bitwarden session missing or expired, unlocking again")
        BW_SESSION = bw_unlock_get_session()
        BW_SESSION_TS = time.time()
        return BW_SESSION

def find_item_id(session: str, name: str) -> str:
    logger.debug("Searching Bitwarden item by name: %s", name)
    out = run(["bw", "list", "items", "--session", session])
    items = json.loads(out)
    for it in items:
        if it.get("name") == name:
            logger.debug("Matched Bitwarden item name: %s", name)
            return it.get("id", "")
    return ""

def get_totp_by_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise RuntimeError("Item name is empty")

    logger.info("Resolving TOTP for item: %s", name)
    session = get_cached_session()
    item_id = find_item_id(session, name)

    if not item_id:
        # 刷新一次再找
        logger.info("Item not found in current cache, refreshing and retrying: %s", name)
        bw_refresh()
        item_id = find_item_id(session, name)
        if not item_id:
            logger.error("Bitwarden item not found after refresh: %s", name)
            raise RuntimeError(f"Item not found: {name}")

    try:
        logger.debug("Fetching TOTP from Bitwarden for item: %s", name)
        return run(["bw", "get", "totp", item_id, "--session", session])
    except Exception as e:
        # session 可能过期，清掉缓存重来
        logger.warning("Fetching TOTP failed, retrying with a fresh session: %s", e)
        global BW_SESSION, BW_SESSION_TS
        with SESSION_LOCK:
            BW_SESSION = None
            BW_SESSION_TS = 0.0
        session = get_cached_session()
        return run(["bw", "get", "totp", item_id, "--session", session])

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        client_ip = self.client_address[0] if self.client_address else "-"
        try:
            parsed = urlparse(self.path)
            logger.info("Incoming request path=%s from=%s", parsed.path, client_ip)

            if parsed.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"ok\n")
                logger.debug("Health check responded successfully")
                return

            if parsed.path != "/otp":
                logger.warning("Unknown path requested: %s", parsed.path)
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"not found\n")
                return

            # Header 鉴权（可选）
            if AUTH_TOKEN:
                got = self.headers.get("X-Auth", "")
                if got != AUTH_TOKEN:
                    logger.warning("Unauthorized request for /otp from=%s", client_ip)
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
            logger.info("TOTP request completed successfully for item=%s from=%s", name, client_ip)

        except Exception as e:
            logger.exception("Request handling failed for path=%s from=%s", self.path, client_ip)
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write((str(e) + "\n").encode("utf-8"))

    def log_message(self, format, *args):
        return

def main():
    logger.info("Starting bw-totp-sidecar version=%s on port %s", APP_VERSION, PORT)
    bw_config_server()
    bw_login_apikey()
    bw_refresh()

    # 可选：快速校验登录态（不强制崩）
    try:
        st = bw_status()
        if st.get("status") == "unauthenticated":
            logger.warning("Bitwarden status is unauthenticated at startup")
        else:
            logger.info("Bitwarden status at startup: %s", st.get("status"))
    except Exception as e:
        logger.warning("Unable to check Bitwarden status at startup: %s", e)

    logger.info("HTTP server listening on 0.0.0.0:%s", PORT)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

if __name__ == "__main__":
    main()
