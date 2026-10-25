"""Boshqaruv: ertalabki brif, tezkor ogohlantirishlar, maqsadlar, Jarvislar samaradorligi,
budjet ogohlantirishlari, moliya toifalari va sinov (dry-run) rejimi."""
from __future__ import annotations

import datetime as dt
import html
import re
import statistics
from typing import TYPE_CHECKING, Any

from .db import now

if TYPE_CHECKING:
    from .app import App

DAY = 86400
GOALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    metric  TEXT NOT NULL,
    target  REAL NOT NULL,
    month   TEXT NOT NULL,
    created INTEGER NOT NULL,
    UNIQUE(metric, month)
);
"""
METRICS = {
    "users": "Yangi foydalanuvchilar",
    "active": "Faol foydalanuvchilar (test yechgan)",
    "referrals": "Do'st taklifi orqali kelganlar",
    "payments": "To'lovlar soni",
    "revenue": "Tushum (so'm)",
    "posts": "E'lon qilingan videolar",
    "views": "Videolar ko'rishlari",
    "exams": "Imtihon simulyatsiyalari",
}
ALIASES = {"odam": "users", "foydalanuvchi": "users", "yangi": "users", "faol": "active", "referal": "referrals",
           "tolov": "payments", "to'lov": "payments", "tushum": "revenue", "daromad": "revenue", "video": "posts",
           "post": "posts", "korish": "views", "ko'rish": "views", "imtihon": "exams"}
CATEGORIES = {
    "server": r"server|hetzner|hosting|domen|domain|vps",
    "reklama": r"reklama|target|ads|bloger|blogger|promo|reels reklama",
    "ai": r"\bai\b|claude|anthropic|openai|fal|video gen|veo|kling",
    "hamkor": r"hamkor|ulush|komissiya|commission",
    "maosh": r"maosh|oylik|ish haqi|salary",
    "tolov": r"to.?lov|obuna|premium|kurs",
}
AGENT_PURPOSES = {
    "direktor": ("direktor_chat", "director_chat"),
    "marketolog": ("market_", "weekly_trends", "daily_advice"),
    "smm": ("smm_caption", "ig_reply"),
    "studiya": ("director", "plan_content", "quality"),
}


def categorize(note: str, kind: str) -> str:
    t = (note or "").lower()
    for cat, rx in CATEGORIES.items():
        if re.search(rx, t):
            return cat
    return "tushum" if kind == "income" else "boshqa"


class Control:
    def __init__(self, app: "App"):
        self.app = app
        self.db = app.db
        self.db.conn.executescript(GOALS_SCHEMA)
        cols = {r[1] for r in self.db.conn.execute("PRAGMA table_info(ledger)")}
        if cols and "category" not in cols:
            self.db.conn.execute("ALTER TABLE ledger ADD COLUMN category TEXT")

    # ---------------------------------------------------------- sinov rejimi
    @property
    def dry_run(self) -> bool:
        return self.db.get("dry_run") == "1"

    def set_dry_run(self, on: bool) -> None:
        self.db.set("dry_run", "1" if on else "0")
        self.db.audit("owner", "dry_run", "on" if on else "off")

    def notify(self, text: str) -> None:
        self.app.pipeline._notify_owner(text)

    # -------------------------------------------------------------- oylik
    def month(self, d: dt.date | None = None) -> tuple[int, int, str]:
        d = d or self.app.local_now().date()
        tz = self.app.tz
        a = dt.datetime(d.year, d.month, 1, tzinfo=tz)
        b = dt.datetime(d.year + (d.month == 12), d.month % 12 + 1, 1, tzinfo=tz)
        return int(a.timestamp()), int(b.timestamp()), a.strftime("%Y-%m")

    def metric_value(self, metric: str, a: int, b: int) -> float:
        d = self.db
        if metric == "users":
            return d.scalar("SELECT COUNT(*) FROM users WHERE joined_at>=? AND joined_at<?", (a, b))
        if metric == "active":
            return d.scalar("SELECT COUNT(DISTINCT tg_id) FROM answers WHERE at>=? AND at<?", (a, b))
        if metric == "referrals":
            return d.scalar("SELECT COUNT(*) FROM users WHERE referrer_id IS NOT NULL AND joined_at>=? AND joined_at<?", (a, b))
        if metric == "payments":
            return d.scalar("SELECT COUNT(*) FROM payments WHERE at>=? AND at<?", (a, b))
        if metric == "revenue":
            return d.scalar("SELECT COALESCE(SUM(amount),0) FROM payments WHERE currency='UZS' AND at>=? AND at<?", (a, b))
        if metric == "posts":
            return d.scalar("SELECT COUNT(*) FROM posts WHERE status='published' AND published_at>=? AND published_at<?", (a, b))
        if metric == "views":
            total = 0
            for p in d.all("SELECT id FROM posts WHERE status='published' AND published_at>=? AND published_at<?", (a, b)):
                total += sum((v.get("views") or 0) for v in d.latest_metrics(p["id"]).values())
            return total
        if metric == "exams":
            return d.scalar("SELECT COUNT(*) FROM exams WHERE started>=? AND started<?", (a, b))
        raise ValueError(metric)

    # ------------------------------------------------------------ maqsadlar
    def set_goal(self, metric: str, target: float) -> dict:
        metric = ALIASES.get(metric.lower(), metric.lower())
        if metric not in METRICS:
            raise ValueError("Ko'rsatkich: " + ", ".join(METRICS))
        if target <= 0:
            raise ValueError("Maqsad musbat son bo'lsin")
        _, _, key = self.month()
        self.db.execute("INSERT INTO goals(metric, target, month, created) VALUES (?,?,?,?) "
                        "ON CONFLICT(metric, month) DO UPDATE SET target=excluded.target", (metric, target, key, now()))
        self.app.hub.say("ega", "direktor", f"Yangi oylik maqsad: {METRICS[metric]} — {target:,.0f}.")
        return next(g for g in self.goals() if g["metric"] == metric)

    def goals(self) -> list[dict]:
        a, b, key = self.month()
        at = self.app.local_now()
        days = (dt.date.fromtimestamp(b) - dt.date.fromtimestamp(a)).days or 30
        frac = max(1e-6, ((now() - a) / DAY) / days)
        out = []
        for g in self.db.all("SELECT * FROM goals WHERE month=? ORDER BY id", (key,)):
            v = self.metric_value(g["metric"], a, b)
            forecast = v / frac
            out.append({"metric": g["metric"], "name": METRICS[g["metric"]], "target": g["target"], "value": v,
                        "percent": round(100 * v / g["target"]), "forecast": round(forecast),
                        "on_track": forecast >= g["target"], "day": at.day})
        return out

    def goals_text(self) -> str:
        gs = self.goals()
        if not gs:
            return "Bu oy uchun maqsad qo'yilmagan. Masalan: /maqsad users 500"
        lines = ["🎯 <b>Oylik maqsadlar</b>"]
        for g in gs:
            bar = "▰" * min(10, g["percent"] // 10) + "▱" * max(0, 10 - g["percent"] // 10)
            lines.append(f"{'🟢' if g['on_track'] else '🟠'} {html.escape(g['name'])}: {g['value']:,.0f} / {g['target']:,.0f} "
                         f"({g['percent']}%) {bar}\n   prognoz oy oxiriga: {g['forecast']:,.0f}")
        return "\n".join(lines)

    # ------------------------------------------------------- samaradorlik
    def efficiency(self, days: int = 7) -> list[dict]:
        since = now() - days * DAY
        hub = self.app.hub
        cost = {r["purpose"]: r["s"] for r in self.db.all(
            "SELECT purpose, SUM(cost_usd) s FROM ai_usage WHERE at>=? GROUP BY purpose", (since,))}
        msgs = {r["sender"]: r["c"] for r in self.db.all(
            "SELECT sender, COUNT(*) c FROM agent_messages WHERE at>=? GROUP BY sender", (since,))}
        posts = self.db.one("SELECT COUNT(*) made, SUM(status='published') pub, SUM(status='rejected') rej, "
                            "AVG(quality) q, SUM(cost_usd) vc FROM posts WHERE created_at>=?", (since,))
        outputs = {
            "direktor": f"{self.db.scalar('SELECT COUNT(*) FROM tasks WHERE created_at>=?', (since,))} topshiriq",
            "marketolog": f"{self.db.scalar('SELECT COUNT(*) FROM post_reviews WHERE at>=?', (since,))} tahlil",
            "smm": f"{self.db.scalar('SELECT COUNT(*) FROM ig_comments WHERE replied_at>=?', (since,))} izohga javob",
            "studiya": f"{posts['made'] or 0} video (rad: {posts['rej'] or 0}, o'rtacha baho "
                       f"{(posts['q'] or 0):.1f})",
            "nashr": f"{posts['pub'] or 0} e'lon",
            "hisobot": f"{self.db.scalar('SELECT COUNT(*) FROM reports WHERE at>=?', (since,))} hisobot",
            "media": f"{self.db.scalar('SELECT COUNT(*) FROM metrics WHERE collected_at>=?', (since,))} o'lchov",
            "moliya": f"{self.db.scalar('SELECT COUNT(*) FROM ledger WHERE at>=?', (since,))} yozuv",
        }
        rows = []
        for key, a in hub.all_agents().items():
            purposes = AGENT_PURPOSES.get(key, (f"custom_{key}",))
            usd = sum(v or 0 for p, v in cost.items() if any(p == x or (x.endswith("_") and p.startswith(x)) for x in purposes))
            if key == "studiya":
                usd += posts["vc"] or 0
            rows.append({"key": key, "name": a.get("name", key), "messages": msgs.get(key, 0),
                         "output": outputs.get(key, f"{msgs.get(key, 0)} xabar"), "usd": round(usd, 3)})
        return rows

    def efficiency_text(self) -> str:
        lines = ["⚙️ <b>Jarvislar samaradorligi (7 kun)</b>"]
        for r in self.efficiency():
            lines.append(f"• {html.escape(r['name'])}: {html.escape(r['output'])} · {r['usd']:.2f}$")
        return "\n".join(lines)

    # ---------------------------------------------------------- ertalabki brif
    def morning_brief(self) -> str:
        d = self.db
        t0 = self.app.local_day_start()
        y0 = t0 - DAY
        new_y = d.scalar("SELECT COUNT(*) FROM users WHERE joined_at>=? AND joined_at<?", (y0, t0))
        act_y = d.scalar("SELECT COUNT(DISTINCT tg_id) FROM answers WHERE at>=? AND at<?", (y0, t0))
        pub_y = d.scalar("SELECT COUNT(*) FROM posts WHERE status='published' AND published_at>=? AND published_at<?", (y0, t0))
        pending = d.scalar("SELECT COUNT(*) FROM posts WHERE status='pending'")
        esc_c = d.scalar("SELECT COUNT(*) FROM ig_comments WHERE action='escalated' AND at>=?", (y0,))
        slots = ", ".join(f"{m // 60:02d}:{m % 60:02d}" for m in self.app.settings.slot_minutes())
        v = self.app.vbudget.summary(self.app.local_now())
        b = self.app.brain
        lines = ["☀️ <b>Xayrli tong! ZFT ertalabki brif</b>",
                 f"Kecha: +{new_y} yangi odam, {act_y} kishi test yechdi, {pub_y} video chiqdi.",
                 f"Bugun: videolar {slots} da." + (f" ⏳ {pending} ta video tasdig'ingizni kutyapti." if pending else ""),
                 f"Budjet: video {v['spent']:.2f}$/{v['monthly']:.0f}$, AI {b.spent():.2f}$/{b.budget:.0f}$."]
        if esc_c:
            lines.append(f"💬 {esc_c} ta Instagram izohi sizning javobingizni kutyapti.")
        if self.dry_run:
            lines.append("🧪 SINOV rejimi yoqilgan — hech narsa ommaga chiqmaydi. O'chirish: /sinov off")
        gs = self.goals()
        if gs:
            lines.append("")
            lines.append(self.goals_text())
        task = d.one("SELECT text FROM tasks WHERE status='open' ORDER BY id LIMIT 1")
        if task:
            lines.append(f"\n📝 Navbatdagi topshiriq: «{html.escape(task['text'][:120])}»")
        text = "\n".join(lines)
        self.app.hub._store_report("morning", "Ertalabki brif", text)
        self.notify(text)
        return text

    # ----------------------------------------------------- ogohlantirishlar
    def check_alerts(self) -> list[str]:
        sent: list[str] = []
        sent += self._viral()
        sent += self._budget_alerts()
        sent += self._spike()
        sent += self._failures()
        for s in sent:
            self.notify(s)
        return sent

    def _once(self, key: str) -> bool:
        if self.db.get(f"alert:{key}"):
            return False
        self.db.set(f"alert:{key}", now())
        return True

    def _viral(self) -> list[str]:
        out = []
        rows = self.db.all("SELECT id, hook FROM posts WHERE status='published' ORDER BY published_at DESC LIMIT 15")
        views = {r["id"]: sum((v.get("views") or 0) for v in self.db.latest_metrics(r["id"]).values()) for r in rows}
        base = [v for v in views.values() if v]
        if len(base) < 3:
            return out
        med = statistics.median(base)
        for r in rows[:5]:
            v = views[r["id"]]
            if v >= max(500, 3 * med) and self._once(f"viral:{r['id']}"):
                out.append(f"🚀 #{r['id']} «{html.escape(r['hook'] or '')}» viral bo'lyapti: {v:,} ko'rish "
                           f"(odatdagidan {v / med:.1f}× ko'p). Marketolog shu formatni takrorlashni tavsiya qiladi.")
                self.app.hub.say("media", "marketolog", f"#{r['id']} {v} ko'rish — o'rtachadan {v / med:.1f}× yuqori. Tahlil qiling.")
        return out

    def _budget_alerts(self) -> list[str]:
        out = []
        at = self.app.local_now()
        key = at.strftime("%Y-%m")
        v = self.app.vbudget
        b = self.app.brain
        for name, spent, limit in (("video", v.spent(at), v.monthly), ("AI", b.spent(), b.budget)):
            if not limit:
                continue
            pct = spent / limit
            for th in (0.95, 0.8):
                if pct >= th and self._once(f"budget:{name}:{key}:{int(th * 100)}"):
                    out.append(f"💸 {name} budjetining {int(pct * 100)}% sarflandi ({spent:.2f}$ / {limit:.0f}$). "
                               + ("Limitga yetganda tizim bepul formatga o'tadi." if th == 0.95 else
                                  "Oshirish kerak bo'lsa: /budjet <summa>"))
                    break
        return out

    def _spike(self) -> list[str]:
        t0 = self.app.local_day_start()
        today = self.db.scalar("SELECT COUNT(*) FROM users WHERE joined_at>=?", (t0,))
        prev = self.db.scalar("SELECT COUNT(*) FROM users WHERE joined_at>=? AND joined_at<?", (t0 - 7 * DAY, t0)) / 7
        if today >= 20 and today >= 3 * max(prev, 1) and self._once(f"spike:{t0}"):
            src = self.db.one("SELECT source, COUNT(*) c FROM users WHERE joined_at>=? GROUP BY source ORDER BY c DESC LIMIT 1", (t0,))
            label = self.app.reports.label(src["source"]) if src else "?"
            return [f"📈 Bugun {today} ta yangi odam (o'rtacha {prev:.0f}). Asosiy manba: {html.escape(label)}."]
        return []

    def _failures(self) -> list[str]:
        n = self.db.scalar("SELECT COUNT(*) FROM posts WHERE status='failed' AND created_at>=?", (now() - DAY,))
        if n >= 2 and self._once(f"fail:{self.app.local_day_start()}"):
            return [f"⚠️ So'nggi 24 soatda {n} ta video e'lon qilinmadi. Ulanishlar sahifasini tekshiring."]
        return []

    # --------------------------------------------------------------- panel
    def panel_get(self, path: str, query: dict) -> tuple[int, Any] | None:
        if path == "/api/control":
            cats = [dict(r) for r in self.db.all(
                "SELECT kind, COALESCE(category,'boshqa') category, currency, SUM(amount) s FROM ledger WHERE at>=? "
                "GROUP BY kind, category, currency ORDER BY s DESC", (self.month()[0],))]
            return 200, {"dry_run": self.dry_run, "goals": self.goals(), "metrics": METRICS,
                         "efficiency": self.efficiency(), "categories": cats,
                         "learning": self.app.learning.stats()}
        return None

    def panel_action(self, path: str, body: dict) -> tuple[int, dict] | None:
        if path == "/api/dry-run":
            self.set_dry_run(bool(body.get("on")))
            return 200, {"ok": True, "dry_run": self.dry_run}
        if path == "/api/goals":
            try:
                g = self.set_goal(str(body.get("metric", "")), float(body.get("target") or 0))
            except (ValueError, TypeError) as exc:
                return 400, {"error": str(exc)}
            return 200, {"ok": True, "goal": g}
        if path == "/api/brief-now":
            self.app.run_in_background(self.morning_brief)
            return 202, {"ok": True, "message": "Ertalabki brif Telegram'ga yuborilmoqda"}
        return None
