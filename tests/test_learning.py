"""O'qish tizimi: reja, xatolar, mavzu, imtihon, duel, nishonlar, eslatmalar, turnir."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from test_system import FakeTG, cb, make_app, msg

from zft.db import now
from zft.learning import parse_date


def last_buttons(tg: FakeTG, uid: int) -> list[str]:
    for m, p in reversed(tg.calls):
        if m in ("sendMessage", "sendPhoto") and p.get("chat_id") == uid and p.get("reply_markup"):
            return [b.get("callback_data") or b.get("url") for row in p["reply_markup"]["inline_keyboard"] for b in row]
    return []


class LearningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = make_app(self.tmp)
        self.tg: FakeTG = self.app.tg
        self.app.run_in_background = lambda fn, *a: fn(*a)
        self.L = self.app.learning
        self.b = self.app.bot

    def tearDown(self):
        self.app.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def answer_current(self, uid: int, correct: bool = True) -> None:
        data = [d for d in last_buttons(self.tg, uid) if d and d.startswith(("a:", "ex:", "du:"))]
        self.assertTrue(data, "savol tugmalari topilmadi")
        first = data[0]
        if first.startswith("a:"):
            qid = first.split(":")[1]
        elif first.startswith("ex:"):
            e = self.app.db.one("SELECT * FROM exams WHERE id=?", (int(first.split(":")[1]),))
            qid = json.loads(e["qids"])[int(first.split(":")[2])]
        else:
            d = self.app.db.one("SELECT * FROM duels WHERE id=?", (int(first.split(":")[1]),))
            qid = json.loads(d["qids"])[int(first.split(":")[2])]
        q = self.app.bank.by_id(qid)
        idx = q.correct if correct else (q.correct + 1) % len(q.options)
        prefix = first.rsplit(":", 1)[0]
        self.b.handle_update(cb(uid, f"{prefix}:{idx}"))

    def test_parse_date(self):
        today = dt.date(2026, 10, 24)
        self.assertEqual(parse_date("20.11", today), dt.date(2026, 11, 20))
        self.assertEqual(parse_date("2026-12-01", today), dt.date(2026, 12, 1))
        self.assertEqual(parse_date("5.01", today), dt.date(2027, 1, 5))       # o'tgan sana → keyingi yil
        self.assertEqual(parse_date("20 noyabr", today), dt.date(2026, 11, 20))
        self.assertIsNone(parse_date("01.01.2020", today))
        self.assertIsNone(parse_date("salom", today))

    def test_start_asks_exam_date_and_builds_plan(self):
        self.b.handle_update(msg(1, "/start"))
        self.assertIn("Imtihoningiz qachon", self.tg.texts(1)[-1])
        future = (self.L.today() + dt.timedelta(days=10)).strftime("%d.%m.%Y")
        self.b.handle_update(msg(1, future))
        self.assertIn("Imtihongacha: <b>10 kun</b>", self.tg.texts(1)[-1])
        self.assertIn("Kunlik maqsad", self.tg.texts(1)[-1])

    def test_practice_mistakes_and_achievement(self):
        self.b.handle_update(msg(2, "/start"))
        self.b.handle_update(msg(2, "bilmayman"))
        self.b.handle_update(msg(2, "/test"))
        self.answer_current(2, correct=False)
        self.assertIn("why:", " ".join(x for x in last_buttons(self.tg, 2) if x))
        r = self.L.readiness(2)
        self.assertEqual(r["wrong_now"], 1)
        # xatolar rejimi faqat xato qilingan savolni beradi
        wrong_q = self.app.db.one("SELECT qid FROM answers WHERE tg_id=2")["qid"]
        self.b.handle_update(cb(2, "m:mistakes"))
        self.assertTrue(any(d and d.startswith(f"a:{wrong_q}:") for d in last_buttons(self.tg, 2)))
        self.answer_current(2, correct=True)
        self.b.handle_update(cb(2, "nx"))
        self.assertIn("xato qilingan savolingiz qolmadi", self.tg.texts(2)[-1])
        self.assertTrue(self.app.db.one("SELECT 1 FROM achievements WHERE tg_id=2 AND code='first'"))

    def test_explain_without_ai_uses_official(self):
        self.b.handle_update(msg(3, "/start"))
        self.b.handle_update(cb(3, "why:namuna-1"))
        self.assertIn("Qizil signal", self.tg.texts(3)[-1])

    def test_topic_practice(self):
        self.b.handle_update(msg(4, "/mavzu"))
        topics = json.loads(self.app.db.get("topics:4"))
        i = topics.index("svetofor")
        self.b.handle_update(cb(4, f"tp:{i}"))
        qid = [d for d in last_buttons(self.tg, 4) if d.startswith("a:")][0].split(":")[1]
        self.assertEqual(self.app.bank.by_id(qid).topic, "svetofor")

    def test_exam_pass_and_fail(self):
        n = len(self.app.bank)
        self.b.handle_update(msg(5, "/imtihon"))
        for _ in range(n):
            self.answer_current(5, True)
        self.assertIn("O‘tdingiz", " ".join(self.tg.texts(5)[-3:]))
        self.assertTrue(self.app.db.one("SELECT 1 FROM achievements WHERE tg_id=5 AND code='exam_perfect'"))
        # eski tugmani qayta bosish hech narsani buzmaydi
        e = self.app.db.one("SELECT * FROM exams WHERE tg_id=5")
        self.b.handle_update(cb(5, f"ex:{e['id']}:0:0"))
        self.assertEqual(self.app.db.one("SELECT correct FROM exams WHERE id=?", (e["id"],))["correct"], n)

        self.b.handle_update(cb(5, "m:exam"))
        for _ in range(n):
            self.answer_current(5, False)
        e2 = self.app.db.one("SELECT * FROM exams WHERE tg_id=5 ORDER BY id DESC")
        self.assertEqual(e2["passed"], 0)
        self.assertTrue(any("o‘tmadi" in t for t in self.tg.texts(5)))

    def test_exam_timeout_counts_unanswered(self):
        self.b.handle_update(msg(6, "/imtihon"))
        self.answer_current(6, True)
        self.app.db.execute("UPDATE exams SET deadline=? WHERE tg_id=6", (now() - 1,))
        self.answer_current(6, True)  # vaqt tugagandan keyin javob — imtihon yakunlanadi
        e = self.app.db.one("SELECT * FROM exams WHERE tg_id=6")
        self.assertIsNotNone(e["finished"])
        self.assertEqual(e["passed"], 0)

    def test_duel(self):
        self.b.handle_update(msg(10, "/start", "Ali"))
        self.b.handle_update(msg(10, "/duel"))
        d = self.app.db.one("SELECT * FROM duels")
        self.assertIn(f"start=duel_{d['id']}", self.tg.texts(10)[-1])
        self.b.handle_update(msg(11, f"/start duel_{d['id']}", "Vali"))
        self.assertEqual(self.app.db.user(11)["referrer_id"], 10)   # duel havolasi referal ham
        for _ in range(self.L.duel_n if self.L.duel_n <= len(self.app.bank) else len(self.app.bank)):
            self.answer_current(10, True)
            self.answer_current(11, False)
        d = self.app.db.one("SELECT * FROM duels")
        self.assertEqual(d["status"], "done")
        self.assertIn("G'olib: <b>Ali</b>", self.tg.texts(11)[-1])
        self.assertTrue(self.app.db.one("SELECT 1 FROM achievements WHERE tg_id=10 AND code='duel_win'"))
        # o'z dueliga qo'shila olmaydi
        self.b.handle_update(msg(10, "/duel"))
        d2 = self.app.db.one("SELECT id FROM duels ORDER BY id DESC")["id"]
        self.L.join_duel(10, d2)
        self.assertIn("o'zingizning", self.tg.texts(10)[-1])

    def test_reminders_winback_followup_tournament(self):
        db = self.app.db
        for uid in (20, 21, 22):
            self.b.handle_update(msg(uid, "/start", f"U{uid}"))
            self.b.handle_update(msg(uid, "bilmayman"))
        self.answer_and_count = None
        self.b.handle_update(msg(21, "/test"))
        self.answer_current(21, True)            # bugun mashq qildi → eslatma kerak emas
        sent = self.L.daily_reminders(19)
        self.assertEqual(sent, 2)
        self.assertEqual(self.L.daily_reminders(19), 0)   # kuniga 1 marta
        self.assertEqual(self.L.daily_reminders(8), 0)    # boshqa soat tanlanmagan
        # win-back: 4 kun oldin kelgan
        db.execute("UPDATE users SET last_seen=? WHERE tg_id=22", (now() - 4 * 86400,))
        self.assertEqual(self.L.winback(), 1)
        self.assertEqual(self.L.winback(), 0)             # haftada 1 marta
        # imtihondan keyingi so'rov
        db.execute("UPDATE profiles SET exam_date=? WHERE tg_id=20", ((self.L.today() - dt.timedelta(days=1)).isoformat(),))
        self.assertEqual(self.L.exam_followups(), 1)
        self.b.handle_update(cb(20, "pe:yes"))
        self.assertEqual(db.one("SELECT passed FROM profiles WHERE tg_id=20")["passed"], 1)
        # turnir
        res = self.L.weekly_tournament()
        self.assertEqual(res["winners"][0]["tg_id"], 21)
        self.assertTrue(any("Haftalik turnir" in (p.get("text") or "") for m, p in self.tg.calls
                            if p.get("chat_id") == -100123 or p.get("chat_id") == "-100123"))
        st = self.L.stats()
        self.assertEqual(st["passed_real"], 1)

    def test_scheduler_has_learning_jobs(self):
        at = dt.datetime(2026, 10, 25, 20, 31, tzinfo=self.app.tz)   # yakshanba
        names = [n for n, _ in self.app.due_jobs(at)]
        self.assertIn("remind_20", names)
        self.assertIn("tournament", names)
        self.assertNotIn("remind_22", names)


if __name__ == "__main__":
    unittest.main()
