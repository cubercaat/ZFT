"""Kontent quvuri: budjet → konsept → AI sahnalar → montaj → tasdiq/avtopilot → e'lon → natija.

Kuniga bir nechta slot (standart: 12:30 va 20:00). Har slot uchun alohida video.
Darajalar: cinematic (AI video sahnalar) → light (AI rasmlar harakatda) → quiz (bepul test-video).
Biror daraja xato bersa, keyingisiga o'tadi — slot hech qachon bo'sh qolmaydi.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import random
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .db import now
from .director import Concept
from .genai import GenError
from .publishers.base import PostPayload
from .telegram_api import TelegramError, kb
from .video import VideoPlan

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)
DAY = 86400
TIER_NAMES = {"cinematic": "Kinematografik AI video", "light": "AI rasmlar + harakat", "quiz": "Test-video (bepul)"}


def score(m: dict[str, dict[str, int]]) -> int:
    """Post bahosi: saqlash/ulashish/izoh ko'rishdan qimmatroq (algoritm ham shunday baholaydi)."""
    total = 0
    for v in m.values():
        total += v.get("views", 0) + 3 * v.get("likes", 0) + 8 * v.get("comments", 0) \
            + 12 * v.get("saves", 0) + 12 * v.get("shares", 0)
    return total


class Pipeline:
    def __init__(self, app: "App"):
        self.app = app
        self.db = app.db
        self.s = app.settings
        self.defer_review = False          # Jarvislar: SMMchi matnni yozgandan keyin egaga yuboriladi
        self.last_info: dict[int, dict] = {}

    # ------------------------------------------------------------ context
    def context(self) -> dict:
        posts = []
        for r in self.db.all("SELECT * FROM posts WHERE status='published' ORDER BY id DESC LIMIT 14"):
            m = self.db.latest_metrics(r["id"])
            age_h = (now() - (r["published_at"] or now())) // 3600
            posts.append({"id": r["id"], "format": r["format"], "tier": r["tier"], "hook": r["hook"],
                          "slot": r["slot"], "age_hours": age_h, "metrics": m, "score": score(m)})
        week = now() - 7 * DAY
        sources = {r["source"]: r["c"] for r in self.db.all(
            "SELECT source, COUNT(*) c FROM users WHERE joined_at>=? GROUP BY source ORDER BY c DESC", (week,))}
        hard = self.hard_questions()
        topics = []
        for h in hard[:5]:
            q = self.app.bank.by_id(h["id"])
            if q:
                topics.append({"topic": q.topic, "question": q.question[:140], "wrong_rate": h["wrong_rate"]})
        try:
            directive = json.loads(self.db.get("market_directive") or "null")
        except ValueError:
            directive = None
        return {"posts": posts, "sources": sources, "hard_questions": hard, "hard_topics": topics,
                "trends": self.db.get("trend_notes"), "directive": directive}

    def hard_questions(self, limit: int = 8) -> list[dict]:
        stats: dict[str, list[int]] = {}
        for r in self.db.all("SELECT data FROM events WHERE kind='quiz_a' AND at>=?", (now() - 30 * DAY,)):
            try:
                d = json.loads(r["data"])
            except (TypeError, ValueError):
                continue
            s = stats.setdefault(d["q"], [0, 0])
            s[0] += 1
            s[1] += 0 if d["ok"] else 1
        out = [{"id": q, "attempts": a, "wrong_rate": round(w / a, 2)} for q, (a, w) in stats.items() if a >= 3]
        return sorted(out, key=lambda x: -x["wrong_rate"])[:limit]

    def candidates(self, n: int = 25, rng: random.Random | None = None) -> list[dict]:
        rng = rng or random.Random()
        bank = self.app.bank
        used = {r["question_id"] for r in self.db.all("SELECT question_id FROM posts WHERE status!='rejected'"
                                                       " AND question_id IS NOT NULL")}
        hard = [h["id"] for h in self.hard_questions()]
        picked: dict[str, None] = {}
        for qid in hard:
            q = bank.by_id(qid)
            if q and qid not in used:
                picked[qid] = None
        tries = 0
        while len(picked) < min(n, len(bank)) and tries < n * 6:
            tries += 1
            q = bank.pick(exclude=used | set(picked), rng=rng, video_friendly=True)
            picked[q.id] = None
        return [{"id": q.id, "topic": q.topic, "text": q.question[:160]}
                for q in (bank.by_id(i) for i in picked) if q]

    # -------------------------------------------------------------- slots
    def slot_time(self, slot: int, day: dt.date) -> dt.datetime:
        mins = self.s.slot_minutes()
        m = mins[slot % len(mins)]
        return dt.datetime(day.year, day.month, day.day, m // 60, m % 60, tzinfo=self.app.tz)

    def next_slot(self, at: dt.datetime | None = None) -> tuple[int, dt.datetime]:
        at = at or self.app.local_now()
        mins = self.s.slot_minutes()
        for day_off in (0, 1):
            day = (at + dt.timedelta(days=day_off)).date()
            for i in range(len(mins)):
                t = self.slot_time(i, day)
                if t > at + dt.timedelta(minutes=10):
                    taken = self.db.one("SELECT id FROM posts WHERE publish_at=? AND status IN"
                                        " ('pending','approved','published')", (int(t.timestamp()),))
                    if not taken:
                        return i, t
        return len(mins) - 1, self.slot_time(len(mins) - 1, (at + dt.timedelta(days=2)).date())

    # ------------------------------------------------------------- tasks
    def add_task(self, text: str, source: str = "owner") -> int:
        cur = self.db.execute("INSERT INTO tasks(text, status, created_at, source) VALUES (?,?,?,?)",
                              (text.strip()[:1000], "open", now(), source))
        self.db.audit(source, "task_added", text[:200])
        return int(cur.lastrowid)

    def open_task(self) -> dict | None:
        """Egasining topshirig'i har doim birinchi, keyin Marketolognikilar."""
        r = self.db.one("SELECT * FROM tasks WHERE status='open' ORDER BY (source='owner') DESC, id ASC LIMIT 1")
        return dict(r) if r else None

    # ------------------------------------------------------------- create
    def create_daily_content(self, trigger: str = "manual") -> int | None:
        """Bot/panel tugmasi: keyingi bo'sh slot uchun video."""
        slot, at = self.next_slot()
        return self.create_content(slot, at, trigger)

    def create_slot_content(self, slot: int) -> int | None:
        """Rejali: bugungi `slot` uchun video (allaqachon bo'lsa — o'tkazib yuboradi)."""
        at = self.slot_time(slot, self.app.local_now().date())
        if self.db.one("SELECT id FROM posts WHERE publish_at=? AND status IN ('pending','approved','published')",
                       (int(at.timestamp()),)):
            return None
        return self.create_content(slot, at, "schedule")

    def create_content(self, slot: int, publish_at: dt.datetime, trigger: str) -> int | None:
        nowl = self.app.local_now()
        last_slot = slot == len(self.s.slot_minutes()) - 1
        choice = self.app.vbudget.choose(nowl, last_slot, video_ready=self.s.studio_enabled)
        post_id = self.db.create_post(status="draft", slot=slot, publish_at=int(publish_at.timestamp()),
                                      tier=choice.tier, ai_reason=choice.reason)
        token = secrets.token_hex(6)
        out = self.s.media_dir / f"p{post_id}_{token}.mp4"
        tiers = {"cinematic": ["cinematic", "light", "quiz"], "light": ["light", "quiz"], "quiz": ["quiz"]}[choice.tier]
        errors = []
        result = None
        for tier in tiers:
            try:
                result = self._make(tier, choice.scenes or 3, post_id, out)
                break
            except Exception as exc:  # noqa: BLE001 — keyingi darajaga o'tamiz
                log.warning("%s darajasi xato: %s", tier, exc)
                errors.append(f"{tier}: {str(exc)[:160]}")
                self.db.audit("pipeline", "tier_failed", {"post": post_id, "tier": tier, "error": str(exc)[:300]})
        if not result:
            self.db.update_post(post_id, status="failed", error="; ".join(errors)[:500])
            self._notify_owner(f"⚠️ #{post_id} video yasalmadi:\n{html.escape('; '.join(errors)[:600])}")
            return None
        tier, fields, info = result
        cost = float(self.db.scalar("SELECT SUM(usd) FROM video_costs WHERE post_id=?", (post_id,), 0.0))
        status = "approved" if self.app.auto_publish else "pending"
        reason = fields.pop("reason", "") or ""
        if errors:
            reason = f"{reason} (avvalgi daraja xato berdi, {tier} ishlatildi)"
        self.db.update_post(post_id, video_path=str(out), status=status, tier=tier, cost_usd=cost,
                            approved_at=now() if status == "approved" else None,
                            ai_reason=f"{choice.reason}. {reason}".strip()[:600], **fields)
        self.db.audit("pipeline", "content_created", {"post": post_id, "tier": tier, "slot": slot,
                                                      "cost": round(cost, 3), "status": status, **info})
        self.last_info[post_id] = info
        if not self.defer_review:
            self._send_for_review(post_id, info)
        return post_id

    def _make(self, tier: str, scenes: int, post_id: int, out: Path) -> tuple[str, dict, dict]:
        if tier == "quiz":
            return self._make_quiz(out)
        app = self.app
        task = self.open_task()
        concept: Concept = app.director.concept(self.context(), scenes, task)
        media: list[Path] = []
        tmp = Path(tempfile.mkdtemp(prefix="studio_"))
        try:
            vb = app.vbudget
            for i, sc in enumerate(concept.scenes):
                if tier == "cinematic":
                    g = app.fal.video(self.s.video_model, sc.visual, sc.seconds or vb.scene_seconds,
                                      vb.price_per_sec, self.s.video_resolution)
                    vb.record(post_id, "video_scene", g.model, g.seconds, g.cost_usd)
                    media.append(app.fal.download(g.url, tmp / f"s{i}.mp4"))
                else:
                    g = app.fal.image(self.s.image_model, sc.visual + ", photorealistic, cinematic still, 9:16",
                                      vb.image_price)
                    vb.record(post_id, "image", g.model, 0, g.cost_usd)
                    media.append(app.fal.download(g.url, tmp / f"s{i}.jpg"))
            res = app.studio.make(concept, media, "video" if tier == "cinematic" else "image", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if concept.task_id:
            self.db.execute("UPDATE tasks SET status='done', used_at=?, post_id=? WHERE id=?",
                            (now(), post_id, concept.task_id))
        fields = {"format": concept.format, "hook": concept.hook_text or concept.title, "caption": concept.caption,
                  "hashtags": json.dumps(concept.hashtags, ensure_ascii=False), "concept": concept.to_json(),
                  "reason": concept.reason}
        info = {"ai": concept.by_ai, "voice": res.has_voice, "music": res.has_music,
                "duration": round(res.duration, 1), "task": concept.task_id}
        return tier, fields, info

    def _make_quiz(self, out: Path) -> tuple[str, dict, dict]:
        cands = self.candidates()
        if not cands:
            raise RuntimeError("Savollar bazasi bo'sh")
        plan = self.app.brain.plan_content(self.context(), cands)
        q = (self.app.bank.by_id(plan.question_id) if plan.question_id else None) or self.app.bank.by_id(cands[0]["id"])
        res = self.app.video.make(q, VideoPlan(hook=plan.hook, cta=plan.cta), out)
        fields = {"format": "quiz5", "question_id": q.id, "hook": plan.hook, "caption": plan.caption,
                  "hashtags": json.dumps(plan.hashtags, ensure_ascii=False), "reason": plan.reason}
        return "quiz", fields, {"ai": plan.by_ai, "voice": res.has_voice, "duration": round(res.duration, 1)}

    def _send_for_review(self, post_id: int, info: dict) -> None:
        p = self.db.post(post_id)
        if not self.s.owner_id or not p:
            return
        tags = " ".join(f"#{t}" for t in json.loads(p["hashtags"] or "[]"))
        auto = p["status"] == "approved"
        when = dt.datetime.fromtimestamp(p["publish_at"], self.app.tz).strftime("%d.%m %H:%M") if p["publish_at"] else "?"
        head = f"🤖 Avtopilot: {when} da avtomatik chiqadi" if auto else f"🎬 Yangi video. E'lon vaqti: {when}"
        cap = (f"{head}\n<b>{html.escape(TIER_NAMES.get(p['tier'], p['tier'] or ''))}</b> · "
               f"{(p['cost_usd'] or 0):.2f}$ · {info.get('duration', 0):.0f} s\n\n"
               f"<b>Hook:</b> {html.escape(p['hook'] or '')}\n"
               f"<b>Matn:</b>\n{html.escape((p['caption'] or '')[:380])}\n{html.escape(tags[:150])}\n\n"
               f"<i>{html.escape((p['ai_reason'] or '-')[:220])}</i>")
        markup = kb([[("✅ E'lon qilish", f"ap:{post_id}"), ("❌ Rad etish", f"rj:{post_id}")],
                     [("🔄 Boshqa variant", f"rg:{post_id}")]]) if not auto else \
            kb([[("❌ To'xtatish", f"rj:{post_id}")]])
        try:
            self.app.tg.send_video(self.s.owner_id, p["video_path"], cap[:1024], reply_markup=markup)
        except (TelegramError, OSError) as exc:
            log.warning("Egaga video yuborilmadi: %s", exc)

    # ---------------------------------------------------------- approvals
    def approve(self, post_id: int) -> None:
        p = self.db.post(post_id)
        if p and p["status"] == "pending":
            self.db.update_post(post_id, status="approved", approved_at=now())
            self.db.audit("owner", "post_approved", {"post": post_id})

    def reject(self, post_id: int, reason: str = "owner") -> None:
        p = self.db.post(post_id)
        if p and p["status"] in ("pending", "approved", "draft"):
            self.db.update_post(post_id, status="rejected", error=reason)
            self.db.audit("owner", "post_rejected", {"post": post_id, "reason": reason})

    def regenerate(self, post_id: int) -> int | None:
        """Rad etib, o'sha slot uchun yangi variant."""
        p = self.db.post(post_id)
        self.reject(post_id, reason="regenerate")
        if p and p["publish_at"] and p["publish_at"] > now() + 600:
            at = dt.datetime.fromtimestamp(p["publish_at"], self.app.tz)
            return self.create_content(p["slot"] or 0, at, "rg")
        return self.create_daily_content("rg")

    # ------------------------------------------------------------ publish
    def publish_slot(self, slot: int) -> list[int]:
        """Rejali: vaqti kelgan eng eski tasdiqlangan videoni e'lon qiladi (slotga bittadan)."""
        p = self.db.one("SELECT * FROM posts WHERE status='approved' AND (publish_at IS NULL OR publish_at<=?)"
                        " ORDER BY publish_at ASC, id ASC LIMIT 1", (now() + 120,))
        if not p:
            pend = self.db.one("SELECT id FROM posts WHERE status='pending' AND publish_at<=? ORDER BY id DESC LIMIT 1",
                               (now() + 120,))
            if pend:
                self._notify_owner(f"⏳ #{pend['id']} video tasdiq kutyapti — bu slotda chiqmadi. "
                                   "Tasdiqlasangiz keyingi slotda chiqadi (hozir chiqarish: /navbat chiqar).")
            return []
        return self._publish(p)

    def publish_approved(self, trigger: str = "manual") -> list[int]:
        """Qo'lda: eng eski tasdiqlangan videoni hozir e'lon qilish."""
        p = self.db.one("SELECT * FROM posts WHERE status='approved' ORDER BY publish_at ASC, id ASC LIMIT 1")
        if not p:
            self._notify_owner("Tasdiqlangan video yo'q.")
            return []
        return self._publish(p)

    def _publish(self, p) -> list[int]:
        if self.app.dry_run:
            self.db.update_post(p["id"], status="dry", error="sinov rejimi")
            self.db.audit("pipeline", "publish_dry", {"post": p["id"]})
            self._notify_owner(f"🧪 SINOV: #{p['id']} e'lon qilinmadi (sinov rejimi yoqilgan). O'chirish: /sinov off")
            return []
        payload = PostPayload(
            post_id=p["id"], video_path=Path(p["video_path"]),
            video_url=self.app.public_url(p["video_path"]), caption=p["caption"] or "",
            hashtags=json.loads(p["hashtags"] or "[]"))
        results = []
        for pub in self.app.publishers():
            r = pub.publish(payload)
            results.append(r)
            self.db.audit("pipeline", f"publish_{r.platform}", {"post": p["id"], "ok": r.ok,
                                                                 "id": r.remote_id, "error": r.error})
        fields: dict = {}
        for r in results:
            if r.ok and r.platform == "telegram":
                fields["tg_msg_id"] = r.remote_id
            elif r.ok and r.platform == "instagram":
                fields["ig_media_id"] = r.remote_id
            elif r.ok and r.platform == "tiktok":
                fields["tt_publish_id"] = r.remote_id
        ok_any = any(r.ok for r in results)
        errors = "; ".join(f"{r.platform}: {r.error}" for r in results if not r.ok)
        fields.update(status="published" if ok_any else "failed", published_at=now() if ok_any else None,
                      error=errors[:500] or None)
        self.db.update_post(p["id"], **fields)
        if fields.get("ig_media_id"):
            self.app.smm.after_publish(p["id"])
        lines = [f"📤 #{p['id']} e'lon natijasi:"]
        if not results:
            lines.append("Hech bir platforma ulanmagan (.env ni tekshiring).")
        for r in results:
            extra = f" ({r.extra.get('privacy')})" if r.extra.get("privacy") else ""
            lines.append(f"{'✅' if r.ok else '❌'} {r.platform}{extra}" + ("" if r.ok else f": {r.error}"))
        self._notify_owner(html.escape("\n".join(lines)))
        return [p["id"]] if ok_any else []

    # ------------------------------------------------------------ metrics
    def collect_metrics(self) -> int:
        n = 0
        ig, tt = self.app.instagram, self.app.tiktok
        for p in self.db.all("SELECT * FROM posts WHERE status='published' AND published_at>=?",
                             (now() - 21 * DAY,)):
            if ig and ig.enabled and p["ig_media_id"]:
                m = ig.metrics(p["ig_media_id"])
                if m:
                    self.db.add_metric(p["id"], "instagram", **m)
                    n += 1
            if tt and tt.enabled and p["tt_publish_id"] and p["tt_publish_id"].isdigit():
                m = tt.metrics(p["tt_publish_id"])
                if m:
                    self.db.add_metric(p["id"], "tiktok", **m)
                    n += 1
            time.sleep(0.2)
        return n

    # -------------------------------------------------------- group quiz
    def send_group_quiz(self) -> str | None:
        gid = self.app.conn.group_id()
        if not gid or self.app.dry_run:
            return None
        recent = {r["question_id"] for r in self.db.all(
            "SELECT question_id FROM group_polls ORDER BY sent_at DESC LIMIT 60")}
        q = self.app.bank.pick(exclude=recent)
        if len(q.question) > 300 or any(len(o) > 100 for o in q.options):
            q = self.app.bank.pick(exclude=recent | {q.id}, video_friendly=True)
        try:
            if q.image:
                with open(q.image, "rb") as f:
                    self.app.tg.call("sendPhoto", files={"photo": f}, chat_id=gid, caption="🧠 Kunlik savol 👇")
            msg = self.app.tg.send_quiz_poll(gid, q.question, q.options, q.correct, q.explanation)
        except (TelegramError, OSError) as exc:
            log.warning("Guruh viktorinasi yuborilmadi: %s", exc)
            return None
        poll_id = msg["poll"]["id"]
        self.db.execute("INSERT OR REPLACE INTO group_polls(poll_id, question_id, correct_index, sent_at)"
                        " VALUES (?,?,?,?)", (poll_id, q.id, q.correct, now()))
        return poll_id

    def _notify_owner(self, text: str) -> None:
        if self.s.owner_id:
            try:
                self.app.tg.send_message(self.s.owner_id, text)
            except TelegramError as exc:
                log.warning("Egaga xabar yuborilmadi: %s", exc)


__all__ = ["Pipeline", "score", "DAY", "TIER_NAMES", "GenError"]
