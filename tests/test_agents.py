"""ZFT Jarvislari testi: navbat bilan ishlash, suhbat, moliya."""
from __future__ import annotations

import http.client
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_system import OWNER, cb, make_app, msg  # noqa: E402


class AgentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = make_app(self.tmp)
        self.app.run_in_background = lambda fn, *a: fn(*a)
        self.hub = self.app.hub

    def tearDown(self):
        self.app.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_slot_teamwork_order_and_one_at_a_time(self):
        seen = []
        orig = self.hub.turn

        def spy(agent, doing):
            active_now = [a["key"] for a in self.hub.status() if a["active"]]
            seen.append((agent, active_now))
            return orig(agent, doing)

        self.hub.turn = spy
        # slot vaqti kelajakda bo'lishi uchun 2-slotni olamiz
        pid = self.hub.prep_slot(1)
        self.assertIsNotNone(pid)
        for agent, active in seen:  # yangi navbat boshlanayotganda hech kim ishlamayotgan bo'ladi
            self.assertEqual(active, [], f"{agent} boshlanganda boshqasi ishlayapti: {active}")
        pairs = [(m["sender"], m["recipient"]) for m in self.hub.messages()]
        expected = [("direktor", "media"), ("media", "direktor"), ("direktor", "marketolog"),
                    ("marketolog", "studiya"), ("marketolog", "media"), ("direktor", "moliya"), ("moliya", "direktor"),
                    ("direktor", "studiya"), ("studiya", "direktor"), ("direktor", "smm"), ("smm", "direktor"),
                    ("direktor", "studiya"), ("direktor", "ega")]
        self.assertEqual(pairs, expected)
        self.assertTrue(all(not a["active"] for a in self.hub.status()))  # ish tugadi — hamma dam oladi
        # ikkinchi marta shu slot uchun video yasalmaydi
        self.assertIsNone(self.hub.prep_slot(1))

    def test_parallel_requests_are_serialized(self):
        log = []
        orig_make = self.app.pipeline.create_daily_content

        def slow(trigger="manual"):
            log.append(("start", threading.get_ident()))
            time.sleep(0.3)
            r = orig_make(trigger)
            log.append(("end", threading.get_ident()))
            return r

        self.app.pipeline.create_daily_content = slow
        ts = [threading.Thread(target=self.hub.make_now) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        kinds = [k for k, _ in log]
        self.assertEqual(kinds, ["start", "end", "start", "end"])  # ustma-ust emas, navbat bilan

    def test_market_research_with_ai_creates_tasks_and_owner_first(self):
        from unittest import mock
        from test_system import FakeResp
        strat = {"summary": "Hikoyaviy video kam.", "pillars": ["Imtihon hayajoni"], "media_watch": ["saqlashlar"],
                 "studio_tasks": ["Imtihon navbatidagi hayajon", "Do'stlar bahsi"], "offer_ideas": ["Guruh promokodi"],
                 "competitors": [{"name": "X bot", "what": "testlar", "weakness": "tushuntirish yo'q"}]}
        self.app.brain.api_key = "k"
        sess = mock.Mock()
        sess.post.return_value = FakeResp(200, {"content": [{"type": "text", "text": json.dumps(strat)}],
                                                "usage": {"input_tokens": 4000, "output_tokens": 900,
                                                          "server_tool_use": {"web_search_requests": 3}}})
        self.app.brain.http = sess
        self.hub.market_research()
        body = sess.post.call_args.kwargs["json"]
        self.assertEqual(body["tools"][0]["name"], "web_search")
        self.assertEqual(self.hub.strategy()["pillars"], ["Imtihon hayajoni"])
        self.assertEqual(self.app.db.scalar("SELECT COUNT(*) FROM tasks WHERE source='marketolog'"), 2)
        self.app.pipeline.add_task("Egamizning g'oyasi")
        self.assertEqual(self.app.pipeline.open_task()["text"], "Egamizning g'oyasi")  # ega birinchi
        recips = {(m["sender"], m["recipient"]) for m in self.hub.messages()}
        self.assertTrue({("marketolog", "media"), ("marketolog", "studiya"), ("marketolog", "ega")} <= recips)

    def test_directive_reaches_director_prompt(self):
        self.hub.market_directive({"text": "brif", "fmt_avg": {"father_son": 900}})
        ctx = self.app.pipeline.context()
        self.assertEqual(ctx["directive"]["format"], "father_son")

    def test_publish_goes_through_nashr(self):
        pid = self.hub.make_now()
        self.app.pipeline.approve(pid)
        self.app.db.update_post(pid, publish_at=int(time.time()) - 30)
        self.assertEqual(self.hub.publish_slot(0), [pid])
        texts = [(m["sender"], m["text"]) for m in self.hub.messages()]
        self.assertTrue(any(s == "nashr" and "joylandi" in t and "telegram" in t for s, t in texts))

    def test_owner_chat_without_ai_creates_task(self):
        reply = self.hub.owner_message("Imtihon kuni hayajonlangan qiz haqida video qiling")
        self.assertIn("Studiyaga", reply)
        self.assertEqual(self.app.pipeline.open_task()["text"], "Imtihon kuni hayajonlangan qiz haqida video qiling")
        short = self.hub.owner_message("holat?")
        self.assertIn("yangi odam", short)

    def test_owner_chat_with_ai(self):
        from unittest import mock
        from test_system import FakeResp
        self.app.brain.api_key = "k"
        sess = mock.Mock()
        sess.post.return_value = FakeResp(200, {"content": [{"type": "text", "text": json.dumps(
            {"reply": "Bugun 0 ta yangi odam. G'oyangizni Studiyaga berdim.", "task": "Ota-bola skit"})}],
            "usage": {"input_tokens": 500, "output_tokens": 80}})
        self.app.brain.http = sess
        reply = self.hub.owner_message("Ota-bola haqida video kerak")
        self.assertIn("Studiyaga berdim", reply)
        self.assertEqual(self.app.pipeline.open_task()["text"], "Ota-bola skit")

    def test_finance_and_ledger(self):
        self.app.vbudget.record(None, "video_scene", "m", 6, 1.8)
        self.hub.add_ledger("income", 450000, "UZS", "3 ta obuna")
        f = self.hub.finance()
        self.assertEqual(f["video"], 1.8)
        self.assertGreater(f["total"], 1.8)  # server ulushi ham qo'shiladi
        self.assertEqual(f["ledger"]["income:UZS"], 450000)
        self.assertIn("Kirim", self.hub.messages()[-1]["text"])
        self.assertIn("prognoz", self.hub.finance_text(f))

    def test_bot_owner_text_goes_to_director_and_commands(self):
        self.app.bot.handle_update(msg(OWNER, "Ertaga ko'proq kulgili video qilinglar iltimos"))
        self.assertTrue(any("Bosh direktor" in t for t in self.app.tg.texts(OWNER)))
        self.app.bot.handle_update(msg(OWNER, "/kirim 150000 UZS 1 ta obuna"))
        self.assertEqual(self.app.db.scalar("SELECT SUM(amount) FROM ledger"), 150000)
        self.app.bot.handle_update(msg(OWNER, "/jamoa"))
        self.assertTrue(any("ZFT jamoasi" in t for t in self.app.tg.texts(OWNER)))
        self.app.bot.handle_update(msg(9, "salom direktor"))  # begona odam direktorga yoza olmaydi
        self.assertFalse(any(m["sender"] == "ega" and m["text"] == "salom direktor" for m in self.hub.messages()))


class AgentsPanelTest(unittest.TestCase):
    def test_agents_chat_ledger_endpoints(self):
        tmp = Path(tempfile.mkdtemp())
        app = make_app(tmp)
        app.run_in_background = lambda fn, *a: fn(*a)
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

        r, _ = req("POST", "/api/login", {"password": "juda-kuchli-parol-123"})
        ck = r.getheader("Set-Cookie").split(";")[0]
        r, body = req("POST", "/api/chat", {"text": "Bugungi holat qanday?"}, ck)
        self.assertEqual(r.status, 200)
        self.assertTrue(json.loads(body)["reply"])
        r, body = req("GET", "/api/agents?since=0", ck=ck)
        d = json.loads(body)
        self.assertEqual(len(d["agents"]), 8)
        self.assertTrue(any(m["sender"] == "ega" for m in d["messages"]))
        last = d["messages"][-1]["id"]
        r, body = req("GET", f"/api/agents?since={last}", ck=ck)
        self.assertEqual(json.loads(body)["messages"], [])
        r, _ = req("POST", "/api/ledger", {"kind": "expense", "amount": 6.5, "currency": "USD", "note": "server"}, ck)
        self.assertEqual(r.status, 200)
        r, _ = req("POST", "/api/ledger", {"kind": "boshqa", "amount": 1}, ck)
        self.assertEqual(r.status, 400)
        r, body = req("GET", "/api/finance", ck=ck)
        self.assertEqual(json.loads(body)["entries"][0]["note"], "server")
        r, _ = req("GET", "/api/agents")
        self.assertEqual(r.status, 401)
        srv.shutdown()
        app.db.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
