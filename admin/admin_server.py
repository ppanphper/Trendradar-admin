#!/usr/bin/env python3
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

BASE_DIR = Path(os.environ.get("TRENDRADAR_BASE_DIR", "/app"))
CONFIG_DIR = Path(os.environ.get("TRENDRADAR_CONFIG_DIR", str(BASE_DIR / "config")))
OUTPUT_DIR = Path(os.environ.get("TRENDRADAR_OUTPUT_DIR", str(BASE_DIR / "output")))
DOCS_DIR = Path(os.environ.get("TRENDRADAR_DOCS_DIR", str(BASE_DIR / "docs")))
ADMIN_DIR = Path(os.environ.get("TRENDRADAR_ADMIN_DIR", str(BASE_DIR / "admin")))
VENDOR_DIR = ADMIN_DIR / "vendor"
BACKUP_DIR = CONFIG_DIR / "backups"
ADMIN_TOKEN = os.environ.get("TRENDRADAR_ADMIN_TOKEN", "").strip()
if not ADMIN_TOKEN and os.environ.get("TRENDRADAR_ADMIN_TOKEN_FILE"):
    try:
        ADMIN_TOKEN = Path(os.environ["TRENDRADAR_ADMIN_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
    except OSError:
        ADMIN_TOKEN = ""
RUN_ENABLED = os.environ.get("TRENDRADAR_ADMIN_ENABLE_RUN", "0").strip().lower() in ("1", "true", "yes")
# Host 白名单用于防御 DNS rebinding；通过局域网或反向代理访问时需加入对应主机名
ALLOWED_HOSTS = {
    host.strip().lower()
    for host in os.environ.get("TRENDRADAR_ADMIN_ALLOWED_HOSTS", "127.0.0.1,localhost,[::1]").split(",")
    if host.strip()
}
SESSION_TTL = int(os.environ.get("TRENDRADAR_ADMIN_SESSION_TTL", "43200"))
SESSION_COOKIE = "tr_admin_session"
MAX_BODY_BYTES = 2 * 1024 * 1024
LOGIN_FAILURE_DELAY = 1.0
SESSIONS = {}
SESSIONS_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()
RUN_STATE = {"running": False, "started_at": None, "finished_at": None, "exit_code": None, "log": ""}

TEXT_FILES = {
    "config": {"path": CONFIG_DIR / "config.yaml", "label": "主配置 config.yaml", "kind": "yaml"},
    "frequency": {"path": CONFIG_DIR / "frequency_words.txt", "label": "关键词 frequency_words.txt", "kind": "text"},
    "timeline": {"path": CONFIG_DIR / "timeline.yaml", "label": "时间线 timeline.yaml", "kind": "yaml"},
    "ai_interests": {"path": CONFIG_DIR / "ai_interests.txt", "label": "AI 兴趣 ai_interests.txt", "kind": "text"},
    "ai_analysis_prompt": {"path": CONFIG_DIR / "ai_analysis_prompt.txt", "label": "AI 分析提示词", "kind": "text"},
    "ai_translation_prompt": {"path": CONFIG_DIR / "ai_translation_prompt.txt", "label": "AI 翻译提示词", "kind": "text"},
}

# 官方编辑器引用的 CDN 资源改由本地 vendor/ 提供（来源与校验见 admin/vendor/SOURCES.md），
# 这样第三方脚本无法在管理页面的同源上下文中运行
CDN_REWRITES = (
    ('"https://cdn.tailwindcss.com"', '"/vendor/tailwindcss-play-3.4.17.js"'),
    ('"https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"', '"/vendor/fontawesome/css/all.min.css"'),
    ('"https://cdnjs.cloudflare.com/ajax/libs/js-yaml/4.1.0/js-yaml.min.js"', '"/vendor/js-yaml-4.1.0.min.js"'),
    ('"https://cdnjs.cloudflare.com/ajax/libs/Sortable/1.15.0/Sortable.min.js"', '"/vendor/Sortable-1.15.0.min.js"'),
)
EXTERNAL_RESOURCE_RE = re.compile(r'<(?:script|link)\b[^>]*(?:src|href)="(https?://[^"]+)"', re.IGNORECASE)
# 服务器模式下无意义或有风险的官方按钮：加载官网配置会覆盖编辑器内容
OFFICIAL_BUTTON_RE = re.compile(r'<button\b[^>]*onclick="(?:openLoadConfigModal|copyResult)\(\)"[^>]*>.*?</button>\s*', re.DOTALL)
QUERY_RE = re.compile(r"\?[^\s\"]*")

EDITOR_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
LOGIN_CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"


class RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def validate_file(file_key: str, content: str):
    spec = TEXT_FILES[file_key]
    if spec["kind"] != "yaml":
        return True
    loaded = yaml.safe_load(content) if content.strip() else {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{spec['label']} 必须是 YAML 对象")
    if file_key == "config":
        required = ("app", "platforms", "rss", "report", "filter", "notification")
        missing = [key for key in required if key not in loaded]
        if missing:
            raise ValueError("config.yaml 缺少必要段落: " + ", ".join(missing))
    if file_key == "timeline" and "presets" not in loaded and "custom" not in loaded:
        raise ValueError("timeline.yaml 至少需要 presets 或 custom 段落")
    return True


def backup_file(path: Path, reason="save"):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = BACKUP_DIR / f"{stamp}-{reason}-{path.name}"
    shutil.copy2(path, dest)
    return dest.name


def load_yaml_file(file_key):
    content = read_text(TEXT_FILES[file_key]["path"])
    return (yaml.safe_load(content) or {}, content) if content.strip() else ({}, content)


def dump_yaml(data):
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)


def set_nested(data, dotted, value):
    current = data
    parts = dotted.split(".")
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def latest_report():
    candidates = [OUTPUT_DIR / "html" / "latest" / "current.html", OUTPUT_DIR / "index.html"]
    for path in candidates:
        if path.exists():
            return {"mtime": path.stat().st_mtime, "size": path.stat().st_size}
    return None


def service_urls(handler):
    scheme = handler.headers.get("X-Forwarded-Proto", "http").split(",")[0].strip() or "http"
    host = handler.headers.get("X-Forwarded-Host") or handler.headers.get("Host") or "127.0.0.1:8081"
    admin_url = os.environ.get("TRENDRADAR_ADMIN_PUBLIC_URL", "").strip() or f"{scheme}://{host}/"
    web_url = os.environ.get("TRENDRADAR_WEB_PUBLIC_URL", "").strip()
    if not web_url:
        web_port = os.environ.get("TRENDRADAR_WEB_PORT", "8080")
        web_url = f"{scheme}://{split_hostname(host)}:{web_port}/"
    return {"web": web_url.rstrip("/") + "/", "admin": admin_url.rstrip("/") + "/"}


def split_hostname(host: str) -> str:
    if host.startswith("[") and "]" in host:
        return host[:host.index("]") + 1]
    return host.split(":", 1)[0]


def list_backups():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(BACKUP_DIR.glob("*"), reverse=True)[:100]:
        if path.is_file():
            items.append({"name": path.name, "size": path.stat().st_size, "mtime": path.stat().st_mtime})
    return items


def safe_child(base: Path, request_path: str):
    target = (base / request_path.lstrip("/")).resolve()
    base_resolved = base.resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError:
        return None
    return target


def content_type_for(path: Path):
    guess, _ = mimetypes.guess_type(str(path))
    if guess:
        if guess.startswith("text/") or guess in ("application/javascript", "application/json"):
            return guess + "; charset=utf-8"
        return guess
    if path.suffix == ".woff2":
        return "font/woff2"
    return "application/octet-stream"


def official_editor_html():
    index_path = DOCS_DIR / "index.html"
    if not index_path.exists():
        return "<!doctype html><meta charset=\"utf-8\"><title>TrendRadar Admin</title><p>官方配置编辑器文件不存在，请检查 /app/docs/index.html。</p>"
    html = index_path.read_text(encoding="utf-8")
    html = html.replace("纯静态页面，数据仅保存在你的本地浏览器，请放心使用", "服务器增强模式：配置保存到当前 TrendRadar 部署")
    for remote, local in CDN_REWRITES:
        html = html.replace(remote, local)
    remaining = EXTERNAL_RESOURCE_RE.findall(html)
    if remaining:
        # CSP 会拦截这些资源；通常是官方编辑器升级了依赖版本，需要同步更新 vendor/
        print("WARNING: editor still references external resources blocked by CSP: " + ", ".join(remaining), flush=True)
    bridge = '    <script src="/admin-bridge.js"></script>\n'
    if "/admin-bridge.js" not in html:
        html = html.replace("</body>", bridge + "</body>")
    html = OFFICIAL_BUTTON_RE.sub("", html)
    return remove_support_sidebar(html)


def remove_support_sidebar(html: str) -> str:
    marker = '<div class="support-sidebar-wrap'
    start = html.find(marker)
    if start < 0:
        return html

    tag_pattern = re.compile(r"</?div\b[^>]*>", re.IGNORECASE)
    depth = 0
    for match in tag_pattern.finditer(html, start):
        tag = match.group(0)
        if tag.startswith("</"):
            depth -= 1
            if depth == 0:
                return html[:start] + html[match.end():]
        else:
            depth += 1
    return html


def create_session():
    sid = secrets.token_urlsafe(32)
    now = time.time()
    with SESSIONS_LOCK:
        for key in [key for key, expiry in SESSIONS.items() if expiry < now]:
            del SESSIONS[key]
        SESSIONS[sid] = now + SESSION_TTL
    return sid


def session_valid(sid: str) -> bool:
    if not sid:
        return False
    with SESSIONS_LOCK:
        expiry = SESSIONS.get(sid)
        if expiry is None:
            return False
        if expiry < time.time():
            del SESSIONS[sid]
            return False
        return True


def drop_session(sid: str):
    with SESSIONS_LOCK:
        SESSIONS.pop(sid, None)


def token_matches(candidate: str) -> bool:
    if not ADMIN_TOKEN:
        return False
    return hmac.compare_digest(candidate.encode("utf-8", "replace"), ADMIN_TOKEN.encode("utf-8"))


def run_crawl_background():
    with RUN_LOCK:
        if RUN_STATE.get("running"):
            return False
        RUN_STATE.update({"running": True, "started_at": now_iso(), "finished_at": None, "exit_code": None, "log": ""})

    def target():
        try:
            proc = subprocess.run(
                ["python", "-m", "trendradar"],
                cwd=str(BASE_DIR),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=int(os.environ.get("TRENDRADAR_RUN_TIMEOUT", "900")),
            )
            with RUN_LOCK:
                RUN_STATE["exit_code"] = proc.returncode
                RUN_STATE["log"] = proc.stdout[-50000:]
        except Exception as exc:
            with RUN_LOCK:
                RUN_STATE["exit_code"] = -1
                RUN_STATE["log"] = str(exc)
        finally:
            with RUN_LOCK:
                RUN_STATE["finished_at"] = now_iso()
                RUN_STATE["running"] = False

    threading.Thread(target=target, daemon=True).start()
    return True


LOGIN_PAGE = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>TrendRadar Admin 登录</title><style>body{font-family:sans-serif;background:#f6f7fb;display:grid;place-items:center;height:100vh;margin:0}.box{background:white;padding:28px;border-radius:16px;box-shadow:0 10px 30px #0001}input{padding:10px;border:1px solid #ddd;border-radius:8px;width:280px}button{padding:10px 14px;border:0;border-radius:8px;background:#4f46e5;color:white;font-weight:700}.error{color:#b91c1c;font-size:14px}</style></head><body><form class="box" method="POST" action="/login"><h2>TrendRadar Admin</h2><label for="token">访问 Token</label><p><input id="token" name="token" type="password" autocomplete="current-password" autofocus required> <button>进入</button></p>{error}</form></body></html>'''


class Handler(BaseHTTPRequestHandler):
    server_version = "TrendRadarAdmin/1.0"

    def log_message(self, fmt, *args):
        # 查询串可能带凭据（例如旧版 ?token= 链接），日志中一律隐去
        message = QUERY_RE.sub("?<redacted>", fmt % args)
        print("[%s] %s" % (self.log_date_time_string(), message), flush=True)

    def _cookie(self, name):
        for part in self.headers.get("Cookie", "").split(";"):
            key, sep, value = part.strip().partition("=")
            if sep and key == name:
                return value
        return ""

    def _authorized(self):
        if not ADMIN_TOKEN:
            return False
        auth_value = self.headers.get("Authorization", "")
        if auth_value.startswith("Bearer ") and token_matches(auth_value[len("Bearer "):]):
            return True
        return session_valid(self._cookie(SESSION_COOKIE))

    def _host_allowed(self):
        if "*" in ALLOWED_HOSTS:
            return True
        host = (self.headers.get("Host") or "").strip().lower()
        return split_hostname(host) in ALLOWED_HOSTS

    def _same_origin(self):
        # 浏览器把同一 IP 的不同端口视为 same-site，SameSite cookie 挡不住，必须校验来源
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site is not None:
            # 现代浏览器自动附带、页面脚本无法伪造；表单导航的 Origin 可能是 null，以此为准
            return fetch_site in ("same-origin", "none")
        origin = self.headers.get("Origin")
        if origin is None:
            # curl 等非浏览器客户端两者都不带，也拿不到浏览器里的会话 cookie
            return True
        host = self.headers.get("Host", "")
        allowed = {f"http://{host}", f"https://{host}"}
        public_url = os.environ.get("TRENDRADAR_ADMIN_PUBLIC_URL", "").strip()
        if public_url:
            parsed = urlparse(public_url)
            allowed.add(f"{parsed.scheme}://{parsed.netloc}")
        return origin in allowed

    def _send(self, status=200, body=b"", content_type="application/json; charset=utf-8", extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status=200):
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _text(self, status, message):
        self._send(status, message, "text/plain; charset=utf-8")

    def _login_page(self, status=200, error=""):
        body = LOGIN_PAGE.replace("{error}", f'<p class="error" role="alert">{error}</p>' if error else "")
        self._send(status, body, "text/html; charset=utf-8", {"Content-Security-Policy": LOGIN_CSP})

    def _raw_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            raise RequestError(400, "invalid Content-Length")
        if length < 0:
            raise RequestError(400, "invalid Content-Length")
        if length > MAX_BODY_BYTES:
            raise RequestError(413, "request body too large")
        return self.rfile.read(length) if length else b""

    def _json_body(self):
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise RequestError(415, "Content-Type must be application/json")
        raw = self._raw_body()
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RequestError(400, "invalid JSON body")
        if not isinstance(data, dict):
            raise RequestError(400, "JSON body must be an object")
        return data

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._host_allowed():
            self._text(403, "host not allowed")
            return
        parsed = urlparse(self.path)
        if parsed.path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        authorized = self._authorized()
        if parsed.path == "/":
            if authorized:
                self._send(200, official_editor_html(), "text/html; charset=utf-8", {"Content-Security-Policy": EDITOR_CSP})
            else:
                self._login_page()
            return
        if not authorized:
            if parsed.path.startswith("/api"):
                self._json({"success": False, "error": "unauthorized"}, 401)
            else:
                self._text(401, "unauthorized")
            return
        try:
            if parsed.path == "/admin-bridge.js":
                bridge_path = ADMIN_DIR / "admin_bridge.js"
                if bridge_path.exists():
                    self._send(200, bridge_path.read_bytes(), "application/javascript; charset=utf-8")
                else:
                    self._text(404, "admin bridge not found")
            elif parsed.path.startswith("/assets/"):
                self._static(DOCS_DIR, parsed.path)
            elif parsed.path.startswith("/vendor/"):
                self._static(VENDOR_DIR, parsed.path[len("/vendor/"):])
            elif parsed.path == "/api/state":
                config, _ = load_yaml_file("config")
                texts = {key: read_text(spec["path"]) for key, spec in TEXT_FILES.items()}
                files = []
                for key, spec in TEXT_FILES.items():
                    path = spec["path"]
                    files.append({"key": key, "label": spec["label"], "exists": path.exists(), "mtime": path.stat().st_mtime if path.exists() else None})
                self._json({
                    "success": True,
                    "config": config,
                    "texts": texts,
                    "files": files,
                    "report": latest_report(),
                    "service": service_urls(self),
                    "features": {"run": RUN_ENABLED},
                })
            elif parsed.path == "/api/run":
                self._json({"success": True, "enabled": RUN_ENABLED, **RUN_STATE})
            elif parsed.path == "/api/backups":
                self._json({"success": True, "items": list_backups()})
            elif parsed.path.startswith("/api"):
                self._json({"success": False, "error": "not found"}, 404)
            else:
                self._text(404, "not found")
        except Exception as exc:
            self._json({"success": False, "error": str(exc)}, 500)

    def _static(self, base: Path, request_path: str):
        asset = safe_child(base, request_path)
        if asset and asset.is_file():
            self._send(200, asset.read_bytes(), content_type_for(asset), {"Cache-Control": "private, max-age=3600"})
        else:
            self._text(404, "asset not found")

    def do_POST(self):
        if not self._host_allowed():
            self._text(403, "host not allowed")
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/login":
                self._handle_login()
            elif parsed.path == "/logout":
                self._handle_logout()
            elif parsed.path.startswith("/api"):
                self._handle_api_post(parsed.path)
            else:
                self._text(404, "not found")
        except RequestError as exc:
            self._json({"success": False, "error": str(exc)}, exc.status)

    def _handle_login(self):
        if not self._same_origin():
            self._text(403, "cross-origin request rejected")
            return
        form = parse_qs(self._raw_body().decode("utf-8", "replace"))
        if not token_matches(form.get("token", [""])[0].strip()):
            time.sleep(LOGIN_FAILURE_DELAY)
            self._login_page(401, "Token 不正确")
            return
        sid = create_session()
        cookie = f"{SESSION_COOKIE}={sid}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Strict"
        self._send(303, "", "text/plain; charset=utf-8", {"Set-Cookie": cookie, "Location": "/"})

    def _handle_logout(self):
        if not self._same_origin():
            self._text(403, "cross-origin request rejected")
            return
        drop_session(self._cookie(SESSION_COOKIE))
        cookie = f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
        self._send(303, "", "text/plain; charset=utf-8", {"Set-Cookie": cookie, "Location": "/"})

    def _handle_api_post(self, path):
        if not self._authorized():
            self._json({"success": False, "error": "unauthorized"}, 401)
            return
        if not self._same_origin():
            self._json({"success": False, "error": "cross-origin request rejected"}, 403)
            return
        data = self._json_body()
        try:
            if path.startswith("/api/file/"):
                key = path.rsplit("/", 1)[-1]
                if key not in TEXT_FILES:
                    raise ValueError("unknown file")
                content = data.get("content", "")
                validate_file(key, content)
                file_path = TEXT_FILES[key]["path"]
                backup = backup_file(file_path, "save")
                atomic_write(file_path, content)
                self._json({"success": True, "backup": backup})
            elif path == "/api/validate":
                key = data.get("file", "config")
                if key not in TEXT_FILES:
                    raise ValueError("unknown file")
                validate_file(key, data.get("content", ""))
                self._json({"success": True})
            elif path == "/api/config/patch":
                config, _ = load_yaml_file("config")
                for key, value in data.get("patch", {}).items():
                    set_nested(config, key, value)
                content = dump_yaml(config)
                validate_file("config", content)
                backup = backup_file(TEXT_FILES["config"]["path"], "patch")
                atomic_write(TEXT_FILES["config"]["path"], content)
                self._json({"success": True, "backup": backup})
            elif path == "/api/run":
                if not RUN_ENABLED:
                    self._json({"success": False, "error": "manual run is disabled (TRENDRADAR_ADMIN_ENABLE_RUN)"}, 403)
                    return
                started = run_crawl_background()
                self._json({"success": True, "started": started, **RUN_STATE})
            elif path == "/api/backup/restore":
                name = Path(data.get("name", "")).name
                src = BACKUP_DIR / name
                if not src.exists():
                    raise ValueError("backup not found")
                allowed_names = {spec["path"].name for spec in TEXT_FILES.values()}
                target_name = None
                for allowed in allowed_names:
                    if name.endswith("-" + allowed) or name == allowed:
                        target_name = allowed
                        break
                if not target_name:
                    raise ValueError("backup target not allowed")
                target = CONFIG_DIR / target_name
                backup_file(target, "pre-restore")
                shutil.copy2(src, target)
                self._json({"success": True})
            else:
                self._json({"success": False, "error": "not found"}, 404)
        except Exception as exc:
            self._json({"success": False, "error": str(exc)}, 400)


def main():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not ADMIN_TOKEN:
        raise SystemExit("TRENDRADAR_ADMIN_TOKEN or TRENDRADAR_ADMIN_TOKEN_FILE is required")
    host = os.environ.get("TRENDRADAR_ADMIN_HOST", "127.0.0.1")
    port = int(os.environ.get("TRENDRADAR_ADMIN_PORT", "8081"))
    print(f"TrendRadar Admin listening on {host}:{port} (allowed hosts: {', '.join(sorted(ALLOWED_HOSTS))}; manual run {'enabled' if RUN_ENABLED else 'disabled'})", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
