"""To'liq tizim testi: soxta Telegram va soxta Claude API bilan (internet kerak emas).

Ishga tushirish:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


class FakeTG:
    """TelegramAPI o'rnini bosuvchi: barcha chaqiruvlarni yozib boradi."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self._mid = 100
        self.poll_seq = 0

    def call(self, method, files=None, **params):
        self.calls.append((method, params))
        self._mid += 1
        if method == "sendPoll":
            self.poll_seq += 1
            return {"message_id": self._mid, "poll": {"id": f"poll{self.poll_seq}"}}
        if method == "getChatMemberCount":
            return 42
        if method == "getMe":
            return {"username": "TestBot"}
        return {"message_id": self._mid}

    def send_message(self, chat_id, text, reply_markup=None, **kw):
        return self.call("sendMessage", chat_id=chat_id, text=text, reply_markup=reply_markup)

    def send_video(self, chat_id, video_path, caption="", reply_markup=None, **kw):
        assert Path(video_path).exists()
        return self.call("sendVideo", chat_id=chat_id, caption=caption, reply_markup=reply_markup)

    def send_quiz_poll(self, chat_id, question, options, correct_index, explanation=""):
        return self.call("sendPoll", chat_id=chat_id, question=question, options=options,
                         correct_option_id=correct_index)

    def answer_callback(self, callback_id, text="", show_alert=False):
        self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    def edit_markup(self, chat_id, message_id, reply_markup=None):
        self.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id)

    def member_count(self, chat_id):
        return 42

    def texts(self, chat_id=None):
        return [p.get("text") or p.get("caption") or "" for m, p in self.calls
                if m in ("sendMessage", "sendVideo") and (chat_id is None or p.get("chat_id") == chat_id)]


class FakeResp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


OWNER = 777


def make_app(tmp: Path, **env):
    base = {"BOT_TOKEN": "x", "BOT_USERNAME": "TestBot", "OWNER_ID": str(OWNER), "CHANNEL_ID": "@kanal",
            "GROUP_ID": "-100123", "DB_PATH": str(tmp / "t.db"), "MEDIA_DIR": str(tmp / "media"),
            "QUESTIONS_PATH": str(ROOT / "data" / "questions.json"), "ANTHROPIC_API_KEY": "",
            "AUTO_PUBLISH": "false"}
    base.update(env)
    with mock.patch.dict(os.environ, base, clear=False):
        from zft.app import App
        from zft.config import Settings
        s = Settings.load(env_file=tmp / "none.env")
        return App(s, tg=FakeTG())


def msg(uid, text, name="Ali"):
    return {"update_id": 1, "message": {"chat": {"id": uid, "type": "private"},
                                        "from": {"id": uid, "first_name": name}, "text": text}}


