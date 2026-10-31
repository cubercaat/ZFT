"""Egasi uchun tugmali menyu: bo'limlar, matn so'rash, tugmalar bilan amallar."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from test_system import OWNER, FakeTG, cb, make_app, msg


def buttons(markup):
    return [b.get("callback_data") or b.get("url") for row in (markup or {}).get("inline_keyboard", []) for b in row]


class AdminMenuTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = make_app(self.tmp, OWNER_ONLY="1", PUBLIC_BOT_USERNAME="PRAVAQUIZ_bot")
        self.tg: FakeTG = self.app.tg
        self.app.run_in_background = lambda fn, *a: fn(*a)
        self.b = self.app.bot

    def tearDown(self):
        self.app.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def last(self, uid=OWNER):
        return [p for m, p in self.tg.calls if p.get("chat_id") == uid and m in ("sendMessage", "editMessageText")][-1]

    def test_start_opens_menu_with_persistent_button(self):
        self.b.handle_update(msg(OWNER, "/start"))
        kinds = [p.get("reply_markup") for m, p in self.tg.calls if p.get("chat_id") == OWNER]
        self.assertTrue(any(k and "keyboard" in k for k in kinds))          # pastdagi "📋 Menyu"
        self.assertIn("ad:money", buttons(self.last()["reply_markup"]))
        self.b.handle_update(msg(OWNER, "📋 Menyu"))
        self.assertIn("ZFT boshqaruvi", self.last()["text"])

    def test_every_menu_screen_and_action_runs(self):
        self.b.handle_update(msg(OWNER, "/start"))
        seen, queue = set(), ["ad:main"]
        while queue:
            data = queue.pop()
            if data in seen or data.startswith("ad:fire") or data == "ad:do:video":
                continue
            seen.add(data)
            self.b.handle_update(cb(OWNER, data))
            mk = self.last().get("reply_markup")
            queue += [x for x in buttons(mk) if x and x.startswith("ad:") and x not in seen]
        self.assertGreater(len(seen), 30)
        self.assertFalse(any("Traceback" in t for t in self.tg.texts(OWNER)))

    def test_ask_and_input_flow(self):
        self.b.handle_update(cb(OWNER, "ad:ask:income"))
        self.assertIn("Kirim", self.last()["text"])
        self.b.handle_update(msg(OWNER, "150000 premium obuna"))
        self.assertEqual(self.app.db.one("SELECT amount FROM ledger")["amount"], 150000)
        # keyingi oddiy matn endi Bosh direktorga ketadi, kirimga emas
        self.b.handle_update(msg(OWNER, "holat qanday"))
        self.assertEqual(self.app.db.scalar("SELECT COUNT(*) FROM ledger"), 1)

    def test_goal_flow_and_cancel(self):
        self.b.handle_update(cb(OWNER, "ad:goal:users"))
        self.b.handle_update(msg(OWNER, "500"))
        self.assertEqual(self.app.control.goals()[0]["target"], 500)
        self.b.handle_update(cb(OWNER, "ad:ask:budget"))
        self.b.handle_update(cb(OWNER, "ad:cancel"))
        self.b.handle_update(msg(OWNER, "60"))
        self.assertNotEqual(self.app.vbudget.monthly, 60)

    def test_toggles(self):
        self.assertFalse(self.app.auto_publish)
        self.b.handle_update(cb(OWNER, "ad:toggle:auto"))
        self.assertTrue(self.app.auto_publish)
        self.b.handle_update(cb(OWNER, "ad:toggle:dry"))
        self.assertTrue(self.app.dry_run)

    def test_hire_order_fire_with_buttons(self):
        self.b.handle_update(cb(OWNER, "ad:ask:hire"))
        self.b.handle_update(msg(OWNER, "Izohchi | Izohlarga javob loyihasini yozadi"))
        key = self.app.db.one("SELECT key FROM custom_agents")["key"]
        self.b.handle_update(cb(OWNER, "ad:agents"))
        self.assertIn(f"ad:ord:{key}", buttons(self.last()["reply_markup"]))
        self.b.handle_update(cb(OWNER, f"ad:fire:{key}"))
        self.b.handle_update(cb(OWNER, f"ad:fire!:{key}"))
        self.assertFalse(self.app.db.one("SELECT 1 FROM custom_agents WHERE active=1"))

    def test_strangers_cannot_press_admin_buttons(self):
        self.b.handle_update(cb(555, "ad:toggle:auto"))
        self.assertFalse(self.app.auto_publish)


if __name__ == "__main__":
    unittest.main()
