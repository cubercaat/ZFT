"""Video budjeti: pul oy davomida tekis taqsimlanadi, kechki (asosiy) slotga ustunlik beriladi.

Budjet paneldan yoki /budjet buyrug'i bilan istalgan payt o'zgaradi — darhol kuchga kiradi.
"""
from __future__ import annotations

import calendar
import datetime as dt
import os
from dataclasses import dataclass

from .db import DB, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS video_costs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      INTEGER NOT NULL,
    post_id INTEGER,
    kind    TEXT,
    model   TEXT,
    seconds REAL DEFAULT 0,
    usd     REAL DEFAULT 0
);
"""


@dataclass
class TierChoice:
    tier: str          # cinematic | light | quiz
    scenes: int
    est_cost: float
    reason: str


class VideoBudget:
    def __init__(self, db: DB, tz: dt.timezone):
        self.db = db
        self.tz = tz
        db.conn.executescript(SCHEMA)

    # --- sozlamalar (kv ustun, keyin .env) ---
    def _num(self, key: str, env: str, default: float) -> float:
        v = self.db.get(key)
        if v is None:
            v = os.environ.get(env, default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    @property
    def monthly(self) -> float:
        return self._num("video_budget_usd", "VIDEO_MONTHLY_BUDGET_USD", 25.0)

    def set_monthly(self, usd: float) -> None:
        self.db.set("video_budget_usd", round(max(0.0, usd), 2))
        self.db.audit("owner", "video_budget", usd)

    @property
    def price_per_sec(self) -> float:
        return self._num("video_price_per_sec", "VIDEO_PRICE_PER_SEC", 0.10)

    @property
    def image_price(self) -> float:
        return self._num("image_price", "IMAGE_PRICE", 0.004)

    @property
    def scene_seconds(self) -> int:
        return int(self._num("scene_seconds", "SCENE_SECONDS", 6))

    # --- hisob ---
    def month_start(self, at: dt.datetime | None = None) -> int:
        n = at or dt.datetime.now(self.tz)
        return int(dt.datetime(n.year, n.month, 1, tzinfo=self.tz).timestamp())

    def spent(self, at: dt.datetime | None = None) -> float:
        return float(self.db.scalar("SELECT SUM(usd) FROM video_costs WHERE at>=?", (self.month_start(at),), 0.0))

    def record(self, post_id: int | None, kind: str, model: str, seconds: float, usd: float) -> None:
        self.db.execute("INSERT INTO video_costs(at, post_id, kind, model, seconds, usd) VALUES (?,?,?,?,?,?)",
                        (now(), post_id, kind, model, seconds, usd))

    def cinematic_cost(self, scenes: int) -> float:
        return scenes * self.scene_seconds * self.price_per_sec

    def light_cost(self, scenes: int = 4) -> float:
        return scenes * self.image_price

    def allowance(self, at: dt.datetime) -> float:
        """Bugun oxirigacha sarflash mumkin bo'lgan summa (tekis taqsimot)."""
        days = calendar.monthrange(at.year, at.month)[1]
        target = self.monthly * at.day / days
        return target - self.spent(at)

    def choose(self, at: dt.datetime, is_last_slot_today: bool, video_ready: bool) -> TierChoice:
        spent, budget = self.spent(at), self.monthly
        if spent >= budget:
            return TierChoice("quiz", 0, 0.0, f"Oylik video budjeti tugadi ({spent:.2f}$ / {budget:.0f}$)")
        allow = self.allowance(at)
        if not video_ready:  # fal.ai kaliti yo'q: AI video ham, AI rasm ham yo'q
            return TierChoice("quiz", 0, 0.0, "FAL_KEY kiritilmagan — bepul test-video")
        if video_ready:
            for n in (3, 2):
                c = self.cinematic_cost(n)
                need = c if is_last_slot_today else 2 * c  # ertalabgi slot kechkiga ham joy qoldiradi
                if allow >= need and spent + c <= budget:
                    return TierChoice("cinematic", n, c, f"Budjet yetadi: bugungi limit {allow:.2f}$, video ≈ {c:.2f}$")
        lc = self.light_cost()
        if spent + lc <= budget:
            why = "AI video kaliti yo'q" if not video_ready else \
                f"Pul tejaldi: bugungi limit {allow:.2f}$ kinematografik video uchun yetmaydi"
            return TierChoice("light", 4, lc, why)
        return TierChoice("quiz", 0, 0.0, "Budjet tugash arafasida — bepul format")

    def summary(self, at: dt.datetime) -> dict:
        days = calendar.monthrange(at.year, at.month)[1]
        rows = self.db.all("SELECT kind, COUNT(*) c, SUM(usd) s FROM video_costs WHERE at>=? GROUP BY kind",
                           (self.month_start(at),))
        return {"monthly": self.monthly, "spent": round(self.spent(at), 3), "allowance_today": round(self.allowance(at), 3),
                "price_per_sec": self.price_per_sec, "scene_seconds": self.scene_seconds,
                "cinematic_cost": round(self.cinematic_cost(3), 2), "days_left": days - at.day,
                "by_kind": {r["kind"]: {"count": r["c"], "usd": round(r["s"] or 0, 3)} for r in rows}}
