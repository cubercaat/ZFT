"""Vertikal (9:16) test-video generatori: hook → savol + taymer → javob → chaqiriq.

Faqat Pillow + ffmpeg ishlatadi (bepul). O'zbekcha ovoz: edge-tts (bepul, ixtiyoriy).
Ovoz topilmasa, assets/music.mp3 fon musiqasi yoki jim audio qo'yiladi.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .quiz import Question

log = logging.getLogger(__name__)

W, H = 1080, 1920
BG_TOP = (7, 10, 18)
BG_BOTTOM = (20, 28, 52)
ACCENT = (255, 195, 0)      # yo'l belgisi sariq
WHITE = (245, 247, 250)
MUTED = (150, 160, 180)
GREEN = (34, 197, 94)
RED = (239, 68, 68)
CARD = (30, 40, 66)
LETTERS = "ABCDEFGHIJ"

FONT_CANDIDATES_BOLD = [
    "assets/fonts/Poppins-Bold.ttf",
    "assets/fonts/Montserrat-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
]
FONT_CANDIDATES_REG = [
    "assets/fonts/Poppins-Medium.ttf",
    "assets/fonts/Montserrat-Medium.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf",
]


def _font(size: int, bold: bool = True, root: Path | None = None) -> ImageFont.FreeTypeFont:
    for cand in (FONT_CANDIDATES_BOLD if bold else FONT_CANDIDATES_REG):
        p = Path(cand)
        if not p.is_absolute() and root:
            p = root / p
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size)


def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        words, cur = para.split(), ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if draw.textlength(trial, font=font) <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def fit_text(draw, text: str, max_w: int, max_h: int, start: int, min_size: int, bold=True, root=None,
             spacing: float = 1.25):
    size = start
    while size >= min_size:
        f = _font(size, bold, root)
        lines = wrap(draw, text, f, max_w)
        if len(lines) * size * spacing <= max_h:
            return f, lines, size
        size -= 4
    f = _font(min_size, bold, root)
    return f, wrap(draw, text, f, max_w), min_size


@dataclass
class VideoPlan:
    """AI (yoki zaxira shablon) tayyorlagan video matni."""
    hook: str
    cta: str
    countdown: int = 5
    reveal_line: str = "To'g'ri javob"


@dataclass
class VideoResult:
    path: Path
    duration: float
    has_voice: bool


class VideoMaker:
    def __init__(self, root: Path, brand: str = "CHORRAHA", bot_username: str = "",
                 site: str = "chorrahaa.uz", tts_voice: str = "uz-UZ-MadinaNeural"):
        self.root = root
        self.brand = brand
        self.bot_username = bot_username
        self.site = site.replace("https://", "").replace("http://", "").rstrip("/")
        self.tts_voice = tts_voice
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg topilmadi: sudo apt install ffmpeg")

    # ------------------------------------------------------------- drawing
    def _base(self) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        img = Image.new("RGB", (W, H), BG_TOP)
        d = ImageDraw.Draw(img)
        for y in range(H):  # vertikal gradient
            t = y / H
            c = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3))
            d.line([(0, y), (W, y)], fill=c)
        # yo'l chizig'i motivi (yuqori va pastda)
        for x in range(0, W, 120):
            d.rectangle([x, 0, x + 60, 14], fill=ACCENT)
            d.rectangle([x + 60, H - 14, x + 120, H], fill=ACCENT)
        f = _font(46, True, self.root)
        d.text((W // 2, 110), self.brand, font=f, fill=ACCENT, anchor="mm")
        fs = _font(32, False, self.root)
        d.text((W // 2, H - 90), self.site, font=fs, fill=MUTED, anchor="mm")
        return img, d

    def _center_lines(self, d, lines, font, size, top, color, spacing=1.25):
        y = top
        for ln in lines:
            d.text((W // 2, y), ln, font=font, fill=color, anchor="ma")
            y += int(size * spacing)
        return y

    def frame_hook(self, hook: str, seconds: int = 5) -> Image.Image:
        img, d = self._base()
        f, lines, size = fit_text(d, hook, W - 160, 700, 104, 56, root=self.root)
        total = len(lines) * size * 1.25
        y = self._center_lines(d, lines, f, size, int(H / 2 - total / 2 - 60), WHITE)
        d.rounded_rectangle([W // 2 - 220, y + 40, W // 2 + 220, y + 130], radius=45, fill=ACCENT)
        d.text((W // 2, y + 85), f"{seconds} soniya beramiz", font=_font(40, True, self.root), fill=BG_TOP, anchor="mm")
        return img

    def _question_block(self, d, img, q: Question, highlight: int | None) -> int:
        f, lines, size = fit_text(d, q.question, W - 140, 420, 64, 40, root=self.root)
        y = self._center_lines(d, lines, f, size, 300, WHITE) + 40
        if q.image and Path(q.image).exists():
            try:
                pic = Image.open(q.image).convert("RGB")
                pic.thumbnail((W - 200, 420))
                img.paste(pic, ((W - pic.width) // 2, y))
                y += pic.height + 30
            except OSError as exc:
                log.warning("Rasm ochilmadi %s: %s", q.image, exc)
        opt_font_size = 44 if len(q.options) <= 3 else 40
        for i, opt in enumerate(q.options):
            of, olines, osize = fit_text(d, opt, W - 300, 200, opt_font_size, 30, bold=False, root=self.root)
            h = int(len(olines) * osize * 1.25) + 50
            if highlight is None:
                fill, outline, txt = CARD, None, WHITE
            elif i == highlight:
                fill, outline, txt = GREEN, None, (255, 255, 255)
            else:
                fill, outline, txt = (40, 46, 62), None, MUTED
            d.rounded_rectangle([70, y, W - 70, y + h], radius=28, fill=fill, outline=outline)
            badge_fill = ACCENT if highlight is None else ((255, 255, 255) if i == highlight else (70, 78, 98))
            d.ellipse([100, y + h // 2 - 36, 172, y + h // 2 + 36], fill=badge_fill)
            d.text((136, y + h // 2), LETTERS[i], font=_font(40, True, self.root),
                   fill=BG_TOP if highlight is None or i == highlight else MUTED, anchor="mm")
            ty = y + 25
            for ln in olines:
                d.text((200, ty), ln, font=of, fill=txt)
                ty += int(osize * 1.25)
            y += h + 22
        return y

    def frame_question(self, q: Question, remaining: int | None, total: int) -> Image.Image:
        img, d = self._base()
        y = self._question_block(d, img, q, None)
        if remaining is not None:
            bar_y = max(y + 40, H - 330)
            d.rounded_rectangle([120, bar_y, W - 120, bar_y + 26], radius=13, fill=(50, 58, 82))
            frac = remaining / total
            d.rounded_rectangle([120, bar_y, 120 + int((W - 240) * frac), bar_y + 26], radius=13,
                                fill=ACCENT if remaining > 2 else RED)
            d.text((W // 2, bar_y + 110), str(remaining), font=_font(110, True, self.root),
                   fill=ACCENT if remaining > 2 else RED, anchor="mm")
        return img

    def frame_reveal(self, q: Question, line: str) -> Image.Image:
        img, d = self._base()
        y = self._question_block(d, img, q, q.correct)
        if q.explanation:
            f, lines, size = fit_text(d, q.explanation, W - 180, max(160, H - 260 - y - 40), 40, 28,
                                      bold=False, root=self.root)
            self._center_lines(d, lines, f, size, y + 30, ACCENT)
        d.rounded_rectangle([W // 2 - 260, 190, W // 2 + 260, 250], radius=30, fill=GREEN)
        d.text((W // 2, 220), line, font=_font(34, True, self.root), fill=(255, 255, 255), anchor="mm")
        return img

    def frame_cta(self, cta: str) -> Image.Image:
        img, d = self._base()
        f, lines, size = fit_text(d, cta, W - 160, 600, 84, 48, root=self.root)
        y = self._center_lines(d, lines, f, size, 560, WHITE)
        if self.bot_username:
            d.rounded_rectangle([110, y + 60, W - 110, y + 190], radius=60, fill=ACCENT)
            d.text((W // 2, y + 125), f"@{self.bot_username}", font=_font(60, True, self.root),
                   fill=BG_TOP, anchor="mm")
            y += 190
        d.text((W // 2, y + 110), "Javobingizni izohda yozing", font=_font(40, False, self.root),
               fill=MUTED, anchor="mm")
        return img

    # --------------------------------------------------------------- audio
    def _tts(self, text: str, out: Path) -> float | None:
        exe = shutil.which("edge-tts")
        if not exe:
            return None
        try:
            subprocess.run([exe, "--voice", self.tts_voice, "--text", text, "--write-media", str(out)],
                           check=True, capture_output=True, timeout=90)
            return probe_duration(out)
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("TTS ishlamadi: %s", exc)
            return None

    # ---------------------------------------------------------------- make
    def make(self, q: Question, plan: VideoPlan, out_path: Path) -> VideoResult:
        """Asosiy format — «Chorri» bilan tez, animatsiyali video; xato bo'lsa oddiy formatga qaytadi."""
        try:
            from .hype import HypeMaker
            return HypeMaker(self.root, self.bot_username, self.site, self.tts_voice).make(q, plan, out_path)
        except Exception as exc:  # noqa: BLE001 — video baribir chiqishi kerak
            log.warning("Hype format ishlamadi, oddiy formatga o'tildi: %s", exc)
        return self.make_simple(q, plan, out_path)

    def make_simple(self, q: Question, plan: VideoPlan, out_path: Path) -> VideoResult:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            # 1) ovoz bo'laklari
            opts_spoken = ". ".join(f"{LETTERS[i]}: {o}" for i, o in enumerate(q.options))
            voice = {
                "hook": self._tts(plan.hook, tmp / "v_hook.mp3"),
                "q": self._tts(f"{q.question} {opts_spoken}", tmp / "v_q.mp3"),
                "reveal": self._tts(f"{plan.reveal_line}: {LETTERS[q.correct]}. {q.correct_text}. "
                                    f"{q.explanation}", tmp / "v_reveal.mp3"),
                "cta": self._tts(plan.cta, tmp / "v_cta.mp3"),
            }
            has_voice = all(v is not None for v in voice.values())
            if not has_voice:
                voice = {k: None for k in voice}

            # 2) sahnalar va davomiyliklar
            n = max(3, plan.countdown)
            hook_d = max(2.5, (voice["hook"] or 0) + 0.4)
            read_d = max(3.0, (voice["q"] or 0) + 0.3)   # savol o'qilguncha taymersiz
            reveal_d = max(4.0, (voice["reveal"] or 0) + 0.6)
            cta_d = max(3.5, (voice["cta"] or 0) + 0.8)
            scenes: list[tuple[Image.Image, float]] = [(self.frame_hook(plan.hook, n), hook_d),
                                                       (self.frame_question(q, None, n), read_d)]
            for r in range(n, 0, -1):
                scenes.append((self.frame_question(q, r, n), 1.0))
            scenes += [(self.frame_reveal(q, plan.reveal_line), reveal_d), (self.frame_cta(plan.cta), cta_d)]

            concat = tmp / "list.txt"
            with concat.open("w", encoding="utf-8") as fh:
                for i, (im, dur) in enumerate(scenes):
                    p = tmp / f"s{i:02d}.png"
                    im.save(p, optimize=False)
                    fh.write(f"file '{Path(p).as_posix()}'\nduration {dur:.3f}\n")
                fh.write(f"file '{(tmp / f's{len(scenes) - 1:02d}.png').as_posix()}'\n")
            total = sum(d for _, d in scenes)

            # 3) audio trek
            audio = tmp / "audio.m4a"
            if has_voice:
                segs = [("hook", hook_d), ("q", read_d + n), ("reveal", reveal_d), ("cta", cta_d)]
                inputs, filters = [], []
                for i, (key, dur) in enumerate(segs):
                    inputs += ["-i", str(tmp / f"v_{key}.mp3")]
                    filters.append(f"[{i}:a]aresample=44100,apad,atrim=0:{dur:.3f}[a{i}]")
                filters.append("".join(f"[a{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=0:a=1[out]")
                cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters), "-map", "[out]",
                       "-c:a", "aac", "-b:a", "128k", str(audio)]
            elif (self.root / "assets" / "music.mp3").exists():
                cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(self.root / "assets" / "music.mp3"),
                       "-t", f"{total:.3f}", "-af", f"volume=0.35,afade=t=out:st={max(0, total - 1.5):.2f}:d=1.5",
                       "-c:a", "aac", "-b:a", "128k", str(audio)]
            else:
                cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{total:.3f}",
                       "-c:a", "aac", "-b:a", "64k", str(audio)]
            run_ffmpeg(cmd)

            # 4) video (Instagram/TikTok talabi: H.264, yuv420p, 30fps, AAC)
            run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-i", str(audio),
                        "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "21", "-profile:v", "high", "-c:a", "copy", "-shortest",
                        "-movflags", "+faststart", str(out_path)])
        return VideoResult(out_path, probe_duration(out_path) or total, has_voice)


def run_ffmpeg(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg xato: {res.stderr[-800:]}")


def probe_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                              str(path)], capture_output=True, text=True, timeout=30)
        return float(json.loads(out.stdout)["format"]["duration"])
    except (subprocess.SubprocessError, KeyError, ValueError, OSError):
        return None
