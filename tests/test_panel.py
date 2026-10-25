"""Panel testi: haqiqiy HTTP server localhost'da, soxta Telegram bilan."""
from __future__ import annotations

import http.client
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_system import OWNER, make_app  # noqa: E402


class PanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.app = make_app(cls.tmp)
        cls.app.run_in_background = lambda fn, *a: fn(*a)
        from zft.panel import Panel
        cls.panel = Panel(cls.app, "juda-kuchli-parol-123", port=0, secure_cookie=False)
        cls.srv = cls.panel.serve()
        cls.port = cls.srv.server_address[1]
        cls.pid = cls.app.pipeline.create_daily_content("manual")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.app.db.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, method, path, body=None, cookie=None, csrf=True, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Content-Type": "application/json"}
        if csrf:
            h["X-Panel"] = "1"
        if cookie:
            h["Cookie"] = cookie
        h.update(headers or {})
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def login(self):
        r, _ = self.req("POST", "/api/login", {"password": "juda-kuchli-parol-123"})
        self.assertEqual(r.status, 200)
        return r.getheader("Set-Cookie").split(";")[0]

    def test_page_and_auth_required(self):
        r, body = self.req("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn(b"ZFT Panel", body)
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
        r, _ = self.req("GET", "/api/overview")
        self.assertEqual(r.status, 401)
        r, _ = self.req("GET", f"/api/video/{self.pid}")
        self.assertEqual(r.status, 401)

    def test_wrong_password_and_lockout(self):
        from zft.panel import Panel
        p = Panel(self.app, "boshqa-parol-xyz-1")
        for _ in range(5):
            self.assertIsNone(p.login("9.9.9.9", "notogri"))
        self.assertIsNone(p.login("9.9.9.9", "boshqa-parol-xyz-1"))  # bloklangan
        self.assertIsNotNone(p.login("8.8.8.8", "boshqa-parol-xyz-1"))

    def test_csrf_header_required(self):
        ck = self.login()
        r, _ = self.req("POST", "/api/auto", {"on": True}, cookie=ck, csrf=False)
        self.assertEqual(r.status, 403)

    def test_forged_cookie_rejected(self):
        r, _ = self.req("GET", "/api/overview", cookie="chorraha_panel=9999999999.abc")
        self.assertEqual(r.status, 401)

    def test_overview_actions_and_video(self):
        ck = self.login()
        r, body = self.req("GET", "/api/overview", cookie=ck)
        self.assertEqual(r.status, 200)
        d = json.loads(body)
        self.assertEqual(d["pending"]["id"], self.pid)
        self.assertEqual(len(d["series"]), 14)
        self.assertTrue(d["sample_questions"])
        # video oqimi (Range bilan)
        r, chunk = self.req("GET", f"/api/video/{self.pid}", cookie=ck, headers={"Range": "bytes=0-99"})
        self.assertEqual(r.status, 206)
        self.assertEqual(len(chunk), 100)
        # havola yaratish
        r, body = self.req("POST", "/api/links", {"code": "avtomaktab_7", "label": "7-avtomaktab"}, cookie=ck)
        self.assertEqual(r.status, 200)
        self.assertIn("start=avtomaktab_7", json.loads(body)["url"])
        r, _ = self.req("POST", "/api/links", {"code": "yomon kod!"}, cookie=ck)
        self.assertEqual(r.status, 400)
        # tasdiqlash → e'lon
        r, body = self.req("POST", f"/api/posts/{self.pid}/approve", {}, cookie=ck)
        self.assertEqual(json.loads(body)["status"], "approved")
        r, _ = self.req("POST", "/api/publish-now", {}, cookie=ck)
        self.assertEqual(r.status, 202)
        self.assertEqual(self.app.db.post(self.pid)["status"], "published")
        # avto e'lon
        self.req("POST", "/api/auto", {"on": True}, cookie=ck)
        self.assertTrue(self.app.auto_publish)
        self.req("POST", "/api/auto", {"on": False}, cookie=ck)
        # ro'yxatlar
        r, body = self.req("GET", "/api/posts", cookie=ck)
        self.assertTrue(any(p["id"] == self.pid for p in json.loads(body)))
        r, body = self.req("GET", "/api/log", cookie=ck)
        actions = {x["action"] for x in json.loads(body)}
        self.assertIn("post_approve", actions)
        self.assertIn("login", actions)


if __name__ == "__main__":
    unittest.main()
