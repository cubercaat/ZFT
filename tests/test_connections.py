"""Ulanishlar: Instagram/TikTok OAuth (parolsiz), token yangilash, Telegram chatni avto-aniqlash."""
from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_system import OWNER, FakeResp, make_app  # noqa: E402

ENV = {"IG_APP_ID": "111", "IG_APP_SECRET": "sec", "TIKTOK_CLIENT_KEY": "ck", "TIKTOK_CLIENT_SECRET": "cs",
       "PANEL_URL": "https://panel.test"}


class ConnectionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = make_app(self.tmp, PUBLIC_MEDIA_URL="https://media.test")
        self.app.run_in_background = lambda fn, *a: fn(*a)
        self.c = self.app.conn
        self.env = mock.patch.dict(os.environ, ENV)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.app.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_app_credentials_gives_clear_error(self):
        from zft.connections import ConnectError
        with mock.patch.dict(os.environ, {"IG_APP_ID": "", "IG_APP_SECRET": ""}):
            with self.assertRaises(ConnectError):
                self.c.start("instagram")

    def test_instagram_oauth_full_flow(self):
        url = self.c.start("instagram")
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.assertTrue(url.startswith("https://www.instagram.com/oauth/authorize"))
        self.assertEqual(q["redirect_uri"], "https://panel.test/oauth/instagram/callback")
        self.assertIn("instagram_business_content_publish", q["scope"])
        self.assertNotIn("password", url)
        sess = mock.Mock()
        sess.post.return_value = FakeResp(200, {"access_token": "SHORT", "user_id": 777})
        sess.get.side_effect = [FakeResp(200, {"access_token": "LONG", "expires_in": 5184000}),
                                FakeResp(200, {"user_id": "17841", "username": "chorraha.uz"})]
        self.c.http = sess
        d = self.c.callback("instagram", "CODE", q["state"])
        self.assertEqual((d["token"], d["username"], d["user_id"]), ("LONG", "chorraha.uz", "17841"))
        self.assertEqual(sess.post.call_args.kwargs["data"]["grant_type"], "authorization_code")
        # state bir martalik — qayta ishlatib bo'lmaydi
        from zft.connections import ConnectError
        with self.assertRaises(ConnectError):
            self.c.callback("instagram", "CODE", q["state"])
        ig = self.app.instagram
        self.assertIsNotNone(ig)
        self.assertEqual((ig.ig, ig.token), ("17841", "LONG"))
        self.assertTrue(ig.base.startswith("https://graph.instagram.com"))
        self.assertIn("instagram", [p.platform for p in self.app.publishers()])
        self.assertTrue(self.c.status()["instagram"]["connected"])
        self.c.disconnect("instagram")
        self.assertIsNone(self.app.instagram)

    def test_forged_state_rejected(self):
        from zft.connections import ConnectError
        with self.assertRaises(ConnectError):
            self.c.callback("instagram", "CODE", "soxta-state")

    def test_tiktok_flow_and_refresh(self):
        url = self.c.start("tiktok")
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.assertIn("video.publish", q["scope"])
        sess = mock.Mock()
        sess.post.return_value = FakeResp(200, {"access_token": "AT", "refresh_token": "RT", "open_id": "o1",
                                                "expires_in": 86400, "refresh_expires_in": 31536000})
        sess.get.return_value = FakeResp(200, {"data": {"user": {"display_name": "chorraha"}}, "error": {"code": "ok"}})
        self.c.http = sess
        d = self.c.callback("tiktok", "CODE", q["state"])
        self.assertEqual((d["token"], d["username"]), ("AT", "chorraha"))
        self.assertEqual(self.app.tiktok.token, "AT")
        # muddati tugashiga oz qolganda o'zi yangilanadi
        d["expires_at"] = d["expires_at"] - 86000
        self.c.save("tiktok", d)
        sess.post.return_value = FakeResp(200, {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 86400})
        self.assertEqual(self.c.refresh_all()["tiktok"], "yangilandi")
        self.assertEqual(self.app.tiktok.token, "AT2")

    def test_instagram_refresh_failure_notifies_owner(self):
        self.c.save("instagram", {"token": "T", "user_id": "1", "username": "x", "expires_at": 10**10 + 0, "refreshed_at": 0})
        d = self.c.get("instagram")
        d["expires_at"] = int(__import__("time").time()) + 3 * 86400
        self.c.save("instagram", d)
        sess = mock.Mock()
        sess.get.return_value = FakeResp(400, {"error": {"message": "Session expired", "code": 190}})
        self.c.http = sess
        self.assertTrue(self.c.refresh_all()["instagram"].startswith("xato"))
        self.assertTrue(any("Ulanishlar" in m["text"] for m in self.app.hub.messages()))

    def test_telegram_chat_autodetect_and_select(self):
        upd = {"update_id": 5, "my_chat_member": {"chat": {"id": -100555, "title": "Chorraha kanal", "type": "channel"},
                                                  "new_chat_member": {"status": "administrator"}}}
        self.app.bot.handle_update(upd)
        st = self.c.status()["telegram"]
        self.assertEqual(st["chats"][0]["title"], "Chorraha kanal")
        self.assertTrue(any("admin bo'ldi" in t for t in self.app.tg.texts(OWNER)))
        self.c.use_chat("-100555", "channel")
        self.app.publishers()
        self.assertEqual(self.app.channel.channel_id, "-100555")
        self.app.bot.handle_update({"update_id": 6, "my_chat_member": {"chat": {"id": -100555, "type": "channel"},
                                                                       "new_chat_member": {"status": "left"}}})
        self.assertEqual(self.c.status()["telegram"]["chats"], [])


class ConnectionsPanelTest(unittest.TestCase):
    def test_panel_endpoints_and_callback_redirect(self):
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.dict(os.environ, ENV):
            app = make_app(tmp)
            from zft.panel import Panel
            srv = Panel(app, "juda-kuchli-parol-123", port=0, secure_cookie=False).serve()
            port = srv.server_address[1]

            def req(method, path, body=None, ck=None):
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
                h = {"Content-Type": "application/json", "X-Panel": "1"}
                if ck:
                    h["Cookie"] = ck
                c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
                r = c.getresponse()
                data = r.read()
                c.close()
                return r, data

            r, _ = req("POST", "/api/connections/instagram/start", {})
            self.assertEqual(r.status, 401)  # kirmasdan ulab bo'lmaydi
            r, _ = req("POST", "/api/login", {"password": "juda-kuchli-parol-123"})
            ck = r.getheader("Set-Cookie").split(";")[0]
            r, body = req("POST", "/api/connections/instagram/start", {}, ck)
            url = json.loads(body)["url"]
            state = parse_qs(urlparse(url).query)["state"][0]
            r, _ = req("GET", f"/oauth/instagram/callback?error=access_denied&state={state}")
            self.assertEqual((r.status, r.getheader("Location")), (302, "/#ulanishlar"))
            r, body = req("GET", "/api/connections", ck=ck)
            d = json.loads(body)
            self.assertIn("ulanmadi", d["last"])
            self.assertFalse(d["instagram"]["connected"])
            self.assertEqual(d["instagram"]["redirect_uri"], "https://panel.test/oauth/instagram/callback")
            srv.shutdown()
            app.db.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
