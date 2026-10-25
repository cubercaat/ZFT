"""AI rejissyor: hikoyaga asoslangan "native" reklama konsepti.

Tomoshabin avval qiziqarli hikoyani ko'radi; Chorraha faqat oxirida, hikoyaning tabiiy
yechimi sifatida chiqadi. Halollik chegaralari promptda qat'iy yozilgan.
"""
from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass, field
from typing import Any

from .brain import Brain

log = logging.getLogger(__name__)

FORMATS = [
    ("pov_exam", "POV: imtihon kuni — hayajon, kompyuter oldida oxirgi savol, tarang musiqa; yechim oxirida"),
    ("plot_twist", "Kutilmagan burilish: oddiy kundalik vaziyat, oxirida kutilmagan kulgili yechim"),
    ("father_son", "Oilaviy skit: dadasi o'g'liga (yoki qiziga) mashina kalitini berishdan oldin sinab ko'radi"),
    ("street_choice", "Ko'chadagi vaziyat: chorrahada qaror qabul qilish lahzasi — 'siz nima qilardingiz?'"),
    ("myth_fact", "Mif yoki haqiqat: haydovchilar orasidagi keng tarqalgan noto'g'ri tushuncha, keyin to'g'risi"),
    ("first_key", "Orzu: birinchi mashina kaliti, birinchi mustaqil yo'l — hissiy, ilhomlantiruvchi"),
    ("friends_bet", "Do'stlar bahsi: kim imtihondan birinchi o'tadi — yengil raqobat va hazil"),
]

SYSTEM = """Sen — reklama agentligining bosh kreativ direktori va qisqa video rejissyorisan.
Mijoz: "Chorraha" — O'zbekistonda haydovchilik guvohnomasi (prava) nazariy imtihoniga tayyorlaydigan
xizmat: chorrahaa.uz sayti (rasmiy savollar, o'zbekcha ovozli tushuntirish, AI yordamchi, 3D simulyator)
va Telegram bot. Auditoriya: 17–35 yoshli, prava olishni rejalashtirayotgan yoshlar va ularning ota-onalari.

VAZIFA: Instagram Reels / TikTok uchun 15–25 soniyalik "native" reklama konseptini yarat.
Tomoshabin 1-soniyadan qiziqib qolishi va oxirigacha bu reklama ekanini sezmasligi kerak:
- 1-sahna: kuchli hook — kutilmagan holat, hissiyot yoki savol (skrolni to'xtatadi).
- O'rta sahna(lar): hikoya rivoji, taranglik yoki hazil, tomoshabin o'zini tanib oladi.
- Oxirgi sahna: tabiiy yechim. Chorraha faqat shu yerda, hikoyaning davomi sifatida chiqadi.

HALOLLIK CHEGARALARI (buzilmaydi):
- Personajlar to'qima va bu ochiq ko'rinib turadi; "haqiqiy mijoz guvohligi" ham, "men shu bilan o'tdim"
  degan soxta sharh ham YO'Q. Real shaxslar, mashhurlar, YHXBB xodimlari va rasmiy belgilar ishlatilmaydi.
- To'qima statistika ("90% yiqiladi") va kafolat ("100% o'tasiz") YO'Q.
- Xavfli haydashni chiroyli qilib ko'rsatish yo'q: tezlik, telefon bilan haydash va qoida buzish faqat
  salbiy misol sifatida va oqibati bilan ko'rsatiladi.
- Boshqa brend va logotiplar yo'q, raqobatchilarni kamsitish yo'q.

VIZUAL PROMPTLAR (video modeli uchun, INGLIZ tilida):
- Har sahna — bitta aniq kadr: kim, qayerda, nima qilyapti, kamera harakati, yorug'lik, kayfiyat.
- Joy — O'zbekiston: Toshkent ko'chalari, avtomaktab hovlisi, o'zbek xonadoni, imtihon zali.
- Personaj tashqi ko'rinishini HAR sahnada bir xil so'zlar bilan takrorla (izchillik uchun).
- Kadrda matn, yozuv, logotip, raqam bo'lmasin (model yozuvni buzadi). Vertikal 9:16, kinematografik,
  realistik, 35mm, sayoz fokus. Yuz ifodasi va harakat aniq bo'lsin.

OVOZ (o'zbek, lotin): har sahnaga bitta qisqa jumla (6 soniyaga 8–14 so'z), jonli, yoshlar tilida.
SUBTITR: har sahnaga 2–6 so'zli ekran matni.

Faqat JSON qaytar, boshqa matnsiz."""


