"""SQLite baza: bitta fayl, server arzon bo'lsa ham yetadi."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id        INTEGER PRIMARY KEY,
    first_name   TEXT,
    username     TEXT,
    source       TEXT DEFAULT 'direct',
    referrer_id  INTEGER,
    joined_at    INTEGER NOT NULL,
    last_seen    INTEGER NOT NULL,
    quiz_correct INTEGER DEFAULT 0,
    quiz_total   INTEGER DEFAULT 0,
    blocked      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_users_source ON users(source);
CREATE INDEX IF NOT EXISTS idx_users_ref ON users(referrer_id);

CREATE TABLE IF NOT EXISTS links (
    code       TEXT PRIMARY KEY,
    label      TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER,
    kind  TEXT NOT NULL,
    data  TEXT,
    at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_kind_at ON events(kind, at);

CREATE TABLE IF NOT EXISTS posts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id   TEXT,
    format        TEXT,
    hook          TEXT,
    caption       TEXT,
    hashtags      TEXT,
    video_path    TEXT,
    status        TEXT NOT NULL DEFAULT 'draft',
    created_at    INTEGER NOT NULL,
    approved_at   INTEGER,
    published_at  INTEGER,
    tg_msg_id     TEXT,
    ig_media_id   TEXT,
    tt_publish_id TEXT,
    ai_reason     TEXT,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);

CREATE TABLE IF NOT EXISTS metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id      INTEGER NOT NULL,
    platform     TEXT NOT NULL,
    collected_at INTEGER NOT NULL,
    views        INTEGER DEFAULT 0,
    likes        INTEGER DEFAULT 0,
    comments     INTEGER DEFAULT 0,
    saves        INTEGER DEFAULT 0,
    shares       INTEGER DEFAULT 0,
    reach        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_metrics_post ON metrics(post_id, platform);

CREATE TABLE IF NOT EXISTS ai_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    at            INTEGER NOT NULL,
    model         TEXT,
    purpose       TEXT,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    searches      INTEGER DEFAULT 0,
    cost_usd      REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS group_polls (
    poll_id       TEXT PRIMARY KEY,
    question_id   TEXT,
    correct_index INTEGER,
    sent_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS poll_answers (
    poll_id TEXT NOT NULL,
    tg_id   INTEGER NOT NULL,
    correct INTEGER NOT NULL,
    at      INTEGER NOT NULL,
    PRIMARY KEY (poll_id, tg_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     INTEGER NOT NULL,
    actor  TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'open',
    created_at INTEGER NOT NULL,
    used_at    INTEGER,
    post_id    INTEGER
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def now() -> int:
    return int(time.time())


class DB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(posts)")}
        for name, typ in (("slot", "INTEGER"), ("tier", "TEXT"), ("cost_usd", "REAL DEFAULT 0"),
                          ("concept", "TEXT"), ("publish_at", "INTEGER"),
                          ("first_comment", "TEXT"), ("quality", "REAL"), ("quality_note", "TEXT"),
                          ("ig_comment_id", "TEXT")):
            if name not in cols:
                self.conn.execute(f"ALTER TABLE posts ADD COLUMN {name} {typ}")
        tcols = {r[1] for r in self.conn.execute("PRAGMA table_info(tasks)")}
        if "source" not in tcols:
            self.conn.execute("ALTER TABLE tasks ADD COLUMN source TEXT DEFAULT 'owner'")

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # --- past darajali ---
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, tuple(params))

    def one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.execute(sql, params).fetchall()

    def scalar(self, sql: str, params: Iterable[Any] = (), default: Any = 0) -> Any:
        row = self.one(sql, params)
        return default if row is None or row[0] is None else row[0]

    # --- jurnal (har bir harakat yoziladi) ---
    def audit(self, actor: str, action: str, detail: Any = None) -> None:
        if detail is not None and not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=False)
        self.execute(
            "INSERT INTO audit_log(at, actor, action, detail) VALUES (?,?,?,?)",
            (now(), actor, action, detail),
        )

    def event(self, kind: str, tg_id: int | None = None, data: Any = None) -> None:
        if data is not None and not isinstance(data, str):
            data = json.dumps(data, ensure_ascii=False)
        self.execute("INSERT INTO events(tg_id, kind, data, at) VALUES (?,?,?,?)", (tg_id, kind, data, now()))

    # --- kv ---
    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return row["value"] if row else default

    def set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    # --- foydalanuvchilar ---
    def upsert_user(self, tg_id: int, first_name: str, username: str | None,
                    source: str = "direct", referrer_id: int | None = None) -> bool:
        """Yangi foydalanuvchi bo'lsa True. Manba faqat birinchi kirishda yoziladi."""
        t = now()
        with self._lock:
            existing = self.one("SELECT tg_id FROM users WHERE tg_id=?", (tg_id,))
            if existing:
                self.execute(
                    "UPDATE users SET first_name=?, username=?, last_seen=?, blocked=0 WHERE tg_id=?",
                    (first_name, username, t, tg_id),
                )
                return False
            if referrer_id == tg_id or (referrer_id and not self.one(
                    "SELECT 1 FROM users WHERE tg_id=?", (referrer_id,))):
                referrer_id = None  # o'zini taklif qila olmaydi, mavjud bo'lmagan referer hisoblanmaydi
            self.execute(
                "INSERT INTO users(tg_id, first_name, username, source, referrer_id, joined_at, last_seen)"
                " VALUES (?,?,?,?,?,?,?)",
                (tg_id, first_name, username, source, referrer_id, t, t),
            )
            return True

    def user(self, tg_id: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM users WHERE tg_id=?", (tg_id,))

    def ref_count(self, tg_id: int) -> int:
        return self.scalar("SELECT COUNT(*) FROM users WHERE referrer_id=?", (tg_id,))

    def record_quiz(self, tg_id: int, correct: bool) -> None:
        self.execute(
            "UPDATE users SET quiz_total=quiz_total+1, quiz_correct=quiz_correct+? WHERE tg_id=?",
            (1 if correct else 0, tg_id),
        )

    # --- postlar ---
    def create_post(self, **fields: Any) -> int:
        fields.setdefault("created_at", now())
        cols = ", ".join(fields)
        qs = ", ".join("?" for _ in fields)
        cur = self.execute(f"INSERT INTO posts({cols}) VALUES ({qs})", fields.values())
        return int(cur.lastrowid)

    def update_post(self, post_id: int, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE posts SET {sets} WHERE id=?", [*fields.values(), post_id])

    def post(self, post_id: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM posts WHERE id=?", (post_id,))

    def add_metric(self, post_id: int, platform: str, **vals: int) -> None:
        keys = ["views", "likes", "comments", "saves", "shares", "reach"]
        self.execute(
            "INSERT INTO metrics(post_id, platform, collected_at, views, likes, comments, saves, shares, reach)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (post_id, platform, now(), *[int(vals.get(k, 0) or 0) for k in keys]),
        )

    def latest_metrics(self, post_id: int) -> dict[str, dict[str, int]]:
        rows = self.all(
            "SELECT m.* FROM metrics m JOIN (SELECT platform, MAX(id) mid FROM metrics WHERE post_id=?"
            " GROUP BY platform) x ON m.id=x.mid",
            (post_id,),
        )
        return {r["platform"]: {k: r[k] for k in ("views", "likes", "comments", "saves", "shares", "reach")}
                for r in rows}

    # --- AI xarajati ---
    def ai_spent_this_month(self, month_start: int) -> float:
        return float(self.scalar("SELECT SUM(cost_usd) FROM ai_usage WHERE at>=?", (month_start,), 0.0))
