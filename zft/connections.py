"""Akkauntlarni ulash: Instagram va TikTok — rasmiy "Ulash" (OAuth), Telegram — botni admin qilish.

Parol hech qachon so'ralmaydi va saqlanmaydi. Ega platformaning o'z sahifasida kiradi va ruxsat beradi;
ZFT faqat cheklangan huquqli tokenni saqlaydi (video joylash va statistika). Token muddati kelganda
o'zi yangilanadi. "Uzish" tokenni bazadan o'chiradi.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import requests

from .db import now

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    platform   TEXT PRIMARY KEY,       -- instagram | tiktok | telegram_channel | telegram_group
    data       TEXT NOT NULL,          -- json: token, akkaunt nomi, muddati
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tg_chats (
    chat_id  TEXT PRIMARY KEY,
    title    TEXT,
    type     TEXT,                     -- channel | supergroup | group
    is_admin INTEGER,
    seen_at  INTEGER
);
"""

IG_AUTH = "https://www.instagram.com/oauth/authorize"
IG_TOKEN = "https://api.instagram.com/oauth/access_token"
IG_GRAPH = "https://graph.instagram.com"
TT_AUTH = "https://www.tiktok.com/v2/auth/authorize/"
TT_TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
TT_API = "https://open.tiktokapis.com"


class ConnectError(RuntimeError):
    pass


