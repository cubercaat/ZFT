"""AI miya: natijalarni tahlil qilib, keyingi videoni rejalaydi (Claude API).

Budjet nazorati: oylik limitga yetsa AI o'chadi va tizim oddiy shablonlar bilan
ishlashda davom etadi — pul hech qachon limitdan oshmaydi.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import random
import re
from dataclasses import dataclass, field
from typing import Any

import requests

from .db import DB, now

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"

# $ / 1M token. Narx o'zgarsa .env orqali emas, shu yerda yangilang (manba: platform.claude.com/docs pricing)
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}
SEARCH_PRICE = 0.01  # $10 / 1000 qidiruv

SYSTEM_PROMPT = """Sen MAKE — "Chorraha" loyihasining SMM va o'sish bo'yicha AI mutaxassisisan.
Chorraha: O'zbekistonda haydovchilik guvohnomasi (prava) nazariy imtihoniga tayyorlaydigan sayt
(chorrahaa.uz: rasmiy savollar, o'zbekcha ovozli tushuntirish, AI yordamchi, 3D simulyator),
Telegram bot va PRAVA QUIZ guruhi. Maqsad: botga va saytga ko'proq odam olib kelish.

Har kuni 1 ta qisqa vertikal video chiqadi (Instagram Reels, TikTok, Telegram kanal):
hook → savol va 5 soniyalik taymer → to'g'ri javob → botga chaqiriq.

QOIDALAR
- Faqat haqiqat: to'qima statistika ("90% odam yiqiladi"), soxta va'da ("100% o'tasiz") yo'q.
  Hook qiziqish uyg'otsin, lekin yolg'on bo'lmasin.
- Savolning o'zi, variantlari va to'g'ri javobini O'ZGARTIRMAYSAN — ular rasmiy bazadan.
- Raqobatchilarni kamsitmaysan, spam usullarini taklif qilmaysan.
- Til: o'zbek (lotin), sodda, yoshlar tilida, lekin hurmat bilan.
- Natija raqamlariga qarab qaror qil: nima ishladi — takrorla, nima ishlamadi — o'zgartir.
  Ma'lumot kam bo'lsa (yangi akkaunt), turli hook uslublarini sinab ko'r va buni ochiq ayt.
- Javobni FAQAT so'ralgan JSON ko'rinishida ber, boshqa matnsiz."""


@dataclass
class ContentPlan:
    question_id: str | None
    topic: str | None
    hook: str
    caption: str
    hashtags: list[str] = field(default_factory=list)
    cta: str = "Imtihondagi barcha savollar bepul botda"
    reason: str = ""
    by_ai: bool = False


FALLBACK_HOOKS = [
    "Shu savolga 5 soniyada javob bera olasizmi?",
    "Imtihonga borishdan oldin buni bilib oling",
    "Prava imtihonida shunga o'xshash savol bo'ladi",
    "Do'stingizni belgilang: u buni biladimi?",
    "Ko'pchilik shu yerda o'ylanib qoladi. Siz-chi?",
    "Haydovchi bo'lmoqchimisiz? Mana sinov",
    "Javobni izohda yozing, keyin tekshiring",
]
BASE_TAGS = ["prava", "pravaimtihon", "yolqoidalari", "haydovchilik", "avtomaktab", "uzbekistan"]


class Brain:
    def __init__(self, db: DB, api_key: str, model: str, monthly_budget: float,
                 web_search: bool = True, web_search_type: str = "web_search_20260209",
                 tz_offset: int = 5, session: requests.Session | None = None, api_url: str = API_URL):
        self.db = db
        self.api_key = api_key
        self.model = model
        self.budget = monthly_budget
        self.web_search = web_search
        self.web_search_type = web_search_type
        self.tz = dt.timezone(dt.timedelta(hours=tz_offset))
        self.http = session or requests.Session()
        self.api_url = api_url

    # ------------------------------------------------------------- budjet
    def month_start(self) -> int:
        n = dt.datetime.now(self.tz)
        return int(dt.datetime(n.year, n.month, 1, tzinfo=self.tz).timestamp())

    def spent(self) -> float:
        return self.db.ai_spent_this_month(self.month_start())

    def available(self, reserve: float = 0.0) -> bool:
        return bool(self.api_key) and self.spent() + reserve < self.budget

    def _cost(self, usage: dict, searches: int, model: str | None = None) -> float:
        pin, pout = PRICES.get(model or self.model, PRICES["claude-sonnet-5-5"])  # noma'lum model → qimmatroq hisoblaymiz
        return (usage.get("input_tokens", 0) * pin + usage.get("output_tokens", 0) * pout) / 1e6 \
            + searches * SEARCH_PRICE

    # -------------------------------------------------------------- call
    def ask(self, purpose: str, user: str, max_tokens: int = 1200, use_search: bool = False,
            system: str = SYSTEM_PROMPT, model: str | None = None) -> str | None:
        model = model or self.model
        reserve = 0.05 if use_search else (0.05 if "haiku" not in model else 0.005)
        if not self.available(reserve):
            log.info("AI o'chiq yoki budjet tugagan (%s): %.3f$/%.2f$", purpose, self.spent(), self.budget)
            return None
        body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system,
                                "messages": [{"role": "user", "content": user}]}
        if use_search and self.web_search:
            body["tools"] = [{"type": self.web_search_type, "name": "web_search", "max_uses": 3}]
        try:
            r = self.http.post(self.api_url, json=body, timeout=120, headers={
                "x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
            data = r.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("Claude API ulanmadi: %s", exc)
            return None
        if r.status_code != 200:
            log.warning("Claude API xato %s: %s", r.status_code, str(data)[:300])
            self.db.audit("brain", "api_error", {"purpose": purpose, "status": r.status_code})
            return None
        usage = data.get("usage") or {}
        searches = int((usage.get("server_tool_use") or {}).get("web_search_requests", 0) or 0)
        cost = self._cost(usage, searches, model)
        self.db.execute(
            "INSERT INTO ai_usage(at, model, purpose, input_tokens, output_tokens, searches, cost_usd)"
            " VALUES (?,?,?,?,?,?,?)",
            (now(), model, purpose, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
             searches, cost))
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return text.strip() or None

    @staticmethod
    def parse_json(text: str | None) -> dict | None:
        if not text:
            return None
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None

    # ---------------------------------------------------- content planning
    def plan_content(self, context: dict, candidates: list[dict], rng: random.Random | None = None) -> ContentPlan:
        """context: oldingi postlar natijalari, manbalar, trendlar. candidates: tanlash mumkin bo'lgan savollar."""
        rng = rng or random.Random()
        prompt = (
            "Bugungi video uchun reja tuz.\n\n"
            f"OXIRGI POSTLAR VA NATIJALARI (yangidan eskiga):\n{json.dumps(context.get('posts', []), ensure_ascii=False)}\n\n"
            f"ODAMLAR QAYERDAN KELYAPTI (7 kun):\n{json.dumps(context.get('sources', {}), ensure_ascii=False)}\n\n"
            f"BOTDA ENG KO'P XATO QILINGAN SAVOLLAR:\n{json.dumps(context.get('hard_questions', []), ensure_ascii=False)}\n\n"
            f"HAFTALIK TREND KUZATUVI:\n{context.get('trends') or 'hali yo`q'}\n\n"
            f"TANLASH MUMKIN BO'LGAN SAVOLLAR (id, mavzu, matn):\n{json.dumps(candidates, ensure_ascii=False)}\n\n"
            "Javob JSON:\n"
            '{"question_id": "ro\'yxatdan bitta id", "hook": "videoning birinchi 2 soniyasidagi matn, 4-9 so\'z",'
            ' "caption": "post matni 2-4 qator, oxirida botga chaqiriq", "hashtags": ["5-8 ta, # belgisisiz"],'
            ' "cta": "video oxiridagi chaqiriq, 4-8 so\'z", "reason": "nega aynan shunday (1-2 gap, raqamlarga tayanib)"}'
        )
        data = self.parse_json(self.ask("plan_content", prompt, max_tokens=700))
        valid_ids = {c["id"] for c in candidates}
        if data and data.get("question_id") in valid_ids and data.get("hook") and data.get("caption"):
            tags = [re.sub(r"[^\w]", "", str(t).lstrip("#")) for t in data.get("hashtags") or []]
            tags = [t for t in tags if t][:8] or BASE_TAGS
            return ContentPlan(
                question_id=data["question_id"], topic=None, hook=str(data["hook"])[:90],
                caption=str(data["caption"])[:1500], hashtags=tags,
                cta=str(data.get("cta") or "Imtihondagi barcha savollar bepul botda")[:70],
                reason=str(data.get("reason") or "")[:400], by_ai=True)
        return self.fallback_plan(context, candidates, rng)

    def fallback_plan(self, context: dict, candidates: list[dict], rng: random.Random) -> ContentPlan:
        """AI yo'q bo'lsa: eng yaxshi ishlagan hook uslubini ko'proq, boshqalarini ham sinab turadi."""
        posts = context.get("posts", [])
        scored = [(p.get("score", 0), p.get("hook")) for p in posts if p.get("hook") in FALLBACK_HOOKS]
        if scored and rng.random() < 0.6:
            hook = max(scored)[1]
            why = "eng yaxshi natija bergan hook takrorlandi"
        else:
            used = {p.get("hook") for p in posts[:5]}
            fresh = [h for h in FALLBACK_HOOKS if h not in used] or FALLBACK_HOOKS
            hook = rng.choice(fresh)
            why = "yangi hook sinovdan o'tkazilmoqda"
        hard = [h["id"] for h in context.get("hard_questions", []) if h["id"] in {c["id"] for c in candidates}]
        qid = hard[0] if hard and rng.random() < 0.5 else (rng.choice(candidates)["id"] if candidates else None)
        caption = ("Javobingizni izohda yozing 👇\n\n"
                   "Imtihondagi barcha savollar, tushuntirishlar va testlar — botda bepul.")
        return ContentPlan(question_id=qid, topic=None, hook=hook, caption=caption, hashtags=BASE_TAGS,
                           reason=f"Shablon rejim (AI o'chiq): {why}", by_ai=False)

    # ------------------------------------------------------------- trends
    def weekly_trends(self) -> str | None:
        prompt = (
            "Internetdan qidirib, shu hafta global va O'zbekistonda qisqa videolarda (TikTok, Instagram Reels) "
            "qanday formatlar, challenge va hook uslublari trendda ekanini top. Shuningdek O'zbekistonda "
            "haydovchilik guvohnomasi imtihoni yoki yo'l harakati qoidalari bo'yicha yangiliklar bormi — tekshir.\n"
            "Natijani o'zbek tilida, ko'pi bilan 8 ta qisqa punkt qilib yoz: har punktda trend va uni "
            "prava test-videosiga qanday moslash. Manbasi topilmagan narsani yozma."
        )
        text = self.ask("weekly_trends", prompt, max_tokens=900, use_search=True,
                        system="Sen qisqa video trendlari bo'yicha tahlilchisan. Faqat tekshirilgan ma'lumot.")
        if text:
            self.db.set("trend_notes", text[:3000])
            self.db.set("trend_notes_at", now())
        return text

    def daily_advice(self, summary: dict) -> str | None:
        prompt = (
            "Bugungi natijalar:\n" + json.dumps(summary, ensure_ascii=False) +
            "\n\nEgaga 3 ta qisqa, aniq tavsiya yoz (har biri 1 gap, raqamga tayan). Agar ma'lumot kam bo'lsa,"
            " shuni ayt. JSON emas, oddiy matn, har tavsiya yangi qatorda '• ' bilan."
        )
        return self.ask("daily_advice", prompt, max_tokens=300,
                        system="Sen o'sish tahlilchisisan. Qisqa, halol, raqamlarga tayangan maslahat berasan.")
