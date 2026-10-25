"""Telegram Bot API uchun yengil mijoz (faqat requests)."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    pass


class TelegramAPI:
    def __init__(self, token: str, base_url: str = "https://api.telegram.org", timeout: int = 60):
        self.token = token
        self.base = f"{base_url.rstrip('/')}/bot{token}"
        self.timeout = timeout
        self.session = requests.Session()

    def call(self, method: str, files: dict | None = None, **params: Any) -> Any:
        data = {}
        for k, v in params.items():
            if v is None:
                continue
            data[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
        for attempt in range(3):
            try:
                resp = self.session.post(f"{self.base}/{method}", data=data, files=files,
                                         timeout=self.timeout + 10)
                payload = resp.json()
            except (requests.RequestException, ValueError) as exc:
                log.warning("Telegram %s xato (%s), qayta urinish %d", method, exc, attempt + 1)
                time.sleep(2 * (attempt + 1))
                continue
            if payload.get("ok"):
                return payload["result"]
            retry = (payload.get("parameters") or {}).get("retry_after")
            if retry:  # flood control
                time.sleep(min(int(retry), 30))
                continue
            raise TelegramError(f"{method}: {payload.get('description')}")
        raise TelegramError(f"{method}: tarmoq xatosi")

    # --- qulay usullar ---
    def send_message(self, chat_id: Any, text: str, reply_markup: dict | None = None,
                     parse_mode: str | None = "HTML", disable_web_page_preview: bool = True) -> dict:
        return self.call("sendMessage", chat_id=chat_id, text=text, reply_markup=reply_markup,
                         parse_mode=parse_mode, disable_web_page_preview=disable_web_page_preview)

    def send_video(self, chat_id: Any, video_path: Path, caption: str = "",
                   reply_markup: dict | None = None, parse_mode: str | None = "HTML") -> dict:
        with open(video_path, "rb") as f:
            return self.call("sendVideo", files={"video": f}, chat_id=chat_id, caption=caption[:1024],
                             reply_markup=reply_markup, parse_mode=parse_mode, supports_streaming=True)

    def send_quiz_poll(self, chat_id: Any, question: str, options: list[str], correct_index: int,
                       explanation: str = "") -> dict:
        return self.call(
            "sendPoll", chat_id=chat_id, question=question[:300],
            options=[{"text": o[:100]} for o in options],
            type="quiz", correct_option_id=correct_index, is_anonymous=False,
            explanation=explanation[:200] or None,
        )

    def answer_callback(self, callback_id: str, text: str = "", show_alert: bool = False) -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_id, text=text, show_alert=show_alert)

    def edit_markup(self, chat_id: Any, message_id: int, reply_markup: dict | None = None) -> None:
        try:
            self.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                      reply_markup=reply_markup or {"inline_keyboard": []})
        except TelegramError as exc:
            log.debug("edit_markup: %s", exc)

    def get_updates(self, offset: int | None, timeout: int = 50) -> list[dict]:
        return self.call("getUpdates", offset=offset, timeout=timeout,
                         allowed_updates=["message", "callback_query", "poll_answer", "my_chat_member"])

    def member_count(self, chat_id: Any) -> int:
        return int(self.call("getChatMemberCount", chat_id=chat_id))


def kb(rows: list[list[tuple[str, str]]]) -> dict:
    """[[('Matn', 'callback_data' yoki 'url:https://...')]] → inline klaviatura."""
    out = []
    for row in rows:
        r = []
        for text, action in row:
            if action.startswith("url:"):
                r.append({"text": text, "url": action[4:]})
            else:
                r.append({"text": text, "callback_data": action[:64]})
        out.append(r)
    return {"inline_keyboard": out}
