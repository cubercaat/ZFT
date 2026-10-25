"""Sozlamalar: .env faylidan o'qiladi. Kalitlar hech qachon kodga yozilmaydi."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Oddiy .env o'quvchi (python-dotenv kerak emas). Mavjud env ustun turadi."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value[:1] in ('"', "'") and value.count(value[0]) >= 2:
            value = value[1:value.index(value[0], 1)]
        else:
            # izohni olib tashlash: "QIYMAT   # izoh" → "QIYMAT"
            hash_pos = value.find(" #")
            if value.startswith("#"):
                value = ""
            elif hash_pos != -1:
                value = value[:hash_pos]
            value = value.strip()
        os.environ.setdefault(key, value)


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "ha"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    # --- Telegram ---
    bot_token: str = ""
    bot_username: str = ""            # @ belgisisiz, masalan ChorrahaBot
    owner_id: int = 0                 # faqat shu odam admin buyruqlarini ishlata oladi
    channel_id: str = ""              # @kanal yoki -100... ID
    group_id: str = ""                # PRAVA QUIZ guruhi ID (-100...)
    site_url: str = "https://chorrahaa.uz"

    # --- Claude API ---
    anthropic_api_key: str = ""
    ai_model: str = "claude-haiku-5-5"
    ai_monthly_budget_usd: float = 8.0   # 40$ ning bir qismi; oshsa AI o'chadi, tizim ishlayveradi
    ai_web_search: bool = True           # haftada 1 marta trend qidirish
    ai_web_search_type: str = "web_search_20260209"

    # --- AI Studio (video va rasm generatsiyasi, fal.ai) ---
    fal_key: str = ""
    video_model: str = "fal-ai/veo3.1/fast"
    video_resolution: str = "720p"
    image_model: str = "fal-ai/flux/schnell"
    director_model: str = "claude-sonnet-5-5"
    slots: tuple[str, ...] = ("12:30", "20:00")   # kuniga 2 ta e'lon vaqti
    gen_lead_minutes: int = 150                     # e'londan qancha oldin video tayyorlanadi

    # --- Instagram (Graph API) ---
    ig_user_id: str = ""
    ig_access_token: str = ""
    graph_version: str = "v23.0"

    # --- TikTok (Content Posting API) ---
    tiktok_access_token: str = ""
    tiktok_privacy: str = "SELF_ONLY"    # ilova audit o'tguncha faqat SELF_ONLY ishlaydi

    # --- Media (Instagram va TikTok videoni URL orqali oladi) ---
    public_media_url: str = ""           # masalan https://media.chorrahaa.uz
    media_dir: Path = field(default_factory=lambda: ROOT / "data" / "media")

    # --- Ish tartibi ---
    timezone_offset_hours: int = 5       # Toshkent UTC+5
    content_hour: int = 9                # video tayyorlab egaga yuborish
    publish_hour: int = 19               # tasdiqlangan videoni e'lon qilish (kechqurun ko'proq ko'rishadi)
    report_hour: int = 22                # kunlik hisobot
    group_quiz_hours: tuple[int, ...] = (8, 13, 20)
    auto_publish: bool = False           # dastlabki 2-4 hafta False: har video tasdiqdan o'tadi
    tts_voice: str = "uz-UZ-MadinaNeural"

    db_path: Path = field(default_factory=lambda: ROOT / "data" / "zft.db")
    questions_path: Path = field(default_factory=lambda: ROOT / "data" / "questions.json")

    @classmethod
    def load(cls, env_file: Path | None = None) -> "Settings":
        _load_env_file(env_file or ROOT / ".env")
        e = os.environ.get
        hours = tuple(int(h) for h in e("GROUP_QUIZ_HOURS", "8,13,20").split(",") if h.strip())
        s = cls(
            bot_token=e("BOT_TOKEN", ""),
            bot_username=e("BOT_USERNAME", "").lstrip("@"),
            owner_id=_int("OWNER_ID", 0),
            channel_id=e("CHANNEL_ID", ""),
            group_id=e("GROUP_ID", ""),
            site_url=e("SITE_URL", "https://chorrahaa.uz"),
            anthropic_api_key=e("ANTHROPIC_API_KEY", ""),
            ai_model=e("AI_MODEL", "claude-haiku-5-5"),
            ai_monthly_budget_usd=_float("AI_MONTHLY_BUDGET_USD", 8.0),
            ai_web_search=_bool("AI_WEB_SEARCH", True),
            ai_web_search_type=e("AI_WEB_SEARCH_TYPE", "web_search_20260209"),
            fal_key=e("FAL_KEY", ""),
            video_model=e("VIDEO_MODEL", "fal-ai/veo3.1/fast"),
            video_resolution=e("VIDEO_RESOLUTION", "720p"),
            image_model=e("IMAGE_MODEL", "fal-ai/flux/schnell"),
            director_model=e("DIRECTOR_MODEL", "claude-sonnet-5-5"),
            slots=tuple(x.strip() for x in e("SLOTS", "12:30,20:00").split(",") if x.strip()),
            gen_lead_minutes=_int("GEN_LEAD_MINUTES", 150),
            ig_user_id=e("IG_USER_ID", ""),
            ig_access_token=e("IG_ACCESS_TOKEN", ""),
            graph_version=e("GRAPH_VERSION", "v23.0"),
            tiktok_access_token=e("TIKTOK_ACCESS_TOKEN", ""),
            tiktok_privacy=e("TIKTOK_PRIVACY", "SELF_ONLY"),
            public_media_url=e("PUBLIC_MEDIA_URL", "").rstrip("/"),
            timezone_offset_hours=_int("TZ_OFFSET_HOURS", 5),
            content_hour=_int("CONTENT_HOUR", 9),
            publish_hour=_int("PUBLISH_HOUR", 19),
            report_hour=_int("REPORT_HOUR", 22),
            group_quiz_hours=hours,
            auto_publish=_bool("AUTO_PUBLISH", False),
            tts_voice=e("TTS_VOICE", "uz-UZ-MadinaNeural"),
        )
        if e("DB_PATH"):
            s.db_path = Path(e("DB_PATH"))
        if e("QUESTIONS_PATH"):
            s.questions_path = Path(e("QUESTIONS_PATH"))
        if e("MEDIA_DIR"):
            s.media_dir = Path(e("MEDIA_DIR"))
        s.media_dir.mkdir(parents=True, exist_ok=True)
        s.db_path.parent.mkdir(parents=True, exist_ok=True)
        return s

    # Qaysi modul yoqilgan
    @property
    def instagram_enabled(self) -> bool:
        return bool(self.ig_user_id and self.ig_access_token and self.public_media_url)

    @property
    def tiktok_enabled(self) -> bool:
        return bool(self.tiktok_access_token and self.public_media_url)

    @property
    def studio_enabled(self) -> bool:
        return bool(self.fal_key)

    def slot_minutes(self) -> list[int]:
        out = []
        for t in self.slots:
            h, _, m = t.partition(":")
            try:
                out.append(int(h) * 60 + int(m or 0))
            except ValueError:
                continue
        return sorted(out) or [12 * 60 + 30, 20 * 60]

    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_api_key)
