"""Butun kun simulyatsiyasi: rejalashtirgich 06:00–23:59, avtopilot, 8 Jarvis, hisobotlar, ishga olish."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_system import OWNER, FakeResp, msg, make_app  # noqa: E402


class FakeIG:
    platform = "instagram"
    enabled = True

    def __init__(self):
        self.n = 0

    def publish(self, p):
        from zft.publishers.base import PublishResult
        self.n += 1
        assert p.caption, "caption bo'sh"
        return PublishResult("instagram", True, f"ig{self.n}")

    def username(self):
        return "chorraha"

    def comments(self, media_id, limit=50):
        return []

    def comment(self, media_id, text):
        return "c_" + media_id

    def metrics(self, media_id):
        k = int(media_id[2:])
        return {"views": 800 * k, "likes": 40 * k, "comments": 5, "saves": 10 * k, "shares": 3 * k}


class DayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = make_app(self.tmp, AUTO_PUBLISH="true")
        self.app.run_in_background = lambda fn, *a: fn(*a)
        self.ig = FakeIG()
        self.app.instagram = self.ig
        self.app.publishers = lambda: [self.app.channel, self.ig]

    def tearDown(self):
        self.app.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_day(self, day: dt.date):
        tz = self.app.tz
        fake_now = {"t": None}
        real_local_now = self.app.local_now
        self.app.local_now = lambda: fake_now["t"]
        self.app.local_day_start = lambda: int(dt.datetime(day.year, day.month, day.day, tzinfo=tz).timestamp())
        ran = []
        with mock.patch("zft.db.now", side_effect=lambda: int(fake_now["t"].timestamp())), \
             mock.patch("zft.pipeline.now", side_effect=lambda: int(fake_now["t"].timestamp())), \
             mock.patch("zft.agents.now", side_effect=lambda: int(fake_now["t"].timestamp())):
            for minute in range(6 * 60, 24 * 60, 5):
                fake_now["t"] = dt.datetime(day.year, day.month, day.day, minute // 60, minute % 60, tzinfo=tz)
                for name, fn in self.app.due_jobs(fake_now["t"]):
                    self.app.db.set(f"job:{name}", day.strftime("%Y-%m-%d"))
                    fn()
                    ran.append((fake_now["t"].strftime("%H:%M"), name))
        self.app.local_now = real_local_now
        return ran

    def test_full_monday(self):
        ran = self.run_day(dt.date(2026, 10, 26))  # dushanba
        names = [n for _, n in ran]
        for job in ("weekly", "gen_slot0", "pub_slot0", "gen_slot1", "pub_slot1", "review", "report",
                    "group_quiz_8", "group_quiz_13", "group_quiz_20", "metrics_am", "metrics_pm"):
            self.assertIn(job, names, f"{job} bajarilmadi")
        self.assertEqual(len(names), len(set(names)), "biror ish ikki marta bajarildi")
        # tartib: video e'londan oldin tayyorlanadi
        self.assertLess(names.index("gen_slot0"), names.index("pub_slot0"))
        self.assertLess(names.index("gen_slot1"), names.index("pub_slot1"))
        posts = self.app.db.all("SELECT * FROM posts ORDER BY id")
        self.assertEqual([p["status"] for p in posts], ["published", "published"])  # avtopilot: 2 ta video chiqdi
        self.assertEqual(self.ig.n, 2)
        self.assertNotEqual(posts[0]["publish_at"], posts[1]["publish_at"])
        for p in posts:  # SMMchi ikki yo'lli CTA qo'shgan, em-dash yo'q
            self.assertIn("layk", p["caption"].lower())
            self.assertNotIn("—", p["caption"])
        # har bir Jarvis o'z ishini qilgan
        senders = {r["sender"] for r in self.app.db.all("SELECT DISTINCT sender FROM agent_messages")}
        self.assertTrue({"direktor", "marketolog", "smm", "moliya", "media", "studiya", "nashr", "hisobot"} <= senders,
                        senders)
        # hisobotchi kunlik va haftalik hisobotni saqlagan va egaga yuborgan
        kinds = {r["kind"] for r in self.app.db.all("SELECT kind FROM reports")}
        self.assertTrue({"daily", "weekly"} <= kinds)
        self.assertTrue(any("ZFT · Bugun" in t for t in self.app.tg.texts(OWNER)))
        # trend qidiruvi ikki marta bo'lmaydi (faqat Marketolog)
        self.assertEqual(self.app.db.scalar("SELECT COUNT(*) FROM ai_usage WHERE purpose='weekly_trends'"), 0)
        # budjet: kalitsiz rejimda pul sarflanmadi
        self.assertEqual(self.app.vbudget.spent(), 0)

    def test_second_day_reviews_previous_videos(self):
        self.run_day(dt.date(2026, 10, 26))
        self.app.pipeline.collect_metrics()
        ran = self.run_day(dt.date(2026, 10, 28))  # 2 kundan keyin: oldingi videolar tahlil qilinadi
        rv = self.app.db.all("SELECT * FROM post_reviews")
        self.assertGreaterEqual(len(rv), 2)
        self.assertTrue(all(r["verdict"] in ("yaxshi", "o'rtacha", "kam") for r in rv))
        to_studio = [m for m in self.app.hub.messages(0, 300) if m["sender"] == "marketolog" and m["recipient"] == "studiya"
                     and "ko'rish" in m["text"]]
        self.assertTrue(to_studio)

    def test_hire_custom_jarvis_and_daily_run(self):
        key = self.app.hub.hire("Izohchi", "Instagram izohlariga javob loyihasini yozadi", "", 18)
        self.assertTrue(key.startswith("x_izohchi"))
        self.assertIn(key, [a["key"] for a in self.app.hub.status()])
        ran = self.run_day(dt.date(2026, 10, 27))
        self.assertIn(f"custom_{key}", [n for _, n in ran])
        self.assertTrue(any(m["sender"] == key for m in self.app.hub.messages(0, 300)))
        self.assertTrue(self.app.hub.fire(key))
        self.assertNotIn(key, [a["key"] for a in self.app.hub.status()])

    def test_owner_asks_director_to_hire(self):
        self.app.hub.owner_message("ishga ol: Izohchi - izohlarga javob loyihasini yozadi")
        row = self.app.db.one("SELECT * FROM custom_agents WHERE active=1")
        self.assertEqual(row["name"], "Izohchi")
        self.assertIsNone(self.app.pipeline.open_task())  # bu topshiriq emas, ishga olish
        self.app.bot.handle_update(msg(OWNER, "/ishgaol Tahlilchi | raqobatchilar narxini kuzatadi | 9"))
        self.assertEqual(self.app.db.scalar("SELECT COUNT(*) FROM custom_agents WHERE active=1"), 2)

    def test_smm_with_ai_uses_both_skills(self):
        self.app.brain.api_key = "k"
        sess = mock.Mock()
        sess.post.return_value = FakeResp(200, {"content": [{"type": "text", "text": json.dumps(
            {"caption": "3 ta savol — nechtasiga javob berasiz?\nIzohga \"3\" deb yozing. Shart emas desangiz, bitta layk bosing.",
             "hashtags": ["#prava", "haydovchilik"], "first_comment": "Qaysi biri qiyin?", "audit": "em-dash olib tashlandi"})}],
            "usage": {"input_tokens": 6000, "output_tokens": 300}})
        self.app.brain.http = sess
        pid = self.app.db.create_post(status="pending", hook="h", caption="eski", hashtags="[]", tier="light", concept="{}")
        self.app.hub.smm_polish(pid)
        system = sess.post.call_args.kwargs["json"]["system"]
        self.assertIn("SKILL: asror", system)
        self.assertIn("SKILL: anti-ai-writing", system)
        self.assertIn("Dual-path", system.replace("dual-path", "Dual-path"))
        p = self.app.db.post(pid)
        self.assertNotIn("—", p["caption"])
        self.assertEqual(json.loads(p["hashtags"]), ["prava", "haydovchilik"])


if __name__ == "__main__":
    unittest.main()
