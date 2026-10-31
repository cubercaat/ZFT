"""Egasi uchun tugmali boshqaruv menyusi (buyruq yozish shart emas).

Hamma amal tugma bilan: bo'lim → amal. Matn kerak bo'lsa ("summa va izoh", "video g'oyasi"),
bot so'raydi va keyingi yozgan xabaringizni o'sha amalga beradi. Pastda doim "📋 Menyu" tugmasi turadi.
"""
from __future__ import annotations

import html
from typing import TYPE_CHECKING

from .telegram_api import TelegramError, kb

if TYPE_CHECKING:
    from .bot import Bot

MENU_BUTTON = "📋 Menyu"
CANCEL = ("✖️ Bekor qilish", "ad:cancel")
BACK = ("◀️ Orqaga", "ad:main")

# so'raladigan matnlar: kalit → (savol, misol)
PROMPTS = {
    "task": ("🎬 Video g'oyasini yozing", "Imtihon kunidagi hayajon haqida kulgili hikoya, oxirida Chorraha"),
    "link": ("🔗 Havola kodi va nomini yozing", "avtomaktab_1 Yunusobod avtomaktabi"),
    "income": ("➕ Kirim: summa va izoh", "150000 premium obuna  (dollar bo'lsa: 20 USD izoh)"),
    "expense": ("➖ Xarajat: summa va izoh", "70000 server  (dollar bo'lsa: 6 USD hetzner)"),
    "budget": ("💵 Oylik AI video budjeti, dollarda", "60"),
    "partner": ("🤝 Hamkor: nomi | turi | foiz", "Ali Blog | bloger | 20"),
    "payment": ("💳 To'lov: @username yoki ID, summa, izoh", "@ali 99000 premium"),
    "hire": ("🧑‍💼 Yangi Jarvis: nomi | vazifasi | har kuni soat (ixtiyoriy)", "Izohchi | Instagram izohlariga javob loyihasini yozadi | 18"),
    "startmsg": ("💬 Boshqalar Start bossa yuboriladigan xabar", "Assalomu alaykum! Testlar: @PRAVAQUIZ_bot"),
    "goal": ("🎯 Maqsad soni", "500"),
    "order": ("📨 Topshiriq matni", "Bugungi izohlardan eng ko'p so'ralgan 3 savolni yoz"),
    "director": ("🧭 Bosh direktorga savol yoki buyruq", "Bu hafta nechta odam keldi va nimani yaxshilash kerak?"),
}
GOALS = [("users", "👥 Yangi odamlar"), ("active", "✅ Faol o'quvchilar"), ("referrals", "🤝 Do'st taklifi"),
         ("payments", "💳 To'lovlar"), ("revenue", "💰 Tushum (so'm)"), ("posts", "🎬 Videolar"),
         ("views", "👁 Ko'rishlar"), ("exams", "🎯 Imtihonlar")]


