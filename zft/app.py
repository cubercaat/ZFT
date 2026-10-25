"""Hamma qismlarni bog'laydigan ilova va oddiy, qayta ishga tushishga chidamli rejalashtirgich."""
from __future__ import annotations

import datetime as dt
import logging
import os
import threading
from pathlib import Path
from typing import Callable

from .agents import Hub
from .learning import Learning
from .partners import Partners
from .smm import SmmAuto
from .control import Control
from .bot import Bot
from .brain import Brain
from .budget import VideoBudget
from .director import Director
from .genai import FalClient
from .config import ROOT, Settings
from .db import DB, now as now_ts
from .pipeline import Pipeline
from .publishers.instagram import InstagramPublisher
from .publishers.telegram_channel import TelegramChannelPublisher
from .publishers.tiktok import TikTokPublisher
from .quiz import QuestionBank
from .reports import Reports
from .studio import Studio
from .telegram_api import TelegramAPI, TelegramError
from .video import VideoMaker

log = logging.getLogger(__name__)

DEFAULT_LINKS = {
    "tiktok": "TikTok profil", "insta": "Instagram profil", "tgkanal": "Telegram kanal",
    "guruh": "PRAVA QUIZ guruhi", "sayt": "chorrahaa.uz sayt",
}


class App:
    def __init__(self, settings: Settings, tg: TelegramAPI | None = None):
        self.settings = s = settings
        self.db = DB(s.db_path)
        self.tg = tg or TelegramAPI(s.bot_token)
        self.bank = QuestionBank(s.questions_path)
        self.brain = Brain(self.db, s.anthropic_api_key, s.ai_model, s.ai_monthly_budget_usd,
                           s.ai_web_search, s.ai_web_search_type, s.timezone_offset_hours)
        self.video = VideoMaker(ROOT, bot_username=s.bot_username, site=s.site_url, tts_voice=s.tts_voice)
        self.studio = Studio(ROOT, s.bot_username, s.site_url, s.tts_voice)
        self.fal = FalClient(s.fal_key)
        self.director = Director(self.brain, s.director_model)
        self._ig_override = self._tt_override = None
        self.channel = TelegramChannelPublisher(self.tg, s.channel_id, s.bot_username)
        self.ref_goal = int(os.environ.get("REF_GOAL", "3"))
        self.ref_reward_text = os.environ.get(
            "REF_REWARD_TEXT", "3 ta do'st taklif qilganlar haftalik reytingda alohida e'lon qilinadi")
        self.pipeline = Pipeline(self)
        self.reports = Reports(self)
        self.bot = Bot(self)
        self.tz = dt.timezone(dt.timedelta(hours=s.timezone_offset_hours))
        self.vbudget = VideoBudget(self.db, self.tz)
        self.hub = Hub(self)
        self.learning = Learning(self)
        self.partners = Partners(self)
        self.smm = SmmAuto(self)
        self.control = Control(self)
        self.panel_modules = [self.partners, self.smm, self.control]
        from .connections import Connections
        self.conn = Connections(self)
        for code, label in DEFAULT_LINKS.items():
            self.db.execute("INSERT OR IGNORE INTO links(code, label, created_at) VALUES (?,?,strftime('%s','now'))",
                            (code, label))
        self._jobs_lock = threading.Lock()

    # ---------------------------------------------------------- helpers
    @property
    def auto_publish(self) -> bool:
        v = self.db.get("auto_publish")
        return self.settings.auto_publish if v is None else v == "1"

    def local_now(self) -> dt.datetime:
        return dt.datetime.now(self.tz)

    def local_day_start(self) -> int:
        n = self.local_now()
        return int(dt.datetime(n.year, n.month, n.day, tzinfo=self.tz).timestamp())

    def public_url(self, video_path: str | Path) -> str | None:
        base = self.settings.public_media_url
        return f"{base}/{Path(video_path).name}" if base else None

    # Sinov rejimi: hech narsa ommaga chiqmaydi (e'lon, izoh, guruh, ommaviy xabarlar)
    @property
    def dry_run(self) -> bool:
        return self.control.dry_run

    @dry_run.setter
    def dry_run(self, on: bool) -> None:
        self.control.set_dry_run(on)

    # Instagram/TikTok: paneldagi «Ulash» (OAuth) tokeni yoki .env dagi qiymat
    @property
    def instagram(self):
        if self._ig_override is not None:
            return self._ig_override
        creds = self.conn.instagram_creds()
        if not creds or not self.settings.public_media_url:
            return None
        uid, token, host = creds
        return InstagramPublisher(uid, token, self.settings.graph_version, host=host)

    @instagram.setter
    def instagram(self, value):
        self._ig_override = value

    @property
    def tiktok(self):
        if self._tt_override is not None:
            return self._tt_override
        tok = self.conn.tiktok_token()
        return TikTokPublisher(tok, self.settings.tiktok_privacy) if tok else None

    @tiktok.setter
    def tiktok(self, value):
        self._tt_override = value

    def publishers(self) -> list:
        self.channel.channel_id = self.conn.channel_id()
        return [p for p in (self.channel, self.instagram, self.tiktok) if p and p.enabled]

    def group_members(self) -> int | None:
        gid = self.conn.group_id()
        if not gid:
            return None
        try:
            return self.tg.member_count(gid)
        except TelegramError:
            return None

    def run_in_background(self, fn: Callable, *args) -> threading.Thread:
        def runner():
            with self._jobs_lock:  # og'ir ishlar (video) bir vaqtda bittadan
                try:
                    fn(*args)
                except Exception:
                    log.exception("Fon vazifasi xato: %s", getattr(fn, "__name__", fn))
        t = threading.Thread(target=runner, daemon=True)
        t.start()
        return t

    # -------------------------------------------------------- scheduler
    def due_jobs(self, at: dt.datetime) -> list[tuple[str, Callable]]:
        """Shu daqiqada bajarilishi kerak bo'lgan vazifalar (har biri kuniga 1 marta)."""
        s = self.settings
        h = self.hub
        jobs: list[tuple[str, int, Callable]] = [  # (nom, kun boshidan daqiqa, funksiya)
            ("tokens", 4 * 60, self.conn.refresh_all),
            ("metrics_am", 7 * 60, h.media_collect),
            ("metrics_pm", max(0, s.report_hour - 1) * 60, h.media_collect),
            ("review", max(0, s.report_hour * 60 - 30), h.market_review),
            ("report", s.report_hour * 60, h.daily_report),
        ]
        for i, m in enumerate(s.slot_minutes()):
            jobs.append((f"gen_slot{i}", max(0, m - s.gen_lead_minutes), lambda i=i: h.prep_slot(i)))
            jobs.append((f"pub_slot{i}", m, lambda i=i: h.publish_slot(i)))
        for hr in s.group_quiz_hours:
            jobs.append((f"group_quiz_{hr}", hr * 60, h.group_quiz))
        L = self.learning
        jobs.append(("brief", 8 * 60, self.control.morning_brief))
        for hr in range(9, 23):
            jobs.append((f"alerts_{hr}", hr * 60 + 15, self.control.check_alerts))
        for hr in range(8, 22):  # tinch soatlar 22:00–08:00 — eslatma yuborilmaydi
            jobs.append((f"remind_{hr}", hr * 60, lambda hr=hr: L.daily_reminders(hr)))
        jobs.append(("exam_followup", 10 * 60, L.exam_followups))
        jobs.append(("winback", 18 * 60, L.winback))
        if self.instagram and self.instagram.enabled:  # izohlar har 20 daqiqada (08:00–23:40)
            for hr in range(8, 24):
                for mm in (0, 20, 40):
                    jobs.append((f"ig_comments_{hr}_{mm}", hr * 60 + mm, self.smm.check_comments))
        if at.day == 1:
            jobs.append(("ref_contest", 9 * 60, self.partners.close_contest))
        if at.weekday() == 6:
            jobs.append(("tournament", 20 * 60 + 30, L.weekly_tournament))
        for r in self.db.all("SELECT key, daily_hour FROM custom_agents WHERE active=1 AND daily_hour IS NOT NULL"):
            jobs.append((f"custom_{r['key']}", int(r["daily_hour"]) * 60, lambda k=r["key"]: h.run_custom(k)))
        if at.weekday() == 0:  # dushanba: Marketolog bozorni o'rganadi, keyin haftalik hisobot
            jobs.append(("weekly", 8 * 60, h.weekly))
        elif not self.db.get("market_strategy"):  # birinchi ishga tushganda strategiya bo'lmasa
            jobs.append(("market_first", 8 * 60, h.market_research))
        day = at.strftime("%Y-%m-%d")
        cur = at.hour * 60 + at.minute
        due = []
        for name, minute, fn in jobs:
            if cur >= minute and self.db.get(f"job:{name}") != day:
                # kechikkan ishni faqat 3 soat ichida bajaramiz (server o'chib qolgan bo'lsa)
                window = 25 if name.startswith("ig_comments") else 60 if name.startswith("remind_") else 180
                if cur - minute <= window:
                    due.append((name, fn))
                else:
                    self.db.set(f"job:{name}", day)
        return due

    def scheduler_loop(self, stop: threading.Event, tick: float = 30.0) -> None:
        log.info("Rejalashtirgich ishga tushdi (Toshkent vaqti %s)", self.local_now().strftime("%H:%M"))
        while not stop.is_set():
            at = self.local_now()
            for name, fn in self.due_jobs(at):
                self.db.set(f"job:{name}", at.strftime("%Y-%m-%d"))
                log.info("Vazifa: %s", name)
                self.db.audit("scheduler", "job", name)
                self.run_in_background(fn)
            stop.wait(tick)

    def start_panel(self):
        pw = os.environ.get("PANEL_PASSWORD", "")
        if len(pw) < 10:
            log.warning("Panel o'chiq: .env da PANEL_PASSWORD kamida 10 belgi bo'lsin")
            return None
        from .panel import Panel
        panel = Panel(self, pw, host="127.0.0.1", port=int(os.environ.get("PANEL_PORT", "8090")),
                      secure_cookie=os.environ.get("PANEL_INSECURE_COOKIE", "") != "1")
        return panel.serve()

    def first_start(self) -> None:
        """Birinchi ishga tushish: egaga xush kelibsiz xabari va darhol birinchi video."""
        if not self.settings.owner_id or self.db.get("first_video_done"):
            return
        self.db.set("first_video_done", now_ts())
        s = self.settings
        try:
            self.tg.send_message(s.owner_id, (
                "🚦 <b>ZFT ishga tushdi!</b>\n\n"
                "Hozir birinchi videoni tayyorlayapman (1–5 daqiqa), tayyor bo'lgach shu yerga yuboraman.\n"
                f"Har kuni videolar {', '.join(s.slots)} da chiqadi, ulardan 2,5 soat oldin tayyorlanadi.\n\n"
                "Keyingi qadamlar:\n"
                "• Botni kanalingiz va PRAVA QUIZ guruhiga admin qiling — o'zi aniqlaydi\n"
                "• Instagram/TikTok: panelning «Ulanishlar» sahifasi\n"
                "• Buyruqlar: /admin"))
        except TelegramError:
            pass
        self.run_in_background(self.hub.make_now, "first_run")

    def run(self) -> None:
        stop = threading.Event()
        owner = self.db.get("owner_id")
        if owner and not self.settings.owner_id:
            self.settings.owner_id = int(owner)
        self.start_panel()
        self.first_start()
        threading.Thread(target=self.scheduler_loop, args=(stop,), daemon=True).start()
        try:
            self.bot.poll_forever(stop)
        except KeyboardInterrupt:
            stop.set()