@dataclass
class Scene:
    visual: str
    voice: str
    caption: str
    seconds: int = 6


@dataclass
class Concept:
    format: str
    title: str
    hook_text: str
    scenes: list[Scene]
    reveal_voice: str
    card_title: str
    card_sub: str
    caption: str
    hashtags: list[str] = field(default_factory=list)
    reason: str = ""
    task_id: int | None = None
    by_ai: bool = False

    def to_json(self) -> str:
        d = self.__dict__.copy()
        d["scenes"] = [s.__dict__ for s in self.scenes]
        return json.dumps(d, ensure_ascii=False)


TEMPLATES: list[dict[str, Any]] = [
    {
        "format": "pov_exam", "title": "Oxirgi savol",
        "hook_text": "Oxirgi savol. 30 soniya qoldi",
        "scenes": [
            {"visual": "Close-up of a nervous young Uzbek man, early 20s, short black hair, white shirt, sitting at a computer in a quiet exam hall in Tashkent, sweat on forehead, screen glow on his face, slow push-in, shallow depth of field, cinematic, 9:16",
             "voice": "Imtihon zali. Ekranda oxirgi savol, vaqt esa tugayapti.", "caption": "Oxirgi savol..."},
            {"visual": "Same young Uzbek man, short black hair, white shirt, closes his eyes and remembers: flashback of him at home at night studying on his phone with warm lamp light, smiling as he understands something, soft handheld camera, cinematic, 9:16",
             "voice": "Lekin u bu savolni uyda ovozli tushuntirish bilan o'nlab marta yechgan edi.", "caption": "U tayyor edi"},
            {"visual": "Same young Uzbek man, short black hair, white shirt, walks out of the exam building into bright sunlight in Tashkent, raises his fist in joy, friends hugging him, slow motion, golden hour, cinematic, 9:16",
             "voice": "Natija: o'tdi. Siz ham shunday tayyorlanishingiz mumkin.", "caption": "Birinchi urinishda"},
        ],
        "reveal_voice": "Chorraha — prava imtihoniga aqlli tayyorgarlik. Botda bepul boshlang.",
        "card_title": "Imtihonga tayyormisiz?", "card_sub": "Rasmiy savollar · ovozli tushuntirish",
        "caption": "Oxirgi savol hammani hayajonga soladi 😅\nSizda shunday bo'lganmi? Izohda yozing 👇\n\nTayyorgarlikni bepul boshlang — havola profilda.",
        "hashtags": ["prava", "pravaimtihon", "haydovchilik", "toshkent", "avtomaktab", "yolqoidalari"],
    },
    {
        "format": "father_son", "title": "Kalit",
        "hook_text": "Dadam kalitni berishdan oldin...",
        "scenes": [
            {"visual": "Uzbek father in his 50s with grey mustache and checked shirt holding car keys in a Tashkent courtyard, playfully hiding them behind his back from his smiling 18-year-old son in a black hoodie, warm afternoon light, handheld, cinematic, 9:16",
             "voice": "Dadam kalitni berishdan oldin bitta shart qo'ydi.", "caption": "Bitta shart bor"},
            {"visual": "The same Uzbek father with grey mustache and checked shirt asks a question, raising an eyebrow, while the son in black hoodie thinks hard, looking up, comedic pause, close-ups, shallow depth of field, cinematic, 9:16",
             "voice": "Uchta savol. Bittasida adashsang — yana avtobusda yurasan.", "caption": "Uchta savol"},
            {"visual": "The son in black hoodie answers confidently, the father with grey mustache laughs and tosses him the keys, son catches them in slow motion, both laughing, golden hour, cinematic, 9:16",
             "voice": "Uchalasiga to'g'ri javob. Chunki har kuni botda mashq qilgandim.", "caption": "Kalit meniki"},
        ],
        "reveal_voice": "Chorraha botida har kuni bir necha savol — va imtihon qo'rqinchli emas.",
        "card_title": "Kalitni qo'lga oling", "card_sub": "Har kuni 5 daqiqa mashq",
        "caption": "Sizning dadangiz ham shunday sinaganmi? 😄\nDo'stingizni belgilang 👇\n\nBotda bepul mashq — havola profilda.",
        "hashtags": ["prava", "dadam", "haydovchilik", "uzbekistan", "pravaimtihon", "kulgili"],
    },
    {
        "format": "street_choice", "title": "Chorrahada",
        "hook_text": "Siz bo'lganingizda kim birinchi o'tardi?",
        "scenes": [
            {"visual": "A white sedan approaching an unregulated intersection in a Tashkent neighborhood, another car approaching from the right, view from the driver seat, tense moment, overcast light, cinematic, 9:16",
             "voice": "Chorraha. Svetofor yo'q. O'ngdan boshqa mashina kelyapti.", "caption": "Kim birinchi?"},
            {"visual": "Close-up of a young Uzbek woman driver, long dark hair tied back, beige jacket, hesitating with hands on the steering wheel, eyes moving between left and right mirrors, slow push-in, cinematic, 9:16",
             "voice": "Ko'pchilik shu lahzada ikkilanadi. Siz-chi?", "caption": "Ikkilanish lahzasi"},
            {"visual": "The same young Uzbek woman driver with long dark hair and beige jacket calmly lets the car from the right pass, nods, then drives on smoothly, relieved smile, soft daylight, cinematic, 9:16",
             "voice": "Teng ahamiyatli yo'llar chorrahasida o'ngdan kelganga yo'l beriladi. Buni oldindan bilib oling.", "caption": "O'ngdagiga yo'l"},
        ],
        "reveal_voice": "Chorraha — yo'l qoidalarini yodlamasdan, tushunib o'rganing.",
        "card_title": "Qoidani tushunib o'rganing", "card_sub": "Har savolga tushuntirish",
        "caption": "To'g'ri javob bera oldingizmi? 🤔\nIzohda yozing 👇\n\nBarcha savollar va tushuntirishlar — botda bepul.",
        "hashtags": ["yolqoidalari", "prava", "chorraha", "haydovchilik", "toshkent", "pravaimtihon"],
    },
]


