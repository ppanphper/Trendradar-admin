import contextlib
import http.client
import importlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token-0123456789"

CONFIG_YAML = """app:
  timezone: Asia/Shanghai
platforms:
  sources:
    - id: "toutiao"
      name: "今日头条"
      expected_domain: "toutiao.com"
rss:
  enabled: true
report:
  mode: current
filter:
  method: keyword
notification:
  enabled: false
"""

EDITOR_HTML = """<!doctype html><html><head>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/js-yaml/4.1.0/js-yaml.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Sortable/1.15.0/Sortable.min.js"></script>
</head><body><nav><div class="h-14">
<div class="flex items-center gap-3">
  <button id="lang-toggle-btn" onclick="switchLang('en')">EN</button>
  <button onclick="openLoadConfigModal()" class="x"><i></i><span>加载官网最新配置</span></button>
  <button onclick="copyResult()" class="y"><i></i><span>复制配置</span></button>
</div></div></nav>
<div class="support-sidebar-wrap"><div><img src="https://example.com/a.png"></div></div>
</body></html>
"""


class AdminServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="tr-admin-test-"))
        (cls.tmp / "config").mkdir()
        (cls.tmp / "docs").mkdir()
        (cls.tmp / "output").mkdir()
        (cls.tmp / "config" / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
        (cls.tmp / "docs" / "index.html").write_text(EDITOR_HTML, encoding="utf-8")
        env = {
            "TRENDRADAR_BASE_DIR": str(cls.tmp),
            "TRENDRADAR_CONFIG_DIR": str(cls.tmp / "config"),
            "TRENDRADAR_OUTPUT_DIR": str(cls.tmp / "output"),
            "TRENDRADAR_DOCS_DIR": str(cls.tmp / "docs"),
            "TRENDRADAR_ADMIN_DIR": str(REPO_ROOT / "admin"),
            "TRENDRADAR_ADMIN_TOKEN": TOKEN,
            "TRENDRADAR_ADMIN_ALLOWED_HOSTS": "127.0.0.1,localhost",
        }
        cls._saved_env = {key: os.environ.get(key) for key in [*env, "TRENDRADAR_ADMIN_ENABLE_RUN"]}
        os.environ.update(env)
        os.environ.pop("TRENDRADAR_ADMIN_ENABLE_RUN", None)
        sys.path.insert(0, str(REPO_ROOT / "admin"))
        sys.modules.pop("admin_server", None)
        cls.server_module = importlib.import_module("admin_server")
        cls.server_module.LOGIN_FAILURE_DELAY = 0
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), cls.server_module.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.origin = f"http://127.0.0.1:{cls.port}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        sys.path.remove(str(REPO_ROOT / "admin"))
        for key, value in cls._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def request(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        all_headers = {"Host": host or f"127.0.0.1:{self.port}"}
        all_headers.update({key: value for key, value in (headers or {}).items() if value is not None})
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        with contextlib.redirect_stdout(io.StringIO()):
            conn.request(method, path, body=body, headers=all_headers)
            response = conn.getresponse()
            data = response.read()
        conn.close()
        return response, data

    def login(self):
        response, _ = self.request(
            "POST", "/login", body=f"token={TOKEN}",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": self.origin, "Sec-Fetch-Site": "same-origin"},
        )
        self.assertEqual(response.status, 303)
        cookie = response.getheader("Set-Cookie")
        return cookie.split(";", 1)[0], cookie

    def browser_post(self, path, payload, cookie, origin=None, fetch_site="same-origin", content_type="application/json"):
        return self.request("POST", path, body=payload, headers={
            "Cookie": cookie,
            "Origin": origin or self.origin,
            "Sec-Fetch-Site": fetch_site,
            "Content-Type": content_type,
        })

    def test_unauthenticated_requests_are_rejected(self):
        response, data = self.request("GET", "/")
        self.assertEqual(response.status, 200)
        self.assertIn("访问 Token".encode(), data)
        self.assertNotIn(b"admin-bridge.js", data)
        for path in ("/api/state", "/admin-bridge.js", "/vendor/js-yaml-4.1.0.min.js", "/assets/x.js"):
            self.assertEqual(self.request("GET", path)[0].status, 401, path)

    def test_query_token_no_longer_logs_in(self):
        response, data = self.request("GET", f"/?token={TOKEN}")
        self.assertIsNone(response.getheader("Set-Cookie"))
        self.assertIn("访问 Token".encode(), data)

    def test_login_with_wrong_token_fails(self):
        response, data = self.request(
            "POST", "/login", body="token=wrong",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": self.origin},
        )
        self.assertEqual(response.status, 401)
        self.assertIsNone(response.getheader("Set-Cookie"))
        self.assertIn("Token 不正确".encode(), data)

    def test_session_cookie_is_strict_and_grants_access(self):
        session, raw_cookie = self.login()
        self.assertIn("HttpOnly", raw_cookie)
        self.assertIn("SameSite=Strict", raw_cookie)
        self.assertNotIn(TOKEN, raw_cookie)
        response, data = self.request("GET", "/api/state", headers={"Cookie": session})
        self.assertEqual(response.status, 200)
        state = json.loads(data)
        self.assertEqual(state["features"], {"run": False})
        self.assertEqual(state["service"]["web"], "http://127.0.0.1:8080/")

    def test_cross_origin_login_rejected(self):
        response, _ = self.request(
            "POST", "/login", body=f"token={TOKEN}",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": "http://127.0.0.1:8099", "Sec-Fetch-Site": "same-site"},
        )
        self.assertEqual(response.status, 403)

    def test_csrf_from_another_local_port_is_rejected(self):
        session, _ = self.login()
        payload = {"file": "config", "content": CONFIG_YAML}
        # 同一 IP 的其他端口在浏览器看来是 same-site，cookie 会被带上
        response, _ = self.browser_post("/api/validate", payload, session, origin="http://127.0.0.1:8099", fetch_site="same-site")
        self.assertEqual(response.status, 403)
        response, _ = self.browser_post("/api/validate", payload, session, origin="http://127.0.0.1:8099", fetch_site=None)
        self.assertEqual(response.status, 403)
        response, _ = self.browser_post("/api/validate", json.dumps(payload), session, content_type="text/plain")
        self.assertEqual(response.status, 415)
        response, data = self.browser_post("/api/validate", payload, session)
        self.assertEqual(response.status, 200, data)
        # 表单导航在某些引用策略下 Origin 为 null，以浏览器附带的 Sec-Fetch-Site 为准
        response, data = self.browser_post("/api/validate", payload, session, origin="null", fetch_site="same-origin")
        self.assertEqual(response.status, 200, data)

    def test_bearer_token_works_without_origin(self):
        response, data = self.request(
            "POST", "/api/validate", body={"file": "config", "content": CONFIG_YAML},
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 200, data)
        self.assertEqual(self.request("GET", "/api/state", headers={"Authorization": "Bearer wrong"})[0].status, 401)

    def test_unknown_host_rejected(self):
        response, _ = self.request("GET", "/", host=f"evil.example:{self.port}")
        self.assertEqual(response.status, 403)
        response, _ = self.request("POST", "/login", body=f"token={TOKEN}", host="evil.example")
        self.assertEqual(response.status, 403)

    def test_logout_invalidates_session(self):
        session, _ = self.login()
        response, _ = self.request("POST", "/logout", headers={"Cookie": session, "Origin": self.origin})
        self.assertEqual(response.status, 303)
        self.assertEqual(self.request("GET", "/api/state", headers={"Cookie": session})[0].status, 401)

    def test_report_route_removed_and_run_disabled(self):
        session, _ = self.login()
        self.assertEqual(self.request("GET", "/report", headers={"Cookie": session})[0].status, 404)
        response, _ = self.browser_post("/api/run", {}, session)
        self.assertEqual(response.status, 403)

    def test_body_size_limit(self):
        session, _ = self.login()
        response, _ = self.request("POST", "/api/validate", headers={
            "Cookie": session, "Origin": self.origin, "Content-Type": "application/json",
            "Content-Length": str(self.server_module.MAX_BODY_BYTES + 1),
        })
        self.assertEqual(response.status, 413)

    def test_editor_uses_local_vendor_assets_with_csp(self):
        session, _ = self.login()
        response, data = self.request("GET", "/", headers={"Cookie": session})
        html = data.decode("utf-8")
        csp = response.getheader("Content-Security-Policy")
        self.assertIn("script-src 'self'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertNotIn("https://cdn", html)
        self.assertIn('/vendor/tailwindcss-play-3.4.17.js', html)
        self.assertNotIn("openLoadConfigModal", html)
        self.assertNotIn("copyResult", html)
        self.assertIn("lang-toggle-btn", html)
        self.assertNotIn("support-sidebar-wrap", html)
        self.assertIn('<script src="/admin-bridge.js"></script>', html)
        for path in ("/vendor/tailwindcss-play-3.4.17.js", "/vendor/fontawesome/css/all.min.css",
                     "/vendor/fontawesome/webfonts/fa-solid-900.woff2"):
            self.assertEqual(self.request("GET", path, headers={"Cookie": session})[0].status, 200, path)

    def test_static_path_traversal_blocked(self):
        session, _ = self.login()
        for path in ("/vendor/../admin_server.py", "/vendor/%2e%2e/admin_server.py", "/assets/../config/config.yaml"):
            self.assertEqual(self.request("GET", path, headers={"Cookie": session})[0].status, 404, path)

    def test_save_creates_backup_and_restore_is_confined(self):
        session, _ = self.login()
        updated = CONFIG_YAML.replace("Asia/Shanghai", "UTC")
        response, data = self.browser_post("/api/file/config", {"content": updated}, session)
        self.assertEqual(response.status, 200, data)
        backup = json.loads(data)["backup"]
        self.assertTrue((self.tmp / "config" / "backups" / backup).exists())
        response, _ = self.browser_post("/api/backup/restore", {"name": "../../etc/passwd"}, session)
        self.assertEqual(response.status, 400)
        response, _ = self.browser_post("/api/backup/restore", {"name": backup}, session)
        self.assertEqual(response.status, 200)
        self.assertEqual((self.tmp / "config" / "config.yaml").read_text(encoding="utf-8"), CONFIG_YAML)

    def test_log_redacts_query_string(self):
        handler = self.server_module.Handler.__new__(self.server_module.Handler)
        handler.log_date_time_string = lambda: "now"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            handler.log_message('"%s" %s %s', f"GET /?token={TOKEN} HTTP/1.1", "200", "-")
        self.assertNotIn(TOKEN, buffer.getvalue())
        self.assertIn("?<redacted>", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
