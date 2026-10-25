"""Sozlash oynasi: token tekshiruvi, .env ga yozish, HTTP oqimi."""
from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from zft import setup_web

TOKEN = "123456789:AAH" + "x" * 32


class FakeHTTP:
    def __init__(self, ok=True):
        self.ok = ok

    def get(self, url, timeout=0):
        ok = self.ok

        class R:
            def json(self_inner):
                return {"ok": True, "result": {"username": "ZftTestBot"}} if ok else {"ok": False}
        return R()


class SetupWebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = self.tmp / ".env"
        self.env.write_text("BOT_TOKEN=\nOWNER_ID=   # izoh\nKEEP=1\n", encoding="utf-8")
        self.patch = mock.patch.dict(os.environ, {"PANEL_PASSWORD": "", "OWNER_CLAIM_CODE": ""})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_configure_writes_env(self):
        r = setup_web.configure(self.env, {"bot_token": TOKEN, "anthropic": "sk-ant-" + "a" * 30}, FakeHTTP())
        self.assertEqual(r["bot_username"], "ZftTestBot")
        self.assertIn("start=egasi_" + r["claim_code"], r["claim_link"])
        text = self.env.read_text(encoding="utf-8")
        self.assertIn(f"BOT_TOKEN={TOKEN}", text)
        self.assertIn("BOT_USERNAME=ZftTestBot", text)
        self.assertIn("KEEP=1", text)
        self.assertIn("PANEL_PASSWORD=" + r["password"], text)
        self.assertEqual(os.environ["BOT_TOKEN"], TOKEN)
        self.assertTrue(r["ai"])
        self.assertFalse(r["studio"])

    def test_bad_tokens(self):
        with self.assertRaises(ValueError):
            setup_web.configure(self.env, {"bot_token": "salom"}, FakeHTTP())
        with self.assertRaises(ValueError):
            setup_web.configure(self.env, {"bot_token": TOKEN}, FakeHTTP(ok=False))
        with self.assertRaises(ValueError):
            setup_web.configure(self.env, {"bot_token": TOKEN, "fal": "short"}, FakeHTTP())

    def test_http_flow(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        os.environ["ZFT_OPEN_BROWSER"] = "0"
        res = {}
        with mock.patch.object(setup_web, "check_token", lambda t, http=None: "ZftTestBot"):
            th = threading.Thread(target=lambda: res.setdefault("ok", setup_web.run_setup(self.env, port=port)))
            th.start()
            time.sleep(0.4)
            page = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
            self.assertIn("Saqlash va ishga tushirish", page)
            req = urllib.request.Request(f"http://127.0.0.1:{port}/setup", data=json.dumps({"bot_token": TOKEN}).encode(),
                                         headers={"X-Panel": "1", "Content-Type": "application/json"})
            data = json.loads(urllib.request.urlopen(req).read())
            self.assertEqual(data["bot_username"], "ZftTestBot")
            th.join(5)
        self.assertTrue(res["ok"])
        with socket.socket() as s:  # port bo'shatilgan — panel (HTTPServer, reuse_address) shu portda ishga tushadi
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))


if __name__ == "__main__":
    unittest.main()
