"""Instagram Reels — Instagram Graph API (Content Publishing).

Talablar: Instagram professional (Business/Creator) akkaunt, unga ulangan Facebook sahifa,
Meta ilovasida instagram_basic, instagram_content_publish, instagram_manage_insights ruxsatlari,
va video ochiq HTTPS URL orqali mavjud bo'lishi (Instagram videoni o'zi yuklab oladi).
"""
from __future__ import annotations

import logging
import time

import requests

from .base import PostPayload, PublishResult

log = logging.getLogger(__name__)


class InstagramPublisher:
    platform = "instagram"

    def __init__(self, ig_user_id: str, access_token: str, graph_version: str = "v23.0",
                 host: str = "https://graph.facebook.com", session: requests.Session | None = None,
                 poll_interval: float = 10.0, max_wait: float = 600.0):
        self.ig = ig_user_id
        self.token = access_token
        self.base = f"{host.rstrip('/')}/{graph_version}"
        self.http = session or requests.Session()
        self.poll_interval = poll_interval
        self.max_wait = max_wait

    @property
    def enabled(self) -> bool:
        return bool(self.ig and self.token)

    def _req(self, method: str, path: str, **params) -> dict:
        params["access_token"] = self.token
        r = self.http.request(method, f"{self.base}/{path}", params=params if method == "GET" else None,
                              data=params if method != "GET" else None, timeout=60)
        data = r.json()
        if "error" in data:
            raise RuntimeError(data["error"].get("message", str(data["error"])))
        return data

    def publish(self, p: PostPayload) -> PublishResult:
        if not p.video_url:
            return PublishResult(self.platform, False, error="PUBLIC_MEDIA_URL sozlanmagan")
        try:
            container = self._req("POST", f"{self.ig}/media", media_type="REELS", video_url=p.video_url,
                                  caption=p.full_caption(2200), share_to_feed="true")
            cid = container["id"]
            waited = 0.0
            while True:
                st = self._req("GET", cid, fields="status_code,status")
                code = st.get("status_code")
                if code == "FINISHED":
                    break
                if code in ("ERROR", "EXPIRED"):
                    return PublishResult(self.platform, False, error=f"Container {code}: {st.get('status')}")
                if waited >= self.max_wait:
                    return PublishResult(self.platform, False, error="Instagram videoni qayta ishlash cho'zildi")
                time.sleep(self.poll_interval)
                waited += self.poll_interval
            pub = self._req("POST", f"{self.ig}/media_publish", creation_id=cid)
            return PublishResult(self.platform, True, pub["id"])
        except (requests.RequestException, ValueError, RuntimeError, KeyError) as exc:
            return PublishResult(self.platform, False, error=str(exc))

    def metrics(self, media_id: str) -> dict[str, int] | None:
        """Ko'rishlar, layk, izoh, saqlash, ulashish, qamrov."""
        out: dict[str, int] = {}
        try:
            ins = self._req("GET", f"{media_id}/insights", metric="views,likes,comments,saved,shares,reach")
            for item in ins.get("data", []):
                vals = item.get("values") or [{}]
                v = item.get("total_value", {}).get("value", vals[0].get("value", 0))
                key = {"saved": "saves"}.get(item["name"], item["name"])
                out[key] = int(v or 0)
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            log.info("Insights olinmadi (%s), oddiy maydonlar olinadi", exc)
            try:
                f = self._req("GET", media_id, fields="like_count,comments_count")
                out = {"likes": int(f.get("like_count", 0)), "comments": int(f.get("comments_count", 0))}
            except (requests.RequestException, ValueError, RuntimeError) as exc2:
                log.warning("Instagram metrika xato: %s", exc2)
                return None
        return out

    # ------------------------------------------------- izohlar va DM (SMM avtomatika)
    def comments(self, media_id: str, limit: int = 50) -> list[dict]:
        data = self._req("GET", f"{media_id}/comments", fields="id,text,username,timestamp,from,replies{id,username}",
                         limit=str(limit))
        return data.get("data", [])

    def reply(self, comment_id: str, text: str) -> str:
        return self._req("POST", f"{comment_id}/replies", message=text[:2000])["id"]

    def comment(self, media_id: str, text: str) -> str:
        return self._req("POST", f"{media_id}/comments", message=text[:2000])["id"]

    def private_reply(self, comment_id: str, text: str) -> dict:
        """Izoh yozgan odamga bitta shaxsiy xabar (Instagram qoidasi: izohdan keyin 7 kun ichida, 1 marta)."""
        import json as _json
        return self._req("POST", f"{self.ig}/messages", recipient=_json.dumps({"comment_id": comment_id}),
                         message=_json.dumps({"text": text[:1000]}))

    def username(self) -> str | None:
        try:
            return self._req("GET", self.ig, fields="username").get("username")
        except (requests.RequestException, ValueError, RuntimeError):
            return None

    def follower_count(self) -> int | None:
        try:
            return int(self._req("GET", self.ig, fields="followers_count").get("followers_count", 0))
        except (requests.RequestException, ValueError, RuntimeError):
            return None
