"""TikTok — Content Posting API (Direct Post, FILE_UPLOAD).

Muhim: TikTok ilovangiz audit (tekshiruv)dan o'tmaguncha postlar faqat SELF_ONLY (shaxsiy)
bo'lib chiqadi. Audit o'tgach TIKTOK_PRIVACY=PUBLIC_TO_EVERYONE qo'ying.
Kerakli scope: video.publish (metrika uchun video.list).
"""
from __future__ import annotations

import logging
import math
import time

import requests

from .base import PostPayload, PublishResult

log = logging.getLogger(__name__)

API = "https://open.tiktokapis.com"
CHUNK = 10 * 1024 * 1024


class TikTokPublisher:
    platform = "tiktok"

    def __init__(self, access_token: str, privacy: str = "SELF_ONLY", session: requests.Session | None = None,
                 api: str = API, poll_interval: float = 10.0, max_wait: float = 600.0):
        self.token = access_token
        self.privacy = privacy
        self.http = session or requests.Session()
        self.api = api.rstrip("/")
        self.poll_interval = poll_interval
        self.max_wait = max_wait

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _post(self, path: str, body: dict, params: dict | None = None) -> dict:
        r = self.http.post(f"{self.api}{path}", json=body, params=params, timeout=60, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json; charset=UTF-8"})
        data = r.json()
        err = data.get("error") or {}
        if err.get("code") not in (None, "ok"):
            raise RuntimeError(f"{err.get('code')}: {err.get('message')}")
        return data.get("data") or {}

    def publish(self, p: PostPayload) -> PublishResult:
        try:
            info = self._post("/v2/post/publish/creator_info/query/", {})
            allowed = info.get("privacy_level_options") or [self.privacy]
            privacy = self.privacy if self.privacy in allowed else allowed[0]
            size = p.video_path.stat().st_size
            chunk = size if size <= CHUNK else CHUNK
            total = max(1, math.floor(size / chunk)) if size > CHUNK else 1
            init = self._post("/v2/post/publish/video/init/", {
                "post_info": {"title": p.full_caption(2200), "privacy_level": privacy,
                              "disable_comment": False, "disable_duet": False, "disable_stitch": False},
                "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                                "chunk_size": chunk, "total_chunk_count": total},
            })
            publish_id, upload_url = init["publish_id"], init["upload_url"]
            with open(p.video_path, "rb") as f:
                for i in range(total):
                    start = i * chunk
                    end = size - 1 if i == total - 1 else start + chunk - 1  # oxirgi bo'lak qoldiqni oladi
                    f.seek(start)
                    part = f.read(end - start + 1)
                    r = self.http.put(upload_url, data=part, timeout=300, headers={
                        "Content-Type": "video/mp4", "Content-Length": str(len(part)),
                        "Content-Range": f"bytes {start}-{end}/{size}"})
                    if r.status_code not in (200, 201, 206):
                        return PublishResult(self.platform, False, publish_id, f"Yuklash xatosi {r.status_code}")
            waited = 0.0
            while waited < self.max_wait:
                st = self._post("/v2/post/publish/status/fetch/", {"publish_id": publish_id})
                status = st.get("status")
                if status == "PUBLISH_COMPLETE":
                    ids = st.get("publicaly_available_post_id") or st.get("publicly_available_post_id") or []
                    return PublishResult(self.platform, True, str(ids[0]) if ids else publish_id,
                                         extra={"privacy": privacy})
                if status == "FAILED":
                    return PublishResult(self.platform, False, publish_id, f"TikTok: {st.get('fail_reason')}")
                time.sleep(self.poll_interval)
                waited += self.poll_interval
            return PublishResult(self.platform, True, publish_id,
                                 extra={"privacy": privacy, "note": "hali qayta ishlanmoqda"})
        except (requests.RequestException, ValueError, RuntimeError, KeyError, OSError) as exc:
            return PublishResult(self.platform, False, error=str(exc))

    def metrics(self, video_id: str) -> dict[str, int] | None:
        try:
            d = self._post("/v2/video/query/", {"filters": {"video_ids": [video_id]}},
                           params={"fields": "id,view_count,like_count,comment_count,share_count"})
            vids = d.get("videos") or []
            if not vids:
                return None
            v = vids[0]
            return {"views": v.get("view_count", 0), "likes": v.get("like_count", 0),
                    "comments": v.get("comment_count", 0), "shares": v.get("share_count", 0)}
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            log.info("TikTok metrika olinmadi: %s", exc)
            return None
