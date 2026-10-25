from __future__ import annotations

from ..telegram_api import TelegramAPI, TelegramError, kb
from .base import PostPayload, PublishResult


class TelegramChannelPublisher:
    platform = "telegram"

    def __init__(self, tg: TelegramAPI, channel_id: str, bot_username: str):
        self.tg = tg
        self.channel_id = channel_id
        self.bot_username = bot_username

    @property
    def enabled(self) -> bool:
        return bool(self.channel_id)

    def publish(self, p: PostPayload) -> PublishResult:
        caption = p.full_caption(limit=1024)
        markup = kb([[("🧪 Testni botda yechish", f"url:https://t.me/{self.bot_username}?start=tgkanal")]]) \
            if self.bot_username else None
        try:
            msg = self.tg.send_video(self.channel_id, p.video_path, caption, reply_markup=markup, parse_mode=None)
            return PublishResult(self.platform, True, str(msg.get("message_id")))
        except (TelegramError, OSError) as exc:
            return PublishResult(self.platform, False, error=str(exc))
