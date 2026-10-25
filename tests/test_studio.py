"""AI Studio testi: soxta fal.ai (mahalliy sun'iy kliplar) bilan to'liq kinematografik sikl."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_system import OWNER, FakeResp, cb, make_app, msg  # noqa: E402,F401

from zft.genai import GenError, GenResult, find_media_url, video_input  # noqa: E402


class FakeFal:
    enabled = True

    def __init__(self, tmp: Path, fail_video: bool = False, fail_image: bool = False):
        self.tmp, self.fail_video, self.fail_image = tmp, fail_video, fail_image
        self.video_prompts: list[str] = []
        self.image_prompts: list[str] = []

    def video(self, model, prompt, seconds, price_per_sec, resolution="720p", extra=None):
        if self.fail_video:
            raise GenError("model band")
        self.video_prompts.append(prompt)
        p = self.tmp / f"clip{len(self.video_prompts)}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=24",
                        "-t", "6", str(p)], check=True)
        return GenResult(str(p), 6, round(6 * price_per_sec, 4), model)

    def image(self, model, prompt, price):
        if self.fail_image:
            raise GenError("rasm xato")
        self.image_prompts.append(prompt)
        from PIL import Image
        p = self.tmp / f"img{len(self.image_prompts)}.jpg"
        Image.new("RGB", (768, 1344), (40, 90, 160)).save(p)
        return GenResult(str(p), 0, price, model)

    def download(self, url, dest):
        shutil.copy(url, dest)
        return dest


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg kerak")
class StudioPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = make_app(self.tmp, FAL_KEY="test", VIDEO_MONTHLY_BUDGET_USD="1000")
        self.app.run_in_background = lambda fn, *a: fn(*a)
        self.fal = FakeFal(self.tmp)
        self.app.fal = self.fal

    def tearDown(self):
        self.app.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cinematic_video_with_task_and_cost(self):
        tid = self.app.pipeline.add_task("Imtihon kunidagi hayajon haqida")
        pid = self.app.pipeline.create_daily_content("manual")
        p = self.app.db.post(pid)
        self.assertEqual(p["tier"], "cinematic")
        self.assertEqual(len(self.fal.video_prompts), 3)
        self.assertAlmostEqual(p["cost_usd"], 3 * 6 * 0.10, places=4)
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-of",
                              "json", p["video_path"]], capture_output=True, text=True)
        streams = json.loads(out.stdout)["streams"]
        v = next(s for s in streams if s["codec_type"] == "video")
        self.assertEqual((v["width"], v["height"]), (1080, 1920))
        self.assertTrue(any(s["codec_type"] == "audio" for s in streams))
        concept = json.loads(p["concept"])
        self.assertGreaterEqual(len(concept["scenes"]), 2)
        # AI kaliti yo'q → shablon konsept topshiriqni "bajarildi" deb belgilamaydi
        self.assertEqual(self.app.db.one("SELECT status FROM tasks WHERE id=?", (tid,))["status"], "open")
        cap = [p for m, p in self.app.tg.calls if m == "sendVideo" and p["chat_id"] == OWNER][-1]["caption"]
        self.assertIn("Kinematografik", cap)

    def test_ai_director_uses_task(self):
        concept = {"format": "father_son", "title": "Kalit", "hook_text": "Dadam sinadi",
                   "scenes": [{"visual": "Uzbek father in courtyard", "voice": "Dadam savol berdi.", "caption": "Savol"},
                              {"visual": "Son answers", "voice": "To'g'ri javob berdim.", "caption": "Javob"}],
                   "reveal_voice": "Chorraha botida mashq qiling.", "card_title": "Kalitni oling",
                   "card_sub": "Har kuni 5 daqiqa", "caption": "Sizda ham shunday bo'lganmi?",
                   "hashtags": ["prava"], "reason": "topshiriq"}
        from unittest import mock
        self.app.brain.api_key = "k"
        sess = mock.Mock()
        sess.post.return_value = FakeResp(200, {"content": [{"type": "text", "text": json.dumps(concept)}],
                                                "usage": {"input_tokens": 3000, "output_tokens": 800}})
        self.app.brain.http = sess
        tid = self.app.pipeline.add_task("Ota-bola haqida")
        pid = self.app.pipeline.create_daily_content("manual")
        sent = sess.post.call_args.kwargs["json"]
        self.assertEqual(sent["model"], "claude-sonnet-5-5")
        self.assertIn("Ota-bola haqida", sent["messages"][0]["content"])
        t = self.app.db.one("SELECT * FROM tasks WHERE id=?", (tid,))
        self.assertEqual((t["status"], t["post_id"]), ("done", pid))
        self.assertEqual(self.app.db.post(pid)["format"], "father_son")

    def test_fallback_to_light_then_quiz(self):
        self.fal.fail_video = True
        pid = self.app.pipeline.create_daily_content("manual")
        self.assertEqual(self.app.db.post(pid)["tier"], "light")
        self.fal.fail_image = True
        pid2 = self.app.pipeline.create_daily_content("manual")
        self.assertEqual(self.app.db.post(pid2)["tier"], "quiz")


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        from zft.budget import VideoBudget
        from zft.db import DB
        self.db = DB(self.tmp / "b.db")
        self.tz = dt.timezone(dt.timedelta(hours=5))
        self.vb = VideoBudget(self.db, self.tz)
        self.vb.set_monthly(30)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pacing_prefers_evening_slot(self):
        day15 = dt.datetime(2026, 10, 15, 10, 0, tzinfo=self.tz)  # oy o'rtasi: limit ≈ 14.5$
        # hech narsa sarflanmagan: ikkala slotga ham yetadi
        self.assertEqual(self.vb.choose(day15, False, True).tier, "cinematic")
        # oyning boshida (1-kun limit ≈ 0.97$) — ertalab tejaladi, kechqurun 2 sahnali bo'lmaydi ham
        day1 = dt.datetime(2026, 10, 1, 10, 0, tzinfo=self.tz)
        self.assertEqual(self.vb.choose(day1, False, True).tier, "light")
        self.assertEqual(self.vb.choose(day1, True, True).tier, "light")
        self.vb.set_monthly(60)  # budjet oshirildi → darhol kuchga kiradi
        c = self.vb.choose(day1, True, True)
        self.assertEqual((c.tier, c.scenes), ("cinematic", 3))

    def test_hard_stop_when_budget_spent(self):
        at = dt.datetime.now(self.tz)
        self.vb.record(None, "video_scene", "m", 6, 30.0)
        self.assertEqual(self.vb.choose(at, True, True).tier, "quiz")

    def test_no_fal_key_uses_free_quiz(self):
        at = dt.datetime(2026, 10, 20, 10, 0, tzinfo=self.tz)
        self.assertEqual(self.vb.choose(at, True, False).tier, "quiz")


class GenAITest(unittest.TestCase):
    def test_inputs_and_url_parsing(self):
        b = video_input("fal-ai/veo3.1/fast", "p", 8, "720p")
        self.assertEqual((b["duration"], b["aspect_ratio"], b["generate_audio"]), ("8s", "9:16", False))
        self.assertEqual(video_input("fal-ai/kling-video/x", "p", 6, "720p")["duration"], "5")
        self.assertEqual(find_media_url({"video": {"url": "https://x/a.mp4"}}, (".mp4",)), "https://x/a.mp4")
        self.assertEqual(find_media_url({"images": [{"url": "https://x/a.png"}]}, (".png",)), "https://x/a.png")
        self.assertEqual(find_media_url({"data": {"output": "https://x/b.mp4?t=1"}}, (".mp4",)), "https://x/b.mp4?t=1")

    def test_queue_flow(self):
        from unittest import mock
        from zft.genai import FalClient
        sess = mock.Mock()
        sess.post.return_value = FakeResp(200, {"request_id": "r1", "status_url": "https://q/s", "response_url": "https://q/r"})
        sess.get.side_effect = [FakeResp(200, {"status": "IN_QUEUE"}), FakeResp(200, {"status": "COMPLETED"}),
                                FakeResp(200, {"video": {"url": "https://cdn/v.mp4"}})]
        g = FalClient("KEY", session=sess, poll_interval=0).video("fal-ai/veo3.1/fast", "prompt", 6, 0.10)
        self.assertEqual((g.url, g.seconds, g.cost_usd), ("https://cdn/v.mp4", 6, 0.6))
        self.assertEqual(sess.post.call_args.kwargs["headers"]["Authorization"], "Key KEY")

    def test_queue_failure(self):
        from unittest import mock
        from zft.genai import FalClient
        sess = mock.Mock()
        sess.post.return_value = FakeResp(200, {"request_id": "r1"})
        sess.get.return_value = FakeResp(200, {"status": "FAILED", "error": "nsfw"})
        with self.assertRaises(GenError):
            FalClient("K", session=sess, poll_interval=0).image("fal-ai/flux/schnell", "p", 0.004)


if __name__ == "__main__":
    unittest.main()
