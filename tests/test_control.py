"""Boshqaruv: maqsadlar, brif, ogohlantirishlar, budjet, toifalar, sinov rejimi, samaradorlik."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from test_system import OWNER, FakeTG, make_app, msg

from zft.control import categorize
from zft.db import now


class ControlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = make_app(self.tmp)
        self.tg: FakeTG = self.app.tg
        self.app.run_in_background = lambda fn, *a: fn(*a)
        self.C = self.app.control
        self.b = self.app.bot

    def tearDown(self):
        self.app.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_categorize(self):
        self.assertEqual(categorize("Hetzner server oktyabr", "expense"), "server")
        self.assertEqual(categorize("instagram target reklama", "expense"), "reklama")
        self.assertEqual(categorize("fal.ai kredit", "expense"), "ai")
        self.assertEqual(categorize("non", "expense"), "boshqa")
        self.assertEqual(categorize("", "income"), "tushum")
        self.b.handle_update(msg(OWNER, "/xarajat 70000 UZS hetzner server"))
        self.assertEqual(self.app.db.one("SELECT category FROM ledger")["category"], "server")

    def test_goals(self):
        for i in range(5):
            self.b.handle_update(msg(100 + i, "/start"))
        self.b.handle_update(msg(OWNER, "/maqsad odam 10"))
        out = self.tg.texts(OWNER)[-1]
        self.assertIn("6 / 10", out)
        self.assertIn("60%", out)
        self.b.handle_update(msg(OWNER, "/maqsad nimadir 10"))
        self.assertIn("❌", self.tg.texts(OWNER)[-1])

    def test_morning_brief_and_dry_run(self):
        self.b.handle_update(msg(OWNER, "/sinov on"))
        self.assertTrue(self.app.dry_run)
        self.b.handle_update(msg(OWNER, "/brif"))
        brief = self.tg.texts(OWNER)[-1]
        self.assertIn("ertalabki brif", brief)
        self.assertIn("SINOV", brief)
        # sinov rejimida e'lon qilinmaydi va guruhga yuborilmaydi
        pid = self.app.db.create_post(format="quiz", status="approved", video_path=str(self.tmp / "x.mp4"), publish_at=now())
        self.assertEqual(self.app.pipeline.publish_approved(), [])
        self.assertEqual(self.app.db.post(pid)["status"], "dry")
        self.assertIsNone(self.app.pipeline.send_group_quiz())
        self.assertFalse(any(m == "sendVideo" for m, _ in self.tg.calls))
        self.b.handle_update(msg(OWNER, "/sinov off"))
        self.assertFalse(self.app.dry_run)

    def test_budget_alert_once(self):
        self.app.vbudget.set_monthly(10)
        self.app.vbudget.record(None, "video_scene", "m", 10, 8.5)
        sent = self.C.check_alerts()
        self.assertTrue(any("80%" in s or "85%" in s for s in sent))
        self.assertEqual([s for s in self.C.check_alerts() if "video budjet" in s], [])
        self.app.vbudget.record(None, "video_scene", "m", 2, 1.2)
        self.assertTrue(any("97%" in s or "95%" in s or "96%" in s for s in self.C.check_alerts()))

    def test_viral_alert(self):
        db = self.app.db
        for i, v in enumerate([300, 400, 350, 5000]):
            pid = db.create_post(format="quiz", status="published", hook=f"h{i}", published_at=now() - (10 - i) * 3600)
            db.add_metric(pid, "instagram", views=v)
        sent = self.C.check_alerts()
        self.assertTrue(any("viral" in s for s in sent))
        self.assertFalse(any("viral" in s for s in self.C.check_alerts()))

    def test_efficiency_and_panel(self):
        rows = self.C.efficiency()
        self.assertEqual(len(rows), len(self.app.hub.all_agents()))
        code, data = self.C.panel_get("/api/control", {})
        self.assertEqual(code, 200)
        self.assertIn("learning", data)
        code, data = self.C.panel_action("/api/dry-run", {"on": True})
        self.assertTrue(data["dry_run"])
        code, data = self.C.panel_action("/api/goals", {"metric": "users", "target": 100})
        self.assertEqual(code, 200)

    def test_jobs(self):
        import datetime as dt
        at = dt.datetime(2026, 11, 1, 9, 20, tzinfo=self.app.tz)
        names = [n for n, _ in self.app.due_jobs(at)]
        for n in ("brief", "alerts_9", "ref_contest"):
            self.assertIn(n, names)


if __name__ == "__main__":
    unittest.main()
