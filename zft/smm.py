"""SMM avtomatikasi: Instagram izohlariga avto-javob, kalit so'z → DM, birinchi izoh,
hashtag samaradorligi va e'londan oldingi sifat bahosi.

Xavfsizlik qoidalari:
- Narx, to'lov, shikoyat, va'da/kafolat haqidagi izohlarga AI javob BERMAYDI — egaga yuboriladi.
- Bir izohga faqat bir marta javob; soatlik va kunlik limit bor (spam deb bloklanmaslik uchun).
- Instagram qoidasi: izoh yozgan odamga 1 ta shaxsiy xabar (private reply), izohdan keyin 7 kun ichida.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import random
import re
from typing import TYPE_CHECKING, Any

from .brain import Brain
from .db import now

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ig_comments (
    id        TEXT PRIMARY KEY,
    post_id   INTEGER,
    media_id  TEXT,
    username  TEXT,
    text      TEXT,
    at        INTEGER NOT NULL,
    action    TEXT,          -- replied | dm | escalated | skipped | dry
    reply     TEXT,
    replied_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_igc_at ON ig_comments(at);
"""

ESCALATE = re.compile(
    r"narx|necha pul|qancha tur|qancha so.?m|pul(?:i|lik)|to.?lov|tolov|karta|click|payme|qaytar|aldov|aldash|firib|"
    r"shikoyat|ishlamay|ishlamaydi|xato|kafolat|garantiya|va.?da|vada|100%|aniq o.?tam|скольк|цен|оплат|обман|жалоб|гарант",
    re.I)
SPAM = re.compile(r"https?://|t\.me/|follow ?back|f4f|l4l|💰💰|crypto|kripto|stavka|casino|kazino|1xbet", re.I)
REPLY_SYSTEM = """Sen "Chorraha" (prava nazariy imtihoniga tayyorgarlik: Telegram bot va chorrahaa.uz) Instagram sahifasi
nomidan izohlarga javob yozasan. Qoidalar: jonli og'zaki o'zbekcha, 1–2 qisqa gap, samimiy, "siz" deb murojaat.
Hech qanday va'da, kafolat, narx, statistika yozma. Havola yozma (Instagram izohida ishlamaydi) — kerak bo'lsa
"profildagi havola" yoki Telegram'dagi bot nomini ayt. Yo'l harakati qoidalari bo'yicha savol bo'lsa va javobga
100% ishonching bo'lmasa, "botda shu mavzudagi savollar va tushuntirish bor" deb yo'naltir. Em-dash ishlatma.
Javob faqat JSON."""

QUALITY_SYSTEM = """Sen qisqa video reklama sifatini baholaydigan qattiqqo'l muharrirsan (Instagram Reels/TikTok,
mavzu: prava nazariy imtihoniga tayyorgarlik). Mezonlar: 1) birinchi 2 soniyadagi hook kuchi, 2) tushunarlilik,
3) reklama ekanini kech sezdirish (native), 4) aniq CTA, 5) halollik (soxta sharh/statistika/va'da yo'q, xavfli
haydash yo'q), 6) matn tabiiyligi (AI hidi yo'q). Javob faqat JSON."""