def cb(uid, data, cid="c1"):
    return {"update_id": 2, "callback_query": {"id": cid, "from": {"id": uid, "first_name": "X"}, "data": data,
                                               "message": {"message_id": 5}}}


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg kerak")
class SystemTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = make_app(self.tmp)
        self.tg: FakeTG = self.app.tg
        # fon vazifalarini darhol (sinxron) bajaramiz
        self.app.run_in_background = lambda fn, *a: fn(*a)

    def tearDown(self):
        self.app.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- manba kuzatuvi va referal
    def test_source_tracking_and_referral(self):
        b = self.app.bot
        b.handle_update(msg(1, "/start tiktok", "Aziz"))
        b.handle_update(msg(2, "/start ref_1", "Bek"))
        b.handle_update(msg(3, "/start ref_3", "O'zi"))       # o'zini taklif qila olmaydi
        b.handle_update(msg(4, "/start nomalum_kod"))
        b.handle_update(msg(1, "/start insta"))              # qayta kirish manbani o'zgartirmaydi
        db = self.app.db
        self.assertEqual(db.user(1)["source"], "tiktok")
        self.assertEqual(db.user(2)["source"], "referral")
        self.assertEqual(db.user(2)["referrer_id"], 1)
        self.assertIsNone(db.user(3)["referrer_id"])
        self.assertEqual(db.user(4)["source"], "other:nomalum_kod")
        self.assertTrue(any("qo'shildi" in t for t in self.tg.texts(1)))
        report = self.app.reports.sources_text()
        self.assertIn("TikTok profil", report)

    def test_referral_goal_notifies_owner(self):
        b = self.app.bot
        b.handle_update(msg(10, "/start"))
        for uid in (11, 12, 13):
            b.handle_update(msg(uid, "/start ref_10"))
        self.assertEqual(self.app.db.ref_count(10), 3)
        self.assertTrue(any("Referal maqsadi" in t for t in self.tg.texts(OWNER)))

    # --- shaxsiy test
    def test_private_quiz_flow(self):
        b = self.app.bot
        b.handle_update(msg(5, "/start"))
        b.handle_update(cb(5, "quiz"))
        q_events = self.app.db.all("SELECT data FROM events WHERE kind='quiz_q'")
        qid = q_events[-1]["data"]
        q = self.app.bank.by_id(qid)
        b.handle_update(cb(5, f"a:{qid}:{q.correct}"))
        wrong = (q.correct + 1) % len(q.options)
        b.handle_update(cb(5, f"a:{qid}:{wrong}"))
        u = self.app.db.user(5)
        self.assertEqual((u["quiz_correct"], u["quiz_total"]), (1, 2))
        self.assertTrue(any("To'g'ri javob" in t for t in self.tg.texts(5)))

    # --- admin himoyasi
    def test_admin_commands_owner_only(self):
        b = self.app.bot
        b.handle_update(msg(9, "/stat"))
        self.assertFalse(any("Bugun" in t for t in self.tg.texts(9)))
        b.handle_update(msg(OWNER, "/link avtomaktab_1 Avtomaktab №1"))
        self.assertTrue(self.app.db.one("SELECT 1 FROM links WHERE code='avtomaktab_1'"))
        b.handle_update(msg(OWNER, "/stat"))
        self.assertTrue(any("Bugun" in t for t in self.tg.texts(OWNER)))
        # boshqa odam tasdiqlash tugmasini bosa olmaydi
        pid = self.app.db.create_post(status="pending", hook="h", caption="c", hashtags="[]")
        b.handle_update(cb(9, f"ap:{pid}"))
        self.assertEqual(self.app.db.post(pid)["status"], "pending")

    # --- to'liq kontent sikli (FAL kalitsiz → bepul test-video darajasi)
    def test_content_cycle_with_approval(self):
        pid = self.app.pipeline.create_daily_content("manual")
        self.assertIsNotNone(pid)
        post = self.app.db.post(pid)
        self.assertEqual((post["status"], post["tier"]), ("pending", "quiz"))
        self.assertTrue(Path(post["video_path"]).exists())
        self.assertTrue(any(m == "sendVideo" and p["chat_id"] == OWNER for m, p in self.tg.calls))
        self.app.db.update_post(pid, publish_at=int(time.time()) - 60)  # vaqti keldi
        # tasdiqsiz e'lon qilinmaydi, egaga eslatma boradi
        self.assertEqual(self.app.pipeline.publish_slot(0), [])
        self.assertTrue(any("tasdiq kutyapti" in t for t in self.tg.texts(OWNER)))
        self.app.bot.handle_update(cb(OWNER, f"ap:{pid}"))
        self.assertEqual(self.app.db.post(pid)["status"], "approved")
        self.assertEqual(self.app.pipeline.publish_slot(0), [pid])
        self.assertEqual(self.app.db.post(pid)["status"], "published")
        self.assertTrue(any(m == "sendVideo" and p["chat_id"] == "@kanal" for m, p in self.tg.calls))
        self.assertEqual(self.app.pipeline.publish_slot(1), [])  # boshqa tasdiqlangan yo'q
        actions = {r["action"] for r in self.app.db.all("SELECT action FROM audit_log")}
        self.assertTrue({"content_created", "post_approved", "publish_telegram"} <= actions)

    def test_two_slots_get_separate_videos(self):
        a = self.app.pipeline.create_daily_content("manual")
        b = self.app.pipeline.create_daily_content("manual")
        pa, pb = self.app.db.post(a), self.app.db.post(b)
        self.assertNotEqual(pa["publish_at"], pb["publish_at"])

    def test_auto_publish_mode(self):
        self.app.bot.handle_update(msg(OWNER, "/avtopilot on"))
        pid = self.app.pipeline.create_daily_content("manual")
        self.assertEqual(self.app.db.post(pid)["status"], "approved")

    def test_tasks_and_budget_commands(self):
        self.app.bot.handle_update(msg(OWNER, "/topshiriq Ota-bola kulgili vaziyat, oxirida Chorraha"))
        self.assertEqual(self.app.pipeline.open_task()["text"], "Ota-bola kulgili vaziyat, oxirida Chorraha")
        self.app.bot.handle_update(msg(OWNER, "/budjet 60"))
        self.assertEqual(self.app.vbudget.monthly, 60.0)
        self.app.bot.handle_update(msg(9, "/budjet 1000"))  # begona odam o'zgartira olmaydi
        self.assertEqual(self.app.vbudget.monthly, 60.0)

    # --- guruh viktorinasi
    def test_group_quiz_and_answers(self):
        poll_id = self.app.pipeline.send_group_quiz()
        self.assertEqual(poll_id, "poll1")
        row = self.app.db.one("SELECT * FROM group_polls WHERE poll_id=?", (poll_id,))
        self.app.bot.handle_update({"update_id": 3, "poll_answer": {
            "poll_id": poll_id, "user": {"id": 50, "first_name": "G"}, "option_ids": [row["correct_index"]]}})
        u = self.app.db.user(50)
        self.assertEqual((u["source"], u["quiz_correct"]), ("group", 1))

    # --- rejalashtirgich
    def test_scheduler_runs_each_job_once(self):
        tz = self.app.tz
        at = dt.datetime(2026, 10, 26, 10, 5, tzinfo=tz)  # dushanba 10:05 (12:30 slotidan 2.5 soat oldin)
        names = {n for n, _ in self.app.due_jobs(at)}
        self.assertIn("gen_slot0", names)
        self.assertIn("group_quiz_8", names)
        self.assertIn("weekly", names)
        self.assertNotIn("pub_slot0", names)
        self.assertNotIn("gen_slot1", names)
        for n in names:
            self.app.db.set(f"job:{n}", at.strftime("%Y-%m-%d"))
        self.assertEqual(self.app.due_jobs(at), [])
        late = dt.datetime(2026, 10, 26, 22, 30, tzinfo=tz)  # 3 soat ichidagi kechikkan ish bajariladi, eskisi o`tkaziladi
        names_late = {n for n, _ in self.app.due_jobs(late)}
        self.assertNotIn("pub_slot0", names_late)
        self.assertIn("pub_slot1", names_late)
        self.assertIn("report", names_late)

    # --- hisobot
    def test_daily_report(self):
        self.app.bot.handle_update(msg(1, "/start tiktok"))
        self.app.reports.send_daily_report()
        self.assertTrue(any("Yangi foydalanuvchi" in t for t in self.tg.texts(OWNER)))


class BrainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        from zft.db import DB
        self.db = DB(self.tmp / "b.db")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _brain(self, responses, budget=8.0):
        from zft.brain import Brain
        sess = mock.Mock()
        sess.post.side_effect = responses
        return Brain(self.db, "key", "claude-haiku-5-5", budget, session=sess), sess

    def test_ai_plan_parsed_and_cost_recorded(self):
        ai_json = {"question_id": "namuna-2", "hook": "Piyodaga kim yo'l beradi?", "caption": "Izohda yozing",
                   "hashtags": ["#prava", "test uz"], "cta": "Botda davom eting", "reason": "test"}
        resp = FakeResp(200, {"content": [{"type": "text", "text": "```json\n" + json.dumps(ai_json) + "\n```"}],
                              "usage": {"input_tokens": 2000, "output_tokens": 300}})
        brain, sess = self._brain([resp])
        plan = brain.plan_content({"posts": []}, [{"id": "namuna-2", "topic": "x", "text": "t"}])
        self.assertTrue(plan.by_ai)
        self.assertEqual(plan.question_id, "namuna-2")
        self.assertEqual(plan.hashtags, ["prava", "testuz"])
        expected = (2000 * 0.10 + 300 * 0.50) / 1e6
        self.assertAlmostEqual(brain.spent(), expected, places=9)
        sent = sess.post.call_args.kwargs["json"]
        self.assertEqual(sent["model"], "claude-haiku-5-5")

    def test_invalid_ai_answer_falls_back(self):
        resp = FakeResp(200, {"content": [{"type": "text", "text": '{"question_id": "yoq-id", "hook": "x",'
                                                                    ' "caption": "y"}'}], "usage": {}})
        brain, _ = self._brain([resp])
        plan = brain.plan_content({"posts": []}, [{"id": "namuna-1", "topic": "x", "text": "t"}])
        self.assertFalse(plan.by_ai)
        self.assertEqual(plan.question_id, "namuna-1")

    def test_budget_guard_blocks_calls(self):
        brain, sess = self._brain([], budget=0.01)
        self.db.execute("INSERT INTO ai_usage(at, model, purpose, cost_usd) VALUES (strftime('%s','now'),'m','p',0.02)")
        self.assertIsNone(brain.ask("x", "salom"))
        sess.post.assert_not_called()

    def test_web_search_tool_added_and_billed(self):
        resp = FakeResp(200, {"content": [{"type": "text", "text": "• trend"}],
                              "usage": {"input_tokens": 100, "output_tokens": 100,
                                        "server_tool_use": {"web_search_requests": 2}}})
        brain, sess = self._brain([resp])
        self.assertEqual(brain.weekly_trends(), "• trend")
        tools = sess.post.call_args.kwargs["json"]["tools"]
        self.assertEqual(tools[0]["type"], "web_search_20260209")
        self.assertGreaterEqual(brain.spent(), 0.02)
        self.assertEqual(self.db.get("trend_notes"), "• trend")


