from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PublishResult:
    platform: str
    ok: bool
    remote_id: str | None = None
    error: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class PostPayload:
    post_id: int
    video_path: Path
    video_url: str | None      # ochiq URL (Instagram uchun shart)
    caption: str
    hashtags: list[str]

    def full_caption(self, limit: int = 2200) -> str:
        tags = " ".join(f"#{t}" for t in self.hashtags)
        return f"{self.caption}\n\n{tags}".strip()[:limit]
