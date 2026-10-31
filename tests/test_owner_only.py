"""Shaxsiy boshqaruv boti rejimi: faqat egasi bilan gaplashadi, havolalar ommaviy botga."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_system import OWNER, FakeTG, make_app, msg, cb


class OwnerOnlyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = make_app(self.tmp, OWNER_ONLY="1", PUBLIC_BOT_USERNAME="PRAVAQUIZ_bot")
        self.tg: FakeTG = self.app.tg
        self.app.run_in_background = lambda fn, *a: None

    def tearDown(self):
        self.app.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_strangers_redirected(self):
        self.app.bot.handle_update(msg(500, "/start"))
        self.app.bot.handle_update(msg(500, "/test"))
        self.assertTrue(all("@PRAVAQUIZ_bot" in t for t in self.tg.texts(500)))
        self.assertIsNone(self.app.db.user(500))
        self.app.bot.handle_update(cb(500, "quiz"))
        self.assertFalse(any(p.get("chat_id") == 500 and "Savol" in (p.get("text") or "") for m, p in self.tg.calls))

    def test_owner_gets_admin(self):
        self.app.bot.handle_update(msg(OWNER, "/start"))
        self.assertIn("admin buyruqlari", self.tg.texts(OWNER)[-1])

    def test_public_links_use_public_bot(self):
        self.assertIn("t.me/PRAVAQUIZ_bot?start=", self.app.bot.bot_link("ref_1"))
        self.assertEqual(self.app.smm.bot_handle(), "@PRAVAQUIZ_bot")
        self.assertEqual(self.app.studio.bot_username, "PRAVAQUIZ_bot")

    def test_claim_still_works(self):
        self.app.settings.owner_id = 0
        with mock.patch.dict(os.environ, {"OWNER_CLAIM_CODE": "777111"}):
            self.app.bot.handle_update(msg(42, "/start egasi_777111"))
        self.assertEqual(self.app.settings.owner_id, 42)


if __name__ == "__main__":
    unittest.main()


class OwnerOnlyStartMessage(OwnerOnlyTest):
    def test_owner_never_sees_quiz(self):
        for t in ("/test", "/imtihon", "/xatolar", "/start"):
            self.app.bot.handle_update(msg(OWNER, t))
        self.app.bot.handle_update(cb(OWNER, "quiz"))
        joined = " ".join(self.tg.texts(OWNER))
        self.assertNotIn("Testni boshlash uchun", joined)
        self.assertFalse(any("a:" in str(p.get("reply_markup")) for m, p in self.tg.calls if p.get("chat_id") == OWNER))

    def test_start_message_from_panel(self):
        code, d = self.app.control.panel_action("/api/start-message", {"text": "Salom! Testlar shu yerda", "button": "Ochish", "url": "https://t.me/PRAVAQUIZ_bot"})
        self.assertEqual(code, 200)
        self.app.bot.handle_update(msg(900, "/start"))
        self.assertEqual(self.tg.texts(900)[-1], "Salom! Testlar shu yerda")
        last = [p for m, p in self.tg.calls if p.get("chat_id") == 900][-1]
        self.assertIn("PRAVAQUIZ_bot", str(last.get("reply_markup")))
        code, d = self.app.control.panel_action("/api/start-message", {"text": "x", "button": "B", "url": "javascript:1"})
        self.assertEqual(code, 400)
        code, d = self.app.control.panel_get("/api/control", {})
        self.assertEqual(d["start_message"]["button"], "Ochish")

    def test_password_command(self):
        with mock.patch.dict(os.environ, {"PANEL_PASSWORD": "abcdefghijkl"}):
            self.app.bot.handle_update(msg(OWNER, "/parol"))
        self.assertIn("abcdefghijkl", self.tg.texts(OWNER)[-1])
        self.app.bot.handle_update(msg(901, "/parol"))
        self.assertNotIn("abcdefghijkl", " ".join(self.tg.texts(901)))
