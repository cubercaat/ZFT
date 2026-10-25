"""SMM avtomatika: izohlarga javob, eskalatsiya, DM, birinchi izoh, hashtag, sifat bahosi."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from test_system import OWNER, FakeTG, make_app

from zft.db import now


class FakeIGc:
    platform = "instagram"
    enabled = True

    def __init__(self):
        self.replies, self.dms, self.comments_made = [], [], []
        self.items = {}

    def username(self):
        return "chorraha_uz"

    def comments(self, media_id, limit=50):
        return self.items.get(media_id, [])

    def reply(self, cid, text):
        self.replies.append((cid, text))
        return "r" + cid

    def private_reply(self, cid, text):
        self.dms.append((cid, text))
        return {}

    def comment(self, media_id, text):
        self.comments_made.append((media_id, text))
        return "fc1"


class SmmTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = make_app(self.tmp)
        self.tg: FakeTG = self.app.tg
        self.app.run_in_background = lambda fn, *a: fn(*a)
        self.ig = FakeIGc()
        self.app.instagram = self.ig
        self.S = self.app.smm
        self.pid = self.app.db.create_post(format="quiz", created_at=now(), status="published", hook="Qizil chiroq", caption="Botga o'ting",
                                           hashtags=json.dumps(["prava", "test"]), published_at=now(), ig_media_id="m1")

    def tearDown(self):
        self.app.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_classify(self):
        S = self.S
        self.assertEqual(S.classify("Narxi qancha?"), "escalate")
        self.assertEqual(S.classify("ishlamayapti bot"), "escalate")
        self.assertEqual(S.classify("test"), "dm")
        self.assertEqual(S.classify("+"), "dm")
        self.assertEqual(S.classify("Zo'r video!"), "reply")
        self.assertEqual(S.classify("follow back https://x.y"), "skip")

    def test_check_comments(self):
        self.ig.items["m1"] = [
            {"id": "1", "text": "Zo'r ekan, rahmat", "username": "ali"},
            {"id": "2", "text": "Narxi qancha?", "username": "vali"},
            {"id": "3", "text": "test", "username": "gani"},
            {"id": "4", "text": "o'zimizniki", "username": "chorraha_uz"},
            {"id": "5", "text": "Qanday?", "username": "x", "replies": {"data": [{"id": "9", "username": "chorraha_uz"}]}},
        ]
        r = self.S.check_comments()
        self.assertEqual((r["new"], r["replied"], r["dm"], r["escalated"], r["skipped"]), (4, 1, 1, 1, 1))
        self.assertEqual([c for c, _ in self.ig.replies], ["1", "3"])
        self.assertIn("@TestBot", self.ig.dms[0][1])
        self.assertTrue(any("Narxi qancha" in t for t in self.tg.texts(OWNER)))
        # qayta tekshirish — takror javob yo'q
        r2 = self.S.check_comments()
        self.assertEqual(r2["new"], 0)
        self.assertEqual(len(self.ig.replies), 2)

    def test_daily_limit(self):
        self.S.max_run = 2
        self.ig.items["m1"] = [{"id": str(i), "text": "Rahmat", "username": f"u{i}"} for i in range(5)]
        self.assertEqual(self.S.check_comments()["replied"], 2)
        self.assertEqual(self.S.check_comments()["replied"], 2)   # qolganlari keyingi safar
        self.assertEqual(self.S.check_comments()["replied"], 1)

    def test_dry_run_no_api(self):
        self.app.dry_run = True
        self.ig.items["m1"] = [{"id": "1", "text": "Rahmat", "username": "ali"}]
        self.S.check_comments()
        self.assertEqual(self.ig.replies, [])
        self.assertEqual(self.app.db.one("SELECT action FROM ig_comments WHERE id='1'")["action"], "dry")

    def test_first_comment(self):
        self.app.db.update_post(self.pid, first_comment="Siz qaysi savolda adashdingiz?")
        self.S.after_publish(self.pid)
        self.assertIn("adashdingiz", self.ig.comments_made[0][1])
        self.assertIsNone(self.S.after_publish(self.pid))  # ikkinchi marta yozilmaydi

    def test_hashtag_stats(self):
        db = self.app.db
        db.add_metric(self.pid, "instagram", views=1000, likes=10)
        p2 = db.create_post(format="quiz", created_at=now(), status="published", hashtags=json.dumps(["prava", "avto"]), published_at=now())
        db.add_metric(p2, "instagram", views=200)
        rows = {r["tag"]: r for r in self.S.hashtag_stats()}
        self.assertEqual(rows["prava"]["avg_views"], 600)
        self.assertEqual(rows["test"]["avg_views"], 1000)

    def test_quality_heuristic(self):
        p = self.app.db.create_post(format="quiz", created_at=now(), status="pending", hook="", caption="— matn", hashtags="[]")
        sc, note = self.S.score(p)
        self.assertLess(sc, 6)
        self.assertIn("CTA", note)


if __name__ == "__main__":
    unittest.main()
