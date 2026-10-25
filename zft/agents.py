"""ZFT Jarvislari: 5 ta agent bitta jamoa.

  Bosh direktor — hammasini boshqaradi, vazifa beradi, natijani tekshiradi, egaga hisobot beradi
  Media        — natijalar, trendlar, odamlar qayerdan kelyapti; Studiyaga brif
  Moliya       — barcha xarajat va kirim, budjet taqsimoti, prognoz, ogohlantirish
  Studiya      — AI video yasaydi
  Nashr        — Instagram, TikTok, Telegram kanal va guruhga joylaydi

Ish tartibi: bir vaqtda faqat BITTA Jarvis ishlaydi, qolganlari dam oladi. Har bir topshiriq va
javob `agent_messages` jadvaliga yoziladi — bu ularning suhbati (panelda jonli ko'rinadi).
"""
from __future__ import annotations

import calendar
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from .brain import Brain
from .db import now
from .pipeline import DAY, TIER_NAMES, score

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)

AGENTS = {
    "direktor": {"name": "Bosh direktor", "role": "Boshqaradi: vazifa taqsimoti, tasdiq nazorati, yangi Jarvis ishga olish"},
    "marketolog": {"name": "Marketolog", "role": "Strategiya: bozor, auditoriya, har video yo'nalishi, videolar tahlili (nima va nega)"},
    "smm": {"name": "SMMchi", "role": "Matn: post matni, hook, CTA, hashtag (Asror uslubi + anti-AI)"},
    "moliya": {"name": "Moliya", "role": "Pul: xarajat, kirim, budjet taqsimoti, prognoz"},
    "media": {"name": "Media", "role": "Raqamlar: ko'rishlar, manbalar, botdagi qiyin savollar"},
    "studiya": {"name": "Studiya", "role": "Video: ssenariy, AI sahnalar, ovoz, montaj"},
    "nashr": {"name": "Nashr", "role": "Joylash: Instagram, TikTok, Telegram kanal va guruh"},
    "hisobot": {"name": "Hisobotchi", "role": "Hisobot: barcha bo'limlardan yig'ib Telegram'ga yuboradi"},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_messages (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     INTEGER NOT NULL,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    text   TEXT NOT NULL,
    data   TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_messages_at ON agent_messages(at);

CREATE TABLE IF NOT EXISTS post_reviews (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL UNIQUE,
    at      INTEGER NOT NULL,
    verdict TEXT,               -- yaxshi | o'rtacha | kam
    text    TEXT,
    data    TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    at    INTEGER NOT NULL,
    kind  TEXT NOT NULL,        -- daily | weekly | manual
    title TEXT,
    text  TEXT
);

CREATE TABLE IF NOT EXISTS custom_agents (
    key          TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    role         TEXT NOT NULL,
    instructions TEXT NOT NULL,
    daily_hour   INTEGER,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       INTEGER NOT NULL,
    kind     TEXT NOT NULL,      -- income | expense
    amount   REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'UZS',
    note     TEXT
);
"""

MARKET_SYSTEM = """Sen ZFT jamoasining Marketologisan. Mahsulot: "Chorraha" — O'zbekistonda haydovchilik guvohnomasi
(prava) nazariy imtihoniga tayyorlov: chorrahaa.uz (rasmiy savollar, o'zbekcha ovozli tushuntirish, AI yordamchi,
3D simulyator), Telegram bot, PRAVA QUIZ guruhi. Kanallar: Instagram Reels, TikTok, Telegram.
Qoidalar: faqat topilgan yoki berilgan ma'lumotga tayan, manbasiz raqam yozma; raqobatchilarni kamsitish yo'q;
soxta sharh, real shaxslar va aldov usullari taklif qilinmaydi. Javob faqat JSON, o'zbek tilida (lotin)."""

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def skill_text(name: str, limit: int = 9000) -> str:
    """Skill faylini o'qiydi (frontmatter'siz)."""
    p = SKILLS_DIR / f"{name}.md"
    if not p.exists():
        return ""
    t = p.read_text(encoding="utf-8")
    if t.startswith("---"):
        t = t.split("---", 2)[-1]
    return t.strip()[:limit]


SMM_SYSTEM_HEAD = """Sen ZFT jamoasining SMMchisisan: "Chorraha" (prava nazariy imtihoniga tayyorgarlik — chorrahaa.uz va Telegram bot)
uchun Instagram/TikTok/Telegram post matnlarini yozasan. Asosiy uslub manbang — quyidagi "Asror" skilli
(raqamli hook, to'g'ridan-to'g'ri "siz" murojaati, jonli og'zaki o'zbekcha, ikki yo'lli CTA: qattiq so'rov + yengil
"layk bosib qo'ying" chiqish yo'li). Asror haqida biografik fakt to'qima, uning nomini matnga yozma — faqat uslub
naqshlaridan foydalan. Yozilgan matnga (caption) "anti-ai-writing" skillining TO'LIQ tekshiruvini qo'lla:
em-dash yo'q, bo'sh "X emas, Y" yo'q, hype va bo'sh so'zlar yo'q, aniq detal bor. Soxta sharh, to'qima statistika,
kafolat yo'q. Javob faqat JSON."""

DIREKTOR_SYSTEM = """Sen ZFT tizimining Bosh direktori — "Chorraha" (prava imtihoniga tayyorgarlik: chorrahaa.uz,
Telegram bot) marketingini boshqaradigan AI jamoaning rahbarisan. Jamoang: Marketolog (bozor va strategiya), SMMchi (post matni), Media (tahlil), Moliya (pul), Hisobotchi (hisobotlar),
Studiya (AI video), Nashr (joylash). Ega bilan qisqa, aniq, o'zbek tilida gaplashasan.
Faqat berilgan raqamlarga tayan, bilmagan narsani to'qima. Pul sarflash, budjet va narx o'zgarishlari
faqat egasining ruxsati bilan. Ega yangi video g'oyasi aytsa — uni Studiya uchun topshiriq qil.
Ega yangi xodim (Jarvis) ishga olishni so'rasa — "hire" maydonini to'ldir.
Javob JSON: {"reply": "egaga javob (1-4 gap)", "task": "Studiya uchun topshiriq matni yoki null",
 "hire": {"name": "qisqa nom", "role": "vazifasi 1 gap", "instructions": "batafsil ko'rsatma", "daily_hour": soat yoki null} yoki null}"""


class Hub:
    def __init__(self, app: "App"):
        self.app = app
        self.db = app.db
        self.db.conn.executescript(SCHEMA)
        self._lock = threading.RLock()
        self._active: dict[str, Any] = {"agent": None, "doing": None, "since": None}

    # ------------------------------------------------------------ suhbat
    def say(self, sender: str, recipient: str, text: str, data: Any = None) -> int:
        if data is not None and not isinstance(data, str):
            data = json.dumps(data, ensure_ascii=False, default=str)
        cur = self.db.execute("INSERT INTO agent_messages(at, sender, recipient, text, data) VALUES (?,?,?,?,?)",
                              (now(), sender, recipient, text[:2000], data))
        return int(cur.lastrowid)

    def messages(self, since_id: int = 0, limit: int = 80) -> list[dict]:
        rows = self.db.all("SELECT * FROM agent_messages WHERE id>? ORDER BY id DESC LIMIT ?", (since_id, limit))
        return [dict(r) for r in reversed(rows)]

    @contextmanager
    def turn(self, agent: str, doing: str) -> Iterator[None]:
        """Faqat bitta Jarvis ishlaydi: boshqasi navbat kutadi (dam oladi)."""
        with self._lock:
            prev = dict(self._active)
            self._active = {"agent": agent, "doing": doing, "since": now()}
            self.db.set(f"agent_last:{agent}", json.dumps({"doing": doing, "at": now()}, ensure_ascii=False))
            try:
                yield
            finally:
                self._active = prev if prev.get("agent") and prev["agent"] != agent else \
                    {"agent": None, "doing": None, "since": None}

    def all_agents(self) -> dict[str, dict]:
        out = {k: dict(v) for k, v in AGENTS.items()}
        for r in self.db.all("SELECT * FROM custom_agents WHERE active=1 ORDER BY created_at"):
            out[r["key"]] = {"name": r["name"], "role": r["role"], "custom": True, "daily_hour": r["daily_hour"]}
        return out

    def status(self) -> list[dict]:
        day0 = self.app.local_day_start()
        out = []
        for key, meta in self.all_agents().items():
            last = json.loads(self.db.get(f"agent_last:{key}") or "{}")
            sent = self.db.scalar("SELECT COUNT(*) FROM agent_messages WHERE sender=? AND at>=?", (key, day0))
            out.append({"key": key, **meta, "active": self._active.get("agent") == key,
                        "doing": self._active.get("doing") if self._active.get("agent") == key else None,
                        "last_doing": last.get("doing"), "last_at": last.get("at"), "messages_today": sent})
        return out

    # -------------------------------------------------------- MARKETOLOG
    def strategy(self) -> dict:
        try:
            return json.loads(self.db.get("market_strategy") or "{}")
        except ValueError:
            return {}

    def market_research(self) -> dict:
        """Haftalik bozor tahlili (internet qidiruvi bilan) → strategiya → Media va Studiyaga buyruqlar."""
        with self.turn("marketolog", "Bozorni o'rganyapti: raqobatchilar, auditoriya, trendlar"):
            ctx = self.app.pipeline.context()
            prompt = (
                "Internetdan qidirib, shu hafta uchun marketing strategiyasi tuz.\n"
                "1) O'zbekistonda prava nazariy imtihoniga tayyorlaydigan raqobatchilar (sayt, ilova, Telegram bot, "
                "Instagram/TikTok akkauntlar): ular nima taklif qiladi, qanday kontent qiladi, zaif tomoni.\n"
                "2) Auditoriya segmentlari va ularning og'riqlari (imtihondan yiqilish qo'rquvi, vaqt, til, narx...).\n"
                "3) Mavsumiylik va yangiliklar (imtihon tartibi o'zgarishi, bayramlar, o'qish mavsumi).\n"
                "4) Shu hafta qisqa videolarda (TikTok, Reels) qanday format va hook uslublari trendda, ularni prava "
                "mavzusiga qanday moslash mumkin.\n"
                "5) Bizning natijalarimiz: " + json.dumps({"posts": ctx["posts"][:8], "sources": ctx["sources"],
                                                         "hard_topics": ctx["hard_topics"]}, ensure_ascii=False) + "\n\n"
                'JSON: {"summary": "3-4 gap", "competitors": [{"name": "", "what": "", "weakness": ""}],'
                ' "audience": [{"segment": "", "pain": ""}], "pillars": ["3-5 ta kontent yo\'nalishi"],'
                ' "media_watch": ["Media kuzatadigan 2-4 narsa"], "studio_tasks": ["3-5 ta aniq video g\'oyasi"],'
                ' "offer_ideas": ["1-3 ta taklif/aksiya g\'oyasi egaga"]}')
            data = Brain.parse_json(self.app.brain.ask("market_research", prompt, max_tokens=1800, use_search=True,
                                                      system=MARKET_SYSTEM, model=self.app.settings.director_model))
            if not data:
                data = {"summary": "Internet tahlili bajarilmadi (AI kaliti yo'q yoki budjet tugagan). Asosiy "
                                   "yo'nalishlar saqlanib qoldi.",
                        "pillars": ["Imtihon hayajoni va g'alaba hikoyalari", "Chorrahadagi vaziyatlar: siz nima qilardingiz?",
                                    "Oilaviy va do'stlar skitlari", "Mif yoki haqiqat"],
                        "media_watch": ["Qaysi format ko'proq saqlanmoqda", "TikTok va Instagram'dan kelayotgan odamlar soni"],
                        "studio_tasks": [], "competitors": [], "audience": [], "offer_ideas": []}
            data["updated_at"] = now()
            self.db.set("market_strategy", json.dumps(data, ensure_ascii=False))
            notes = str(data.get("summary", ""))
            if data.get("pillars"):
                notes += " Yo'nalishlar: " + "; ".join(map(str, data["pillars"]))
            self.db.set("trend_notes", notes[:3000])
            self.say("marketolog", "direktor", "Haftalik bozor tahlili: " + str(data.get("summary", ""))[:900], data)
            if data.get("media_watch"):
                self.say("marketolog", "media", "Shu hafta kuzating: " + "; ".join(map(str, data["media_watch"]))[:600])
            for t in (data.get("studio_tasks") or [])[:5]:
                tid = self.app.pipeline.add_task(str(t), source="marketolog")
                self.say("marketolog", "studiya", f"Topshiriq #{tid}: «{str(t)[:280]}»")
            if data.get("offer_ideas"):
                self.say("marketolog", "ega", "Taklif g'oyalari (sizning qaroringiz): " + "; ".join(map(str, data["offer_ideas"]))[:600])
            return data

    def market_directive(self, brief: dict) -> dict:
        """Har video oldidan: Media brifi + strategiya → Studiyaga aniq buyruq."""
        with self.turn("marketolog", "Bugungi video uchun yo'nalish belgilayapti"):
            st = self.strategy()
            pillars = st.get("pillars") or ["Imtihon hayajoni va g'alaba hikoyalari", "Chorrahadagi vaziyatlar",
                                            "Oilaviy va do'stlar skitlari", "Mif yoki haqiqat"]
            data = Brain.parse_json(self.app.brain.ask(
                "market_directive",
                "Strategiya: " + json.dumps({k: st.get(k) for k in ("summary", "pillars", "audience")}, ensure_ascii=False)
                + "\nMedia brifi: " + brief.get("text", "") +
                '\n\nBugungi bitta video uchun buyruq. JSON: {"audience": "kim uchun", "message": "asosiy fikr 1 gap",'
                ' "pillar": "yo\'nalish", "format": "format kodi yoki tavsifi", "hook_angle": "hook g\'oyasi",'
                ' "media_watch": "Media shu videoda nimani o\'lchasin"}',
                max_tokens=350, system=MARKET_SYSTEM))
            if not data:
                k = int(self.db.get("pillar_idx") or 0)
                self.db.set("pillar_idx", k + 1)
                best = max(brief.get("fmt_avg", {}).items(), key=lambda x: x[1])[0] if brief.get("fmt_avg") else None
                data = {"audience": "prava olishni rejalashtirayotgan 17–30 yoshlilar",
                        "message": "Imtihon qo'rqinchli emas, agar oldindan tushunib tayyorlansang",
                        "pillar": pillars[k % len(pillars)], "format": best or "hikoya",
                        "hook_angle": "birinchi 2 soniyada kutilmagan holat",
                        "media_watch": "saqlash va ulashish soni"}
            self.db.set("market_directive", json.dumps(data, ensure_ascii=False))
            self.say("marketolog", "studiya",
                     f"Buyruq: auditoriya — {data.get('audience')}; yo'nalish — {data.get('pillar')}; asosiy fikr — "
                     f"{data.get('message')}; format — {data.get('format')}; hook — {data.get('hook_angle')}.", data)
            self.say("marketolog", "media", f"Bu videoda o'lchang: {data.get('media_watch')}.")
            return data

    # -------------------------------------------------------- MARKETOLOG: video tahlili
    def market_review(self, min_age_hours: int = 36) -> list[dict]:
        """Joylangan har videoni tahlil qiladi: qancha ko'rildi, nega kam/ko'p, qanday yaxshilash."""
        out = []
        rows = self.db.all("SELECT p.* FROM posts p LEFT JOIN post_reviews r ON r.post_id=p.id WHERE p.status='published'"
                           " AND r.id IS NULL AND p.published_at<=? ORDER BY p.id LIMIT 6", (now() - min_age_hours * 3600,))
        if not rows:
            return out
        with self.turn("marketolog", "Joylangan videolarni tahlil qilyapti"):
            hist = []
            for r in self.db.all("SELECT * FROM posts WHERE status='published' ORDER BY id DESC LIMIT 30"):
                m = self.db.latest_metrics(r["id"])
                hist.append({"id": r["id"], "score": score(m), "views": sum(v.get("views", 0) for v in m.values())})
            scores = sorted(h["score"] for h in hist) or [0]
            median = scores[len(scores) // 2]
            for p in rows:
                m = self.db.latest_metrics(p["id"])
                sc, views = score(m), sum(v.get("views", 0) for v in m.values())
                saves = sum(v.get("saves", 0) for v in m.values())
                shares = sum(v.get("shares", 0) for v in m.values())
                verdict = "yaxshi" if sc >= median * 1.3 and sc > 0 else ("kam" if sc < median * 0.7 or sc == 0 else "o'rtacha")
                concept = json.loads(p["concept"] or "{}") if p["concept"] else {}
                facts = {"id": p["id"], "hook": p["hook"], "format": p["format"], "tier": p["tier"], "slot": p["slot"],
                         "metrics": m, "score": sc, "median_score": median, "views": views, "saves": saves,
                         "shares": shares, "scenes": len(concept.get("scenes", [])), "caption": (p["caption"] or "")[:300]}
                data = Brain.parse_json(self.app.brain.ask(
                    "market_review", "Joylangan video natijasi: " + json.dumps(facts, ensure_ascii=False) +
                    '\n\nTahlil qil. JSON: {"why": "nega shunday natija (raqamga tayan, 1-2 gap)",'
                    ' "improve": ["keyingi videoda aniq 2-3 o\'zgarish"], "keep": "nima takrorlansin yoki null"}',
                    max_tokens=400, system=MARKET_SYSTEM))
                if not data:
                    why = []
                    if not m:
                        why.append("Raqamlar hali yig'ilmagan (Instagram/TikTok ulanmagan yoki 48 soat o'tmagan)")
                    elif verdict == "kam":
                        why.append(f"Ball {sc}, o'rtacha {median}: birinchi 2 soniya ushlab qolmagan bo'lishi mumkin")
                        if views and saves / max(views, 1) < 0.005:
                            why.append("saqlash juda kam — foydali ma'lumot yetishmagan")
                    elif verdict == "yaxshi":
                        why.append(f"Ball {sc}, o'rtachadan {round(sc / max(median, 1), 1)} baravar yuqori")
                    else:
                        why.append(f"Ball {sc}, o'rtacha darajada")
                    data = {"why": "; ".join(why) + ".",
                            "improve": ["Hookni 1-soniyada savol yoki kutilmagan holat bilan boshlang",
                                        "Oxirida aniq harakat: izohga javob yozish yoki botga o'tish"] if verdict != "yaxshi" else [],
                            "keep": f"«{p['format']}» formati va «{p['hook']}» uslubi" if verdict == "yaxshi" else None}
                text = (f"#{p['id']} «{p['hook']}» — {verdict}: {views} ko'rish, {saves} saqlash, {shares} ulashish. "
                        f"Sabab: {data.get('why')}")
                if data.get("improve"):
                    text += " Yaxshilash: " + "; ".join(map(str, data["improve"]))[:400] + "."
                if data.get("keep"):
                    text += f" Saqlab qolish: {data['keep']}."
                self.db.execute("INSERT OR REPLACE INTO post_reviews(post_id, at, verdict, text, data) VALUES (?,?,?,?,?)",
                                (p["id"], now(), verdict, text, json.dumps({**facts, **data}, ensure_ascii=False)))
                self.say("marketolog", "studiya", text)
                if data.get("improve"):
                    self.say("marketolog", "smm", f"#{p['id']} bo'yicha matn va CTA uchun: " + "; ".join(map(str, data["improve"]))[:300])
                out.append({"post_id": p["id"], "verdict": verdict, "text": text})
        return out

    def reviews_context(self, limit: int = 5) -> list[dict]:
        return [{"post": r["post_id"], "verdict": r["verdict"], "text": r["text"][:300]}
                for r in self.db.all("SELECT * FROM post_reviews ORDER BY id DESC LIMIT ?", (limit,))]

    # ---------------------------------------------------------------- SMM
    def smm_polish(self, post_id: int) -> dict | None:
        """SMMchi: post matni, hashtag va CTA (Asror uslubi + anti-AI tozalash)."""
        p = self.db.post(post_id)
        if not p or p["tier"] == "quiz" and not p["caption"]:
            return None
        with self.turn("smm", "Post matnini yozyapti (Asror uslubi + anti-AI)"):
            concept = json.loads(p["concept"] or "{}") if p["concept"] else {}
            system = (SMM_SYSTEM_HEAD + "\n\n=== SKILL: asror ===\n" + skill_text("asror")
                      + "\n\n=== SKILL: anti-ai-writing ===\n" + skill_text("anti-ai-writing"))
            prompt = ("Video: " + json.dumps({"hook": p["hook"], "format": p["format"], "title": concept.get("title"),
                                              "scenes": [s.get("caption") for s in concept.get("scenes", [])],
                                              "draft_caption": p["caption"]}, ensure_ascii=False)
                      + "\nMarketolog izohlari: " + json.dumps(self.reviews_context(3), ensure_ascii=False)
                      + ("\n" + self.app.smm.hashtag_hint() if self.app.smm.hashtag_hint() else "")
                      + '\n\nInstagram/TikTok caption yoz. JSON: {"caption": "3-6 qator, 1-qator kuchli hook, oxirida qattiq CTA'
                        ' (izohga so\'z yozish yoki profildagi havola) + yengil chiqish yo\'li (layk)", "hashtags": ["5-8 ta"],'
                        ' "first_comment": "post ostiga birinchi izoh (savol)", "audit": "anti-AI tekshiruvi: nima tuzatildi, 1 gap"}')
            data = Brain.parse_json(self.app.brain.ask("smm_caption", prompt, max_tokens=700, system=system,
                                                       model=self.app.settings.director_model))
            if data and data.get("caption"):
                caption = clean_caption(str(data["caption"]))[:2000]
                tags = [t.lstrip("#").strip() for t in (data.get("hashtags") or []) if str(t).strip()][:8]
                self.db.update_post(post_id, caption=caption,
                                    hashtags=json.dumps(tags or json.loads(p["hashtags"] or "[]"), ensure_ascii=False),
                                    first_comment=clean_caption(str(data.get("first_comment") or ""))[:300] or None)
                note = f"#{post_id} matni tayyor. Anti-AI: {str(data.get('audit') or 'tozalandi')[:200]}"
            else:
                base = clean_caption(p["caption"] or "")
                if "layk" not in base.lower():
                    base = (base + "\n\nBotga o'tish uchun profildagi havolani bosing. Hozircha shart emas desangiz, "
                                   "shu videoga bitta layk bosib qo'ying.").strip()
                self.db.update_post(post_id, caption=base[:2000])
                data = {"caption": base}
                note = f"#{post_id} matniga ikki yo'lli CTA qo'shildi va tinish belgilari tozalandi (AI'siz rejim)."
            self.say("smm", "direktor", note)
            return data

    # ----------------------------------------------------------- HISOBOTCHI
    def _store_report(self, kind: str, title: str, text: str) -> int:
        cur = self.db.execute("INSERT INTO reports(at, kind, title, text) VALUES (?,?,?,?)", (now(), kind, title, text))
        return int(cur.lastrowid)

    def daily_report(self) -> None:
        with self.turn("hisobot", "Bo'limlardan ma'lumot yig'yapti"):
            self.say("hisobot", "moliya", "Kunlik moliyaviy xulosani bering.")
        with self.turn("moliya", "Kunlik hisobot"):
            ft = self.finance_text()
            self.say("moliya", "hisobot", ft)
        with self.turn("hisobot", "Media va Marketologdan xulosa so'rayapti"):
            self.say("hisobot", "marketolog", "Bugungi video tahlillari bormi?")
        reviews = self.market_review()
        with self.turn("hisobot", "Kunlik hisobotni tayyorlab, Telegram'ga yuboryapti"):
            extra = f"💵 <b>Moliya:</b> {ft}"
            if reviews:
                extra += "\n\n📈 <b>Marketolog — videolar tahlili:</b>\n" + "\n".join("• " + r["text"][:400] for r in reviews)
            text = self.app.reports.send_daily_report("schedule", extra=extra)
            self._store_report("daily", "Kunlik hisobot " + self.app.local_now().strftime("%d.%m.%Y"), text or extra)
            self.say("hisobot", "ega", "Kunlik hisobot Telegram'ga yuborildi.")
            self.say("hisobot", "direktor", "Kunlik hisobot egaga yetkazildi.")

    def weekly(self) -> None:
        self.market_research()  # trendlar ham shu tahlil ichida (bitta internet qidiruvi — ortiqcha xarajat yo'q)
        with self.turn("hisobot", "Haftalik hisobotni tayyorlayapti"):
            text = self.app.reports.send_weekly_report()
            reviews = self.db.all("SELECT verdict, COUNT(*) c FROM post_reviews WHERE at>=? GROUP BY verdict",
                                  (now() - 7 * DAY,))
            summ = ", ".join(f"{r['verdict']}: {r['c']}" for r in reviews)
            self._store_report("weekly", "Haftalik hisobot " + self.app.local_now().strftime("%d.%m.%Y"),
                               (text or "") + (f"\n\nVideolar bahosi: {summ}" if summ else ""))
            self.say("hisobot", "ega", "Haftalik hisobot yuborildi: nima ishladi, nima ishlamadi, trendlar.")

    def report_on(self, section: str) -> str:
        """Ega so'ragan bo'lim bo'yicha alohida hisobot."""
        with self.turn("hisobot", f"«{section}» bo'yicha hisobot tayyorlayapti"):
            if section == "moliya":
                text = "💵 Moliya: " + self.finance_text()
            elif section == "marketolog":
                st = self.strategy()
                rv = self.reviews_context(6)
                text = "📈 Marketolog: " + str(st.get("summary") or "strategiya yo'q") + "\n" + \
                    "\n".join("• " + r["text"] for r in rv)
            elif section == "media":
                text = self.app.reports.sources_text()
            else:
                text = self.app.reports.today_summary()
            self._store_report("manual", f"{AGENTS.get(section, {}).get('name', section)} hisoboti", text)
            try:
                if self.app.settings.owner_id:
                    self.app.tg.send_message(self.app.settings.owner_id, text[:3900])
            except Exception:  # noqa: BLE001
                log.warning("Hisobot yuborilmadi")
            self.say("hisobot", "ega", f"«{AGENTS.get(section, {}).get('name', section)}» hisoboti Telegram'ga yuborildi.")
            return text

    # ------------------------------------------------------------- MEDIA
    def media_brief(self, slot: int | None = None) -> dict:
        with self.turn("media", "Natijalarni tahlil qilyapti"):
            ctx = self.app.pipeline.context()
            posts = ctx["posts"]
            best = max(posts, key=lambda p: p["score"]) if posts else None
            by_fmt: dict[str, list[int]] = {}
            for p in posts:
                by_fmt.setdefault(p.get("format") or "?", []).append(p["score"])
            fmt_avg = {k: round(sum(v) / len(v)) for k, v in by_fmt.items()}
            top_src = max(ctx["sources"].items(), key=lambda x: x[1]) if ctx["sources"] else None
            parts = []
            if best:
                parts.append(f"Eng yaxshi video #{best['id']} «{best['hook']}» — {best['score']} ball.")
            if fmt_avg:
                f = max(fmt_avg, key=fmt_avg.get)
                parts.append(f"Formatlar bo'yicha o'rtacha: eng kuchlisi «{f}» ({fmt_avg[f]} ball).")
            else:
                parts.append("Hali natija yo'q — turli formatlarni sinashni taklif qilaman.")
            if top_src:
                parts.append(f"7 kunda eng ko'p odam «{self.app.reports.label(top_src[0])}» dan keldi ({top_src[1]}).")
            if ctx["hard_topics"]:
                t = ctx["hard_topics"][0]
                parts.append(f"Botda eng ko'p xato: «{t['question'][:70]}» ({int(t['wrong_rate'] * 100)}%).")
            watch = self.strategy().get("media_watch")
            if watch:
                parts.append("Marketolog so'ragan kuzatuv: " + "; ".join(map(str, watch[:3])) + ".")
            text = " ".join(parts)
            self.say("media", "direktor", text, {"fmt_avg": fmt_avg})
            return {"text": text, "fmt_avg": fmt_avg}

    def media_collect(self) -> int:
        with self.turn("media", "Instagram va TikTok raqamlarini yig'yapti"):
            n = self.app.pipeline.collect_metrics()
            self.say("media", "direktor", f"{n} ta video bo'yicha yangi raqamlar yig'ildi.")
            return n

    def media_trends(self) -> None:
        with self.turn("media", "Haftalik trendlarni qidiryapti"):
            t = self.app.brain.weekly_trends()
            self.say("media", "studiya", "Shu hafta trendlar:\n" + t[:1200] if t else
                     "Trend qidiruvi bajarilmadi (AI kaliti yo'q yoki budjet).")

    # ------------------------------------------------------------ MOLIYA
    def finance(self) -> dict:
        app = self.app
        at = app.local_now()
        days = calendar.monthrange(at.year, at.month)[1]
        server = float(os.environ.get("SERVER_MONTHLY_USD", "6.5"))
        video = app.vbudget.spent(at)
        ai = app.brain.spent()
        server_so_far = server * at.day / days
        total = video + ai + server_so_far
        plan = app.vbudget.monthly + app.brain.budget + server
        forecast = total / at.day * days if at.day else total
        m0 = app.vbudget.month_start(at)
        led = {r["kind"] + ":" + r["currency"]: r["s"] for r in self.db.all(
            "SELECT kind, currency, SUM(amount) s FROM ledger WHERE at>=? GROUP BY kind, currency", (m0,))}
        new_users = self.db.scalar("SELECT COUNT(*) FROM users WHERE joined_at>=?", (m0,))
        return {"video": round(video, 2), "ai": round(ai, 2), "server": round(server_so_far, 2),
                "total": round(total, 2), "plan": round(plan, 2), "forecast": round(forecast, 2),
                "video_budget": app.vbudget.monthly, "ai_budget": app.brain.budget, "server_monthly": server,
                "ledger": led, "new_users": new_users,
                "cost_per_user": round(total / new_users, 3) if new_users else None,
                "day": at.day, "days": days}

    def finance_text(self, f: dict | None = None) -> str:
        f = f or self.finance()
        lines = [f"Shu oy sarflandi: {f['total']:.2f}$ (reja {f['plan']:.0f}$). Video {f['video']:.2f}$, "
                 f"AI matn {f['ai']:.2f}$, server {f['server']:.2f}$.",
                 f"Oy oxirigacha prognoz: {f['forecast']:.2f}$."]
        if f["cost_per_user"] is not None:
            lines.append(f"1 yangi foydalanuvchi narxi: {f['cost_per_user']:.2f}$ ({f['new_users']} kishi).")
        inc = {k.split(":")[1]: v for k, v in f["ledger"].items() if k.startswith("income")}
        exp = {k.split(":")[1]: v for k, v in f["ledger"].items() if k.startswith("expense")}
        if inc or exp:
            lines.append("Qo'lda kiritilgan: kirim " + (", ".join(f"{v:,.0f} {c}" for c, v in inc.items()) or "0")
                         + "; xarajat " + (", ".join(f"{v:,.0f} {c}" for c, v in exp.items()) or "0") + ".")
        if f["forecast"] > f["plan"] * 1.05:
            lines.append("⚠️ Prognoz rejadan oshyapti — Studiya arzonroq formatga o'tadi yoki budjetni oshiring.")
        return " ".join(lines)

    def moliya_check(self, slot: int) -> dict:
        with self.turn("moliya", "Budjetni hisoblayapti"):
            last = slot == len(self.app.settings.slot_minutes()) - 1
            c = self.app.vbudget.choose(self.app.local_now(), last, self.app.settings.studio_enabled)
            v = self.app.vbudget.summary(self.app.local_now())
            text = (f"{TIER_NAMES.get(c.tier, c.tier)} tavsiya qilaman (≈{c.est_cost:.2f}$). {c.reason}. "
                    f"Oy: {v['spent']:.2f}$ / {v['monthly']:.0f}$ sarflandi.")
            self.say("moliya", "direktor", text, {"tier": c.tier, "cost": c.est_cost})
            return {"tier": c.tier, "cost": c.est_cost}

    def add_ledger(self, kind: str, amount: float, currency: str, note: str, category: str | None = None) -> int:
        from .control import categorize
        cat = category or categorize(note, kind)
        cur = self.db.execute("INSERT INTO ledger(at, kind, amount, currency, note, category) VALUES (?,?,?,?,?,?)",
                              (now(), kind, amount, currency.upper()[:5], note[:200], cat))
        self.db.audit("owner", f"ledger_{kind}", {"amount": amount, "currency": currency, "note": note[:100]})
        with self.turn("moliya", "Hisobga yozyapti"):
            self.say("moliya", "direktor", f"{'Kirim' if kind == 'income' else 'Xarajat'} yozildi: "
                                           f"{amount:,.0f} {currency.upper()} — {note or 'izohsiz'}.")
        return int(cur.lastrowid)

    # ------------------------------------------------------- DIREKTOR
    def prep_slot(self, slot: int) -> int | None:
        """Slot oldidan butun jamoa ishi: Media → Moliya → Studiya → Direktor tekshiruvi."""
        p = self.app.pipeline
        at = p.slot_time(slot, self.app.local_now().date())
        if self.db.one("SELECT id FROM posts WHERE publish_at=? AND status IN ('pending','approved','published')",
                       (int(at.timestamp()),)):
            return None
        with self.turn("direktor", f"{at.strftime('%H:%M')} sloti uchun reja tuzyapti"):
            self.say("direktor", "media", f"{at.strftime('%H:%M')} videosi uchun brif tayyorlang.")
        brief = self.media_brief(slot)
        with self.turn("direktor", "Marketologdan yo'nalish so'rayapti"):
            self.say("direktor", "marketolog", "Media brifi tayyor. Bugungi video yo'nalishini belgilang.")
        self.market_directive(brief)
        with self.turn("direktor", "Budjetni so'rayapti"):
            self.say("direktor", "moliya", "Bu slot uchun qancha sarflashimiz mumkin?")
        self.moliya_check(slot)
        task = p.open_task()
        with self.turn("direktor", "Studiyaga vazifa beryapti"):
            self.say("direktor", "studiya", "Video yasang. " + (f"Egamizning topshirig'i: «{task['text']}»." if task
                                                               else "Topshiriq yo'q — o'zingiz eng yaxshi formatni tanlang."))
        return self._studio_make(lambda: p.create_slot_content(slot))

    def _studio_make(self, fn) -> int | None:
        with self.turn("studiya", "Video yasayapti: ssenariy, sahnalar, montaj"):
            self.app.pipeline.defer_review = True
            try:
                pid = fn()
            finally:
                self.app.pipeline.defer_review = False
            post = self.db.post(pid) if pid else None
            if post:
                self.say("studiya", "direktor",
                         f"#{pid} tayyor: {TIER_NAMES.get(post['tier'], post['tier'])}, «{post['hook']}», "
                         f"{(post['cost_usd'] or 0):.2f}$.", {"post": pid})
            else:
                self.say("studiya", "direktor", "Video yasalmadi — xato tafsiloti egaga yuborildi.")
        if post:
            with self.turn("direktor", "SMMchiga matn buyuryapti"):
                self.say("direktor", "smm", f"#{pid} uchun post matni, hashtag va CTA yozing.")
            self.smm_polish(pid)
            pid = self.app.smm.quality_gate(pid)
            post = self.db.post(pid)
            self.app.pipeline._send_for_review(pid, self.app.pipeline.last_info.pop(pid, {}))
            with self.turn("direktor", "Videoni tekshiryapti"):
                when = post["publish_at"]
                hhmm = time.strftime("%d.%m %H:%M", time.gmtime(
                    (when or now()) + self.app.settings.timezone_offset_hours * 3600))
                if post["status"] == "approved":
                    self.say("direktor", "nashr", f"#{pid} avtopilot bilan tasdiqlandi — {hhmm} da joylang.")
                else:
                    self.say("direktor", "ega", f"#{pid} tasdig'ingizni kutyapti ({hhmm} da chiqishi kerak).")
        return pid

    def make_now(self, trigger: str = "manual") -> int | None:
        with self.turn("direktor", "Studiyaga shoshilinch vazifa beryapti"):
            self.say("direktor", "studiya", "Egamiz so'radi: keyingi slot uchun hozir video yasang.")
        return self._studio_make(lambda: self.app.pipeline.create_daily_content(trigger))

    def publish_slot(self, slot: int) -> list[int]:
        with self.turn("direktor", "E'lon vaqtini nazorat qilyapti"):
            self.say("direktor", "nashr", "Vaqt bo'ldi — tasdiqlangan videoni hamma platformaga joylang.")
        return self._nashr(lambda: self.app.pipeline.publish_slot(slot))

    def publish_now(self) -> list[int]:
        with self.turn("direktor", "Shoshilinch e'lon"):
            self.say("direktor", "nashr", "Egamiz so'radi: tasdiqlangan videoni hozir joylang.")
        return self._nashr(self.app.pipeline.publish_approved)

    def _nashr(self, fn) -> list[int]:
        with self.turn("nashr", "Instagram, TikTok va Telegram'ga joylayapti"):
            ids = fn()
            if ids:
                rows = self.db.all("SELECT action, detail FROM audit_log WHERE action LIKE 'publish_%' ORDER BY id DESC LIMIT 3")
                okp = [r["action"][8:] for r in rows if '"ok": true' in (r["detail"] or "")]
                self.say("nashr", "direktor", f"#{ids[0]} joylandi: {', '.join(sorted(set(okp))) or '—'}.")
                self.say("nashr", "media", f"#{ids[0]} chiqdi — raqamlarini kuzatishni boshlang.")
            else:
                self.say("nashr", "direktor", "Joylash uchun tasdiqlangan video yo'q.")
        return ids

    def group_quiz(self) -> None:
        with self.turn("nashr", "Guruhga viktorina yuboryapti"):
            pid = self.app.pipeline.send_group_quiz()
            if pid:
                self.say("nashr", "media", "Guruhga yangi viktorina yuborildi — javoblarni hisoblang.")

    # ------------------------------------------------- DIREKTOR: ishga olish
    def hire(self, name: str, role: str, instructions: str = "", daily_hour: int | None = None) -> str:
        """Bosh direktor yangi Jarvisni ishga oladi (ega so'rovi bilan)."""
        import re as _re
        name = name.strip()[:40] or "Yangi Jarvis"
        base = _re.sub(r"[^a-z0-9]+", "", name.lower().replace("'", ""))[:16] or "jarvis"
        key, n = "x_" + base, 1
        while key in AGENTS or self.db.one("SELECT 1 FROM custom_agents WHERE key=?", (key,)):
            n += 1
            key = f"x_{base}{n}"
        if daily_hour is not None and not 0 <= int(daily_hour) <= 23:
            daily_hour = None
        instr = (instructions or role).strip()[:2000]
        with self.turn("direktor", f"«{name}» ni ishga olyapti"):
            self.db.execute("INSERT INTO custom_agents(key, name, role, instructions, daily_hour, active, created_at)"
                            " VALUES (?,?,?,?,?,1,?)", (key, name, role.strip()[:200], instr, daily_hour, now()))
            self.db.audit("direktor", "agent_hired", {"key": key, "name": name, "role": role[:200]})
            when = f" Har kuni soat {int(daily_hour):02d}:00 da o'zi ishlaydi." if daily_hour is not None else ""
            self.say("direktor", "ega", f"«{name}» jamoaga qo'shildi. Vazifasi: {role}.{when} Unga topshiriqni uning sahifasida yoki "
                                        f"/buyur {key} matn bilan bering.")
        return key

    def fire(self, key: str) -> bool:
        r = self.db.one("SELECT * FROM custom_agents WHERE key=? AND active=1", (key,))
        if not r:
            return False
        self.db.execute("UPDATE custom_agents SET active=0 WHERE key=?", (key,))
        self.db.audit("direktor", "agent_fired", key)
        with self.turn("direktor", "Jamoani qayta tuzyapti"):
            self.say("direktor", "ega", f"«{r['name']}» jamoadan chiqarildi.")
        return True

    def run_custom(self, key: str, task: str | None = None) -> str:
        r = self.db.one("SELECT * FROM custom_agents WHERE key=? AND active=1", (key,))
        if not r:
            return "Bunday Jarvis topilmadi."
        sender = "ega" if task else "direktor"
        task = (task or "Kunlik vazifangni bajar.").strip()
        self.say(sender, key, task)
        with self.turn(key, task[:80]):
            ctx = {"bugun_yangi": self.db.scalar("SELECT COUNT(*) FROM users WHERE joined_at>=?", (self.app.local_day_start(),)),
                   "jami_foydalanuvchi": self.db.scalar("SELECT COUNT(*) FROM users"),
                   "manbalar_7kun": self.app.pipeline.context()["sources"], "moliya": self.finance(),
                   "strategiya": self.strategy().get("summary"), "oxirgi_tahlillar": self.reviews_context(3)}
            system = (f"Sen ZFT jamoasidagi «{r['name']}» Jarvisisan. Vazifang: {r['role']}.\nKo'rsatmalar: {r['instructions']}\n"
                      "Mahsulot: Chorraha (prava nazariy imtihoniga tayyorgarlik, chorrahaa.uz, Telegram bot). O'zbek tilida, qisqa "
                      "va aniq yoz. Faqat berilgan ma'lumotga tayan, to'qima raqam yozma. Pul sarflash, e'lon qilish va narxga oid "
                      "qarorlarni o'zing qilmaysan — egaga taklif qilasan.")
            out = self.app.brain.ask(f"custom_{key}", f"Holat: {json.dumps(ctx, ensure_ascii=False, default=str)}\n\nTopshiriq: {task}",
                                     max_tokens=700, system=system)
            text = out or "AI kaliti kiritilmagani yoki budjet tugagani uchun bu topshiriqni bajara olmadim."
            self.say(key, "ega", text[:1800])
        return text

    def owner_message(self, text: str) -> str:
        """Ega Bosh direktor bilan gaplashadi."""
        self.say("ega", "direktor", text)
        with self.turn("direktor", "Egaga javob yozyapti"):
            f = self.finance()
            pend = self.db.scalar("SELECT COUNT(*) FROM posts WHERE status='pending'")
            ctx = {"bugun_yangi": self.db.scalar("SELECT COUNT(*) FROM users WHERE joined_at>=?",
                                                 (self.app.local_day_start(),)),
                   "jami": self.db.scalar("SELECT COUNT(*) FROM users"), "tasdiq_kutayotgan": pend,
                   "moliya": f, "avtopilot": self.app.auto_publish,
                   "keyingi_slot": self.app.pipeline.next_slot()[1].strftime("%d.%m %H:%M")}
            data = Brain.parse_json(self.app.brain.ask(
                "direktor_chat", f"Holat: {json.dumps(ctx, ensure_ascii=False)}\n\nEga yozdi: {text}",
                max_tokens=400, system=DIREKTOR_SYSTEM))
            task = None
            hire = None
            low = text.lower()
            if data and data.get("reply"):
                reply = str(data["reply"])[:800]
                task = data.get("task") or None
                hire = data.get("hire") if isinstance(data.get("hire"), dict) else None
            elif "ishga ol" in low:  # AIsiz: "ishga ol: Nomi — vazifasi"
                body = text.split(":", 1)[1] if ":" in text else text[low.index("ishga ol") + 8:]
                name, _, role = body.replace("—", "-").partition("-")
                hire = {"name": name.strip(" .") or "Yangi Jarvis", "role": (role or body).strip(" .")}
                reply = "Qabul qildim."
            else:  # AIsiz: uzun xabarni topshiriq deb qabul qilamiz
                if len(text) >= 15:
                    task = text
                    reply = "Qabul qildim — Studiyaga topshiriq qilib berdim. Keyingi video shu asosda bo'ladi."
                else:
                    reply = (f"Bugun {ctx['bugun_yangi']} yangi odam, jami {ctx['jami']}. Tasdiq kutayotgan video: {pend}. "
                             f"Keyingi e'lon: {ctx['keyingi_slot']}.")
            self.say("direktor", "ega", reply)
        if hire and hire.get("name"):
            self.hire(str(hire["name"]), str(hire.get("role") or hire["name"]), str(hire.get("instructions") or ""),
                      hire.get("daily_hour") if isinstance(hire.get("daily_hour"), int) else None)
            task = None
        with self.turn("direktor", "Topshiriqni taqsimlayapti"):
            if task:
                tid = self.app.pipeline.add_task(str(task))
                self.say("direktor", "studiya", f"Yangi topshiriq #{tid}: «{str(task)[:300]}»")
        return reply


def clean_caption(text: str) -> str:
    """Yozilgan matndagi eng ko'zga tashlanadigan AI belgilarini tozalaydi (anti-ai-writing)."""
    import re
    t = text.replace(" — ", ". ").replace("—", ", ").replace(" – ", ". ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()