class Connections:
    def __init__(self, app: "App", session: requests.Session | None = None):
        self.app = app
        self.db = app.db
        self.db.conn.executescript(SCHEMA)
        self.http = session or requests.Session()

    # ------------------------------------------------------------ sozlama
    @property
    def panel_url(self) -> str:
        return os.environ.get("PANEL_URL", "https://panel.chorrahaa.uz").rstrip("/")

    def redirect_uri(self, platform: str) -> str:
        return f"{self.panel_url}/oauth/{platform}/callback"

    @staticmethod
    def ig_app() -> tuple[str, str]:
        return os.environ.get("IG_APP_ID", ""), os.environ.get("IG_APP_SECRET", "")

    @staticmethod
    def tt_app() -> tuple[str, str]:
        return os.environ.get("TIKTOK_CLIENT_KEY", ""), os.environ.get("TIKTOK_CLIENT_SECRET", "")

    # --------------------------------------------------------------- baza
    def get(self, platform: str) -> dict | None:
        r = self.db.one("SELECT data FROM connections WHERE platform=?", (platform,))
        return json.loads(r["data"]) if r else None

    def save(self, platform: str, data: dict) -> None:
        self.db.execute("INSERT INTO connections(platform, data, updated_at) VALUES (?,?,?) "
                        "ON CONFLICT(platform) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                        (platform, json.dumps(data, ensure_ascii=False), now()))

    def disconnect(self, platform: str) -> None:
        self.db.execute("DELETE FROM connections WHERE platform=?", (platform,))
        self.db.audit("owner", "disconnected", platform)

    # ------------------------------------------------------- OAuth holati
    def _new_state(self, platform: str) -> str:
        st = secrets.token_urlsafe(24)
        self.db.set(f"oauth_state:{st}", json.dumps({"p": platform, "at": now()}))
        return st

    def _check_state(self, platform: str, state: str) -> bool:
        raw = self.db.get(f"oauth_state:{state}") if state else None
        if not raw:
            return False
        self.db.execute("DELETE FROM kv WHERE key=?", (f"oauth_state:{state}",))  # bir martalik
        d = json.loads(raw)
        return d.get("p") == platform and now() - int(d.get("at", 0)) < 900

    def start(self, platform: str) -> str:
        if platform == "instagram":
            cid, sec = self.ig_app()
            if not (cid and sec):
                raise ConnectError("IG_APP_ID va IG_APP_SECRET kiritilmagan (README: Instagram ilovasi)")
            scopes = os.environ.get("IG_SCOPES", "instagram_business_basic,instagram_business_content_publish,instagram_business_manage_comments,"
                                                "instagram_business_manage_messages,"
                                                 "instagram_business_manage_insights")
            q = {"client_id": cid, "redirect_uri": self.redirect_uri("instagram"), "response_type": "code",
                 "scope": scopes, "state": self._new_state("instagram"), "enable_fb_login": "0"}
            return f"{IG_AUTH}?{urlencode(q)}"
        if platform == "tiktok":
            key, sec = self.tt_app()
            if not (key and sec):
                raise ConnectError("TIKTOK_CLIENT_KEY va TIKTOK_CLIENT_SECRET kiritilmagan (README: TikTok ilovasi)")
            q = {"client_key": key, "scope": os.environ.get("TIKTOK_SCOPES", "user.info.basic,video.publish,video.list"),
                 "response_type": "code", "redirect_uri": self.redirect_uri("tiktok"), "state": self._new_state("tiktok")}
            return f"{TT_AUTH}?{urlencode(q)}"
        raise ConnectError("Noma'lum platforma")

    def callback(self, platform: str, code: str, state: str) -> dict:
        if not self._check_state(platform, state):
            raise ConnectError("Havola eskirgan yoki soxta (state). Paneldan qaytadan «Ulash» ni bosing.")
        if not code:
            raise ConnectError("Ruxsat berilmadi")
        data = self._ig_exchange(code) if platform == "instagram" else self._tt_exchange(code)
        self.save(platform, data)
        self.db.audit("owner", "connected", {"platform": platform, "account": data.get("username")})
        self.app.hub.say("nashr", "ega", f"{platform.capitalize()} ulandi: @{data.get('username') or '?'}. Endi videolarni o'zim joylayman.")
        return data

    # ----------------------------------------------------------- Instagram
    @staticmethod
    def _json(r: requests.Response) -> dict:
        try:
            d = r.json()
        except ValueError as exc:
            raise ConnectError(f"Javob noto'g'ri ({r.status_code})") from exc
        err = d.get("error") if isinstance(d, dict) else None
        if isinstance(err, dict):
            if err.get("code") not in (None, "ok", 0):
                raise ConnectError(str(err.get("message") or err.get("code"))[:300])
        elif err:
            raise ConnectError(str(d.get("error_description") or d.get("error_message") or err)[:300])
        if r.status_code >= 400:
            raise ConnectError(str(d.get("error_message") or d)[:300])
        return d

    def _ig_exchange(self, code: str) -> dict:
        cid, sec = self.ig_app()
        short = self._json(self.http.post(IG_TOKEN, data={
            "client_id": cid, "client_secret": sec, "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri("instagram"), "code": code}, timeout=30))
        if "data" in short and isinstance(short["data"], list):  # ba'zi javoblar ro'yxat ichida keladi
            short = short["data"][0]
        long = self._json(self.http.get(f"{IG_GRAPH}/access_token", params={
            "grant_type": "ig_exchange_token", "client_secret": sec, "access_token": short["access_token"]}, timeout=30))
        token = long["access_token"]
        me = self._json(self.http.get(f"{IG_GRAPH}/me", params={"fields": "user_id,username", "access_token": token},
                                      timeout=30))
        return {"token": token, "user_id": str(me.get("user_id") or short.get("user_id")), "username": me.get("username"),
                "expires_at": now() + int(long.get("expires_in", 5184000)), "refreshed_at": now(), "host": IG_GRAPH}

    def _ig_refresh(self, c: dict) -> dict:
        d = self._json(self.http.get(f"{IG_GRAPH}/refresh_access_token", params={
            "grant_type": "ig_refresh_token", "access_token": c["token"]}, timeout=30))
        c.update(token=d["access_token"], expires_at=now() + int(d.get("expires_in", 5184000)), refreshed_at=now())
        return c

    # --------------------------------------------------------------- TikTok
    def _tt_exchange(self, code: str) -> dict:
        key, sec = self.tt_app()
        d = self._json(self.http.post(TT_TOKEN, data={
            "client_key": key, "client_secret": sec, "code": code, "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri("tiktok")}, timeout=30,
            headers={"Content-Type": "application/x-www-form-urlencoded"}))
        data = {"token": d["access_token"], "refresh_token": d.get("refresh_token"), "open_id": d.get("open_id"),
                "expires_at": now() + int(d.get("expires_in", 86400)),
                "refresh_expires_at": now() + int(d.get("refresh_expires_in", 31536000)), "refreshed_at": now()}
        try:
            u = self._json(self.http.get(f"{TT_API}/v2/user/info/", params={"fields": "open_id,display_name"},
                                         headers={"Authorization": f"Bearer {data['token']}"}, timeout=30))
            data["username"] = ((u.get("data") or {}).get("user") or {}).get("display_name")
        except (ConnectError, requests.RequestException):
            data["username"] = None
        return data

    def _tt_refresh(self, c: dict) -> dict:
        key, sec = self.tt_app()
        d = self._json(self.http.post(TT_TOKEN, data={
            "client_key": key, "client_secret": sec, "grant_type": "refresh_token",
            "refresh_token": c["refresh_token"]}, timeout=30,
            headers={"Content-Type": "application/x-www-form-urlencoded"}))
        c.update(token=d["access_token"], refresh_token=d.get("refresh_token", c["refresh_token"]),
                 expires_at=now() + int(d.get("expires_in", 86400)), refreshed_at=now())
        return c

    # ------------------------------------------------------- yangilash
    def refresh_all(self) -> dict[str, str]:
        """Kunda bir marta: muddati yaqinlashgan tokenlarni yangilaydi."""
        out = {}
        ig = self.get("instagram")
        if ig and ig["expires_at"] - now() < 15 * 86400 and now() - ig.get("refreshed_at", 0) > 86400:
            try:
                self.save("instagram", self._ig_refresh(ig))
                out["instagram"] = "yangilandi"
            except (ConnectError, requests.RequestException) as exc:
                out["instagram"] = f"xato: {exc}"
        tt = self.get("tiktok")
        if tt and tt.get("refresh_token") and tt["expires_at"] - now() < 6 * 3600:
            try:
                self.save("tiktok", self._tt_refresh(tt))
                out["tiktok"] = "yangilandi"
            except (ConnectError, requests.RequestException) as exc:
                out["tiktok"] = f"xato: {exc}"
        for p, res in out.items():
            if res.startswith("xato"):
                self.app.hub.say("nashr", "ega", f"{p} tokenini yangilab bo'lmadi ({res}). «Ulanishlar» sahifasida qayta ulang.")
        return out

    # ------------------------------------------------------------ Telegram
    def on_chat_member(self, upd: dict) -> None:
        """Bot kanal/guruhga qo'shilsa yoki admin qilinsa — o'zi yozib oladi."""
        chat = upd.get("chat") or {}
        status = ((upd.get("new_chat_member") or {}).get("status")) or ""
        if not chat.get("id"):
            return
        if status in ("left", "kicked"):
            self.db.execute("DELETE FROM tg_chats WHERE chat_id=?", (str(chat["id"]),))
            return
        self.db.execute("INSERT INTO tg_chats(chat_id, title, type, is_admin, seen_at) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, type=excluded.type, "
                        "is_admin=excluded.is_admin, seen_at=excluded.seen_at",
                        (str(chat["id"]), chat.get("title") or chat.get("username") or "", chat.get("type"),
                         1 if status == "administrator" else 0, now()))
        if status == "administrator" and self.app.settings.owner_id:
            kind = "kanal" if chat.get("type") == "channel" else "guruh"
            try:
                self.app.tg.send_message(self.app.settings.owner_id,
                                         f"✅ Bot «{chat.get('title')}» {kind}ida admin bo'ldi. Panelning «Ulanishlar» "
                                         f"sahifasida uni {kind} sifatida tanlang.")
            except Exception:  # noqa: BLE001
                pass

    def use_chat(self, chat_id: str, role: str) -> dict:
        r = self.db.one("SELECT * FROM tg_chats WHERE chat_id=?", (chat_id,))
        if not r:
            raise ConnectError("Bu chat topilmadi. Botni avval kanal yoki guruhga admin qilib qo'shing.")
        if role not in ("channel", "group"):
            raise ConnectError("Noto'g'ri tanlov")
        self.save(f"telegram_{role}", {"chat_id": chat_id, "title": r["title"], "type": r["type"]})
        self.db.audit("owner", "telegram_chat", {"role": role, "chat": chat_id})
        return dict(r)

    # --------------------------------------------------------------- holat
    def status(self) -> dict:
        s = self.app.settings
        ig, tt = self.get("instagram"), self.get("tiktok")
        ch, gr = self.get("telegram_channel"), self.get("telegram_group")
        bot = {"username": s.bot_username, "ok": bool(s.bot_token)}

        def exp(c):
            return max(0, int((c["expires_at"] - now()) / 86400)) if c and c.get("expires_at") else None
        return {
            "instagram": {"connected": bool(ig) or bool(s.ig_user_id and s.ig_access_token),
                          "username": (ig or {}).get("username"), "expires_days": exp(ig),
                          "app_ready": all(self.ig_app()), "source": "oauth" if ig else ("env" if s.ig_access_token else None),
                          "redirect_uri": self.redirect_uri("instagram")},
            "tiktok": {"connected": bool(tt) or bool(s.tiktok_access_token), "username": (tt or {}).get("username"),
                       "expires_days": exp(tt), "app_ready": all(self.tt_app()), "privacy": s.tiktok_privacy,
                       "redirect_uri": self.redirect_uri("tiktok")},
            "telegram": {"bot": bot, "channel": ch or ({"chat_id": s.channel_id, "title": s.channel_id} if s.channel_id else None),
                         "group": gr or ({"chat_id": s.group_id, "title": s.group_id} if s.group_id else None),
                         "chats": [dict(r) for r in self.db.all("SELECT * FROM tg_chats ORDER BY seen_at DESC")]},
            "media_url": bool(s.public_media_url),
        }

    # ----------------------------------------- ishlatiladigan qiymatlar
    def channel_id(self) -> str:
        c = self.get("telegram_channel")
        return c["chat_id"] if c else self.app.settings.channel_id

    def group_id(self) -> str:
        c = self.get("telegram_group")
        return c["chat_id"] if c else self.app.settings.group_id

    def instagram_creds(self) -> tuple[str, str, str] | None:
        c = self.get("instagram")
        if c:
            return c["user_id"], c["token"], c.get("host", IG_GRAPH)
        s = self.app.settings
        if s.ig_user_id and s.ig_access_token:
            return s.ig_user_id, s.ig_access_token, "https://graph.facebook.com"
        return None

    def tiktok_token(self) -> str | None:
        c = self.get("tiktok")
        return c["token"] if c else (self.app.settings.tiktok_access_token or None)
