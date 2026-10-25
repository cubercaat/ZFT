"""Hamkorlar: havola, promo, to'lov ulushi, portal, oylik konkurs."""
from __future__ import annotations

import datetime as dt
import shutil
import tempfile
import unittest
from pathlib import Path

from test_system import OWNER, FakeTG, make_app, msg

from zft.db import now


class PartnersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = make_app(self.tmp, PANEL_URL="https://panel.test")
        self.tg: FakeTG = self.app.tg
        self.app.run_in_background = lambda fn, *a: fn(*a)
        self.P = self.app.partners
        self.b = self.app.bot

    def tearDown(self):
        self.app.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_partner_flow(self):
        self.b.handle_update(msg(OWNER, "/hamkor Ali Blog | bloger | 25 | ALI25"))
        out = self.tg.texts(OWNER)[-1]
        self.assertIn("start=p_ali_blog", out)
        self.assertIn("https://panel.test/p/", out)
        # havola orqali kelgan
        self.b.handle_update(msg(50, "/start p_ali_blog", "Sardor"))
        self.assertEqual(self.app.db.user(50)["source"], "partner:ali_blog")
        # promo-kod orqali bog'langan
        self.b.handle_update(msg(51, "/start", "Kamol"))
        self.b.handle_update(msg(51, "bilmayman"))
        self.b.handle_update(msg(51, "ali25"))
        self.assertIn("Promo-kod qabul", self.tg.texts(51)[-1])
        self.assertEqual(self.app.db.user(51)["source"], "partner:ali_blog")
        # to'lov → 25% ulush
        self.b.handle_update(msg(OWNER, "/tolov 50 100000 premium"))
        self.assertIn("25,000", self.tg.texts(OWNER)[-1])
        st = self.P.stats("ali_blog")
        self.assertEqual((st["users"], st["payments"], st["earned"]), (2, 1, 25000))
        self.P.payout("ali_blog", 10000)
        self.assertEqual(self.P.stats("ali_blog")["balance"], 15000)
        self.assertIn("Hamkor: Ali Blog", self.app.reports.sources_text())
        # portal: faqat sonlar, foydalanuvchi ismi yo'q
        token = self.app.db.one("SELECT token FROM partners")["token"]
        page = self.P.portal_html(token)
        self.assertIn("Ali Blog", page)
        self.assertIn("15,000", page)
        self.assertNotIn("Sardor", page)
        self.assertIsNone(self.P.portal_html("notatoken12345"))
        # moliya kitobiga kirim va chiqim yozildi
        kinds = [r["kind"] for r in self.app.db.all("SELECT kind FROM ledger")]
        self.assertEqual(sorted(kinds), ["expense", "income"])

    def test_payment_without_partner(self):
        self.b.handle_update(msg(60, "/start"))
        r = self.P.record_payment(60, 50000)
        self.assertIsNone(r["partner"])
        self.assertEqual(r["commission"], 0)

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.P.create("A", percent=20)
        with self.assertRaises(ValueError):
            self.P.create("Katta", percent=90)
        a = self.P.create("Avto Maktab 1", "avtomaktab", 10)
        b = self.P.create("Avto Maktab 1", "avtomaktab", 10)
        self.assertNotEqual(a["code"], b["code"])
        self.assertNotEqual(a["promo"], b["promo"])

    def test_monthly_contest(self):
        db = self.app.db
        self.b.handle_update(msg(70, "/start", "Lider"))
        self.b.handle_update(msg(71, "/start", "Ikkinchi"))
        today = self.app.local_now().date()
        last_month = today.replace(day=1) - dt.timedelta(days=5)
        t = int(dt.datetime(last_month.year, last_month.month, last_month.day, 12, tzinfo=self.app.tz).timestamp())
        for i, ref in enumerate([70, 70, 70, 71]):
            self.b.handle_update(msg(100 + i, f"/start ref_{ref}"))
            db.execute("UPDATE users SET joined_at=? WHERE tg_id=?", (t, 100 + i))
        res = self.P.close_contest()
        self.assertEqual(res["winners"][0]["tg_id"], 70)
        self.assertEqual(res["winners"][0]["c"], 3)
        self.assertIsNone(self.P.close_contest())      # bir marta
        self.assertTrue(any("Mukofot bering" in t for t in self.tg.texts(OWNER)))
        self.b.handle_update(msg(70, "/konkurs"))
        self.assertIn("oylik konkurs", self.tg.texts(70)[-1])

    def test_panel_routes(self):
        code, d = self.app.panel_modules[0].panel_action("/api/partners", {"name": "Test Bloger", "percent": 15})
        self.assertEqual(code, 200)
        c = d["partner"]["code"]
        code, d = self.P.panel_action("/api/payments", {"amount": 1000, "partner": c})
        self.assertEqual(d["commission"], 150)
        code, d = self.P.panel_get("/api/partners", {})
        self.assertEqual(d["partners"][0]["earned"], 150)
        code, d = self.P.panel_action("/api/partners", {"name": "", "percent": 15})
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
