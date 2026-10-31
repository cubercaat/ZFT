"""Telegram bot: manba kuzatuvi, referal, test, reyting va egaga admin panel."""
from __future__ import annotations

import html
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from .db import now
from .telegram_api import TelegramError, kb

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)

PAYLOAD_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
LETTERS = "ABCDEFGHIJ"


def esc(s: Any) -> str:
    return html.escape(str(s or ""))


class Bot:
    def __init__(self, app: "App"):
        self.app = app
        self.tg = app.tg
        self.db = app.db
        self.s = app.settings
        from .admin_menu import AdminMenu
        self.menu = AdminMenu(self)

    # ------------------------------------------------------------------ utils
    def is_owner(self, uid: int) -> bool:
        return bool(self.s.owner_id) and uid == self.s.owner_id

    def bot_link(self, payload: str) -> str:
        return f"https://t.me/{self.s.cta_bot}?start={payload}"

    def site_link(self, source: str) -> str:
        sep = "&" if "?" in self.s.site_url else "?"
        return f"{self.s.site_url}{sep}utm_source=telegram_bot&utm_medium=bot&utm_campaign={source}"

    def main_menu(self, uid: int) -> dict:
        u = self.db.user(uid)
        source = u["source"] if u else "direct"
        return kb([
            [("🧪 Testni boshlash", "quiz"), ("🎯 Imtihon (20 savol)", "m:exam")],
            [("📚 Mavzu bo'yicha", "m:topics"), ("🔁 Xatolarim", "m:mistakes")],
            [("📅 Shaxsiy reja", "m:plan"), ("📊 Natijam", "m:stats")],
            [("⚔️ Do'st bilan duel", "m:duel"), ("🏆 Reyting", "top")],
            [("👥 Do'stni taklif qilish", "ref")],
            [("🌐 Saytda barcha savollar", "url:" + self.site_link(source))],
        ])

    # --------------------------------------------------------------- dispatch
    def handle_update(self, upd: dict) -> None:
        try:
            if "message" in upd:
                self.on_message(upd["message"])
            elif "callback_query" in upd:
                self.on_callback(upd["callback_query"])
            elif "poll_answer" in upd:
                self.on_poll_answer(upd["poll_answer"])
            elif "my_chat_member" in upd:
                self.app.conn.on_chat_member(upd["my_chat_member"])
        except TelegramError as exc:
            log.warning("Telegram xatosi: %s", exc)
            if "blocked" in str(exc).lower():
                chat = (upd.get("message") or {}).get("chat", {}).get("id")
                if chat:
                    self.db.execute("UPDATE users SET blocked=1 WHERE tg_id=?", (chat,))
        except Exception:  # bot hech qachon yiqilmasin
            log.exception("Update qayta ishlashda xato: %s", upd.get("update_id"))

    # ---------------------------------------------------------------- message
    def on_message(self, m: dict) -> None:
        chat = m.get("chat", {})
        if chat.get("type") != "private":
            return  # guruhda faqat viktorina (poll) ishlaydi
        frm = m.get("from", {})
        uid = frm.get("id")
        text = (m.get("text") or "").strip()
        if not uid or not text:
            return
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()

        if cmd == "/start" and not (self.s.owner_only and self.is_owner(uid)):
            return self.cmd_start(frm, arg.strip())
        if cmd == "/egasi":
            return self.cmd_claim(uid, arg.strip())
        if self.s.owner_only and not self.is_owner(uid):
            return self.private_notice(uid)
        if self.s.owner_only:  # shaxsiy boshqaruv boti: test/menyu yo'q, faqat boshqaruv
            return self.owner_message(uid, cmd, arg, text)
        # foydalanuvchi bazada bo'lmasa (masalan /start bosmasdan yozsa) — qo'shib qo'yamiz
        if not self.db.user(uid):
            self.db.upsert_user(uid, frm.get("first_name", ""), frm.get("username"))
        else:
            self.db.execute("UPDATE users SET last_seen=? WHERE tg_id=?", (now(), uid))

        L = self.app.learning
        if not text.startswith("/") and L.handle_text(uid, text):
            return
        if not text.startswith("/") and re.fullmatch(r"[A-Za-z0-9_]{2,20}", text) and not self.is_owner(uid):
            pname = self.app.partners.apply_promo(uid, text)
            if pname:
                return self.tg.send_message(uid, f"✅ Promo-kod qabul qilindi ({esc(pname)}). Omad!", self.main_menu(uid))
        if cmd == "/konkurs":
            return self.tg.send_message(uid, self.app.partners.contest_text(uid), kb([[("👥 Do'stni taklif qilish", "ref")]]))
        if cmd in ("/test", "/quiz", "/mashq"):
            return self.send_question(uid)
        learn_cmds = {"/reja": lambda: L.handle_callback(uid, "m:plan"), "/xatolar": lambda: L.handle_callback(uid, "m:mistakes"),
                      "/mavzu": lambda: L.send_topics(uid), "/imtihon": lambda: L.start_exam(uid),
                      "/natija": lambda: L.send_stats(uid), "/duel": lambda: L.create_duel(uid),
                      "/menu": lambda: L.handle_callback(uid, "m:menu")}
        if cmd in learn_cmds:
            learn_cmds[cmd]()
            return
        if cmd in ("/taklif", "/ref"):
            return self.send_ref(uid)
        if cmd in ("/reyting", "/top"):
            return self.send_top(uid)
        if cmd == "/help":
            return self.tg.send_message(uid, "Buyruqlar: /test, /imtihon, /xatolar, /mavzu, /reja, /natija, /duel, /taklif, /reyting", self.main_menu(uid))

        if self.is_owner(uid) and self.owner_message(uid, cmd, arg, text, fallback=False):
            return
        self.tg.send_message(uid, "Testni boshlash uchun pastdagi tugmani bosing 👇", self.main_menu(uid))

    def owner_message(self, uid: int, cmd: str, arg: str, text: str, fallback: bool = True) -> bool:
        """Egasining buyrug'i, tugmali menyu yoki Bosh direktorga matn. Bajarilsa True."""
        from .admin_menu import MENU_BUTTON
        if text == MENU_BUTTON or cmd in ("/menu", "/menyu"):
            self.menu.open(uid)
            return True
        if not text.startswith("/") and self.menu.take_input(uid, text):
            return True
        if True:
            admin = {
                "/admin": self.cmd_admin_help, "/stat": self.cmd_stat, "/manbalar": self.cmd_sources,
                "/link": self.cmd_link, "/video": self.cmd_video, "/hisobot": self.cmd_report,
                "/auto": self.cmd_auto, "/avtopilot": self.cmd_auto, "/budjet": self.cmd_budget,
                "/navbat": self.cmd_queue, "/topshiriq": self.cmd_task, "/topshiriqlar": self.cmd_tasks,
                "/moliya": self.cmd_finance, "/kirim": self.cmd_income, "/xarajat": self.cmd_expense,
                "/tahlil": self.cmd_review, "/ishgaol": self.cmd_hire, "/boshat": self.cmd_fire, "/buyur": self.cmd_order,
                "/jamoa": self.cmd_team, "/hamkor": self.cmd_partner, "/hamkorlar": self.cmd_partners,
                "/tolov": self.cmd_payment, "/maqsad": self.cmd_goal, "/samaradorlik": self.cmd_efficiency,
                "/sinov": self.cmd_dry, "/brif": self.cmd_brief, "/oqish": self.cmd_learning_stats, "/buyruqlar": self.cmd_commands, "/bozor": self.cmd_market, "/strategiya": self.cmd_strategy,
            }
            admin.update({"/parol": self.cmd_password, "/startmatn": self.cmd_start_message})
            if cmd in admin:
                admin[cmd](uid, arg.strip())
                return True
            if not text.startswith("/"):  # oddiy matn — Bosh direktorga
                self.ask_director(uid, text)
                return True
        if fallback:
            self.cmd_admin_help(uid, "")
            return True
        return False

    def ask_director(self, uid: int, text: str) -> None:
        self.app.run_in_background(lambda: self.tg.send_message(
            uid, "🧭 <b>Bosh direktor:</b> " + esc(self.app.hub.owner_message(text))))

    def cmd_password(self, uid: int, _: str) -> None:
        import os
        pw = os.environ.get("PANEL_PASSWORD", "")
        url = os.environ.get("PANEL_URL", "")
        self.tg.send_message(uid, (f"🔐 Panel paroli: <code>{esc(pw)}</code>" if pw else "Panel paroli o'rnatilmagan.")
                             + (f"\nPanel: {esc(url)}" if url else "") + "\nBu xabarni o'qigach o'chirib yuboring.")

    def cmd_start_message(self, uid: int, arg: str) -> None:
        if arg:
            self.db.set("start_message", arg[:2000])
            self.db.audit("owner", "start_message", arg[:200])
            self.tg.send_message(uid, "✅ Saqlandi. Boshqalar Start bossa shu boradi:")
            return self.private_notice(uid)
        self.tg.send_message(uid, "Hozirgi matn (o'zgartirish: /startmatn yangi matn):")
        self.private_notice(uid)

    def cmd_claim(self, uid: int, code: str) -> None:
        """Bir martalik kod bilan bot egasini belgilash (o'rnatishda server ko'rsatgan kod)."""
        import os
        import hmac
        want = os.environ.get("OWNER_CLAIM_CODE", "")
        if self.s.owner_id:
            return self.tg.send_message(uid, "✅ Siz bot egasisiz. Buyruqlar: /admin" if uid == self.s.owner_id
                                        else "Bu botning egasi allaqachon belgilangan.")
        if not want or not code or not hmac.compare_digest(code, want):
            self.db.audit("bot", "owner_claim_failed", {"tg_id": uid})
            return self.tg.send_message(uid, "Kod noto'g'ri.")
        self.s.owner_id = uid
        self.db.set("owner_id", uid)
        self.db.audit("bot", "owner_claimed", {"tg_id": uid})
        self.tg.send_message(uid, "✅ Siz bot egasisiz. Admin buyruqlari: /admin")
        self.app.first_start()

    def start_message(self) -> tuple[str, str, str]:
        pub = self.s.public_bot
        text = self.db.get("start_message") or ("Assalomu alaykum! Prava nazariy imtihoniga tayyorlanish uchun: @" + pub
                                                if pub else "Assalomu alaykum!")
        return text, self.db.get("start_button_text") or "", self.db.get("start_button_url") or ""

    def private_notice(self, uid: int) -> None:
        text, btn, url = self.start_message()
        markup = kb([[(btn, "url:" + url)]]) if btn and url.startswith("https://") else None
        self._safe_send(uid, esc(text), markup)

    def cmd_start(self, frm: dict, payload: str) -> None:
        uid = frm["id"]
        source, referrer = "direct", None
        if payload.startswith("egasi_"):  # sozlash oynasidagi "botni ochish" havolasi
            return self.cmd_claim(uid, payload[6:])
        if self.s.owner_only:
            return self.private_notice(uid)
        duel_id = None
        if payload and PAYLOAD_RE.match(payload) and payload.startswith("duel_") and payload[5:].isdigit():
            duel_id = int(payload[5:])
            d = self.db.one("SELECT a FROM duels WHERE id=?", (duel_id,))
            payload = f"ref_{d['a']}" if d else ""
        psrc = self.app.partners.source_for(payload) if payload else None
        if psrc:
            source, payload = psrc, ""
        if payload and PAYLOAD_RE.match(payload):
            if payload.startswith("ref_") and payload[4:].isdigit():
                source, referrer = "referral", int(payload[4:])
            elif self.db.one("SELECT 1 FROM links WHERE code=?", (payload,)):
                source = payload
            else:
                source = "other:" + payload[:40]
        is_new = self.db.upsert_user(uid, frm.get("first_name", ""), frm.get("username"), source, referrer)
        if is_new:
            self.db.event("join", uid, {"source": source, "referrer": referrer})
            u = self.db.user(uid)
            if u and u["referrer_id"]:
                self._reward_referrer(u["referrer_id"], frm)
        name = esc(frm.get("first_name") or "do'st")
        text = (
            f"Salom, {name}! 👋\n\n"
            "Bu yerda haydovchilik guvohnomasi <b>nazariy imtihoniga</b> tayyorlanasiz.\n"
            "• Rasmiy savollar asosida testlar\n"
            "• Har bir javobga tushuntirish\n"
            "• Do'stlaringiz bilan reytingda bellashing\n\n"
            "Boshlaymizmi? 👇"
        )
        if duel_id:
            return self.app.learning.join_duel(uid, duel_id)
        self.tg.send_message(uid, text, self.main_menu(uid))
        if is_new:
            self.app.learning.ask_exam_date(uid)

    def _reward_referrer(self, ref_id: int, newcomer: dict) -> None:
        count = self.db.ref_count(ref_id)
        goal = self.app.ref_goal
        msg = f"🎉 Sizning havolangiz orqali {esc(newcomer.get('first_name'))} qo'shildi! Jami: <b>{count}</b>."
        if count == goal:
            msg += f"\n\n🏅 {esc(self.app.ref_reward_text)}"
            if self.s.owner_id:
                ru = self.db.user(ref_id)
                uname = f"@{ru['username']}" if ru and ru["username"] else str(ref_id)
                self._safe_send(self.s.owner_id,
                                f"🏅 Referal maqsadi bajarildi: {esc(uname)} ({goal} ta do'st). Mukofotni bering.")
            self.db.audit("bot", "ref_goal_reached", {"tg_id": ref_id, "count": count})
        elif count < goal:
            msg += f"\nYana {goal - count} ta do'st — va mukofot sizniki."
        self._safe_send(ref_id, msg)

    def _safe_send(self, chat_id: Any, text: str, markup: dict | None = None) -> None:
        try:
            self.tg.send_message(chat_id, text, markup)
        except TelegramError as exc:
            log.info("Xabar yuborilmadi %s: %s", chat_id, exc)

    # ------------------------------------------------------------------- quiz
    def send_question(self, uid: int) -> None:
        if not len(self.app.bank):
            return self.tg.send_message(uid, "Savollar hozircha yuklanmagan.")
        self.app.learning.send_practice(uid, {})

    def on_answer(self, cq: dict, qid: str, idx: int) -> None:
        uid = cq["from"]["id"]
        q = self.app.bank.by_id(qid)
        msg = cq.get("message") or {}
        if msg:
            self.tg.edit_markup(uid, msg["message_id"])
        if not q:
            return self.tg.answer_callback(cq["id"], "Savol topilmadi")
        ok = idx == q.correct
        L = self.app.learning
        new_ach = L.record(uid, qid, ok, L.mode(uid).get("type") or "practice")
        self.tg.answer_callback(cq["id"], "✅ To'g'ri!" if ok else "❌ Noto'g'ri")
        u = self.db.user(uid)
        stat = f"Natijangiz: {u['quiz_correct']}/{u['quiz_total']}" if u else ""
        text = ("✅ <b>To'g'ri!</b>" if ok else
                f"❌ <b>Noto'g'ri.</b> To'g'ri javob: {LETTERS[q.correct]}) {esc(q.correct_text)}")
        if q.explanation:
            text += f"\n\n💡 {esc(q.explanation)}"
        plan = L.plan(uid)
        text += f"\n\n{stat} · bugun {plan['done_today']}/{plan['goal']} · tayyorlik {plan['percent']}%"
        if plan["done_today"] == plan["goal"]:
            text += "\n🎯 Bugungi maqsad bajarildi! Zo'r!"
        self.tg.send_message(uid, text, kb(L.after_answer_buttons(uid, qid)))
        L.announce_achievements(uid, new_ach)

    def send_ref(self, uid: int) -> None:
        link = self.bot_link(f"ref_{uid}")
        count = self.db.ref_count(uid)
        text = (
            "👥 <b>Do'stlaringizni taklif qiling</b>\n\n"
            f"Sizning shaxsiy havolangiz:\n{link}\n\n"
            f"Taklif qilganlaringiz: <b>{count}</b> / {self.app.ref_goal}\n"
            f"🏅 Mukofot: {esc(self.app.ref_reward_text)}"
        )
        share = ("https://t.me/share/url?url=" + requests_quote(link) + "&text=" +
                 requests_quote("Prava nazariy imtihoniga bepul tayyorlanish — sinab ko'r!"))
        self.tg.send_message(uid, text, kb([[("📤 Ulashish", "url:" + share)]]))

    def send_top(self, uid: int) -> None:
        rows = self.db.all("SELECT first_name, quiz_correct, quiz_total FROM users WHERE quiz_total>0"
                           " ORDER BY quiz_correct DESC, quiz_total ASC LIMIT 10")
        refs = self.db.all("SELECT u.first_name, COUNT(r.tg_id) c FROM users u JOIN users r"
                           " ON r.referrer_id=u.tg_id GROUP BY u.tg_id ORDER BY c DESC LIMIT 5")
        medals = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, 11)]
        lines = ["🏆 <b>Eng ko'p to'g'ri javob</b>"]
        lines += [f"{medals[i]} {esc(r['first_name'])} — {r['quiz_correct']}" for i, r in enumerate(rows)] or ["—"]
        lines += ["", "👥 <b>Eng ko'p do'st taklif qilganlar</b>"]
        lines += [f"{medals[i]} {esc(r['first_name'])} — {r['c']}" for i, r in enumerate(refs)] or ["—"]
        self.tg.send_message(uid, "\n".join(lines), self.main_menu(uid))

    # ------------------------------------------------------------- callbacks
    def on_callback(self, cq: dict) -> None:
        data = cq.get("data") or ""
        uid = cq["from"]["id"]
        if data.startswith("ad:") and self.is_owner(uid):
            return self.menu.handle(cq)
        if self.s.owner_only and (not self.is_owner(uid) or data[:3] not in ("ap:", "rj:", "rg:")):
            return self.tg.answer_callback(cq["id"], "" if self.is_owner(uid) else "Shaxsiy bot")
        if data == "quiz":
            self.tg.answer_callback(cq["id"])
            return self.send_question(uid)
        if data.startswith("a:"):
            _, qid, idx = data.split(":", 2)
            return self.on_answer(cq, qid, int(idx))
        if data == "ref":
            self.tg.answer_callback(cq["id"])
            return self.send_ref(uid)
        if data == "top":
            self.tg.answer_callback(cq["id"])
            return self.send_top(uid)
        if self.app.learning.handle_callback(uid, data):
            self.tg.answer_callback(cq["id"])
            return
        if data[:3] in ("ap:", "rj:", "rg:"):
            if not self.is_owner(uid):
                return self.tg.answer_callback(cq["id"], "Ruxsat yo'q", show_alert=True)
            post_id = int(data[3:])
            msg = cq.get("message") or {}
            if msg:
                self.tg.edit_markup(uid, msg["message_id"])
            action = data[:2]
            if action == "ap":
                self.app.pipeline.approve(post_id)
                self.tg.answer_callback(cq["id"], "✅ Tasdiqlandi")
                p = self.db.post(post_id)
                when = time.strftime("%d.%m %H:%M", time.gmtime((p["publish_at"] or now()) + self.s.timezone_offset_hours * 3600)) if p else ""
                self.tg.send_message(uid, f"✅ #{post_id} tasdiqlandi. {when} da e'lon qilinadi. "
                                          "Hozir chiqarish: /navbat chiqar")
            elif action == "rj":
                self.app.pipeline.reject(post_id)
                self.tg.answer_callback(cq["id"], "Rad etildi")
            else:
                self.tg.answer_callback(cq["id"], "Yangi variant tayyorlanmoqda…")
                self.app.run_in_background(
                    lambda: self.app.hub._studio_make(lambda: self.app.pipeline.regenerate(post_id)))
            return
        self.tg.answer_callback(cq["id"])

    # ------------------------------------------------------------ group poll
    def on_poll_answer(self, pa: dict) -> None:
        poll = self.db.one("SELECT * FROM group_polls WHERE poll_id=?", (pa.get("poll_id"),))
        user = pa.get("user") or {}
        uid = user.get("id")
        if not poll or not uid or not pa.get("option_ids"):
            return
        ok = pa["option_ids"][0] == poll["correct_index"]
        self.db.upsert_user(uid, user.get("first_name", ""), user.get("username"), source="group")
        self.db.execute("INSERT OR IGNORE INTO poll_answers(poll_id, tg_id, correct, at) VALUES (?,?,?,?)",
                        (poll["poll_id"], uid, 1 if ok else 0, now()))
        self.db.record_quiz(uid, ok)

    # ------------------------------------------------------------------ admin
    def cmd_admin_help(self, uid: int, _: str) -> None:
        self.menu.open(uid)

    def cmd_commands(self, uid: int, _: str) -> None:
        self.tg.send_message(uid, (
            "<b>ZFT — admin buyruqlari</b>\n"
            "/stat — bugungi raqamlar\n"
            "/manbalar — odamlar qayerdan kelyapti\n"
            "/link kod Nomi — yangi kuzatuvli havola (masalan /link tiktok TikTok profil)\n"
            "/video — hozir yangi video tayyorlash\n"
            "/navbat — tasdiq kutayotgan va tasdiqlangan postlar; /navbat chiqar — hozir e'lon qilish\n"
            "/hisobot — kunlik hisobot; /hisobot moliya | marketolog | media — bo'lim hisoboti\n"
            "/tahlil — Marketolog joylangan videolarni tahlil qilsin\n"
            "/jamoa — Jarvislar holati va suhbati\n"
            "/bozor — Marketolog hozir bozorni o'rgansin; /strategiya — joriy strategiya\n"
            "/ishgaol Nomi | vazifasi | soat — yangi Jarvis; /buyur kod matn — unga topshiriq; /boshat kod\n"
            "/moliya — moliyaviy hisobot; /kirim 150000 UZS izoh; /xarajat 20 USD izoh\n"
            "Oddiy matn yozsangiz — Bosh direktorga boradi\n"
            "/topshiriq matn — AI'ga topshiriq (masalan: imtihon kunidagi hayajon haqida kulgili reklama)\n"
            "/topshiriqlar — navbatdagi topshiriqlar\n"
            "/avtopilot on|off — videolar tasdiqsiz o'zi chiqadi\n"
            "/budjet — xarajatlar; /budjet 60 — oylik video budjetini 60$ qilish\n"
            "/hamkor Nomi | bloger | 20 — yangi hamkor; /hamkorlar — ro'yxat; /tolov @user 99000 izoh — to'lov\n"
            "/maqsad users 500 — oylik maqsad; /maqsad — progress\n"
            "/samaradorlik — Jarvislar 7 kunlik ishi; /brif — ertalabki brif hozir\n"
            "/oqish — o'quvchilar statistikasi; /sinov on|off — sinov rejimi (hech narsa ommaga chiqmaydi)\n"
            "/parol — panel paroli; /startmatn matn — boshqalar Start bossa yuboriladigan xabar"
        ))

    def cmd_partner(self, uid: int, arg: str) -> None:
        parts = [p.strip() for p in arg.split("|")]
        if not parts[0]:
            return self.tg.send_message(uid, "Format: /hamkor Nomi | bloger yoki avtomaktab | foiz | promo (ixtiyoriy)\n"
                                             "Masalan: /hamkor Ali Blog | bloger | 20")
        try:
            pct = float(parts[2]) if len(parts) > 2 and parts[2] else 20
            d = self.app.partners.create(parts[0], parts[1] if len(parts) > 1 else "bloger", pct,
                                         parts[3] if len(parts) > 3 and parts[3] else None)
        except ValueError as exc:
            return self.tg.send_message(uid, f"❌ {esc(exc)}")
        self.tg.send_message(uid, (f"🤝 <b>{esc(d['name'])}</b> qo'shildi ({d['percent']:g}%)\n"
                                   f"Bot havolasi: {esc(d['bot_link'])}\nSayt: {esc(d['site_link'])}\n"
                                   f"Promo-kod: <code>{esc(d['promo'])}</code>\nKabinet (hamkorga bering): {esc(d['portal'])}"))

    def cmd_goal(self, uid: int, arg: str) -> None:
        C = self.app.control
        parts = arg.split()
        if len(parts) >= 2:
            try:
                C.set_goal(parts[0], float(parts[1].replace(",", "")))
            except ValueError as exc:
                return self.tg.send_message(uid, f"❌ {esc(exc)}")
        elif parts:
            return self.tg.send_message(uid, "Format: /maqsad users 500\nKo'rsatkichlar: " + ", ".join(
                f"{k} ({v})" for k, v in __import__("zft.control", fromlist=["METRICS"]).METRICS.items()))
        self.tg.send_message(uid, C.goals_text())

    def cmd_efficiency(self, uid: int, _: str) -> None:
        self.tg.send_message(uid, self.app.control.efficiency_text())

    def cmd_dry(self, uid: int, arg: str) -> None:
        a = arg.strip().lower()
        if a in ("on", "yoq", "yoqish", "1"):
            self.app.control.set_dry_run(True)
        elif a in ("off", "ochir", "o'chir", "0"):
            self.app.control.set_dry_run(False)
        on = self.app.control.dry_run
        self.tg.send_message(uid, ("🧪 SINOV rejimi YOQILGAN: video, izoh, guruh va ommaviy xabarlar ommaga chiqmaydi.\nO'chirish: /sinov off"
                                   if on else "✅ Sinov rejimi o'chiq — tizim haqiqiy rejimda.\nYoqish: /sinov on"))

    def cmd_brief(self, uid: int, _: str) -> None:
        self.app.control.morning_brief()

    def cmd_learning_stats(self, uid: int, _: str) -> None:
        st = self.app.learning.stats()
        self.tg.send_message(uid, (f"📚 <b>O'quvchilar</b>\nImtihon sanasini kiritgan: {st['with_exam_date']}\n"
                                   f"3+ kun ketma-ket: {st['active_streaks']}\nO'rtacha tayyorlik: {st['avg_readiness']}%\n"
                                   f"Imtihon simulyatsiyasi (7 kun): {st['exams_week']} · o'tish {st['exam_pass_rate'] or 0:.0f}%\n"
                                   f"Duellar (7 kun): {st['duels_week']}\nHaqiqiy imtihondan o'tdi: {st['passed_real']} · "
                                   f"o'tmadi: {st['failed_real']}"))

    def cmd_partners(self, uid: int, _: str) -> None:
        self.tg.send_message(uid, self.app.partners.summary_text())

    def cmd_payment(self, uid: int, arg: str) -> None:
        parts = arg.split(maxsplit=2)
        if len(parts) < 2:
            return self.tg.send_message(uid, "Format: /tolov <tg_id yoki @username> <summa> [izoh]\nMasalan: /tolov @ali 99000 premium")
        who, amount = parts[0], parts[1].replace(",", "").replace(" ", "")
        if who.startswith("@"):
            r = self.db.one("SELECT tg_id FROM users WHERE lower(username)=?", (who[1:].lower(),))
            tg_id = r["tg_id"] if r else None
        else:
            tg_id = int(who) if who.isdigit() else None
        if tg_id is None:
            return self.tg.send_message(uid, "Foydalanuvchi topilmadi.")
        try:
            r = self.app.partners.record_payment(tg_id, float(amount), "UZS", parts[2] if len(parts) > 2 else "")
        except ValueError as exc:
            return self.tg.send_message(uid, f"❌ {esc(exc)}")
        self.tg.send_message(uid, "✅ To'lov yozildi." + (f" Hamkor {esc(r['partner'])}: {r['commission']:,.0f} so'm ({r['percent']:g}%)"
                                                         if r["partner"] else ""))

    def cmd_stat(self, uid: int, _: str) -> None:
        self.tg.send_message(uid, self.app.reports.today_summary())

    def cmd_sources(self, uid: int, _: str) -> None:
        self.tg.send_message(uid, self.app.reports.sources_text())

    def cmd_link(self, uid: int, arg: str) -> None:
        code, _, label = arg.partition(" ")
        if not code or not PAYLOAD_RE.match(code) or code.startswith("ref_"):
            return self.tg.send_message(uid, "Format: /link kod Nomi\nKod: lotin harf, raqam, _ yoki - (64 tagacha)")
        self.db.execute("INSERT OR REPLACE INTO links(code, label, created_at) VALUES (?,?,?)",
                        (code, label or code, now()))
        self.db.audit("owner", "link_created", {"code": code, "label": label})
        self.tg.send_message(uid, f"✅ Havola tayyor:\n{self.bot_link(code)}\n\nBu havoladan kelganlar "
                                  f"/manbalar da «{esc(label or code)}» bo'lib ko'rinadi.")

    def cmd_video(self, uid: int, _: str) -> None:
        self.tg.send_message(uid, "🎬 Studiya ishga tushdi: AI video 3–10 daqiqada tayyor bo'ladi.")
        self.app.run_in_background(self.app.hub.make_now, "manual")

    def cmd_report(self, uid: int, arg: str) -> None:
        section = arg.strip().lower()
        if section in ("moliya", "marketolog", "media"):
            self.app.run_in_background(self.app.hub.report_on, section)
        else:
            self.app.run_in_background(self.app.hub.daily_report)

    def cmd_review(self, uid: int, _: str) -> None:
        self.tg.send_message(uid, "📈 Marketolog videolarni tahlil qilyapti…")

        def run():
            rv = self.app.hub.market_review(min_age_hours=6)
            self.tg.send_message(uid, "\n\n".join("• " + esc(r["text"]) for r in rv)[:3900] if rv else
                                 "Tahlil uchun yangi video yo'q (video joylanganiga kamida 6 soat bo'lishi kerak).")
        self.app.run_in_background(run)

    def cmd_auto(self, uid: int, arg: str) -> None:
        if arg.lower() in ("on", "off"):
            self.db.set("auto_publish", "1" if arg.lower() == "on" else "0")
            self.db.audit("owner", "auto_publish", arg.lower())
        state = "yoqilgan ✅ — videolar tasdiqsiz o'zi chiqadi" if self.app.auto_publish else \
            "o'chiq — har video sizning tasdig'ingizdan o'tadi"
        self.tg.send_message(uid, f"Avtopilot: {state}\nO'zgartirish: /avtopilot on yoki /avtopilot off")

    def cmd_budget(self, uid: int, arg: str) -> None:
        if arg.strip():
            try:
                usd = float(arg.replace("$", "").replace(",", ".").strip())
            except ValueError:
                return self.tg.send_message(uid, "Format: /budjet 60  (oylik video budjeti, dollarda)")
            self.app.vbudget.set_monthly(usd)
            self.tg.send_message(uid, f"✅ Oylik video budjeti: {usd:.0f}$. Darhol kuchga kirdi.")
        self.tg.send_message(uid, self.app.reports.budget_text())

    def cmd_finance(self, uid: int, _: str) -> None:
        self.tg.send_message(uid, "💵 <b>Moliya:</b> " + esc(self.app.hub.finance_text()))

    def _ledger(self, uid: int, arg: str, kind: str) -> None:
        parts = arg.split(maxsplit=2)
        try:
            amount = float(parts[0].replace(",", "").replace(" ", ""))
        except (IndexError, ValueError):
            return self.tg.send_message(uid, f"Format: /{'kirim' if kind == 'income' else 'xarajat'} 150000 UZS izoh\n"
                                             "Valyuta: UZS yoki USD")
        cur = parts[1].upper() if len(parts) > 1 and parts[1].upper() in ("UZS", "USD", "SO'M", "SOM") else "UZS"
        cur = "UZS" if cur in ("SO'M", "SOM") else cur
        note = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 and parts[1].upper() not in ("UZS", "USD") else "")
        self.app.hub.add_ledger(kind, amount, cur, note)
        self.tg.send_message(uid, f"✅ Moliya yozib oldi: {amount:,.0f} {cur}")

    def cmd_income(self, uid: int, arg: str) -> None:
        self._ledger(uid, arg, "income")

    def cmd_expense(self, uid: int, arg: str) -> None:
        self._ledger(uid, arg, "expense")

    def cmd_hire(self, uid: int, arg: str) -> None:
        parts = [x.strip() for x in arg.split("|")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            return self.tg.send_message(uid, "Format: /ishgaol Nomi | vazifasi | har kuni soat (ixtiyoriy)\n"
                                             "Masalan: /ishgaol Izohchi | Instagram izohlariga javob loyihasini yozadi | 18")
        hour = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        key = self.app.hub.hire(parts[0], parts[1], parts[1], hour)
        self.tg.send_message(uid, f"✅ «{esc(parts[0])}» ishga olindi. Kodi: <code>{key}</code>\nTopshiriq: /buyur {key} matn")

    def cmd_fire(self, uid: int, arg: str) -> None:
        ok = self.app.hub.fire(arg.strip())
        self.tg.send_message(uid, "✅ Jamoadan chiqarildi" if ok else "Topilmadi. Kodlar: /jamoa")

    def cmd_order(self, uid: int, arg: str) -> None:
        key, _, task = arg.partition(" ")
        if not key or not task.strip():
            return self.tg.send_message(uid, "Format: /buyur kod topshiriq")
        self.app.run_in_background(lambda: self.tg.send_message(uid, esc(self.app.hub.run_custom(key.strip(), task))[:3900]))

    def cmd_market(self, uid: int, _: str) -> None:
        self.tg.send_message(uid, "📈 Marketolog bozorni o'rganyapti (1–3 daqiqa)…")
        self.app.run_in_background(lambda: self.tg.send_message(uid, self._strategy_text(self.app.hub.market_research())))

    def cmd_strategy(self, uid: int, _: str) -> None:
        st = self.app.hub.strategy()
        self.tg.send_message(uid, self._strategy_text(st) if st else "Strategiya hali yo'q. Yaratish: /bozor")

    @staticmethod
    def _strategy_text(st: dict) -> str:
        out = ["📈 <b>Marketolog strategiyasi</b>", esc(st.get("summary", ""))]
        if st.get("pillars"):
            out += ["", "<b>Kontent yo'nalishlari:</b>"] + [f"• {esc(p)}" for p in st["pillars"][:5]]
        if st.get("competitors"):
            out += ["", "<b>Raqobatchilar:</b>"] + [f"• {esc(c.get('name'))}: {esc(c.get('what'))} — zaif tomoni: "
                                                    f"{esc(c.get('weakness'))}" for c in st["competitors"][:5]]
        if st.get("offer_ideas"):
            out += ["", "<b>Taklif g'oyalari (qaror sizda):</b>"] + [f"• {esc(o)}" for o in st["offer_ideas"][:3]]
        return "\n".join(out)[:3900]

    def cmd_team(self, uid: int, _: str) -> None:
        lines = ["<b>ZFT jamoasi</b>"]
        for a in self.app.hub.status():
            st = f"⚙️ ishlayapti: {esc(a['doing'])}" if a["active"] else "💤 dam olyapti"
            extra = f" (<code>{a['key']}</code>)" if a.get("custom") else ""
            lines.append(f"<b>{esc(a['name'])}</b>{extra} — {st} · bugun {a['messages_today']} xabar")
        last = self.app.hub.messages(limit=6)
        names = {k: v["name"] for k, v in self.app.hub.all_agents().items()}
        names["ega"] = "Siz"
        if last:
            lines += ["", "<b>Oxirgi suhbat:</b>"] + [f"{esc(names.get(m['sender'], m['sender']))} → "
                                                      f"{esc(names.get(m['recipient'], m['recipient']))}: {esc(m['text'][:140])}"
                                                      for m in last]
        self.tg.send_message(uid, "\n".join(lines))

    def cmd_task(self, uid: int, arg: str) -> None:
        if len(arg.strip()) < 5:
            return self.tg.send_message(uid, "Topshiriqni yozing, masalan:\n/topshiriq Ota-ona va farzand "
                                             "o'rtasidagi kulgili vaziyat, oxirida Chorraha chiqsin")
        tid = self.app.pipeline.add_task(arg)
        slot, at = self.app.pipeline.next_slot()
        self.tg.send_message(uid, f"✅ Topshiriq #{tid} qabul qilindi. Keyingi video ({at.strftime('%d.%m %H:%M')}) "
                                  "shu asosda tayyorlanadi. Hozir tayyorlash: /video")

    def cmd_tasks(self, uid: int, _: str) -> None:
        rows = self.db.all("SELECT * FROM tasks ORDER BY id DESC LIMIT 10")
        if not rows:
            return self.tg.send_message(uid, "Topshiriq yo'q. Qo'shish: /topshiriq matn")
        st = {"open": "⏳ navbatda", "done": "✅ bajarildi", "cancelled": "✖️ bekor"}
        self.tg.send_message(uid, "\n".join(f"#{r['id']} {st.get(r['status'], r['status'])}"
                                            f"{' → video #' + str(r['post_id']) if r['post_id'] else ''}: "
                                            f"{esc(r['text'][:120])}" for r in rows))

    def cmd_queue(self, uid: int, arg: str) -> None:
        if arg.strip().lower() == "chiqar":
            self.tg.send_message(uid, "📤 E'lon qilinmoqda…")
            self.app.run_in_background(self.app.hub.publish_now)
            return
        rows = self.db.all("SELECT id, status, hook, created_at FROM posts WHERE status IN"
                           " ('pending','approved') ORDER BY id DESC LIMIT 10")
        if not rows:
            return self.tg.send_message(uid, "Navbat bo'sh.", kb([[("🎬 Video yasash", "ad:do:video")]]))
        names = {"pending": "⏳ tasdiq kutyapti", "approved": "✅ tasdiqlangan"}
        lines = [f"#{r['id']} — {names.get(r['status'], r['status'])} — {esc(r['hook'])[:60]}" for r in rows]
        self.tg.send_message(uid, "\n".join(lines), kb([[("📤 Tasdiqlanganini hozir e'lon qilish", "ad:pub")]]))

    # ------------------------------------------------------------ long poll
    def poll_forever(self, stop_event) -> None:
        offset = int(self.db.get("tg_offset", "0") or 0) or None
        log.info("Bot ishga tushdi (@%s)", self.s.bot_username)
        while not stop_event.is_set():
            try:
                updates = self.tg.get_updates(offset, timeout=50)
            except TelegramError as exc:
                log.warning("getUpdates: %s", exc)
                time.sleep(5)
                continue
            for upd in updates:
                offset = upd["update_id"] + 1
                self.handle_update(upd)
                self.db.set("tg_offset", offset)


def requests_quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")
