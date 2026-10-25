"""Boshqaruv paneli: brauzerda ochiladigan admin sahifa + JSON API.

Xavfsizlik:
- faqat 127.0.0.1 da tinglaydi, tashqariga nginx (HTTPS) orqali chiqadi;
- PANEL_PASSWORD bilan kirish, HMAC imzoli HttpOnly/Secure/SameSite=Strict cookie;
- 10 daqiqada 5 ta noto'g'ri urinishdan keyin IP bloklanadi;
- har bir POST so'rov `X-Panel: 1` sarlavhasini talab qiladi (CSRF himoyasi);
- har bir harakat audit_log ga yoziladi.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .bot import PAYLOAD_RE
from .db import now
from .pipeline import DAY, score

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "panel" / "index.html"
SESSION_TTL = 7 * DAY
COOKIE = "chorraha_panel"


class Panel:
    def __init__(self, app: "App", password: str, host: str = "127.0.0.1", port: int = 8090,
                 secure_cookie: bool = True):
        self.app = app
        self.db = app.db
        self.password = password
        self.host, self.port = host, port
        self.secure_cookie = secure_cookie
        secret = self.db.get("panel_secret")
        if not secret:
            secret = secrets.token_hex(32)
            self.db.set("panel_secret", secret)
        self.secret = secret.encode()
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- auth
    def make_token(self, ts: int | None = None) -> str:
        ts = ts or now()
        sig = hmac.new(self.secret, f"{ts}".encode(), hashlib.sha256).hexdigest()
        return f"{ts}.{sig}"

    def check_token(self, token: str | None) -> bool:
        if not token or "." not in token:
            return False
        ts_s, sig = token.split(".", 1)
        if not ts_s.isdigit() or now() - int(ts_s) > SESSION_TTL:
            return False
        good = hmac.new(self.secret, ts_s.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(good, sig)

    def blocked(self, ip: str) -> bool:
        with self._lock:
            recent = [t for t in self._fails.get(ip, []) if time.time() - t < 600]
            self._fails[ip] = recent
            return len(recent) >= 5

    def login(self, ip: str, password: str) -> str | None:
        if self.blocked(ip):
            return None
        if self.password and hmac.compare_digest(password.encode(), self.password.encode()):
            self.db.audit("panel", "login", {"ip": ip})
            return self.make_token()
        with self._lock:
            self._fails.setdefault(ip, []).append(time.time())
        self.db.audit("panel", "login_failed", {"ip": ip})
        return None

    # ---------------------------------------------------------------- data
    def overview(self) -> dict:
        d, app = self.db, self.app
        day0 = app.local_day_start()
        week = now() - 7 * DAY
        series = []
        for i in range(13, -1, -1):
            start = day0 - i * DAY
            series.append({
                "day": time.strftime("%Y-%m-%d", time.gmtime(start + app.settings.timezone_offset_hours * 3600)),
                "users": d.scalar("SELECT COUNT(*) FROM users WHERE joined_at>=? AND joined_at<?", (start, start + DAY)),
                "answers": d.scalar("SELECT COUNT(*) FROM events WHERE kind='quiz_a' AND at>=? AND at<?",
                                    (start, start + DAY)),
            })
        sources = [{
            "code": r["source"], "label": app.reports.label(r["source"]), "week": r["week"] or 0,
            "total": r["total"], "active": r["active"] or 0,
        } for r in d.all("SELECT source, COUNT(*) total, SUM(joined_at>=?) week, SUM(quiz_total>0) active"
                         " FROM users GROUP BY source ORDER BY week DESC, total DESC", (week,))]
        links = [{"code": r["code"], "label": r["label"],
                  "url": app.bot.bot_link(r["code"]) if app.settings.bot_username else ""}
                 for r in d.all("SELECT code, label FROM links ORDER BY created_at DESC")]
        pending = d.one("SELECT * FROM posts WHERE status IN ('pending','approved') ORDER BY id DESC LIMIT 1")
        s = app.settings
        return {
            "demo": False,
            "now": app.local_now().strftime("%Y-%m-%d %H:%M"),
            "kpi": {
                "today_users": d.scalar("SELECT COUNT(*) FROM users WHERE joined_at>=?", (day0,)),
                "total_users": d.scalar("SELECT COUNT(*) FROM users"),
                "active_week": d.scalar("SELECT COUNT(*) FROM users WHERE last_seen>=?", (week,)),
                "referrals_week": d.scalar("SELECT COUNT(*) FROM users WHERE referrer_id IS NOT NULL AND joined_at>=?",
                                           (week,)),
                "answers_today": d.scalar("SELECT COUNT(*) FROM events WHERE kind='quiz_a' AND at>=?", (day0,))
                + d.scalar("SELECT COUNT(*) FROM poll_answers WHERE at>=?", (day0,)),
                "group_members": app.group_members(),
                "posts_week": d.scalar("SELECT COUNT(*) FROM posts WHERE status='published' AND published_at>=?",
                                       (week,)),
            },
            "series": series,
            "sources": sources,
            "links": links,
            "budget": {"spent": round(app.brain.spent(), 4), "limit": app.brain.budget, "model": app.brain.model,
                       "enabled": s.ai_enabled},
            "video_budget": {**app.vbudget.summary(app.local_now()), "enabled": s.studio_enabled,
                             "model": s.video_model},
            "slots": list(s.slots),
            "next_slot": app.pipeline.next_slot()[1].strftime("%d.%m %H:%M"),
            "tasks": [dict(r) for r in d.all("SELECT id, text, status, post_id, created_at FROM tasks"
                                             " ORDER BY (status='open') DESC, id DESC LIMIT 12")],
            "auto_publish": app.auto_publish,
            "schedule": {"slots": s.slot_minutes(), "lead": s.gen_lead_minutes, "report": s.report_hour,
                         "quiz": list(s.group_quiz_hours)},
            "platforms": {"telegram": bool(app.conn.channel_id()), "instagram": app.instagram is not None,
                          "tiktok": app.tiktok is not None, "group": bool(app.conn.group_id())},
            "questions": len(app.bank),
            "sample_questions": bool(len(app.bank)) and all(q.id.startswith("namuna") for q in app.bank.questions),
            "pending": self.post_json(pending) if pending else None,
            "hard_questions": [{**h, "text": (app.bank.by_id(h["id"]).question[:120]
                                              if app.bank.by_id(h["id"]) else h["id"])}
                               for h in app.pipeline.hard_questions(5)],
        }

    def post_json(self, r) -> dict:
        m = self.db.latest_metrics(r["id"])
        when = time.strftime("%d.%m %H:%M", time.gmtime(r["publish_at"] + self.app.settings.timezone_offset_hours * 3600)) \
            if r["publish_at"] else None
        return {"id": r["id"], "status": r["status"], "hook": r["hook"], "caption": r["caption"],
                "tier": r["tier"], "format": r["format"], "cost": round(r["cost_usd"] or 0, 3), "publish_at": when,
                "hashtags": json.loads(r["hashtags"] or "[]"), "reason": r["ai_reason"], "error": r["error"],
                "question_id": r["question_id"], "created_at": r["created_at"], "published_at": r["published_at"],
                "metrics": m, "score": score(m),
                "video": f"api/video/{r['id']}" if r["video_path"] and Path(r["video_path"]).exists() else None}

    def charts(self) -> dict:
        d, app = self.db, self.app
        day0 = app.local_day_start()
        days = []
        for i in range(29, -1, -1):
            start = day0 - i * DAY
            end = start + DAY
            days.append({
                "day": time.strftime("%Y-%m-%d", time.gmtime(start + app.settings.timezone_offset_hours * 3600)),
                "users": d.scalar("SELECT COUNT(*) FROM users WHERE joined_at>=? AND joined_at<?", (start, end)),
                "answers": d.scalar("SELECT COUNT(*) FROM events WHERE kind='quiz_a' AND at>=? AND at<?", (start, end))
                + d.scalar("SELECT COUNT(*) FROM poll_answers WHERE at>=? AND at<?", (start, end)),
                "spend": round(float(d.scalar("SELECT SUM(usd) FROM video_costs WHERE at>=? AND at<?", (start, end), 0.0))
                               + float(d.scalar("SELECT SUM(cost_usd) FROM ai_usage WHERE at>=? AND at<?", (start, end), 0.0)), 3),
            })
        posts = []
        for r in d.all("SELECT * FROM posts WHERE status='published' ORDER BY id DESC LIMIT 20"):
            m = d.latest_metrics(r["id"])
            posts.append({"id": r["id"], "hook": r["hook"], "tier": r["tier"],
                          "views": sum(v.get("views", 0) for v in m.values()),
                          "saves": sum(v.get("saves", 0) for v in m.values()),
                          "shares": sum(v.get("shares", 0) for v in m.values()), "score": score(m)})
        sources = [{"label": app.reports.label(r["source"]), "count": r["c"]} for r in d.all(
            "SELECT source, COUNT(*) c FROM users WHERE joined_at>=? GROUP BY source ORDER BY c DESC LIMIT 8",
            (now() - 30 * DAY,))]
        return {"days": days, "posts": list(reversed(posts)), "sources": sources}

    def posts(self) -> list[dict]:
        return [self.post_json(r) for r in self.db.all("SELECT * FROM posts ORDER BY id DESC LIMIT 40")]

    def audit(self) -> list[dict]:
        return [dict(r) for r in self.db.all("SELECT at, actor, action, detail FROM audit_log ORDER BY id DESC LIMIT 80")]

    # ------------------------------------------------------------ actions
    def action(self, path: str, body: dict) -> tuple[int, dict]:
        app, pipe = self.app, self.app.pipeline
        m = re.fullmatch(r"/api/posts/(\d+)/(approve|reject|regenerate)", path)
        if m:
            pid, act = int(m.group(1)), m.group(2)
            if not self.db.post(pid):
                return 404, {"error": "Post topilmadi"}
            if act == "approve":
                pipe.approve(pid)
                app.hub.say("ega", "direktor", f"#{pid} ni tasdiqladim (panel).")
            elif act == "reject":
                pipe.reject(pid)
                app.hub.say("ega", "direktor", f"#{pid} rad etildi (panel).")
            else:
                app.run_in_background(lambda: app.hub._studio_make(lambda: pipe.regenerate(pid)))
            self.db.audit("panel", f"post_{act}", {"post": pid})
            return 200, {"ok": True, "status": self.db.post(pid)["status"]}
        if path == "/api/publish-now":
            app.run_in_background(app.hub.publish_now)
            self.db.audit("panel", "publish_now")
            return 202, {"ok": True, "message": "E'lon qilish boshlandi. Natija Telegram'ga keladi."}
        if path == "/api/video-now":
            app.run_in_background(app.hub.make_now, "manual")
            self.db.audit("panel", "video_now")
            return 202, {"ok": True, "message": "Video tayyorlanmoqda, 1–3 daqiqa."}
        if path == "/api/report-now":
            section = str(body.get("section") or "")
            if section in ("moliya", "marketolog", "media"):
                app.run_in_background(app.hub.report_on, section)
            else:
                app.run_in_background(app.hub.daily_report)
            return 202, {"ok": True, "message": "Hisobotchi tayyorlayapti — Telegram'ga yuboriladi."}
        if path == "/api/review-now":
            app.run_in_background(lambda: app.hub.market_review(min_age_hours=6))
            return 202, {"ok": True, "message": "Marketolog videolarni tahlil qilyapti."}
        if path == "/api/auto":
            on = bool(body.get("on"))
            self.db.set("auto_publish", "1" if on else "0")
            self.db.audit("panel", "auto_publish", "on" if on else "off")
            return 200, {"ok": True, "auto_publish": on}
        if path == "/api/tasks":
            text = str(body.get("text", "")).strip()
            if len(text) < 5:
                return 400, {"error": "Topshiriqni batafsilroq yozing"}
            tid = pipe.add_task(text)
            app.hub.say("ega", "direktor", f"Yangi topshiriq: «{text[:300]}»")
            app.hub.say("direktor", "studiya", f"Topshiriq #{tid} navbatga qo'shildi.")
            if body.get("now"):
                app.run_in_background(app.hub.make_now, "task")
                return 202, {"ok": True, "id": tid, "message": "Topshiriq qabul qilindi, video tayyorlanmoqda."}
            return 200, {"ok": True, "id": tid, "message": "Topshiriq navbatga qo'shildi."}
        m = re.fullmatch(r"/api/tasks/(\d+)/cancel", path)
        if m:
            self.db.execute("UPDATE tasks SET status='cancelled' WHERE id=? AND status='open'", (int(m.group(1)),))
            self.db.audit("panel", "task_cancelled", m.group(1))
            return 200, {"ok": True}
        if path == "/api/budget":
            try:
                usd = float(body.get("usd"))
            except (TypeError, ValueError):
                return 400, {"error": "Summani raqam bilan kiriting"}
            if not 0 <= usd <= 5000:
                return 400, {"error": "Budjet 0–5000$ oralig'ida bo'lsin"}
            app.vbudget.set_monthly(usd)
            return 200, {"ok": True, "message": f"Oylik video budjeti: {usd:.0f}$"}
        if path == "/api/market-research":
            app.run_in_background(app.hub.market_research)
            return 202, {"ok": True, "message": "Marketolog bozorni o'rganyapti, natija Jarvislar suhbatida chiqadi."}
        m = re.fullmatch(r"/api/connections/(instagram|tiktok)/(start|disconnect)", path)
        if m:
            from .connections import ConnectError
            if m.group(2) == "disconnect":
                app.conn.disconnect(m.group(1))
                return 200, {"ok": True, "message": "Uzildi"}
            try:
                return 200, {"ok": True, "url": app.conn.start(m.group(1))}
            except ConnectError as exc:
                return 400, {"error": str(exc)}
        if path == "/api/connections/telegram/use":
            from .connections import ConnectError
            try:
                r = app.conn.use_chat(str(body.get("chat_id", "")), str(body.get("role", "")))
            except ConnectError as exc:
                return 400, {"error": str(exc)}
            return 200, {"ok": True, "message": f"«{r['title']}» tanlandi"}
        if path == "/api/connections/telegram/disconnect":
            role = str(body.get("role", ""))
            if role in ("channel", "group"):
                app.conn.disconnect(f"telegram_{role}")
            return 200, {"ok": True}
        if path == "/api/hire":
            name, role = str(body.get("name", "")).strip(), str(body.get("role", "")).strip()
            if len(name) < 2 or len(role) < 5:
                return 400, {"error": "Nomi va vazifasini yozing"}
            hour = body.get("daily_hour")
            hour = int(hour) if isinstance(hour, (int, str)) and str(hour).isdigit() else None
            key = app.hub.hire(name, role, str(body.get("instructions") or role), hour)
            return 200, {"ok": True, "key": key, "message": f"«{name}» ishga olindi"}
        m = re.fullmatch(r"/api/agents/(x_[a-z0-9]+)/(fire|run)", path)
        if m:
            if m.group(2) == "fire":
                return (200, {"ok": True, "message": "Jamoadan chiqarildi"}) if app.hub.fire(m.group(1)) else (404, {"error": "Topilmadi"})
            task = str(body.get("text", "")).strip()[:1000]
            if not task:
                return 400, {"error": "Topshiriqni yozing"}
            return 200, {"ok": True, "reply": app.hub.run_custom(m.group(1), task)}
        if path == "/api/chat":
            text = str(body.get("text", "")).strip()[:1000]
            if not text:
                return 400, {"error": "Xabar bo'sh"}
            reply = app.hub.owner_message(text)
            return 200, {"ok": True, "reply": reply}
        if path == "/api/ledger":
            kind = body.get("kind")
            try:
                amount = float(body.get("amount"))
            except (TypeError, ValueError):
                return 400, {"error": "Summani raqam bilan kiriting"}
            if kind not in ("income", "expense") or amount <= 0:
                return 400, {"error": "Turi va summasi noto'g'ri"}
            cur = str(body.get("currency") or "UZS").upper()
            if cur not in ("UZS", "USD"):
                return 400, {"error": "Valyuta UZS yoki USD bo'lsin"}
            cat = str(body.get("category") or "") or None
            app.hub.add_ledger(kind, amount, cur, str(body.get("note", ""))[:200], cat)
            return 200, {"ok": True, "message": "Moliya yozib oldi"}
        if path == "/api/links":
            code, label = str(body.get("code", "")).strip(), str(body.get("label", "")).strip()[:80]
            if not PAYLOAD_RE.match(code) or code.startswith("ref_"):
                return 400, {"error": "Kod faqat lotin harf, raqam, _ va - dan iborat bo'lsin (64 tagacha)"}
            self.db.execute("INSERT OR REPLACE INTO links(code, label, created_at) VALUES (?,?,?)",
                            (code, label or code, now()))
            self.db.audit("panel", "link_created", {"code": code, "label": label})
            return 200, {"ok": True, "url": app.bot.bot_link(code)}
        for mod in getattr(app, "panel_modules", []):
            r = mod.panel_action(path, body)
            if r:
                return r
        return 404, {"error": "Noma'lum amal"}

    # ------------------------------------------------------------- server
    def handler(self):
        panel = self

        class H(BaseHTTPRequestHandler):
            server_version = "Chorraha"
            sys_version = ""

            def log_message(self, fmt, *args):  # noqa: N802
                log.debug("panel: " + fmt, *args)

            def ip(self) -> str:
                return (self.headers.get("X-Real-IP") or self.client_address[0])[:64]

            def authed(self) -> bool:
                c = SimpleCookie(self.headers.get("Cookie") or "")
                return panel.check_token(c[COOKIE].value if COOKIE in c else None)

            def send_json(self, code: int, data: Any, cookie: str | None = None):
                raw = json.dumps(data, ensure_ascii=False, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self._sec_headers()
                if cookie is not None:
                    flags = "HttpOnly; SameSite=Strict; Path=/" + ("; Secure" if panel.secure_cookie else "")
                    age = SESSION_TTL if cookie else 0
                    self.send_header("Set-Cookie", f"{COOKIE}={cookie}; Max-Age={age}; {flags}")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _sec_headers(self):
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "same-origin")

            def do_GET(self):  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path in ("/", "/index.html"):
                    raw = (b"<!doctype html><html lang=\"uz\"><head><meta charset=\"utf-8\">"
                           b"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
                           b"<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}"
                           b"body{margin:0}[hidden]{display:none!important}img{max-width:100%}</style></head><body>"
                           + STATIC.read_bytes() + b"</body></html>")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self._sec_headers()
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                m = re.fullmatch(r"/oauth/(instagram|tiktok)/callback", path)
                if m:
                    return self.oauth_callback(m.group(1))
                m = re.fullmatch(r"/p/([A-Za-z0-9_-]{10,64})", path)
                if m:  # hamkor portali — parolsiz, maxfiy token bilan
                    page = panel.app.partners.portal_html(m.group(1))
                    if page is None:
                        return self.send_json(404, {"error": "Topilmadi"})
                    raw = page.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self._sec_headers()
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if not path.startswith("/api/"):
                    return self.send_json(404, {"error": "Topilmadi"})
                if not self.authed():
                    return self.send_json(401, {"error": "Kirish kerak"})
                if path == "/api/overview":
                    return self.send_json(200, panel.overview())
                if path == "/api/posts":
                    return self.send_json(200, panel.posts())
                if path == "/api/log":
                    return self.send_json(200, panel.audit())
                q = self.path.split("?", 1)[1] if "?" in self.path else ""
                if path == "/api/agents":
                    since = int(re.search(r"since=(\d+)", q).group(1)) if re.search(r"since=(\d+)", q) else 0
                    agent = re.search(r"agent=([a-z]+)", q)
                    msgs = panel.app.hub.messages(since, 300)
                    if agent:
                        msgs = [m for m in msgs if agent.group(1) in (m["sender"], m["recipient"])][-80:]
                    return self.send_json(200, {"agents": panel.app.hub.status(), "messages": msgs[-120:],
                                                "strategy": panel.app.hub.strategy()})
                if path == "/api/reviews":
                    rows = [dict(r) for r in panel.db.all(
                        "SELECT r.post_id, r.at, r.verdict, r.text, p.hook FROM post_reviews r JOIN posts p ON p.id=r.post_id"
                        " ORDER BY r.id DESC LIMIT 30")]
                    return self.send_json(200, rows)
                if path == "/api/reports":
                    return self.send_json(200, [dict(r) for r in panel.db.all(
                        "SELECT id, at, kind, title, text FROM reports ORDER BY id DESC LIMIT 30")])
                if path == "/api/charts":
                    return self.send_json(200, panel.charts())
                if path == "/api/connections":
                    st = panel.app.conn.status()
                    st["last"] = panel.db.get("last_connect_msg")
                    return self.send_json(200, st)
                if path == "/api/finance":
                    f = panel.app.hub.finance()
                    led = [dict(r) for r in panel.db.all("SELECT * FROM ledger ORDER BY id DESC LIMIT 40")]
                    return self.send_json(200, {**f, "text": panel.app.hub.finance_text(f), "entries": led})
                from urllib.parse import parse_qs
                qd = {k: v[0] for k, v in parse_qs(q).items()}
                for mod in getattr(panel.app, "panel_modules", []):
                    r = mod.panel_get(path, qd)
                    if r:
                        return self.send_json(*r)
                m = re.fullmatch(r"/api/video/(\d+)", path)
                if m:
                    return self.send_video(int(m.group(1)))
                self.send_json(404, {"error": "Topilmadi"})

            def oauth_callback(self, platform: str):
                from urllib.parse import parse_qs, urlparse
                from .connections import ConnectError
                q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                try:
                    if q.get("error"):
                        raise ConnectError(q.get("error_description") or q["error"])
                    d = panel.app.conn.callback(platform, q.get("code", "").split("#")[0], q.get("state", ""))
                    msg = f"{platform}: ulandi (@{d.get('username') or '?'})"
                except Exception as exc:  # noqa: BLE001 — sabab sahifada ko'rsatiladi
                    msg = f"{platform}: ulanmadi — {str(exc)[:200]}"
                panel.db.set("last_connect_msg", msg)
                self.send_response(302)
                self.send_header("Location", "/#ulanishlar")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()

            def send_video(self, pid: int):
                p = panel.db.post(pid)
                if not p or not p["video_path"] or not Path(p["video_path"]).exists():
                    return self.send_json(404, {"error": "Video topilmadi"})
                f = Path(p["video_path"])
                size = f.stat().st_size
                start, end = 0, size - 1
                rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
                if rng and (rng.group(1) or rng.group(2)):
                    if rng.group(1):
                        start = int(rng.group(1))
                        end = min(int(rng.group(2)), size - 1) if rng.group(2) else size - 1
                    else:
                        start = max(0, size - int(rng.group(2)))
                    if start > end:
                        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                else:
                    self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self._sec_headers()
                self.end_headers()
                with f.open("rb") as fh:
                    fh.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = fh.read(min(65536, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)

            def do_POST(self):  # noqa: N802
                path = self.path.split("?", 1)[0]
                length = min(int(self.headers.get("Content-Length") or 0), 10_000)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    body = {}
                if self.headers.get("X-Panel") != "1":
                    return self.send_json(403, {"error": "So'rov rad etildi"})
                if path == "/api/login":
                    if panel.blocked(self.ip()):
                        return self.send_json(429, {"error": "Ko'p urinish. 10 daqiqadan keyin qayta urining."})
                    tok = panel.login(self.ip(), str(body.get("password", "")))
                    if not tok:
                        return self.send_json(401, {"error": "Parol noto'g'ri"})
                    return self.send_json(200, {"ok": True}, cookie=tok)
                if path == "/api/logout":
                    return self.send_json(200, {"ok": True}, cookie="")
                if not self.authed():
                    return self.send_json(401, {"error": "Kirish kerak"})
                code, data = panel.action(path, body if isinstance(body, dict) else {})
                self.send_json(code, data)

        return H

    def serve(self, stop: threading.Event | None = None) -> ThreadingHTTPServer:
        srv = ThreadingHTTPServer((self.host, self.port), self.handler())
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log.info("Panel: http://%s:%d", self.host, self.port)
        return srv
