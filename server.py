#!/usr/bin/env python3
"""
本地 HTTP 服务器，兼容静态文件服务 + /refresh 接口。

POST /refresh        — 后台启动 fetch_papers.py + fetch_news.py
GET  /refresh/status — 返回 {"done": bool, "ok": bool}
"""
import hashlib
import hmac
import gzip
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

# Railway 注入 PORT；本地默认 8765
PORT = int(os.environ.get("PORT", 8765))
BASE_DIR = Path(__file__).parent

# 如果 /data 存在（Railway Volume），用 /data；否则用项目目录（本地）
_railway_data = Path("/data")
DATA_DIR = Path(os.environ.get("DATA_DIR", str(_railway_data if _railway_data.exists() else BASE_DIR)))

_refresh_state = {
    "running": False,
    "done": False,
    "ok": False,
    "started_at": None,
    "duration_seconds": None,
    "estimate_min_seconds": None,
    "estimate_max_seconds": None,
}
_lock = threading.Lock()
_fav_lock = threading.Lock()
_settings_lock = threading.Lock()

ALLOWED_MODELS = {"claude-sonnet-4-6", "claude-opus-4-7"}


def _settings_path():
    return DATA_DIR / "settings.json"


def _read_settings() -> dict:
    p = _settings_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _write_settings(data: dict):
    p = _settings_path()
    tmp = DATA_DIR / "settings.json.tmp"
    with _settings_lock:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(p))

# 每天一份快照,最多保留 30 天。在 _fav_lock 内调用,无需额外加锁
HISTORY_KEEP = int(os.environ.get("FAVORITES_HISTORY_DAYS", "30"))

# ========== Auth ==========
SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "")
SESSION_COOKIE = "gyne_session"
SESSION_DAYS = 30
# 不需要单独 SESSION_SECRET:用密码本身派生 HMAC 密钥
# 改密码会让所有 session 失效(安全特性)
_SECRET_SEED = (SITE_PASSWORD or "no-password-set").encode()

# 公开路径(不需要登录)
PUBLIC_PATHS = {"/login", "/login.html", "/logout", "/favicon.ico"}