class AdminMenu:
    def __init__(self, bot: "Bot"):
        self.bot = bot
        self.app = bot.app
        self.db = bot.db
        self.tg = bot.tg

    # -------------------------------------------------------------- ekranlar
    def screen(self, name: str) -> tuple[str, dict]:
        a = self.app
        if name == "main":
            t0 = a.local_day_start()
            new = self.db.scalar("SELECT COUNT(*) FROM users WHERE joined_at>=?", (t0,))
            pend = self.db.scalar("SELECT COUNT(*) FROM posts WHERE status='pending'")
            busy = next((x for x in a.hub.status() if x["active"]), None)
            text = (f"🚦 <b>ZFT boshqaruvi</b>\nBugun: +{new} yangi odam · tasdiq kutayotgan video: {pend}\n"
                    + (f"⚙️ Hozir ishlayapti: {html.escape(busy['name'])} — {html.escape(busy['doing'] or '')}" if busy
                       else "💤 Jamoa dam olyapti")
                    + "\n\nBo'limni tanlang yoki shunchaki yozing — Bosh direktor javob beradi.")
            rows = [
                [("📊 Bugungi raqamlar", "ad:do:stat"), ("🧭 Manbalar", "ad:do:sources")],
                [("🎬 Video yasash", "ad:do:video"), ("📤 Navbat", "ad:queue")],
                [("📝 Video g'oyasi berish", "ad:ask:task"), ("📋 Topshiriqlar", "ad:do:tasks")],
                [("🧭 Bosh direktorga yozish", "ad:ask:director"), ("👥 Jamoa", "ad:do:team")],
                [("📈 Hisobotlar", "ad:reports"), ("🧠 Marketolog", "ad:market")],
                [("💰 Moliya", "ad:money"), ("🤝 Hamkorlar", "ad:partners")],
                [("🎯 Maqsadlar", "ad:goals"), ("🧑‍💼 Jarvislar", "ad:agents")],
                [("🔗 Havola yaratish", "ad:ask:link"), ("📚 O'quvchilar", "ad:do:learn")],
                [("⚙️ Sozlamalar", "ad:settings")],
            ]
            panel = __import__("os").environ.get("PANEL_URL", "")
            if panel.startswith("https://"):
                rows.append([("🖥 Veb-panelni ochish", "url:" + panel)])
            return text, kb(rows)
        if name == "reports":
            return "📈 <b>Hisobotlar</b> — tanlang, Hisobotchi tayyorlab yuboradi.", kb([
                [("🗓 Kunlik umumiy", "ad:do:report"), ("☀️ Ertalabki brif", "ad:do:brief")],
                [("💵 Moliya", "ad:do:report_moliya"), ("📈 Marketolog", "ad:do:report_marketolog")],
                [("📊 Media", "ad:do:report_media"), ("⚙️ Jarvislar samaradorligi", "ad:do:eff")],
                [BACK]])
        if name == "market":
            return "🧠 <b>Marketolog</b>", kb([
                [("🔎 Bozorni hozir tahlil qilish", "ad:do:market")],
                [("📜 Joriy strategiya", "ad:do:strategy"), ("🎞 Videolar tahlili", "ad:do:review")],
                [BACK]])
        if name == "money":
            return "💰 <b>Moliya</b>", kb([
                [("📄 Moliya hisoboti", "ad:do:finance"), ("💵 Budjet holati", "ad:do:budget")],
                [("➕ Kirim yozish", "ad:ask:income"), ("➖ Xarajat yozish", "ad:ask:expense")],
                [("✏️ Video budjetini o'zgartirish", "ad:ask:budget")],
                [BACK]])
        if name == "partners":
            return "🤝 <b>Hamkorlar</b> — blogerlar va avtomaktablar", kb([
                [("📋 Ro'yxat va qoldiqlar", "ad:do:partners"), ("🏆 Oylik konkurs", "ad:do:contest")],
                [("➕ Hamkor qo'shish", "ad:ask:partner"), ("💳 To'lov yozish", "ad:ask:payment")],
                [BACK]])
        if name == "goals":
            rows = [[(GOALS[i][1], f"ad:goal:{GOALS[i][0]}"), (GOALS[i + 1][1], f"ad:goal:{GOALS[i + 1][0]}")]
                    for i in range(0, len(GOALS), 2)]
            return "🎯 <b>Oylik maqsadlar</b>\nKo'rsatkichni tanlang va sonini yozing.", kb(
                [[("📊 Progressni ko'rish", "ad:do:goals")]] + rows + [[BACK]])
        if name == "agents":
            customs = [x for x in a.hub.status() if x.get("custom")]
            rows = [[("➕ Yangi Jarvis ishga olish", "ad:ask:hire")]]
            for c in customs[:8]:
                rows.append([(f"📨 {c['name']}ga topshiriq", f"ad:ord:{c['key']}"), ("🗑 Bo'shatish", f"ad:fire:{c['key']}")])
            rows.append([BACK])
            return ("🧑‍💼 <b>Jarvislar</b>\n" + ("Qo'shimcha Jarvislar: " + ", ".join(html.escape(c["name"]) for c in customs)
                                                if customs else "Hozircha faqat asosiy 8 ta Jarvis bor.")), kb(rows)
        if name == "settings":
            auto = a.auto_publish
            dry = a.dry_run
            return ("⚙️ <b>Sozlamalar</b>\n"
                    f"Avtopilot: {'yoqilgan — videolar tasdiqsiz chiqadi' if auto else 'o‘chiq — har video tasdiqdan o‘tadi'}\n"
                    f"Sinov rejimi: {'yoqilgan — hech narsa ommaga chiqmaydi' if dry else 'o‘chiq'}"), kb([
                [(("🟢 Avtopilot: yoqilgan" if auto else "⚪️ Avtopilot: o'chiq"), "ad:toggle:auto")],
                [(("🧪 Sinov rejimi: yoqilgan" if dry else "⚪️ Sinov rejimi: o'chiq"), "ad:toggle:dry")],
                [("💬 Start xabarini o'zgartirish", "ad:ask:startmsg"), ("👁 Hozirgi Start xabari", "ad:do:startshow")],
                [("🔐 Panel paroli", "ad:do:password")],
                [BACK]])
        return self.screen("main")

    def open(self, uid: int, name: str = "main") -> None:
        if not self.db.get(f"menu_kb:{uid}"):
            try:
                self.tg.send_message(uid, "Pastdagi «📋 Menyu» tugmasi doim shu yerda turadi.",
                                     {"keyboard": [[{"text": MENU_BUTTON}]], "resize_keyboard": True, "is_persistent": True})
                self.db.set(f"menu_kb:{uid}", 1)
            except TelegramError:
                pass
        text, markup = self.screen(name)
        self.tg.send_message(uid, text, markup)

    def edit(self, uid: int, message_id: int | None, name: str) -> None:
        text, markup = self.screen(name)
        if message_id:
            try:
                self.tg.call("editMessageText", chat_id=uid, message_id=message_id, text=text,
                             parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
                return
            except TelegramError:
                pass
        self.tg.send_message(uid, text, markup)

    # ------------------------------------------------------------ so'rovlar
    def ask(self, uid: int, key: str, ctx: str = "") -> None:
        q, ex = PROMPTS[key]
        self.db.set(f"admin_await:{uid}", f"{key}|{ctx}")
        self.tg.send_message(uid, f"{q}\n\n<i>Masalan:</i> {html.escape(ex)}", kb([[CANCEL]]))

    def take_input(self, uid: int, text: str) -> bool:
        """Kutilayotgan matn bo'lsa, tegishli amalni bajaradi."""
        raw = self.db.get(f"admin_await:{uid}") or ""
        if not raw:
            return False
        self.db.set(f"admin_await:{uid}", "")
        key, _, ctx = raw.partition("|")
        b = self.bot
        run = {
            "task": lambda: b.cmd_task(uid, text), "link": lambda: b.cmd_link(uid, text),
            "income": lambda: b.cmd_income(uid, text), "expense": lambda: b.cmd_expense(uid, text),
            "budget": lambda: b.cmd_budget(uid, text), "partner": lambda: b.cmd_partner(uid, text),
            "payment": lambda: b.cmd_payment(uid, text), "hire": lambda: b.cmd_hire(uid, text),
            "startmsg": lambda: b.cmd_start_message(uid, text), "goal": lambda: b.cmd_goal(uid, f"{ctx} {text}"),
            "order": lambda: b.cmd_order(uid, f"{ctx} {text}"), "director": lambda: b.ask_director(uid, text),
        }.get(key)
        if run:
            run()
            self.tg.send_message(uid, "Yana nima qilamiz?", kb([[("📋 Menyu", "ad:main")]]))
        return bool(run)

    # ------------------------------------------------------------- tugmalar
    def handle(self, cq: dict) -> None:
        uid = cq["from"]["id"]
        data = cq.get("data") or ""
        mid = (cq.get("message") or {}).get("message_id")
        b = self.bot
        try:
            self.tg.answer_callback(cq["id"])
        except TelegramError:
            pass
        part = data.split(":", 2)
        kind = part[1] if len(part) > 1 else "main"
        arg = part[2] if len(part) > 2 else ""
        if kind == "cancel":
            self.db.set(f"admin_await:{uid}", "")
            return self.edit(uid, mid, "main")
        if kind in ("main", "reports", "market", "money", "partners", "goals", "agents", "settings"):
            return self.edit(uid, mid, kind)
        if kind == "queue":
            return b.cmd_queue(uid, "")
        if kind == "ask" and arg in PROMPTS:
            return self.ask(uid, arg)
        if kind == "goal":
            return self.ask(uid, "goal", arg)
        if kind == "ord":
            return self.ask(uid, "order", arg)
        if kind == "fire":
            return self.tg.send_message(uid, "Rostdan ham jamoadan chiqarilsinmi?",
                                        kb([[("🗑 Ha, chiqarish", f"ad:fire!:{arg}"), ("✖️ Yo'q", "ad:agents")]]))
        if kind == "fire!":
            b.cmd_fire(uid, arg)
            return self.edit(uid, mid, "agents")
        if kind == "toggle":
            if arg == "auto":
                self.db.set("auto_publish", "0" if self.app.auto_publish else "1")
                self.db.audit("owner", "auto_publish", "toggle")
            elif arg == "dry":
                self.app.control.set_dry_run(not self.app.dry_run)
            return self.edit(uid, mid, "settings")
        if kind == "pub":
            b.cmd_queue(uid, "chiqar")
            return
        if kind == "do":
            actions = {
                "stat": b.cmd_stat, "sources": b.cmd_sources, "video": b.cmd_video, "tasks": b.cmd_tasks,
                "team": b.cmd_team, "learn": b.cmd_learning_stats, "report": lambda u, _: b.cmd_report(u, ""),
                "brief": b.cmd_brief, "eff": b.cmd_efficiency, "market": b.cmd_market, "strategy": b.cmd_strategy,
                "review": b.cmd_review, "finance": b.cmd_finance, "budget": lambda u, _: b.cmd_budget(u, ""),
                "partners": b.cmd_partners, "contest": lambda u, _: self.tg.send_message(u, self.app.partners.contest_text()),
                "goals": lambda u, _: b.cmd_goal(u, ""), "password": b.cmd_password,
                "startshow": lambda u, _: b.cmd_start_message(u, ""),
                "report_moliya": lambda u, _: b.cmd_report(u, "moliya"),
                "report_marketolog": lambda u, _: b.cmd_report(u, "marketolog"),
                "report_media": lambda u, _: b.cmd_report(u, "media"),
            }
            fn = actions.get(arg)
            if fn:
                if arg.startswith("report") or arg == "brief":
                    self.tg.send_message(uid, "📝 Hisobotchi tayyorlayapti, bir daqiqada keladi…")
                fn(uid, "")
            return
        self.edit(uid, mid, "main")
