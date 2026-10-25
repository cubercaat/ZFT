"""Egaga hisobotlar: kunlik, haftalik, manbalar, budjet."""
from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING

from .db import now
from .pipeline import DAY, score
from .telegram_api import TelegramError

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)


def pct(a: float, b: float) -> str:
    return f"{(a / b * 100):.0f}%" if b else "—"


class Reports:
    def __init__(self, app: "App"):
        self.app = app
        self.db = app.db

    def label(self, source: str) -> str:
        row = self.db.one("SELECT label FROM links WHERE code=?", (source,))
        names = {"direct": "To'g'ridan-to'g'ri", "referral": "Do'st taklifi", "group": "Guruh viktorinasi"}
        if row:
            return row["label"]
        if source.startswith("partner:"):
            p = self.db.one("SELECT name FROM partners WHERE code=?", (source[8:],))
            return f"Hamkor: {p['name']}" if p else source
        return names.get(source, source)

    def _numbers(self, since: int) -> dict:
        d = self.db
        return {
            "new_users": d.scalar("SELECT COUNT(*) FROM users WHERE joined_at>=?", (since,)),
            "active": d.scalar("SELECT COUNT(*) FROM users WHERE last_seen>=?", (since,)),
            "answers": d.scalar("SELECT COUNT(*) FROM events WHERE kind='quiz_a' AND at>=?", (since,)),
            "group_answers": d.scalar("SELECT COUNT(*) FROM poll_answers WHERE at>=?", (since,)),
            "referrals": d.scalar("SELECT COUNT(*) FROM users WHERE referrer_id IS NOT NULL AND joined_at>=?",
                                  (since,)),
            "posts": d.scalar("SELECT COUNT(*) FROM posts WHERE status='published' AND published_at>=?", (since,)),
            "total_users": d.scalar("SELECT COUNT(*) FROM users"),
            "sources": {r["source"]: r["c"] for r in d.all(
                "SELECT source, COUNT(*) c FROM users WHERE joined_at>=? GROUP BY source ORDER BY c DESC",
                (since,))},
        }

    def today_summary(self) -> str:
        n = self._numbers(self.app.local_day_start())
        src = "\n".join(f"  • {html.escape(self.label(k))}: {v}" for k, v in n["sources"].items()) or "  —"
        members = self.app.group_members()
        return (f"📊 <b>ZFT · Bugun</b>\n"
                f"Yangi foydalanuvchi: <b>{n['new_users']}</b> (jami {n['total_users']})\n"
                f"Faol: {n['active']} · Botda javoblar: {n['answers']} · Guruhda: {n['group_answers']}\n"
                f"Do'st taklifi bilan kelgan: {n['referrals']}\n"
                f"Guruh a'zolari: {members if members is not None else '—'}\n"
                f"E'lon qilingan video: {n['posts']}\n\n"
                f"<b>Qayerdan kelishdi:</b>\n{src}")

    def sources_text(self) -> str:
        week = now() - 7 * DAY
        rows = self.db.all(
            "SELECT source, COUNT(*) total, SUM(joined_at>=?) week, SUM(quiz_total>0) active,"
            " SUM(blocked) blocked FROM users GROUP BY source ORDER BY week DESC, total DESC", (week,))
        if not rows:
            return "Hali foydalanuvchi yo'q."
        lines = ["🧭 <b>Manbalar</b> (7 kun / jami · test yechganlar)"]
        for r in rows:
            lines.append(f"• {html.escape(self.label(r['source']))}: <b>{r['week'] or 0}</b> / {r['total']}"
                         f" · {pct(r['active'] or 0, r['total'])} faol")
        lines.append("\nYangi kuzatuvli havola: /link kod Nomi")
        return "\n".join(lines)

    def budget_text(self) -> str:
        v = self.app.vbudget.summary(self.app.local_now())
        kinds = {"video_scene": "AI video sahnalar", "image": "AI rasmlar"}
        vlines = [f"🎬 <b>Video budjeti shu oy:</b> {v['spent']:.2f}$ / {v['monthly']:.0f}$",
                  f"Bugungacha sarflash mumkin: {max(0, v['allowance_today']):.2f}$ · oy oxirigacha {v['days_left']} kun",
                  f"1 ta kinematografik video ≈ {v['cinematic_cost']:.2f}$ ({html.escape(self.app.settings.video_model)})"]
        vlines += [f"• {kinds.get(k, k)}: {x['count']} ta, {x['usd']:.2f}$" for k, x in v["by_kind"].items()]
        if not self.app.settings.studio_enabled:
            vlines.append("FAL_KEY kiritilmagan — AI video o'chiq, bepul test-videolar chiqadi.")
        b = self.app.brain
        rows = self.db.all("SELECT purpose, COUNT(*) c, SUM(cost_usd) s FROM ai_usage WHERE at>=?"
                           " GROUP BY purpose ORDER BY s DESC", (b.month_start(),))
        lines = [f"💵 <b>AI xarajati shu oy:</b> {b.spent():.3f}$ / {b.budget:.2f}$ limit",
                 f"Model: {html.escape(b.model)}"]
        lines += [f"• {html.escape(r['purpose'])}: {r['c']} marta, {r['s']:.3f}$" for r in rows]
        if not self.app.settings.ai_enabled:
            lines.append("AI kaliti kiritilmagan — tizim shablon rejimida ishlayapti.")
        return "\n".join(vlines + [""] + lines)

    def top_post_text(self, since: int) -> str:
        best, best_s = None, -1
        for p in self.db.all("SELECT * FROM posts WHERE status='published' AND published_at>=?", (since,)):
            s = score(self.db.latest_metrics(p["id"]))
            if s > best_s:
                best, best_s = p, s
        if not best:
            return ""
        m = self.db.latest_metrics(best["id"])
        parts = [f"{k}: {v.get('views', 0)} ko'rish, {v.get('likes', 0)} layk, {v.get('saves', 0)} saqlash"
                 for k, v in m.items()] or ["metrika hali yig'ilmagan"]
        return f"🏅 <b>Eng yaxshi video</b> #{best['id']} «{html.escape(best['hook'] or '')}»\n  " + \
            "\n  ".join(html.escape(x) for x in parts)

    def send_daily_report(self, trigger: str = "schedule", extra: str = "") -> str:
        text = self.today_summary()
        top = self.top_post_text(now() - 7 * DAY)
        if top:
            text += "\n\n" + top
        n = self._numbers(self.app.local_day_start())
        advice = self.app.brain.daily_advice({**n, "top_post": top[:300]})
        if advice:
            text += "\n\n🤖 <b>Tavsiyalar</b>\n" + html.escape(advice)
        if extra:
            text += "\n\n" + extra
        else:
            v = self.app.vbudget
            text += (f"\n\n💵 Video: {v.spent():.2f}$ / {v.monthly:.0f}$ · AI matn: {self.app.brain.spent():.2f}$ (shu oy)")
        self._send(text)
        return text

    def send_weekly_report(self) -> str:
        week = now() - 7 * DAY
        n = self._numbers(week)
        posts = self.db.all("SELECT * FROM posts WHERE status='published' AND published_at>=? ORDER BY id", (week,))
        scored = sorted(((score(self.db.latest_metrics(p["id"])), p) for p in posts), key=lambda x: -x[0])
        lines = [f"📅 <b>ZFT · Haftalik hisobot</b>",
                 f"Yangi foydalanuvchi: <b>{n['new_users']}</b> · jami {n['total_users']}",
                 f"Botdagi javoblar: {n['answers']} · guruhda: {n['group_answers']}",
                 f"Do'st taklifi: {n['referrals']} · videolar: {len(posts)}", "", "<b>Manbalar:</b>"]
        lines += [f"• {html.escape(self.label(k))}: {v}" for k, v in n["sources"].items()] or ["—"]
        if scored:
            lines += ["", "<b>Ishladi:</b>"] + [f"• «{html.escape(p['hook'] or '')}» — {s} ball" for s, p in scored[:2]]
            if len(scored) > 2:
                lines += ["<b>Ishlamadi:</b>"] + [f"• «{html.escape(p['hook'] or '')}» — {s} ball"
                                                  for s, p in scored[-2:]]
        trends = self.db.get("trend_notes")
        if trends:
            lines += ["", "🌍 <b>Shu hafta trendlar</b>", html.escape(trends[:1500])]
        self._send("\n".join(lines))
        return "\n".join(lines)

    def _send(self, text: str) -> None:
        owner = self.app.settings.owner_id
        if not owner:
            log.info(text)
            return
        try:
            for i in range(0, len(text), 3900):
                self.app.tg.send_message(owner, text[i:i + 3900])
        except TelegramError as exc:
            log.warning("Hisobot yuborilmadi: %s", exc)