class SmmAuto:
    def __init__(self, app: "App"):
        self.app = app
        self.db = app.db
        self.db.conn.executescript(SCHEMA)
        self.max_run = int(os.environ.get("IG_REPLY_MAX_RUN", "15"))
        self.max_day = int(os.environ.get("IG_REPLY_MAX_DAY", "150"))
        self.dm_keywords = [k.strip().lower() for k in os.environ.get(
            "IG_DM_KEYWORDS", "test,havola,link,bot,ssilka,ссылка,savollar,+").split(",") if k.strip()]
        self.quality_min = float(os.environ.get("QUALITY_MIN", "6"))

    @property
    def ig(self):
        ig = self.app.instagram
        return ig if ig and ig.enabled else None

    @property
    def dry(self) -> bool:
        return bool(getattr(self.app, "dry_run", False))

    def bot_handle(self) -> str:
        return "@" + self.app.settings.bot_username

    # ------------------------------------------------------------ izohlar
    def classify(self, text: str) -> str:
        t = (text or "").strip()
        if not t or SPAM.search(t):
            return "skip"
        if ESCALATE.search(t):
            return "escalate"
        low = t.lower()
        words = set(re.findall(r"[\w+']+", low))
        if any(k in words or (k == "+" and low == "+") for k in self.dm_keywords):
            return "dm"
        return "reply"

    def _replies_today(self) -> int:
        return self.db.scalar("SELECT COUNT(*) FROM ig_comments WHERE action IN ('replied','dm') AND replied_at>=?",
                              (self.app.local_day_start(),))

    def check_comments(self) -> dict:
        ig = self.ig
        res = {"new": 0, "replied": 0, "dm": 0, "escalated": 0, "skipped": 0}
        if not ig:
            return res
        own = (self.db.get("ig_username") or "").lower()
        if not own:
            own = (ig.username() or "").lower()
            if own:
                self.db.set("ig_username", own)
        budget_left = max(0, min(self.max_run, self.max_day - self._replies_today()))
        to_answer: list[dict] = []
        for p in self.db.all("SELECT id, ig_media_id, hook FROM posts WHERE ig_media_id IS NOT NULL AND published_at>=?",
                             (now() - 14 * 86400,)):
            try:
                items = ig.comments(p["ig_media_id"])
            except Exception as exc:  # noqa: BLE001 — bitta post xatosi butun ishni to'xtatmasin
                log.warning("Izohlar olinmadi #%s: %s", p["id"], exc)
                continue
            for c in items:
                cid = str(c.get("id") or "")
                user = str(c.get("username") or (c.get("from") or {}).get("username") or "")
                if not cid or user.lower() == own or self.db.one("SELECT 1 FROM ig_comments WHERE id=?", (cid,)):
                    continue
                replied_by_us = any((r.get("username") or "").lower() == own for r in (c.get("replies") or {}).get("data", []))
                kind = "skip" if replied_by_us else self.classify(c.get("text", ""))
                self.db.execute("INSERT INTO ig_comments(id, post_id, media_id, username, text, at, action) VALUES (?,?,?,?,?,?,?)",
                                (cid, p["id"], p["ig_media_id"], user[:60], str(c.get("text", ""))[:1000], now(),
                                 "pending" if kind in ("reply", "dm") else ("escalated" if kind == "escalate" else "skipped")))
                res["new"] += 1
                if kind == "escalate":
                    res["escalated"] += 1
                    self.app.pipeline._notify_owner(
                        f"💬 Instagram izohi sizning javobingizni kutyapti (#{p['id']}):\n@{user}: «{str(c.get('text', ''))[:400]}»\n"
                        "Narx/to'lov/shikoyat mavzusi — avto-javob berilmadi.")
                elif kind == "skip":
                    res["skipped"] += 1
                else:
                    to_answer.append({"id": cid, "user": user, "text": str(c.get("text", ""))[:500], "kind": kind,
                                      "post": p["hook"] or ""})
        to_answer = to_answer[:budget_left]
        replies = self.write_replies(to_answer)
        for c in to_answer:
            text = replies.get(c["id"]) or self.template(c)
            action = c["kind"] if c["kind"] == "dm" else "replied"
            if self.dry:
                self.db.execute("UPDATE ig_comments SET action='dry', reply=? WHERE id=?", (text, c["id"]))
                continue
            try:
                if c["kind"] == "dm":
                    ig.private_reply(c["id"], self.dm_text())
                    ig.reply(c["id"], "Yubordik, DM'ni tekshiring 📩")
                    res["dm"] += 1
                    text = "DM: " + self.dm_text()
                else:
                    ig.reply(c["id"], text)
                    res["replied"] += 1
                self.db.execute("UPDATE ig_comments SET action=?, reply=?, replied_at=? WHERE id=?",
                                (action, text, now(), c["id"]))
            except Exception as exc:  # noqa: BLE001
                log.warning("Javob yuborilmadi %s: %s", c["id"], exc)
                self.db.execute("UPDATE ig_comments SET action='failed', reply=? WHERE id=?", (str(exc)[:200], c["id"]))
        # limitdan oshganlari keyingi safar
        self.db.execute("DELETE FROM ig_comments WHERE action='pending'")
        if res["new"]:
            self.app.hub.say("smm", "direktor", f"Instagram izohlari: {res['new']} yangi, {res['replied']} javob, "
                                                f"{res['dm']} DM, {res['escalated']} egaga yuborildi.")
        return res

    def dm_text(self) -> str:
        return os.environ.get("IG_DM_TEXT") or (
            f"Salom! Prava nazariy imtihoniga bepul tayyorlanish: Telegram'da {self.bot_handle()} — rasmiy savollar, "
            f"tushuntirishlar va 20 savollik imtihon simulyatsiyasi. Sayt: {self.app.settings.site_url}")

    def template(self, c: dict) -> str:
        if "?" in c["text"]:
            return random.choice([f"Yaxshi savol! Shu mavzudagi savollar va tushuntirish {self.bot_handle()} botida bor 🙌",
                                  f"Botimizda shu mavzu bo'yicha alohida test bor: {self.bot_handle()} 👍"])
        return random.choice(["Rahmat! 🙌", "Rahmat, imtihonda omad! 🚗", "Rahmat! Do'stlaringizga ham ulashing 🙂"])

    def write_replies(self, items: list[dict]) -> dict[str, str]:
        ask = [c for c in items if c["kind"] == "reply"]
        if not ask or not self.app.settings.ai_enabled:
            return {}
        prompt = ("Izohlar: " + json.dumps([{"id": c["id"], "video": c["post"], "izoh": c["text"]} for c in ask], ensure_ascii=False)
                  + f'\nBot nomi: {self.bot_handle()}\nJSON: {{"replies": [{{"id": "...", "text": "javob"}}]}}')
        data = Brain.parse_json(self.app.brain.ask("ig_reply", prompt, max_tokens=900, system=REPLY_SYSTEM))
        out = {}
        for r in (data or {}).get("replies", []) if isinstance(data, dict) else []:
            t = str(r.get("text") or "").replace("—", ",").strip()
            if t and not re.search(r"https?://", t) and not ESCALATE.search(t):
                out[str(r.get("id"))] = t[:300]
        return out

    # ------------------------------------------------------- birinchi izoh
    def after_publish(self, post_id: int) -> str | None:
        p = self.db.post(post_id)
        if not p or not p["ig_media_id"] or p["ig_comment_id"]:
            return None
        text = (p["first_comment"] or "").strip() or random.choice([
            "Siz qaysi savolda ko'proq adashasiz? Izohda yozing 👇",
            "Shu savolga birinchi urinishda to'g'ri javob berdingizmi? 👇",
        ])
        text += f"\n\nBepul test: Telegram'da {self.bot_handle()}"
        if self.dry or not self.ig:
            return text
        try:
            cid = self.ig.comment(p["ig_media_id"], text)
            self.db.update_post(post_id, ig_comment_id=cid)
            return text
        except Exception as exc:  # noqa: BLE001
            log.warning("Birinchi izoh yozilmadi: %s", exc)
            return None

    # --------------------------------------------------- hashtag tahlili
    def hashtag_stats(self, days: int = 60) -> list[dict]:
        agg: dict[str, list[int]] = {}
        for p in self.db.all("SELECT id, hashtags FROM posts WHERE status='published' AND published_at>=?",
                             (now() - days * 86400,)):
            m = self.db.latest_metrics(p["id"])
            views = sum((v.get("views") or 0) for v in m.values())
            if not m:
                continue
            for t in json.loads(p["hashtags"] or "[]"):
                agg.setdefault(str(t).lower(), []).append(views)
        rows = [{"tag": t, "posts": len(v), "avg_views": round(sum(v) / len(v))} for t, v in agg.items()]
        rows.sort(key=lambda r: (-r["avg_views"], -r["posts"]))
        return rows

    def hashtag_hint(self) -> str:
        rows = [r for r in self.hashtag_stats() if r["posts"] >= 2]
        if len(rows) < 3:
            return ""
        top = ", ".join(f"#{r['tag']} ({r['avg_views']})" for r in rows[:5])
        low = ", ".join(f"#{r['tag']} ({r['avg_views']})" for r in rows[-3:])
        return f"Hashtag natijalari (o'rtacha ko'rish): eng yaxshilari {top}; eng sustlari {low}."

    # ------------------------------------------------------ sifat bahosi
    def score(self, post_id: int) -> tuple[float, str]:
        p = self.db.post(post_id)
        concept = json.loads(p["concept"] or "{}") if p["concept"] else {}
        if self.app.settings.ai_enabled:
            prompt = json.dumps({"hook": p["hook"], "format": p["format"], "tier": p["tier"],
                                 "scenes": [s.get("caption") or s.get("prompt") for s in concept.get("scenes", [])][:6],
                                 "caption": p["caption"], "hashtags": json.loads(p["hashtags"] or "[]")}, ensure_ascii=False)
            data = Brain.parse_json(self.app.brain.ask(
                "quality", prompt + '\nJSON: {"score": 1-10, "issues": "eng muhim 1-2 kamchilik", "fix": "1 gapda tuzatish"}',
                max_tokens=300, system=QUALITY_SYSTEM))
            if isinstance(data, dict) and isinstance(data.get("score"), (int, float)):
                return float(max(1, min(10, data["score"]))), f"{data.get('issues', '')} → {data.get('fix', '')}"[:300]
        # AI'siz oddiy tekshiruv
        s, notes = 10.0, []
        hook = p["hook"] or ""
        if not hook or len(hook) > 70:
            s -= 2; notes.append("hook yo'q yoki juda uzun")
        cap = (p["caption"] or "").lower()
        if not any(w in cap for w in ("bot", "havola", "izoh", "layk", "test")):
            s -= 2; notes.append("CTA yo'q")
        if "—" in (p["caption"] or ""):
            s -= 1; notes.append("em-dash")
        tags = json.loads(p["hashtags"] or "[]")
        if not 3 <= len(tags) <= 10:
            s -= 1; notes.append("hashtag soni 3–10 emas")
        if not p["video_path"]:
            s -= 4; notes.append("video fayli yo'q")
        return s, ", ".join(notes) or "asosiy tekshiruvlar o'tdi"

    def quality_gate(self, post_id: int | None) -> int | None:
        if not post_id or not self.db.post(post_id):
            return post_id
        with self.app.hub.turn("direktor", "Video sifatini baholayapti"):
            sc, note = self.score(post_id)
            self.db.update_post(post_id, quality=sc, quality_note=note)
            self.app.hub.say("direktor", "studiya", f"#{post_id} sifat bahosi: {sc:.0f}/10 — {note}")
        if sc >= self.quality_min:
            return post_id
        p = self.db.post(post_id)
        key = f"quality_regen:{p['publish_at'] or dt.date.today().isoformat()}"
        cost = p["cost_usd"] or 0
        if self.db.get(key) or (cost > 0 and self.app.vbudget.allowance(self.app.local_now()) < cost):
            self.app.hub.say("direktor", "ega", f"#{post_id} bahosi past ({sc:.0f}/10), lekin qayta yasashga budjet yoki "
                                                "imkon yo'q — tasdiqlashdan oldin ko'rib chiqing.")
            return post_id
        self.db.set(key, 1)
        self.app.hub.say("direktor", "studiya", f"#{post_id} past baho oldi — bir marta qayta yasang. Tuzatish: {note}")
        pipe = self.app.pipeline
        pipe.defer_review = True
        try:
            pipe.add_task(f"Oldingi variant past baho oldi: {note}. Shuni tuzatib qayta yasang.", source="direktor")
            new = pipe.regenerate(post_id)
        finally:
            pipe.defer_review = False
        if not new:
            return post_id
        pipe.last_info.pop(post_id, None)
        self.app.hub.smm_polish(new)
        with self.app.hub.turn("direktor", "Yangi variantni baholayapti"):
            sc2, note2 = self.score(new)
            self.db.update_post(new, quality=sc2, quality_note=note2)
            self.app.hub.say("direktor", "studiya", f"Yangi variant #{new}: {sc2:.0f}/10 — {note2}")
        return new

    # --------------------------------------------------------------- panel
    def panel_get(self, path: str, query: dict) -> tuple[int, Any] | None:
        if path == "/api/smm":
            return 200, {"comments": [dict(r) for r in self.db.all("SELECT * FROM ig_comments ORDER BY at DESC LIMIT 60")],
                         "hashtags": self.hashtag_stats()[:30],
                         "quality": [dict(r) for r in self.db.all(
                             "SELECT id, hook, quality, quality_note, status FROM posts WHERE quality IS NOT NULL ORDER BY id DESC LIMIT 20")],
                         "today": self._replies_today(), "limit_day": self.max_day,
                         "instagram": bool(self.ig)}
        return None

    def panel_action(self, path: str, body: dict) -> tuple[int, dict] | None:
        if path == "/api/smm/check-now":
            if not self.ig:
                return 400, {"error": "Instagram ulanmagan"}
            self.app.run_in_background(self.check_comments)
            return 202, {"ok": True, "message": "Izohlar tekshirilmoqda"}
        return None
