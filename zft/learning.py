"""Botdagi o'qish tizimi: shaxsiy reja, eslatma, xatolar ustida ishlash, mavzu testi, imtihon simulyatsiyasi,
tayyorlik foizi, nishonlar, duel, haftalik turnir, AI tushuntirish, qaytaruvchi xabar, imtihondan keyingi so'rov.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import math
import os
import random
import re
from typing import TYPE_CHECKING, Any

from .db import now
from .telegram_api import TelegramError, kb

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)
LETTERS = "ABCDEFGHIJ"

SCHEMA = """
CREATE TABLE IF NOT EXISTS answers (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER NOT NULL,
    qid   TEXT NOT NULL,
    ok    INTEGER NOT NULL,
    mode  TEXT,
    at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_answers_user ON answers(tg_id, qid, id);
CREATE INDEX IF NOT EXISTS idx_answers_at ON answers(at);

CREATE TABLE IF NOT EXISTS profiles (
    tg_id          INTEGER PRIMARY KEY,
    exam_date      TEXT,
    remind_hour    INTEGER DEFAULT 19,
    streak         INTEGER DEFAULT 0,
    best_streak    INTEGER DEFAULT 0,
    last_day       TEXT,
    last_winback   INTEGER DEFAULT 0,
    last_reminder  TEXT,
    followup_sent  INTEGER DEFAULT 0,
    passed         INTEGER
);

CREATE TABLE IF NOT EXISTS achievements (
    tg_id INTEGER NOT NULL,
    code  TEXT NOT NULL,
    at    INTEGER NOT NULL,
    PRIMARY KEY (tg_id, code)
);

CREATE TABLE IF NOT EXISTS exams (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id     INTEGER NOT NULL,
    qids      TEXT NOT NULL,
    idx       INTEGER DEFAULT 0,
    correct   INTEGER DEFAULT 0,
    wrong     TEXT DEFAULT '[]',
    started   INTEGER NOT NULL,
    deadline  INTEGER NOT NULL,
    finished  INTEGER,
    passed    INTEGER
);

CREATE TABLE IF NOT EXISTS duels (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    a        INTEGER NOT NULL,
    b        INTEGER,
    qids     TEXT NOT NULL,
    a_idx    INTEGER DEFAULT 0, b_idx INTEGER DEFAULT 0,
    a_score  INTEGER DEFAULT 0, b_score INTEGER DEFAULT 0,
    a_start  INTEGER, b_start INTEGER,
    a_time   INTEGER, b_time INTEGER,
    status   TEXT DEFAULT 'waiting',   -- waiting | active | done | expired
    created  INTEGER NOT NULL
);
"""

ACHIEVEMENTS = {
    "first": "🌱 Birinchi qadam — birinchi javob",
    "streak3": "🔥 3 kun ketma-ket",
    "streak7": "🔥 7 kun ketma-ket",
    "streak30": "🏆 30 kun ketma-ket",
    "correct50": "✅ 50 ta to'g'ri javob",
    "correct200": "✅ 200 ta to'g'ri javob",
    "exam_pass": "🎓 Imtihon simulyatsiyasidan o'tdi",
    "exam_perfect": "💯 Imtihonni xatosiz yechdi",
    "duel_win": "⚔️ Duelda g'alaba",
    "ref3": "👥 3 ta do'st taklif qildi",
    "ready80": "🚦 Tayyorlik 80% dan oshdi",
}


def esc(s: Any) -> str:
    return html.escape(str(s or ""))


def parse_date(text: str, today: dt.date) -> dt.date | None:
    """'20.11', '20.11.2026', '2026-11-20', '20 noyabr' → sana."""
    t = text.strip().lower()
    months = {"yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5, "iyun": 6, "iyul": 7, "avgust": 8,
              "sentyabr": 9, "oktyabr": 10, "noyabr": 11, "dekabr": 12}
    explicit_year = True
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", t)
        if m:
            d, mo = int(m.group(1)), int(m.group(2))
            explicit_year = bool(m.group(3))
            y = int(m.group(3)) if m.group(3) else today.year
            y = y + 2000 if y < 100 else y
        else:
            m = re.fullmatch(r"(\d{1,2})\s+([a-z']+)", t)
            if not m or m.group(2) not in months:
                return None
            d, mo, y, explicit_year = int(m.group(1)), months[m.group(2)], today.year, False
    try:
        out = dt.date(y, mo, d)
        if out < today and not explicit_year:
            out = dt.date(y + 1, mo, d)
    except ValueError:
        return None
    return out if out >= today else None


class Learning:
    def __init__(self, app: "App"):
        self.app = app
        self.db = app.db
        self.db.conn.executescript(SCHEMA)
        self.exam_n = int(os.environ.get("EXAM_QUESTIONS", "20"))
        self.exam_max_err = int(os.environ.get("EXAM_MAX_ERRORS", "2"))
        self.exam_minutes = int(os.environ.get("EXAM_MINUTES", "25"))
        self.duel_n = 5

    # ------------------------------------------------------------ yordamchi
    @property
    def tg(self):
        return self.app.tg

    def send(self, uid: int, text: str, markup: dict | None = None) -> None:
        try:
            self.tg.send_message(uid, text, markup)
        except TelegramError as exc:
            log.info("Yuborilmadi %s: %s", uid, exc)
            if "blocked" in str(exc).lower():
                self.db.execute("UPDATE users SET blocked=1 WHERE tg_id=?", (uid,))

    def today(self) -> dt.date:
        return self.app.local_now().date()

    def profile(self, uid: int) -> dict:
        r = self.db.one("SELECT * FROM profiles WHERE tg_id=?", (uid,))
        if not r:
            self.db.execute("INSERT OR IGNORE INTO profiles(tg_id) VALUES (?)", (uid,))
            r = self.db.one("SELECT * FROM profiles WHERE tg_id=?", (uid,))
        return dict(r)

    def set_mode(self, uid: int, mode: dict | None) -> None:
        self.db.set(f"mode:{uid}", json.dumps(mode) if mode else "")

    def mode(self, uid: int) -> dict:
        raw = self.db.get(f"mode:{uid}") or ""
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {}

    def menu(self) -> dict:
        return kb([
            [("🧪 Mashq", "quiz"), ("🎯 Imtihon (20 savol)", "m:exam")],
            [("📚 Mavzu bo'yicha", "m:topics"), ("🔁 Xatolarim", "m:mistakes")],
            [("📅 Shaxsiy reja", "m:plan"), ("📊 Natijam", "m:stats")],
            [("⚔️ Do'st bilan duel", "m:duel"), ("🏆 Reyting", "top")],
            [("👥 Do'stni taklif qilish", "ref")],
        ])

    # --------------------------------------------------------- javob yozish
    def record(self, uid: int, qid: str, ok: bool, mode: str = "practice") -> list[str]:
        self.db.execute("INSERT INTO answers(tg_id, qid, ok, mode, at) VALUES (?,?,?,?,?)",
                        (uid, qid, 1 if ok else 0, mode, now()))
        self.db.record_quiz(uid, ok)
        self.db.event("quiz_a", uid, {"q": qid, "ok": ok, "mode": mode})
        self._streak(uid)
        return self.check_achievements(uid)

    def _streak(self, uid: int) -> None:
        p = self.profile(uid)
        today = self.today()
        last = dt.date.fromisoformat(p["last_day"]) if p["last_day"] else None
        if last == today:
            return
        streak = p["streak"] + 1 if last == today - dt.timedelta(days=1) else 1
        self.db.execute("UPDATE profiles SET streak=?, best_streak=MAX(best_streak, ?), last_day=? WHERE tg_id=?",
                        (streak, streak, today.isoformat(), uid))

    def award(self, uid: int, code: str) -> bool:
        cur = self.db.execute("INSERT OR IGNORE INTO achievements(tg_id, code, at) VALUES (?,?,?)", (uid, code, now()))
        return cur.rowcount > 0

    def check_achievements(self, uid: int) -> list[str]:
        p = self.profile(uid)
        u = self.db.user(uid)
        correct = u["quiz_correct"] if u else 0
        new = []
        conds = {"first": True, "streak3": p["streak"] >= 3, "streak7": p["streak"] >= 7, "streak30": p["streak"] >= 30,
                 "correct50": correct >= 50, "correct200": correct >= 200, "ref3": self.db.ref_count(uid) >= 3}
        for code, ok in conds.items():
            if ok and self.award(uid, code):
                new.append(code)
        if self.readiness(uid)["percent"] >= 80 and self.award(uid, "ready80"):
            new.append("ready80")
        return new

    def announce_achievements(self, uid: int, codes: list[str]) -> None:
        for c in codes:
            if c == "first":
                continue  # birinchi javob uchun alohida xabar shart emas
            self.send(uid, f"🎖 <b>Yangi nishon!</b>\n{esc(ACHIEVEMENTS.get(c, c))}\n\nBarcha nishonlaringiz: /natija")

    # ------------------------------------------------------------ tayyorlik
    def latest_answers(self, uid: int) -> dict[str, int]:
        rows = self.db.all("SELECT a.qid, a.ok FROM answers a JOIN (SELECT qid, MAX(id) mid FROM answers WHERE tg_id=?"
                           " GROUP BY qid) x ON a.id=x.mid", (uid,))
        return {r["qid"]: r["ok"] for r in rows}

    def readiness(self, uid: int) -> dict:
        n = max(1, len(self.app.bank))
        latest = self.latest_answers(uid)
        mastered = sum(1 for v in latest.values() if v)
        recent = self.db.all("SELECT ok FROM answers WHERE tg_id=? ORDER BY id DESC LIMIT 60", (uid,))
        acc = sum(r["ok"] for r in recent) / len(recent) if recent else 0.0
        best_exam = self.db.one("SELECT MAX(correct) c, COUNT(*) n FROM exams WHERE tg_id=? AND finished IS NOT NULL", (uid,))
        exam_part = (best_exam["c"] or 0) / self.exam_n if best_exam and best_exam["n"] else acc
        pct = round(100 * (0.5 * mastered / n + 0.3 * acc + 0.2 * exam_part))
        if len(recent) < 10:
            pct = min(pct, 30)  # kam javob bilan yuqori foiz ko'rsatilmaydi
        return {"percent": max(0, min(99, pct)), "mastered": mastered, "total": n,
                "accuracy": round(acc * 100), "answered": len(latest),
                "wrong_now": sum(1 for v in latest.values() if not v)}

    def plan(self, uid: int) -> dict:
        p = self.profile(uid)
        r = self.readiness(uid)
        left = r["total"] - r["mastered"]
        days = None
        if p["exam_date"]:
            days = (dt.date.fromisoformat(p["exam_date"]) - self.today()).days
        if days is not None and days > 0:
            goal = max(10, min(60, math.ceil(left / days * 1.3)))  # takrorlash uchun 30% zaxira
        else:
            goal = 15
        done_today = self.db.scalar("SELECT COUNT(*) FROM answers WHERE tg_id=? AND at>=?", (uid, self.app.local_day_start()))
        return {"goal": goal, "done_today": done_today, "days_left": days, "left": left, **r}

    # -------------------------------------------------------- mashq savoli
    def pick(self, uid: int, mode: dict) -> Any:
        bank = self.app.bank
        recent = {r["qid"] for r in self.db.all("SELECT qid FROM answers WHERE tg_id=? ORDER BY id DESC LIMIT 30", (uid,))}
        kind = mode.get("type")
        if kind == "mistakes":
            wrong = [q for q, ok in self.latest_answers(uid).items() if not ok and bank.by_id(q)]
            if not wrong:
                return None
            return bank.by_id(random.choice(wrong))
        if kind == "topic":
            return bank.pick(exclude=recent, topic=mode.get("topic"))
        # oddiy mashq: avval hali yechilmagan va xato qilingan savollar
        latest = self.latest_answers(uid)
        fresh = [q for q in bank.questions if q.id not in latest and q.id not in recent]
        if fresh and random.random() < 0.7:
            return random.choice(fresh)
        return bank.pick(exclude=recent)

    def send_practice(self, uid: int, mode: dict | None = None) -> None:
        mode = mode if mode is not None else self.mode(uid)
        q = self.pick(uid, mode)
        if q is None:
            self.set_mode(uid, None)
            return self.send(uid, "🎉 Hozircha xato qilingan savolingiz qolmadi! Yangi savollar bilan davom etamiz.",
                             kb([[("🧪 Mashq", "quiz")]]))
        self.set_mode(uid, mode)
        self.db.event("quiz_q", uid, q.id)
        head = {"mistakes": "🔁 Xatolar ustida ishlash", "topic": f"📚 {esc(mode.get('topic', ''))}"}.get(mode.get("type"), "")
        lines = ([f"<i>{head}</i>"] if head else []) + [f"<b>{esc(q.question)}</b>", ""]
        lines += [f"{LETTERS[i]}) {esc(o)}" for i, o in enumerate(q.options)]
        buttons = [[(LETTERS[i], f"a:{q.id}:{i}") for i in range(len(q.options))]]
        self._send_q(uid, q, "\n".join(lines), buttons)

    def _send_q(self, uid: int, q, text: str, buttons) -> None:
        if q.image:
            try:
                with open(q.image, "rb") as f:
                    self.tg.call("sendPhoto", files={"photo": f}, chat_id=uid, caption=text[:1024], parse_mode="HTML",
                                 reply_markup=kb(buttons))
                return
            except (OSError, TelegramError) as exc:
                log.warning("Rasm yuborilmadi: %s", exc)
        self.send(uid, text, kb(buttons))

    def after_answer_buttons(self, uid: int, qid: str) -> list:
        mode = self.mode(uid)
        nxt = {"mistakes": "➡️ Keyingi xato", "topic": "➡️ Shu mavzudan keyingi"}.get(mode.get("type"), "➡️ Keyingi savol")
        return [[(nxt, "nx")], [("🤔 Nega shu javob?", f"why:{qid}")], [("📋 Menyu", "m:menu")]]

    # ---------------------------------------------------------- AI tushuntirish
    def explain(self, uid: int, qid: str) -> None:
        q = self.app.bank.by_id(qid)
        if not q:
            return
        cached = self.db.get(f"why:{qid}")
        if not cached:
            text = self.app.brain.ask(
                "explain", f"Savol: {q.question}\nVariantlar: {q.options}\nTo'g'ri javob: {q.correct_text}\n"
                           f"Rasmiy izoh: {q.explanation or 'yo`q'}\n\nO'quvchiga 3-4 gapda tushuntir: nega aynan shu javob to'g'ri, "
                           "boshqalari nega noto'g'ri. Yo'l harakati qoidalariga zid yoki to'qima ma'lumot yozma; ishonching "
                           "komil bo'lmasa, faqat rasmiy izohga tayan. Sodda o'zbek tilida.",
                max_tokens=300, system="Sen haydovchilik nazariy imtihoni bo'yicha ehtiyotkor ustozsan.")
            cached = text or (q.explanation or "Bu savol bo'yicha batafsil tushuntirish saytda bor.")
            if text:
                self.db.set(f"why:{qid}", cached)
        self.send(uid, f"🤔 <b>Nega «{esc(q.correct_text)}»?</b>\n\n{esc(cached)}",
                  kb([[("➡️ Davom etish", "nx")], [("🌐 Saytda batafsil", "url:" + self.app.settings.site_url)]]))

    # ------------------------------------------------------------- imtihon
    def start_exam(self, uid: int) -> None:
        bank = self.app.bank
        n = min(self.exam_n, len(bank))
        if n == 0:
            return self.send(uid, "Savollar hali yuklanmagan.")
        self.db.execute("UPDATE exams SET finished=? WHERE tg_id=? AND finished IS NULL", (now(), uid))
        qids = [q.id for q in random.sample(bank.questions, n)]
        cur = self.db.execute("INSERT INTO exams(tg_id, qids, started, deadline) VALUES (?,?,?,?)",
                              (uid, json.dumps(qids), now(), now() + self.exam_minutes * 60))
        self.send(uid, f"🎯 <b>Imtihon simulyatsiyasi</b>\n{n} ta savol · {self.exam_minutes} daqiqa · "
                       f"ko'pi bilan {self.exam_max_err} ta xato mumkin.\nOmad! 🍀")
        self._exam_q(int(cur.lastrowid))

    def _exam_q(self, eid: int) -> None:
        e = self.db.one("SELECT * FROM exams WHERE id=?", (eid,))
        qids = json.loads(e["qids"])
        if e["idx"] >= len(qids) or now() > e["deadline"]:
            return self.finish_exam(eid)
        q = self.app.bank.by_id(qids[e["idx"]])
        left = max(0, (e["deadline"] - now()) // 60)
        text = f"<i>Savol {e['idx'] + 1}/{len(qids)} · {left} daqiqa qoldi</i>\n\n<b>{esc(q.question)}</b>\n\n" + \
            "\n".join(f"{LETTERS[i]}) {esc(o)}" for i, o in enumerate(q.options))
        self._send_q(e["tg_id"], q, text, [[(LETTERS[i], f"ex:{eid}:{e['idx']}:{i}") for i in range(len(q.options))]])

    def exam_answer(self, uid: int, eid: int, idx: int, choice: int) -> None:
        e = self.db.one("SELECT * FROM exams WHERE id=? AND tg_id=?", (eid, uid))
        if not e or e["finished"] or e["idx"] != idx:
            return  # eski tugma qayta bosildi
        qids = json.loads(e["qids"])
        q = self.app.bank.by_id(qids[idx])
        ok = q is not None and choice == q.correct
        wrong = json.loads(e["wrong"])
        if not ok:
            wrong.append(qids[idx])
        self.record(uid, qids[idx], ok, "exam")
        self.db.execute("UPDATE exams SET idx=idx+1, correct=correct+?, wrong=? WHERE id=?",
                        (1 if ok else 0, json.dumps(wrong), eid))
        if len(wrong) > self.exam_max_err and not self.db.get(f"exam_fail_note:{eid}"):
            self.db.set(f"exam_fail_note:{eid}", 1)
            self.send(uid, f"⚠️ {len(wrong)} ta xato — haqiqiy imtihonda bu o'tmaydi. Oxirigacha yeching, keyin xatolarni ko'rib chiqamiz.")
        self._exam_q(eid)

    def finish_exam(self, eid: int) -> None:
        e = self.db.one("SELECT * FROM exams WHERE id=?", (eid,))
        if e["finished"]:
            return
        qids, wrong = json.loads(e["qids"]), json.loads(e["wrong"])
        unanswered = len(qids) - e["idx"]
        errors = len(wrong) + unanswered
        passed = errors <= self.exam_max_err
        self.db.execute("UPDATE exams SET finished=?, passed=? WHERE id=?", (now(), 1 if passed else 0, eid))
        uid = e["tg_id"]
        mins = max(1, (now() - e["started"]) // 60)
        text = (f"{'🎓 <b>O‘tdingiz!</b>' if passed else '😔 <b>Bu safar o‘tmadi.</b>'}\n"
                f"To'g'ri: {e['correct']}/{len(qids)} · xato: {len(wrong)}" + (f" · javobsiz: {unanswered}" if unanswered else "")
                + f" · {mins} daqiqa\nTayyorlik: <b>{self.readiness(uid)['percent']}%</b>")
        new = []
        if passed and self.award(uid, "exam_pass"):
            new.append("exam_pass")
        if errors == 0 and self.award(uid, "exam_perfect"):
            new.append("exam_perfect")
        btns = [[("🔁 Xatolar ustida ishlash", "m:mistakes")], [("🎯 Yana imtihon", "m:exam"), ("📋 Menyu", "m:menu")]]
        self.send(uid, text, kb(btns))
        self.announce_achievements(uid, new)

    # ---------------------------------------------------------------- duel
    def create_duel(self, uid: int) -> None:
        bank = self.app.bank
        n = min(self.duel_n, len(bank))
        qids = [q.id for q in random.sample(bank.questions, n)]
        cur = self.db.execute("INSERT INTO duels(a, qids, created) VALUES (?,?,?)", (uid, json.dumps(qids), now()))
        did = int(cur.lastrowid)
        link = f"https://t.me/{self.app.settings.bot_username}?start=duel_{did}"
        from urllib.parse import quote
        share = "https://t.me/share/url?url=" + quote(link, safe="") + "&text=" + quote(
            "Prava savollari bo'yicha duel! Kim ko'proq to'g'ri javob beradi? ⚔️", safe="")
        self.send(uid, f"⚔️ <b>Duel tayyor</b> — {n} ta bir xil savol.\nDo'stingizga yuboring:\n{link}\n\n"
                       "U qo'shilishi bilan ikkalangizga savollar keladi.", kb([[("📤 Do'stga yuborish", "url:" + share)]]))

    def join_duel(self, uid: int, did: int) -> None:
        d = self.db.one("SELECT * FROM duels WHERE id=?", (did,))
        if not d or d["status"] != "waiting" or now() - d["created"] > 86400:
            return self.send(uid, "Bu duel tugagan yoki topilmadi. O'zingiz yangisini boshlang:", kb([[("⚔️ Duel", "m:duel")]]))
        if d["a"] == uid:
            return self.send(uid, "Bu o'zingizning duelingiz — havolani do'stingizga yuboring 🙂")
        self.db.execute("UPDATE duels SET b=?, status='active', a_start=?, b_start=? WHERE id=?", (uid, now(), now(), did))
        for player in (d["a"], uid):
            self.send(player, "⚔️ <b>Duel boshlandi!</b> Tezlik ham hisobga olinadi.")
            self._duel_q(did, player)

    def _duel_q(self, did: int, uid: int) -> None:
        d = self.db.one("SELECT * FROM duels WHERE id=?", (did,))
        side = "a" if d["a"] == uid else "b"
        qids = json.loads(d["qids"])
        idx = d[f"{side}_idx"]
        if idx >= len(qids):
            return self._duel_done(did, uid, side)
        q = self.app.bank.by_id(qids[idx])
        text = f"<i>Duel · {idx + 1}/{len(qids)}</i>\n\n<b>{esc(q.question)}</b>\n\n" + \
            "\n".join(f"{LETTERS[i]}) {esc(o)}" for i, o in enumerate(q.options))
        self._send_q(uid, q, text, [[(LETTERS[i], f"du:{did}:{idx}:{i}") for i in range(len(q.options))]])

    def duel_answer(self, uid: int, did: int, idx: int, choice: int) -> None:
        d = self.db.one("SELECT * FROM duels WHERE id=?", (did,))
        if not d or d["status"] != "active" or uid not in (d["a"], d["b"]):
            return
        side = "a" if d["a"] == uid else "b"
        if d[f"{side}_idx"] != idx:
            return
        qids = json.loads(d["qids"])
        q = self.app.bank.by_id(qids[idx])
        ok = q is not None and choice == q.correct
        self.record(uid, qids[idx], ok, "duel")
        self.db.execute(f"UPDATE duels SET {side}_idx={side}_idx+1, {side}_score={side}_score+? WHERE id=?",
                        (1 if ok else 0, did))
        self._duel_q(did, uid)

    def _duel_done(self, did: int, uid: int, side: str) -> None:
        d = self.db.one("SELECT * FROM duels WHERE id=?", (did,))
        if not d[f"{side}_time"]:
            self.db.execute(f"UPDATE duels SET {side}_time=? WHERE id=?", (now() - d[f"{side}_start"], did))
        d = self.db.one("SELECT * FROM duels WHERE id=?", (did,))
        if not (d["a_time"] is not None and d["b_time"] is not None):
            return self.send(uid, "✅ Tugatdingiz! Raqibingiz tugatishini kutamiz…")
        self.db.execute("UPDATE duels SET status='done' WHERE id=?", (did,))
        a_key, b_key = (d["a_score"], -d["a_time"]), (d["b_score"], -d["b_time"])
        winner = d["a"] if a_key > b_key else d["b"] if b_key > a_key else None
        names = {r["tg_id"]: r["first_name"] for r in self.db.all("SELECT tg_id, first_name FROM users WHERE tg_id IN (?,?)",
                                                                  (d["a"], d["b"]))}
        res = (f"⚔️ <b>Duel natijasi</b>\n{esc(names.get(d['a'], 'O‘yinchi 1'))}: {d['a_score']} ({d['a_time']} s)\n"
               f"{esc(names.get(d['b'], 'O‘yinchi 2'))}: {d['b_score']} ({d['b_time']} s)\n\n"
               + (f"🏆 G'olib: <b>{esc(names.get(winner, ''))}</b>" if winner else "🤝 Durang!"))
        for p in (d["a"], d["b"]):
            self.send(p, res, kb([[("⚔️ Revansh", "m:duel"), ("📋 Menyu", "m:menu")]]))
        if winner and self.award(winner, "duel_win"):
            self.announce_achievements(winner, ["duel_win"])

    # ---------------------------------------------------------------- reja
    def ask_exam_date(self, uid: int) -> None:
        self.db.set(f"await:{uid}", "exam_date")
        self.send(uid, "📅 Imtihoningiz qachon? Sanani yozing, masalan: <b>20.11</b> yoki <b>2026-11-20</b>.\n"
                       "Hali bilmasangiz: <b>bilmayman</b>")

    def handle_text(self, uid: int, text: str) -> bool:
        aw = self.db.get(f"await:{uid}")
        if not aw:
            return False
        self.db.set(f"await:{uid}", "")
        if aw == "exam_date":
            if text.strip().lower() in ("bilmayman", "yo'q", "yoq", "-"):
                self.db.execute("UPDATE profiles SET exam_date=NULL WHERE tg_id=?", (uid,))
                self.send_plan(uid)
                return True
            d = parse_date(text, self.today())
            if not d:
                self.db.set(f"await:{uid}", "exam_date")
                self.send(uid, "Sanani tushunmadim. Masalan: 20.11 yoki 2026-11-20")
                return True
            self.profile(uid)
            self.db.execute("UPDATE profiles SET exam_date=?, followup_sent=0 WHERE tg_id=?", (d.isoformat(), uid))
            self.send_plan(uid)
            return True
        if aw == "remind_hour":
            if text.strip().isdigit() and 7 <= int(text.strip()) <= 22:
                self.db.execute("UPDATE profiles SET remind_hour=? WHERE tg_id=?", (int(text.strip()), uid))
                self.send(uid, f"⏰ Har kuni soat {int(text.strip()):02d}:00 da eslataman.", self.menu())
            else:
                self.send(uid, "7 dan 22 gacha soatni yozing, masalan: 19")
                self.db.set(f"await:{uid}", "remind_hour")
            return True
        return False

    def send_plan(self, uid: int) -> None:
        p = self.plan(uid)
        prof = self.profile(uid)
        lines = ["📅 <b>Shaxsiy reja</b>"]
        if p["days_left"] is not None and p["days_left"] >= 0:
            lines.append(f"Imtihongacha: <b>{p['days_left']} kun</b> ({esc(prof['exam_date'])})")
        else:
            lines.append("Imtihon sanasi kiritilmagan — standart reja.")
        lines += [f"Kunlik maqsad: <b>{p['goal']} ta savol</b> (bugun {p['done_today']} ta yechdingiz)",
                  f"O'zlashtirilgan: {p['mastered']}/{p['total']} · tayyorlik <b>{p['percent']}%</b>",
                  f"Eslatma vaqti: {prof['remind_hour']:02d}:00"]
        self.send(uid, "\n".join(lines), kb([[("🧪 Bugungi mashq", "quiz")],
                                             [("📅 Sanani o'zgartirish", "m:setdate"), ("⏰ Eslatma vaqti", "m:sethour")],
                                             [("📋 Menyu", "m:menu")]]))

    def send_stats(self, uid: int) -> None:
        r = self.readiness(uid)
        p = self.profile(uid)
        ach = [ACHIEVEMENTS[c["code"]] for c in self.db.all("SELECT code FROM achievements WHERE tg_id=? ORDER BY at", (uid,))
               if c["code"] in ACHIEVEMENTS]
        exams = self.db.one("SELECT COUNT(*) n, SUM(passed) p FROM exams WHERE tg_id=? AND finished IS NOT NULL", (uid,))
        bar = "🟩" * (r["percent"] // 10) + "⬜" * (10 - r["percent"] // 10)
        text = (f"📊 <b>Natijangiz</b>\n🚦 Tayyorlik: <b>{r['percent']}%</b>\n{bar}\n\n"
                f"O'zlashtirilgan: {r['mastered']}/{r['total']} · hozir xato turganlari: {r['wrong_now']}\n"
                f"Oxirgi javoblar aniqligi: {r['accuracy']}%\n"
                f"Imtihon simulyatsiyasi: {exams['n'] or 0} marta, o'tgan: {exams['p'] or 0}\n"
                f"🔥 Ketma-ket kunlar: {p['streak']} (eng yaxshisi {p['best_streak']})\n\n"
                "<b>Nishonlar:</b>\n" + ("\n".join(ach) if ach else "hali yo'q — birinchi savoldan boshlang!"))
        self.send(uid, text, kb([[("🔁 Xatolarim", "m:mistakes"), ("🎯 Imtihon", "m:exam")], [("📋 Menyu", "m:menu")]]))

    def send_topics(self, uid: int) -> None:
        topics = sorted(self.app.bank.topics().items(), key=lambda x: -x[1])[:24]
        self.db.set(f"topics:{uid}", json.dumps([t for t, _ in topics]))
        rows, row = [], []
        for i, (t, n) in enumerate(topics):
            row.append((f"{t} ({n})", f"tp:{i}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        self.send(uid, "📚 Qaysi mavzu?", kb(rows or [[("🧪 Mashq", "quiz")]]))

    # ---------------------------------------------------------- callbacklar
    def handle_callback(self, uid: int, data: str) -> bool:
        if data == "nx":
            self.send_practice(uid)
        elif data == "m:menu":
            self.set_mode(uid, None)
            self.send(uid, "📋 Nima qilamiz?", self.menu())
        elif data == "m:exam":
            self.start_exam(uid)
        elif data == "m:topics":
            self.send_topics(uid)
        elif data == "m:mistakes":
            self.send_practice(uid, {"type": "mistakes"})
        elif data == "m:plan":
            if not self.profile(uid)["exam_date"]:
                self.ask_exam_date(uid)
            else:
                self.send_plan(uid)
        elif data == "m:setdate":
            self.ask_exam_date(uid)
        elif data == "m:sethour":
            self.db.set(f"await:{uid}", "remind_hour")
            self.send(uid, "⏰ Har kuni soat nechada eslatay? (7–22), masalan: 19")
        elif data == "m:stats":
            self.send_stats(uid)
        elif data == "m:duel":
            self.create_duel(uid)
        elif data.startswith("tp:"):
            topics = json.loads(self.db.get(f"topics:{uid}") or "[]")
            i = int(data[3:])
            if i < len(topics):
                self.send_practice(uid, {"type": "topic", "topic": topics[i]})
        elif data.startswith("why:"):
            self.explain(uid, data[4:])
        elif data.startswith("ex:"):
            _, eid, idx, ch = data.split(":")
            self.exam_answer(uid, int(eid), int(idx), int(ch))
        elif data.startswith("du:"):
            _, did, idx, ch = data.split(":")
            self.duel_answer(uid, int(did), int(idx), int(ch))
        elif data.startswith("pe:"):
            self.exam_followup_answer(uid, data[3:])
        else:
            return False
        return True

    # -------------------------------------------------------- rejali ishlar
    def daily_reminders(self, hour: int) -> int:
        """Shu soatni tanlagan, bugun hali savol yechmagan foydalanuvchilarga eslatma."""
        if self.app.dry_run:
            return 0
        day = self.today().isoformat()
        day0 = self.app.local_day_start()
        rows = self.db.all("SELECT p.tg_id FROM profiles p JOIN users u ON u.tg_id=p.tg_id WHERE u.blocked=0 AND "
                           "COALESCE(p.remind_hour,19)=? AND COALESCE(p.last_reminder,'')!=? AND "
                           "NOT EXISTS (SELECT 1 FROM answers a WHERE a.tg_id=p.tg_id AND a.at>=?) LIMIT 2000",
                           (hour, day, day0))
        n = 0
        for r in rows:
            p = self.plan(r["tg_id"])
            extra = f" Imtihongacha {p['days_left']} kun." if p["days_left"] and p["days_left"] > 0 else ""
            self.send(r["tg_id"], f"⏰ Bugungi <b>{p['goal']} ta savol</b>ingiz tayyor.{extra}\n"
                                  f"Tayyorlik: {p['percent']}% 🔥 Ketma-ketlikni uzmang!",
                      kb([[("🧪 Boshlash", "quiz")], [("🔕 Vaqtni o'zgartirish", "m:sethour")]]))
            self.db.execute("UPDATE profiles SET last_reminder=? WHERE tg_id=?", (day, r["tg_id"]))
            n += 1
        return n

    def winback(self) -> int:
        """3 kundan beri kelmaganlarga (haftada ko'pi bilan 1 marta) qaytaruvchi xabar."""
        if self.app.dry_run:
            return 0
        rows = self.db.all("SELECT u.tg_id, u.first_name FROM users u LEFT JOIN profiles p ON p.tg_id=u.tg_id "
                           "WHERE u.blocked=0 AND u.last_seen<? AND u.last_seen>? AND COALESCE(p.last_winback,0)<? LIMIT 1000",
                           (now() - 3 * 86400, now() - 30 * 86400, now() - 7 * 86400))
        for r in rows:
            self.profile(r["tg_id"])
            rd = self.readiness(r["tg_id"])
            self.send(r["tg_id"], f"Salom, {esc(r['first_name'] or 'do‘st')}! 👋 Sizni sog'indik.\n"
                                  f"Tayyorligingiz {rd['percent']}% da turibdi — bugun 5 daqiqa ajratsangiz, oldinga siljiymiz.",
                      kb([[("🧪 5 ta savol", "quiz")], [("🎯 Imtihon", "m:exam")]]))
            self.db.execute("UPDATE profiles SET last_winback=? WHERE tg_id=?", (now(), r["tg_id"]))
        return len(rows)

    def exam_followups(self) -> int:
        yesterday = (self.today() - dt.timedelta(days=1)).isoformat()
        rows = self.db.all("SELECT p.tg_id FROM profiles p JOIN users u ON u.tg_id=p.tg_id WHERE u.blocked=0 AND "
                           "p.exam_date IS NOT NULL AND p.exam_date<=? AND p.followup_sent=0", (yesterday,))
        for r in rows:
            self.send(r["tg_id"], "🚗 Imtihoningiz qanday o'tdi?",
                      kb([[("🎉 O'tdim!", "pe:yes"), ("😔 O'tmadim", "pe:no")], [("📅 Hali topshirmadim", "pe:later")]]))
            self.db.execute("UPDATE profiles SET followup_sent=1 WHERE tg_id=?", (r["tg_id"],))
        return len(rows)

    def exam_followup_answer(self, uid: int, ans: str) -> None:
        if ans == "yes":
            self.db.execute("UPDATE profiles SET passed=1 WHERE tg_id=?", (uid,))
            self.send(uid, "🎉 Tabriklaymiz! Endi xavfsiz yo'l tilaymiz.\n\nDo'stlaringiz ham tayyorlanayotgan bo'lsa, "
                           "ularga botni ulashing:", kb([[("👥 Do'stni taklif qilish", "ref")]]))
        elif ans == "no":
            self.db.execute("UPDATE profiles SET passed=0, exam_date=NULL WHERE tg_id=?", (uid,))
            self.send(uid, "Xafa bo'lmang — ko'pchilik ikkinchi urinishda o'tadi. Xatolaringiz ustida ishlaymiz.\n"
                           "Keyingi imtihon sanasini kiriting:", kb([[("📅 Sana kiritish", "m:setdate")],
                                                                     [("🔁 Xatolarim", "m:mistakes")]]))
        else:
            self.ask_exam_date(uid)
        self.db.event("exam_result", uid, ans)

    def weekly_tournament(self) -> dict | None:
        """Yakshanba kechqurun: hafta g'oliblari (to'g'ri javoblar soni bo'yicha)."""
        if self.app.dry_run:
            return None
        since = now() - 7 * 86400
        rows = self.db.all("SELECT a.tg_id, u.first_name, SUM(a.ok) c, COUNT(*) n FROM answers a JOIN users u ON u.tg_id=a.tg_id "
                           "WHERE a.at>=? GROUP BY a.tg_id HAVING c>0 ORDER BY c DESC, n ASC LIMIT 10", (since,))
        if not rows:
            return None
        medals = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, 11)]
        text = "🏆 <b>Haftalik turnir natijalari</b>\n" + "\n".join(
            f"{medals[i]} {esc(r['first_name'])} — {r['c']} ta to'g'ri" for i, r in enumerate(rows)) + \
            "\n\nYangi hafta boshlandi — /test bilan qo'shiling!"
        gid = self.app.conn.group_id()
        if gid:
            try:
                self.tg.send_message(gid, text)
            except TelegramError:
                pass
        for i, r in enumerate(rows[:3]):
            self.send(r["tg_id"], f"{medals[i]} Siz bu haftaning {i + 1}-o'rindagi g'olibisiz! Tabriklaymiz!")
        self.db.event("tournament", None, [dict(r) for r in rows[:3]])
        return {"winners": [dict(r) for r in rows[:3]], "text": text}

    # ----------------------------------------------------------- statistika
    def stats(self) -> dict:
        n = len(self.app.bank) or 1
        users = [r["tg_id"] for r in self.db.all("SELECT DISTINCT tg_id FROM answers WHERE at>=?", (now() - 14 * 86400,))]
        ready = [self.readiness(u)["percent"] for u in users[:300]]
        return {"with_exam_date": self.db.scalar("SELECT COUNT(*) FROM profiles WHERE exam_date IS NOT NULL"),
                "active_streaks": self.db.scalar("SELECT COUNT(*) FROM profiles WHERE streak>=3 AND last_day>=?",
                                                 ((self.today() - dt.timedelta(days=1)).isoformat(),)),
                "avg_readiness": round(sum(ready) / len(ready)) if ready else 0,
                "exams_week": self.db.scalar("SELECT COUNT(*) FROM exams WHERE started>=?", (now() - 7 * 86400,)),
                "exam_pass_rate": self.db.scalar("SELECT ROUND(100.0*AVG(passed)) FROM exams WHERE finished IS NOT NULL AND started>=?",
                                                 (now() - 30 * 86400,), 0),
                "duels_week": self.db.scalar("SELECT COUNT(*) FROM duels WHERE created>=?", (now() - 7 * 86400,)),
                "passed_real": self.db.scalar("SELECT COUNT(*) FROM profiles WHERE passed=1"),
                "failed_real": self.db.scalar("SELECT COUNT(*) FROM profiles WHERE passed=0"),
                "bank": n}