def _make_token() -> str:
    ts = str(int(time.time()))
    sig = hmac.new(_SECRET_SEED, ts.encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def _verify_token(token: str) -> bool:
    if not token or "." not in token:
        return False
    try:
        ts_str, sig = token.split(".", 1)
        expected = hmac.new(_SECRET_SEED, ts_str.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        if time.time() - int(ts_str) > SESSION_DAYS * 86400:
            return False
        return True
    except Exception:
        return False


def _snapshot_favorites(body):
    history_dir = DATA_DIR / "favorites_history"
    history_dir.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    snap_path = history_dir / f"favorites-{today}.json"
    if not snap_path.exists():
        try:
            snap_path.write_bytes(body)
        except Exception as e:
            print(f"[snapshot-fail] {e}", flush=True)
            return
        # 轮转: 只保留最近 N 天
        files = sorted(history_dir.glob("favorites-*.json"))
        for f in files[:-HISTORY_KEEP]:
            try:
                f.unlink()
            except Exception:
                pass

print(f"[startup] BASE_DIR={BASE_DIR} DATA_DIR={DATA_DIR} PORT={PORT}", flush=True)
print(f"[startup] auth: {'ENABLED' if SITE_PASSWORD else 'DISABLED (set SITE_PASSWORD to enable)'}", flush=True)


def _estimate_refresh_time():
    """根据持久缓存的新鲜度给前端一个保守区间，不承诺精确倒计时。"""
    now = time.time()
    cache_files = [
        DATA_DIR / ".pubmed_cache.json",
        DATA_DIR / ".extension_cache.json",
        DATA_DIR / ".news_cache.json",
    ]
    recent = 0
    for path in cache_files:
        try:
            if path.stat().st_size >= 1024 and now - path.stat().st_mtime <= 14 * 86400:
                recent += 1
        except OSError:
            pass

    if recent == len(cache_files):
        return {"estimate_min_seconds": 20, "estimate_max_seconds": 90}
    if recent:
        return {"estimate_min_seconds": 60, "estimate_max_seconds": 180}
    return {"estimate_min_seconds": 120, "estimate_max_seconds": 300}


def _run_refresh():
    env = os.environ.copy()
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

    days = env.get("DAYS", "1")
    # 用户偏好优先,其次环境变量,最后默认
    user_settings = _read_settings()
    model = user_settings.get("model") or env.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    if model not in ALLOWED_MODELS:
        model = "claude-sonnet-4-6"
    # 数据目录传给脚本
    env["DATA_DIR"] = str(DATA_DIR)

    max_ai = env.get("MAX_AI", "25")
    top_news = env.get("TOP_NEWS", "20")
    # 论文和动态互不依赖 —— 并行跑,总耗时 = 两者较慢的那个,而不是相加
    procs = []
    for script, extra in [
        ("fetch_papers.py", ["--days", days, "--max-ai", max_ai]),
        ("fetch_news.py",   ["--days", days, "--top", top_news]),
    ]:
        cmd = [sys.executable, str(BASE_DIR / script), "--model", model] + extra
        print(f"[refresh] running: {' '.join(cmd)}", flush=True)
        procs.append((script, subprocess.Popen(cmd, cwd=str(BASE_DIR), env=env)))
    ok = True
    for script, proc in procs:
        rc = proc.wait()
        print(f"[refresh] {script} exit code: {rc}", flush=True)
        if rc != 0:
            ok = False

    with _lock:
        _refresh_state["running"] = False
        _refresh_state["done"] = True
        _refresh_state["ok"] = ok
        started_at = _refresh_state.get("started_at") or time.time()
        _refresh_state["duration_seconds"] = max(0, round(time.time() - started_at))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 静默日志

    def _serve_file(self, path):
        # 对 data.json / news.json 优先从 DATA_DIR 读,缺失则回退到 BASE_DIR 里打包的初始版本
        if path.name in ("data.json", "news.json"):
            volume_path = DATA_DIR / path.name
            if volume_path.exists():
                path = volume_path
            else:
                path = BASE_DIR / path.name
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            self.send_error(404)
            return
        ct = {
            ".html": "text/html; charset=utf-8",
            ".json": "application/json",
            ".js":   "application/javascript",
            ".css":  "text/css",
        }.get(path.suffix, "application/octet-stream")

        # 远程访问时避免每次刷新都重新传输整份 JSON；首次访问则用 gzip
        # 把约数百 KB 的数据压缩后发送。
        etag = f'"{hashlib.sha256(data).hexdigest()[:16]}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, no-cache")
            self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        accepts_gzip = "gzip" in self.headers.get("Accept-Encoding", "").lower()
        compressible = path.suffix in {".html", ".json", ".js", ".css"}
        encoded = gzip.compress(data, compresslevel=5) if accepts_gzip and compressible and len(data) >= 1024 else data
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "private, no-cache")
        self.send_header("ETag", etag)
        self.send_header("Vary", "Accept-Encoding")
        if encoded is not data:
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        self.wfile.write(encoded)

    def _json(self, status, payload, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _get_cookie(self, name):
        raw = self.headers.get("Cookie", "")
        for piece in raw.split(";"):
            piece = piece.strip()
            if piece.startswith(name + "="):
                return piece[len(name) + 1:]
        return None

    def _is_authed(self) -> bool:
        if not SITE_PASSWORD:
            return True  # 未设密码 = 开放模式(本地/初次部署)
        return _verify_token(self._get_cookie(SESSION_COOKIE) or "")

    def _wants_html(self) -> bool:
        return "text/html" in (self.headers.get("Accept", "") or "")

    def _require_auth(self) -> bool:
        """返回 True 表示已认证可继续;False 表示已写过 401/302 响应,调用方应直接 return"""
        if self._is_authed():
            return True
        if self._wants_html():
            # 浏览器场景:跳转到登录页,带 return 参数
            from urllib.parse import quote
            self._redirect(f"/login.html?return={quote(self.path)}")
        else:
            self._json(401, {"error": "auth required"})
        return False

    def do_GET(self):
        p = self.path.split("?")[0]

        # ===== 公开端点 =====
        if p == "/login.html":
            self._serve_file(BASE_DIR / "login.html")
            return

        if p == "/favicon.ico":
            # 不重要,404 即可
            self.send_error(404)
            return

        # ===== 需要登录 =====
        if not self._require_auth():
            return

        if p == "/refresh/status":
            with _lock:
                state = dict(_refresh_state)
            self._json(200, state)
            return

        if p == "/favorites":
            fav_path = DATA_DIR / "favorites.json"
            try:
                data = fav_path.read_bytes() if fav_path.exists() else b"{}"
            except Exception:
                data = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if p == "/settings":
            self._json(200, _read_settings())
            return

        # 静态文件
        if p == "/" or p == "":
            p = "/dashboard.html"
        file_path = BASE_DIR / p.lstrip("/")
        self._serve_file(file_path)

    def do_PUT(self):
        if not self._require_auth():
            return
        p = self.path.split("?")[0]
        if p == "/favorites":
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n) if n > 0 else b"{}"
                json.loads(body)
            except Exception as e:
                self._json(400, {"error": f"invalid json: {e}"})
                return
            fav_path = DATA_DIR / "favorites.json"
            tmp_path = DATA_DIR / "favorites.json.tmp"
            with _fav_lock:
                tmp_path.write_bytes(body)
                os.replace(str(tmp_path), str(fav_path))
                _snapshot_favorites(body)
            self._json(200, {"ok": True})
            return

        if p == "/settings":
            try:
                n = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(n).decode("utf-8") if n > 0 else "{}"
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("must be an object")
            except Exception as e:
                self._json(400, {"error": f"invalid json: {e}"})
                return
            # 白名单校验
            if "model" in data and data["model"] not in ALLOWED_MODELS:
                self._json(400, {"error": f"model must be one of {sorted(ALLOWED_MODELS)}"})
                return
            # 合并到已有 settings(只更新提供的字段)
            current = _read_settings()
            current.update(data)
            _write_settings(current)
            self._json(200, {"ok": True, "settings": current})
            return

        self.send_error(404)

    def do_POST(self):
        p = self.path.split("?")[0]

        # ===== /login 公开,但要求 POST body =====
        if p == "/login":
            try:
                n = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(n).decode("utf-8") if n > 0 else ""
                # 同时接受 JSON 和表单
                pwd = ""
                try:
                    pwd = json.loads(raw).get("password", "")
                except Exception:
                    qs = parse_qs(raw)
                    pwd = qs.get("password", [""])[0]
            except Exception:
                self._json(400, {"error": "bad request"})
                return

            if not SITE_PASSWORD:
                self._json(503, {"error": "auth not configured on server"})
                return
            # 慢化弱密码爆破:固定 0.3s 延迟
            time.sleep(0.3)
            if not hmac.compare_digest(pwd, SITE_PASSWORD):
                self._json(401, {"error": "wrong password"})
                return

            token = _make_token()
            cookie = (
                f"{SESSION_COOKIE}={token}; Path=/; Max-Age={SESSION_DAYS*86400}; "
                f"HttpOnly; SameSite=Lax"
            )
            # https 自动加 Secure(Railway 反代是 https,但请求 Header 不一定带 X-Forwarded-Proto)
            # 加上 Secure 在 http localhost 会导致 cookie 被丢弃;先不加 Secure,Railway 终端是 https 没问题
            self._json(200, {"ok": True}, extra_headers=[("Set-Cookie", cookie)])
            return

        if p == "/logout":
            cookie = f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
            self._json(200, {"ok": True}, extra_headers=[("Set-Cookie", cookie)])
            return

        # ===== 需要登录 =====
        if not self._require_auth():
            return

        if p == "/refresh":
            with _lock:
                if _refresh_state["running"]:
                    payload = dict(_refresh_state)
                    payload["error"] = "already running"
                    self._json(409, payload)
                    return
                _refresh_state.update({
                    "running": True,
                    "done": False,
                    "ok": False,
                    "started_at": time.time(),
                    "duration_seconds": None,
                    **_estimate_refresh_time(),
                })
                payload = dict(_refresh_state)

            t = threading.Thread(target=_run_refresh, daemon=True)
            t.start()
            self._json(200, {"status": "started", **payload})
            return

        self.send_error(404)


if __name__ == "__main__":
    # 静态文件、收藏与刷新状态可并发响应，避免某个慢客户端占住整个服务。
    httpd = ThreadingHTTPServer(("", PORT), Handler)
    print(f"Serving at http://localhost:{PORT}/dashboard.html")
    httpd.serve_forever()
