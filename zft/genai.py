"""AI video va rasm generatsiyasi — fal.ai navbat (queue) API orqali.

Bitta kalit (FAL_KEY) bilan ko'p model: Veo, Kling, Wan, LTX, Seedance va rasm uchun FLUX.
Model va narx .env yoki paneldan o'zgartiriladi — kod o'zgarmaydi.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

QUEUE = "https://queue.fal.run"
MAX_DOWNLOAD = 300 * 1024 * 1024


class GenError(RuntimeError):
    pass


@dataclass
class GenResult:
    url: str
    seconds: float
    cost_usd: float
    model: str


def find_media_url(obj: Any, exts: tuple[str, ...]) -> str | None:
    """Model javobidan video/rasm URL ni topadi (har xil model har xil sxema qaytaradi)."""
    if isinstance(obj, dict):
        for key in ("video", "image"):
            v = obj.get(key)
            if isinstance(v, dict) and isinstance(v.get("url"), str):
                return v["url"]
        for key in ("videos", "images"):
            v = obj.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict) and isinstance(v[0].get("url"), str):
                return v[0]["url"]
        for v in obj.values():
            found = find_media_url(v, exts)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_media_url(v, exts)
            if found:
                return found
    elif isinstance(obj, str) and obj.startswith("http") and obj.split("?")[0].lower().endswith(exts):
        return obj
    return None


def video_input(model: str, prompt: str, seconds: int, resolution: str, extra: dict | None = None) -> dict:
    """Model oilasiga mos kirish parametrlari."""
    m = model.lower()
    body: dict[str, Any] = {"prompt": prompt, "aspect_ratio": "9:16"}
    if "veo" in m:
        allowed = [4, 6, 8]
        s = min(allowed, key=lambda a: abs(a - seconds))
        body.update(duration=f"{s}s", resolution=resolution, generate_audio=False)
    elif "kling" in m:
        body.update(duration="5" if seconds <= 7 else "10")
    else:  # wan, ltx, seedance va boshqalar
        body.update(duration=int(seconds), resolution=resolution)
    if extra:
        body.update(extra)
    return body


def billed_seconds(model: str, seconds: int) -> int:
    m = model.lower()
    if "veo" in m:
        return min([4, 6, 8], key=lambda a: abs(a - seconds))
    if "kling" in m:
        return 5 if seconds <= 7 else 10
    return int(seconds)


class FalClient:
    def __init__(self, key: str, session: requests.Session | None = None, queue_url: str = QUEUE,
                 poll_interval: float = 5.0, max_wait: float = 900.0):
        self.key = key
        self.http = session or requests.Session()
        self.queue = queue_url.rstrip("/")
        self.poll_interval = poll_interval
        self.max_wait = max_wait

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _h(self) -> dict:
        return {"Authorization": f"Key {self.key}", "Content-Type": "application/json"}

    def run(self, model: str, body: dict) -> dict:
        r = self.http.post(f"{self.queue}/{model}", json=body, headers=self._h(), timeout=60)
        try:
            sub = r.json()
        except ValueError as exc:
            raise GenError(f"{model}: javob JSON emas ({r.status_code})") from exc
        if r.status_code >= 400:
            raise GenError(f"{model}: {r.status_code} {str(sub)[:300]}")
        status_url = sub.get("status_url") or f"{self.queue}/{model}/requests/{sub['request_id']}/status"
        resp_url = sub.get("response_url") or f"{self.queue}/{model}/requests/{sub['request_id']}"
        waited = 0.0
        while waited <= self.max_wait:
            st = self.http.get(status_url, headers=self._h(), timeout=30).json()
            s = st.get("status")
            if s == "COMPLETED":
                if st.get("error"):
                    raise GenError(f"{model}: {st['error']}")
                break
            if s in ("FAILED", "ERROR", "CANCELLED"):
                raise GenError(f"{model}: {st.get('error') or s}")
            time.sleep(self.poll_interval)
            waited += self.poll_interval
        else:
            raise GenError(f"{model}: {int(self.max_wait)} s ichida tayyor bo'lmadi")
        out = self.http.get(resp_url, headers=self._h(), timeout=60)
        data = out.json()
        if out.status_code >= 400:
            raise GenError(f"{model}: natija xatosi {str(data)[:300]}")
        return data

    def video(self, model: str, prompt: str, seconds: int, price_per_sec: float, resolution: str = "720p",
              extra: dict | None = None) -> GenResult:
        data = self.run(model, video_input(model, prompt, seconds, resolution, extra))
        url = find_media_url(data, (".mp4", ".mov", ".webm"))
        if not url:
            raise GenError(f"{model}: javobda video topilmadi: {json.dumps(data)[:300]}")
        secs = billed_seconds(model, seconds)
        return GenResult(url, secs, round(secs * price_per_sec, 4), model)

    def image(self, model: str, prompt: str, price: float) -> GenResult:
        data = self.run(model, {"prompt": prompt, "image_size": "portrait_16_9", "num_images": 1,
                                "enable_safety_checker": True})
        url = find_media_url(data, (".jpg", ".jpeg", ".png", ".webp"))
        if not url:
            raise GenError(f"{model}: javobda rasm topilmadi")
        return GenResult(url, 0, price, model)

    def download(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.http.get(url, stream=True, timeout=120) as r:
            if r.status_code >= 400:
                raise GenError(f"Yuklab bo'lmadi {r.status_code}: {url[:80]}")
            total = 0
            with dest.open("wb") as f:
                for chunk in r.iter_content(1024 * 256):
                    total += len(chunk)
                    if total > MAX_DOWNLOAD:
                        raise GenError("Fayl juda katta")
                    f.write(chunk)
        return dest