class Director:
    def __init__(self, brain: Brain, model: str = "claude-sonnet-5-5"):
        self.brain = brain
        self.model = model

    def concept(self, context: dict, scenes: int, task: dict | None = None,
                rng: random.Random | None = None) -> Concept:
        rng = rng or random.Random()
        recent_formats = [p.get("format") for p in context.get("posts", [])[:4]]
        formats = "\n".join(f"- {k}: {v}" for k, v in FORMATS)
        prompt = (
            f"Sahnalar soni: {scenes} (har biri ≈6 soniya).\n\n"
            + (f"TOPSHIRIQ (ustuvor, shunga amal qil): {task['text']}\n\n" if task else "")
            + (f"MARKETOLOG BUYRUG'I (auditoriya, xabar, format — shunga moslab yoz): "
               f"{json.dumps(context['directive'], ensure_ascii=False)}\n\n" if context.get("directive") else "")
            + f"FORMATLAR (birini tanla yoki aralashtir; oxirgi ishlatilganlar: {recent_formats}):\n{formats}\n\n"
            f"OLDINGI VIDEOLAR NATIJASI (yangidan eskiga, score = ko'rish+3×layk+8×izoh+12×saqlash+12×ulashish):\n"
            f"{json.dumps(context.get('posts', [])[:10], ensure_ascii=False)}\n\n"
            f"ODAMLAR QAYERDAN KELYAPTI (7 kun): {json.dumps(context.get('sources', {}), ensure_ascii=False)}\n"
            f"BOTDA ENG KO'P XATO QILINAYOTGAN MAVZULAR: {json.dumps(context.get('hard_topics', []), ensure_ascii=False)}\n"
            f"SHU HAFTA TRENDLAR: {context.get('trends') or 'maʼlumot yoʻq'}\n\n"
            "Javob JSON sxemasi:\n"
            '{"format": "kod", "title": "2-3 so\'z", "hook_text": "1-sahnadagi ekran matni, 3-7 so\'z",'
            ' "scenes": [{"visual": "English prompt", "voice": "o\'zbekcha jumla", "caption": "2-6 so\'z", "seconds": 6}],'
            ' "reveal_voice": "oxirgi kadrda Chorraha haqida 1 jumla", "card_title": "3-5 so\'z",'
            ' "card_sub": "3-6 so\'z", "caption": "post matni: savol bilan boshlang, izohga chaqiring, oxirida \'havola profilda\'",'
            ' "hashtags": ["6-8 ta"], "reason": "nega aynan shu konsept (raqamlarga tayan, 1-2 gap)"}'
        )
        data = Brain.parse_json(self.brain.ask("director", prompt, max_tokens=1800, system=SYSTEM, model=self.model))
        c = self._from_dict(data, scenes) if data else None
        if c:
            c.by_ai = True
            c.task_id = task["id"] if task else None
            return c
        return self.fallback(scenes, recent_formats, rng, task)

    @staticmethod
    def _clean_tags(tags: Any) -> list[str]:
        out = [re.sub(r"[^\w]", "", str(t).lstrip("#")) for t in (tags or [])]
        return [t for t in out if t][:8] or ["prava", "pravaimtihon", "haydovchilik"]

    def _from_dict(self, d: dict, scenes: int) -> Concept | None:
        try:
            sc = [Scene(visual=str(s["visual"])[:900], voice=str(s["voice"])[:220], caption=str(s.get("caption", ""))[:60],
                        seconds=int(s.get("seconds", 6) or 6)) for s in d["scenes"] if s.get("visual")]
            if len(sc) < 2:
                return None
            return Concept(
                format=str(d.get("format", "ai"))[:30], title=str(d.get("title", ""))[:60],
                hook_text=str(d.get("hook_text", ""))[:70], scenes=sc[:max(2, scenes)],
                reveal_voice=str(d.get("reveal_voice", ""))[:220] or "Chorraha — prava imtihoniga aqlli tayyorgarlik.",
                card_title=str(d.get("card_title", ""))[:50] or "Imtihonga tayyormisiz?",
                card_sub=str(d.get("card_sub", ""))[:60], caption=str(d.get("caption", ""))[:1800],
                hashtags=self._clean_tags(d.get("hashtags")), reason=str(d.get("reason", ""))[:400])
        except (KeyError, TypeError, ValueError):
            return None

    def fallback(self, scenes: int, recent: list, rng: random.Random, task: dict | None) -> Concept:
        pool = [t for t in TEMPLATES if t["format"] not in recent] or TEMPLATES
        t = rng.choice(pool)
        c = self._from_dict(t, scenes)
        assert c is not None
        c.scenes = c.scenes[:max(2, scenes)]
        c.reason = "Shablon konsept (AI o'chiq yoki budjet tugagan)"
        c.task_id = None  # shablon topshiriqni bajara olmaydi — topshiriq navbatda qoladi
        return c
