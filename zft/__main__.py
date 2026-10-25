"""Buyruqlar:
  python -m zft run          — botni va rejalashtirgichni ishga tushirish (server shu bilan ishlaydi)
  python -m zft check        — sozlamalar va ulanishlarni tekshirish
  python -m zft video-test   — namuna video yasab data/media/test.mp4 ga saqlash
  python -m zft import FILE  — savollarni CSV/JSON dan data/questions.json ga import qilish
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import ROOT, Settings


def setup_logging() -> None:
    (ROOT / "data").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(ROOT / "data" / "zft.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def cmd_check(s: Settings) -> int:
    from .app import App
    from .telegram_api import TelegramError
    ok = True

    def line(good: bool, text: str) -> None:
        nonlocal ok
        ok &= good
        print(("✅ " if good else "❌ ") + text)

    line(bool(s.bot_token), "BOT_TOKEN")
    line(bool(s.owner_id), "OWNER_ID (sizning Telegram ID'ingiz)")
    app = App(s)
    line(len(app.bank) > 0, f"Savollar: {len(app.bank)} ta ({s.questions_path.name})")
    if len(app.bank) and all(q.id.startswith("namuna") for q in app.bank.questions):
        print("   ⚠️  Hozir NAMUNA savollar. Saytdagi rasmiy savollarni import qiling: python -m zft import fayl.csv")
    try:
        me = app.tg.call("getMe")
        line(me["username"].lower() == s.bot_username.lower(), f"Bot: @{me['username']} (BOT_USERNAME={s.bot_username})")
    except TelegramError as exc:
        line(False, f"Telegram: {exc}")
    for name, chat in (("Kanal", s.channel_id), ("Guruh", s.group_id)):
        if not chat:
            print(f"➖ {name} ulanmagan")
            continue
        try:
            app.tg.call("getChat", chat_id=chat)
            line(True, f"{name}: {chat} (botni admin qilganingizga ishonch hosil qiling)")
        except TelegramError as exc:
            line(False, f"{name} {chat}: {exc}")
    print(("✅ " if s.ai_enabled else "➖ ") + f"Claude API ({s.ai_model}, limit {s.ai_monthly_budget_usd}$/oy)"
          + ("" if s.ai_enabled else " — kalitsiz shablon rejim"))
    print(("✅ " if s.studio_enabled else "➖ ") + f"AI Studio (fal.ai): {s.video_model}, slotlar {', '.join(s.slots)}"
          + ("" if s.studio_enabled else " — FAL_KEY yo'q, faqat bepul test-videolar"))
    print(("✅ " if len(os.environ.get('PANEL_PASSWORD', '')) >= 10 else "➖ ") + "Boshqaruv paneli (PANEL_PASSWORD)")
    print(("✅ " if s.instagram_enabled else "➖ ") + "Instagram")
    print(("✅ " if s.tiktok_enabled else "➖ ") + f"TikTok (privacy={s.tiktok_privacy})")
    import shutil
    line(bool(shutil.which("ffmpeg")), "ffmpeg")
    print(("✅ " if shutil.which("edge-tts") else "➖ ") + "edge-tts (o'zbekcha ovoz)")
    return 0 if ok else 1


def cmd_video_test(s: Settings) -> int:
    from .quiz import QuestionBank
    from .video import VideoMaker, VideoPlan
    bank = QuestionBank(s.questions_path)
    q = bank.pick(video_friendly=True)
    vm = VideoMaker(ROOT, bot_username=s.bot_username or "ChorrahaBot", site=s.site_url, tts_voice=s.tts_voice)
    out = s.media_dir / "test.mp4"
    r = vm.make(q, VideoPlan(hook="Shu savolga 5 soniyada javob bera olasizmi?",
                             cta="Imtihondagi barcha savollar bepul botda"), out)
    print(f"✅ {r.path} — {r.duration:.1f} s, ovoz: {'bor' if r.has_voice else 'yo`q'}")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "run"
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    setup_logging()
    s = Settings.load()
    if cmd == "check":
        return cmd_check(s)
    if cmd == "video-test":
        return cmd_video_test(s)
    if cmd == "import":
        from .importer import import_questions
        if len(argv) < 3:
            print("Foydalanish: python -m zft import savollar.csv")
            return 2
        n = import_questions(Path(argv[2]), s.questions_path)
        print(f"✅ {n} ta savol import qilindi → {s.questions_path}")
        return 0
    if cmd == "run":
        if not s.bot_token:  # birinchi marta: brauzerda sozlash oynasi
            from .setup_web import run_setup
            if not run_setup(ROOT / ".env", port=int(os.environ.get("PANEL_PORT", "8090"))):
                return 1
            s = Settings.load()
        if not s.bot_username:  # BOT_USERNAME yozilmagan bo'lsa, Telegram'dan o'zi oladi
            from .telegram_api import TelegramAPI, TelegramError
            try:
                s.bot_username = TelegramAPI(s.bot_token).call("getMe")["username"]
            except (TelegramError, KeyError) as exc:
                print(f"❌ BOT_TOKEN ishlamadi: {exc}")
                return 2
        from .app import App
        App(s).run()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
