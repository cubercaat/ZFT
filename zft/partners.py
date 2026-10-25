"""Hamkorlar (blogerlar, avtomaktablar) va oylik referal konkursi.

- Har bir hamkorga shaxsiy bot havolasi (t.me/bot?start=p_<kod>), sayt havolasi (utm) va promo-kod beriladi.
- Hamkor orqali kelgan foydalanuvchi to'lov qilsa, hamkorga kelishilgan % yoziladi.
- Hamkor o'z statistikasini maxfiy portal sahifasida ko'radi: /p/<token> (shaxsiy ma'lumotlarsiz, faqat sonlar).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import secrets
from typing import TYPE_CHECKING, Any

from .db import now
from .telegram_api import TelegramError

if TYPE_CHECKING:
    from .app import App

SCHEMA = """
CREATE TABLE IF NOT EXISTS partners (
    code      TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    kind      TEXT DEFAULT 'bloger',     -- bloger | avtomaktab | boshqa
    percent   REAL DEFAULT 20,
    promo     TEXT UNIQUE,
    token     TEXT UNIQUE NOT NULL,
    contact   TEXT,
    active    INTEGER DEFAULT 1,
    created   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id     INTEGER,
    amount    REAL NOT NULL,
    currency  TEXT DEFAULT 'UZS',
    partner   TEXT,
    commission REAL DEFAULT 0,
    note      TEXT,
    at        INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS payouts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    partner   TEXT NOT NULL,
    amount    REAL NOT NULL,
    currency  TEXT DEFAULT 'UZS',
    note      TEXT,
    at        INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS contests (
    month     TEXT PRIMARY KEY,
    winners   TEXT NOT NULL,
    at        INTEGER NOT NULL
);
"""
CODE_RE = re.compile(r"^[a-z0-9_]{2,30}$")
KINDS = ("bloger", "avtomaktab", "boshqa")


def slug(name: str) -> str:
    tr = str.maketrans({"'": "", "‘": "", "’": "", "`": "", "ʻ": "", "ʼ": ""})
    s = re.sub(r"[^a-z0-9]+", "_", name.lower().translate(tr)).strip("_")
    return (s or "hamkor")[:24]


class Partners:
    def __init__(self, app: "App"):
        self.app = app
        self.db = app.db
        self.db.conn.executescript(SCHEMA)
        self.panel_url = os.environ.get("PANEL_URL", "").rstrip("/")

    # ---------------------------------------------------------------- CRUD
    def create(self, name: str, kind: str = "bloger", percent: float = 20, promo: str | None = None,
               contact: str = "", code: str | None = None) -> dict:
        name = name.strip()[:80]
        if len(name) < 2:
            raise ValueError("Hamkor nomini yozing")
        kind = kind if kind in KINDS else "boshqa"
        if not 0 <= float(percent) <= 60:
            raise ValueError("Foiz 0–60 oralig'ida bo'lsin")
        code = (code or slug(name)).lower()
        if not CODE_RE.match(code):
            raise ValueError("Kod: lotin kichik harf, raqam va _ (2–30)")
        base, i = code, 2
        while self.db.one("SELECT 1 FROM partners WHERE code=?", (code,)):
            code, i = f"{base}{i}", i + 1
        promo = (promo or code).upper()[:20]
        if self.db.one("SELECT 1 FROM partners WHERE promo=?", (promo,)):
            promo = f"{promo}{secrets.randbelow(90) + 10}"
        token = secrets.token_urlsafe(18)
        self.db.execute("INSERT INTO partners(code, name, kind, percent, promo, token, contact, created) VALUES (?,?,?,?,?,?,?,?)",
                        (code, name, kind, float(percent), promo, token, contact[:120], now()))
        self.db.audit("owner", "partner_created", {"code": code, "percent": percent})
        return self.info(code)

    def info(self, code: str) -> dict:
        p = self.db.one("SELECT * FROM partners WHERE code=?", (code,))
        if not p:
            raise ValueError("Hamkor topilmadi")
        d = dict(p)
        d["bot_link"] = self.app.bot.bot_link(f"p_{code}")
        site = self.app.settings.site_url
        d["site_link"] = f"{site}{'&' if '?' in site else '?'}utm_source=partner&utm_campaign={code}"
        panel = self.panel_url
        d["portal"] = f"{panel}/p/{d['token']}" if panel else f"/p/{d['token']}"
        return d

    def set_active(self, code: str, on: bool) -> None:
        self.db.execute("UPDATE partners SET active=? WHERE code=?", (1 if on else 0, code))

    def by_promo(self, promo: str) -> dict | None:
        r = self.db.one("SELECT * FROM partners WHERE promo=? AND active=1", (promo.strip().upper(),))
        return dict(r) if r else None

    def source_for(self, payload: str) -> str | None:
        """/start p_<kod> → foydalanuvchi manbasi."""
        if payload.startswith("p_"):
            code = payload[2:].lower()
            if self.db.one("SELECT 1 FROM partners WHERE code=? AND active=1", (code,)):
                return f"partner:{code}"
        return None

    def apply_promo(self, uid: int, promo: str) -> str | None:
        """Promo-kodni yozgan foydalanuvchi hamkorga bog'lanadi (agar hali boshqa hamkorga bog'lanmagan bo'lsa)."""
        p = self.by_promo(promo)
        if not p:
            return None
        u = self.db.user(uid)
        if u and str(u["source"] or "").startswith("partner:"):
            return p["name"]
        self.db.execute("UPDATE users SET source=? WHERE tg_id=?", (f"partner:{p['code']}", uid))
        self.db.event("promo", uid, p["code"])
        return p["name"]

    # ------------------------------------------------------------- to'lovlar
    def record_payment(self, tg_id: int | None, amount: float, currency: str = "UZS", note: str = "",
                       partner: str | None = None) -> dict:
        if amount <= 0:
            raise ValueError("Summa musbat bo'lsin")
        if partner is None and tg_id:
            u = self.db.user(tg_id)
            src = str(u["source"] or "") if u else ""
            partner = src.split(":", 1)[1] if src.startswith("partner:") else None
        pct = 0.0
        if partner:
            p = self.db.one("SELECT percent FROM partners WHERE code=?", (partner,))
            pct = p["percent"] if p else 0.0
            if not p:
                partner = None
        commission = round(amount * pct / 100, 2)
        self.db.execute("INSERT INTO payments(tg_id, amount, currency, partner, commission, note, at) VALUES (?,?,?,?,?,?,?)",
                        (tg_id, amount, currency, partner, commission, note[:200], now()))
        self.app.hub.add_ledger("income", amount, currency, f"To'lov{(' · ' + partner) if partner else ''} {note}".strip()[:200],
                                "tolov")
        self.db.audit("owner", "payment", {"tg_id": tg_id, "amount": amount, "partner": partner})
        return {"partner": partner, "commission": commission, "percent": pct}

    def payout(self, code: str, amount: float, currency: str = "UZS", note: str = "") -> None:
        if amount <= 0 or not self.db.one("SELECT 1 FROM partners WHERE code=?", (code,)):
            raise ValueError("Hamkor yoki summa noto'g'ri")
        self.db.execute("INSERT INTO payouts(partner, amount, currency, note, at) VALUES (?,?,?,?,?)",
                        (code, amount, currency, note[:200], now()))
        self.app.hub.add_ledger("expense", amount, currency, f"Hamkor ulushi · {code}", "hamkor")
        self.db.audit("owner", "partner_payout", {"code": code, "amount": amount})

    # ----------------------------------------------------------- statistika
    def stats(self, code: str, days: int = 30) -> dict:
        src = f"partner:{code}"
        since = now() - days * 86400
        users = self.db.scalar("SELECT COUNT(*) FROM users WHERE source=?", (src,))
        week_active = self.db.scalar("SELECT COUNT(*) FROM users WHERE source=? AND last_seen>=?", (src, now() - 7 * 86400))
        answers = self.db.scalar("SELECT COUNT(*) FROM answers a JOIN users u ON u.tg_id=a.tg_id WHERE u.source=?", (src,))
        pay = self.db.one("SELECT COUNT(*) n, COALESCE(SUM(amount),0) s, COALESCE(SUM(commission),0) c FROM payments WHERE partner=?",
                          (code,))
        paid = self.db.scalar("SELECT COALESCE(SUM(amount),0) FROM payouts WHERE partner=?", (code,))
        tz = self.app.tz
        daily: dict[str, int] = {}
        for r in self.db.all("SELECT joined_at FROM users WHERE source=? AND joined_at>=?", (src, since)):
            d = dt.datetime.fromtimestamp(r["joined_at"], tz).strftime("%m-%d")
            daily[d] = daily.get(d, 0) + 1
        today = self.app.local_now().date()
        series = [{"day": (today - dt.timedelta(days=i)).strftime("%m-%d"),
                   "n": daily.get((today - dt.timedelta(days=i)).strftime("%m-%d"), 0)} for i in range(days - 1, -1, -1)]
        passed = self.db.scalar("SELECT COUNT(*) FROM profiles p JOIN users u ON u.tg_id=p.tg_id WHERE u.source=? AND p.passed=1",
                                (src,))
        return {"users": users, "active_week": week_active, "answers": answers, "payments": pay["n"],
                "revenue": pay["s"], "earned": pay["c"], "paid": paid, "balance": round(pay["c"] - paid, 2),
                "passed": passed, "daily": series,
                "conversion": round(100 * pay["n"] / users, 1) if users else 0.0}

    def list(self) -> list[dict]:
        out = []
        for r in self.db.all("SELECT code FROM partners ORDER BY active DESC, created DESC"):
            d = self.info(r["code"])
            st = self.stats(r["code"], 14)
            st.pop("daily", None)
            d.update(st)
            out.append(d)
        return out

    def summary_text(self) -> str:
        rows = self.list()
        if not rows:
            return "Hamkorlar hali yo'q. Qo'shish: /hamkor Nomi | bloger | 20"
        lines = ["🤝 <b>Hamkorlar</b>"]
        for d in rows[:20]:
            lines.append(f"{'🟢' if d['active'] else '⚪️'} <b>{html.escape(d['name'])}</b> ({d['kind']}, {d['percent']:g}%) — "
                         f"{d['users']} odam, {d['payments']} to'lov, ulush {d['earned']:,.0f}, qoldiq {d['balance']:,.0f}")
        return "\n".join(lines)

    # ------------------------------------------------------- oylik konkurs
    def contest_board(self, month_start: int, month_end: int, limit: int = 10) -> list[dict]:
        return [dict(r) for r in self.db.all(
            "SELECT r.referrer_id tg_id, u.first_name, COUNT(*) c FROM users r JOIN users u ON u.tg_id=r.referrer_id "
            "WHERE r.joined_at>=? AND r.joined_at<? GROUP BY r.referrer_id ORDER BY c DESC, MIN(r.joined_at) ASC LIMIT ?",
            (month_start, month_end, limit))]

    def month_bounds(self, d: dt.date) -> tuple[int, int, str]:
        tz = self.app.tz
        start = dt.datetime(d.year, d.month, 1, tzinfo=tz)
        nxt = dt.datetime(d.year + (d.month == 12), d.month % 12 + 1, 1, tzinfo=tz)
        return int(start.timestamp()), int(nxt.timestamp()), start.strftime("%Y-%m")

    def close_contest(self) -> dict | None:
        """Oyning 1-kuni: o'tgan oy g'oliblarini e'lon qiladi (bir marta)."""
        today = self.app.local_now().date()
        prev = today.replace(day=1) - dt.timedelta(days=1)
        a, b, key = self.month_bounds(prev)
        if self.db.one("SELECT 1 FROM contests WHERE month=?", (key,)):
            return None
        if self.app.dry_run:
            return None
        board = self.contest_board(a, b, 3)
        self.db.execute("INSERT INTO contests(month, winners, at) VALUES (?,?,?)", (key, json.dumps(board), now()))
        if not board:
            return {"month": key, "winners": []}
        prize = os.environ.get("REF_CONTEST_PRIZE", "maxsus sovg'a")
        medals = ["🥇", "🥈", "🥉"]
        text = (f"🏆 <b>{key} — eng ko'p do'st taklif qilganlar</b>\n" +
                "\n".join(f"{medals[i]} {html.escape(w['first_name'] or '—')} — {w['c']} ta do'st" for i, w in enumerate(board)) +
                "\n\nYangi oy konkursi boshlandi! Havolangiz: /taklif")
        gid = self.app.conn.group_id()
        if gid:
            try:
                self.app.tg.send_message(gid, text)
            except TelegramError:
                pass
        for i, w in enumerate(board):
            try:
                self.app.tg.send_message(w["tg_id"], f"{medals[i]} Siz {key} oyida {i + 1}-o'rinni egalladingiz! "
                                                     f"Mukofot: {html.escape(prize)}. Tez orada siz bilan bog'lanamiz.")
            except TelegramError:
                pass
        owner = self.app.settings.owner_id
        if owner:
            lines = []
            for i, w in enumerate(board):
                u = self.db.user(w["tg_id"])
                lines.append(f"{i + 1}. {('@' + u['username']) if u and u['username'] else w['tg_id']} — {w['c']}")
            try:
                self.app.tg.send_message(owner, "🏆 Oylik referal konkursi yakunlandi. Mukofot bering:\n" + "\n".join(lines))
            except TelegramError:
                pass
        return {"month": key, "winners": board}

    def contest_text(self, uid: int | None = None) -> str:
        a, b, key = self.month_bounds(self.app.local_now().date())
        board = self.contest_board(a, b, 10)
        medals = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, 11)]
        lines = [f"🏆 <b>{key} oylik konkurs</b> — eng ko'p do'st taklif qilgan 3 kishi mukofot oladi."]
        lines += [f"{medals[i]} {html.escape(r['first_name'] or '—')} — {r['c']}" for i, r in enumerate(board)] or ["Hali hech kim yo'q — birinchi bo'ling!"]
        if uid:
            mine = self.db.scalar("SELECT COUNT(*) FROM users WHERE referrer_id=? AND joined_at>=? AND joined_at<?", (uid, a, b))
            lines.append(f"\nSizda bu oy: <b>{mine}</b> ta do'st.")
        return "\n".join(lines)

    # --------------------------------------------------------------- panel
    def panel_get(self, path: str, query: dict) -> tuple[int, Any] | None:
        if path == "/api/partners":
            return 200, {"partners": self.list(),
                         "payments": [dict(r) for r in self.db.all("SELECT * FROM payments ORDER BY id DESC LIMIT 50")],
                         "contest": self.contest_text()}
        return None

    def panel_action(self, path: str, body: dict) -> tuple[int, dict] | None:
        try:
            if path == "/api/partners":
                d = self.create(str(body.get("name", "")), str(body.get("kind", "bloger")), float(body.get("percent") or 20),
                                str(body.get("promo") or "") or None, str(body.get("contact") or ""))
                return 200, {"ok": True, "partner": d, "message": f"«{d['name']}» qo'shildi"}
            m = re.fullmatch(r"/api/partners/([a-z0-9_]+)/(payout|toggle)", path)
            if m:
                if m.group(2) == "toggle":
                    self.set_active(m.group(1), bool(body.get("on")))
                    return 200, {"ok": True}
                self.payout(m.group(1), float(body.get("amount") or 0), str(body.get("currency") or "UZS"),
                            str(body.get("note") or ""))
                return 200, {"ok": True, "message": "To'lov yozildi"}
            if path == "/api/payments":
                tg = body.get("tg_id")
                r = self.record_payment(int(tg) if str(tg or "").lstrip("-").isdigit() else None, float(body.get("amount") or 0),
                                        str(body.get("currency") or "UZS"), str(body.get("note") or ""),
                                        str(body.get("partner") or "") or None)
                return 200, {"ok": True, **r, "message": "To'lov yozildi" +
                             (f", hamkor ulushi {r['commission']:,.0f}" if r["partner"] else "")}
        except (ValueError, TypeError) as exc:
            return 400, {"error": str(exc) or "Ma'lumot noto'g'ri"}
        return None

    def portal_html(self, token: str) -> str | None:
        p = self.db.one("SELECT * FROM partners WHERE token=?", (token,))
        if not p:
            return None
        info = self.info(p["code"])
        st = self.stats(p["code"], 30)
        e = html.escape
        mx = max([x["n"] for x in st["daily"]] + [1])
        bars = "".join(
            f'<rect x="{i * 10 + 1}" y="{100 - 92 * x["n"] / mx:.1f}" width="8" height="{92 * x["n"] / mx:.1f}" rx="2">'
            f'<title>{x["day"]}: {x["n"]}</title></rect>' for i, x in enumerate(st["daily"]))
        cards = [("Kelgan odamlar", st["users"]), ("Shu hafta faol", st["active_week"]), ("To'lovlar", st["payments"]),
                 ("Sizning ulushingiz", f"{st['earned']:,.0f}"), ("To'langan", f"{st['paid']:,.0f}"),
                 ("Qoldiq", f"{st['balance']:,.0f}")]
        return f"""<!doctype html><html lang="uz"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Hamkor kabineti</title>
<style>
:root{{--bg:#f6f4ee;--card:#fff;--ink:#16181d;--muted:#666b75;--acc:#f2b705;--line:#e3e0d6}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121417;--card:#1b1e23;--ink:#f1efe9;--muted:#9aa0aa;--line:#2b2f36}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:760px;margin:0 auto;padding:24px 16px 48px}}
h1{{font-size:24px;margin:0 0 4px}} .muted{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}}
.c{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}}
.c b{{display:block;font-size:24px}} svg rect{{fill:var(--acc)}}
code{{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:2px 6px;word-break:break-all}}
li{{margin:6px 0}}
</style></head><body><main>
<div class="muted">ZFT · Chorraha hamkori</div>
<h1>{e(p['name'])}</h1>
<div class="muted">Ulush: <b>{p['percent']:g}%</b> har bir to'lovdan · promo-kod: <code>{e(p['promo'])}</code></div>
<div class="grid">{''.join(f'<div class="c"><span class="muted">{e(k)}</span><b>{e(str(v))}</b></div>' for k, v in cards)}</div>
<div class="c"><div class="muted">Oxirgi 30 kunda kelganlar</div>
<svg viewBox="0 0 300 100" width="100%" height="120" preserveAspectRatio="none" role="img" aria-label="Kunlik kelganlar">{bars}</svg></div>
<h3>Havolalaringiz</h3><ul>
<li>Bot: <code>{e(info['bot_link'])}</code></li>
<li>Sayt: <code>{e(info['site_link'])}</code></li>
<li>Promo-kod (botga yoziladi): <code>{e(p['promo'])}</code></li></ul>
<p class="muted">Sahifa har safar ochilganda yangilanadi. Bu havolani boshqalarga bermang — u sizning shaxsiy kabinetingiz.</p>
</main></body></html>"""