class PublisherTest(unittest.TestCase):
    def test_instagram_reels_flow(self):
        from zft.publishers.base import PostPayload
        from zft.publishers.instagram import InstagramPublisher
        sess = mock.Mock()
        sess.request.side_effect = [FakeResp(200, {"id": "C1"}), FakeResp(200, {"status_code": "IN_PROGRESS"}),
                                    FakeResp(200, {"status_code": "FINISHED"}), FakeResp(200, {"id": "M9"})]
        ig = InstagramPublisher("IG", "TOKEN", session=sess, poll_interval=0)
        r = ig.publish(PostPayload(1, Path("/x.mp4"), "https://m.uz/x.mp4", "cap", ["prava"]))
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.remote_id, "M9")
        first = sess.request.call_args_list[0]
        self.assertEqual(first.kwargs["data"]["media_type"], "REELS")
        self.assertIn("#prava", first.kwargs["data"]["caption"])

    def test_instagram_requires_public_url(self):
        from zft.publishers.base import PostPayload
        from zft.publishers.instagram import InstagramPublisher
        r = InstagramPublisher("IG", "T", session=mock.Mock()).publish(PostPayload(1, Path("/x"), None, "c", []))
        self.assertFalse(r.ok)

    def test_tiktok_file_upload_flow(self):
        from zft.publishers.base import PostPayload
        from zft.publishers.tiktok import TikTokPublisher
        tmp = Path(tempfile.mkdtemp())
        vid = tmp / "v.mp4"
        vid.write_bytes(b"0" * 1000)
        sess = mock.Mock()
        ok = {"error": {"code": "ok"}}
        sess.post.side_effect = [
            FakeResp(200, {**ok, "data": {"privacy_level_options": ["SELF_ONLY"]}}),
            FakeResp(200, {**ok, "data": {"publish_id": "P1", "upload_url": "https://up"}}),
            FakeResp(200, {**ok, "data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [123]}}),
        ]
        sess.put.return_value = FakeResp(201, {})
        r = TikTokPublisher("T", "PUBLIC_TO_EVERYONE", session=sess, poll_interval=0).publish(
            PostPayload(1, vid, None, "cap", []))
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.extra["privacy"], "SELF_ONLY")  # ruxsat etilmagan rejim tanlanmaydi
        self.assertEqual(sess.put.call_args.kwargs["headers"]["Content-Range"], "bytes 0-999/1000")
        shutil.rmtree(tmp)


class ImporterTest(unittest.TestCase):
    def test_csv_import(self):
        from zft.importer import import_questions
        from zft.quiz import QuestionBank
        tmp = Path(tempfile.mkdtemp())
        src = tmp / "q.csv"
        src.write_text("id;topic;question;option1;option2;option3;correct;explanation\n"
                       "1;belgi;Bu belgi nima?;To'xtash;Yo'l bering;Kirish taqiqlangan;2;Izoh\n",
                       encoding="utf-8")
        n = import_questions(src, tmp / "out.json")
        self.assertEqual(n, 1)
        q = QuestionBank(tmp / "out.json").questions[0]
        self.assertEqual(q.correct_text, "Yo'l bering")
        shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
