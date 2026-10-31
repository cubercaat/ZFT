"""Tez, kulgili va aniq test-video — «Chorri» qahramoni bilan (Reels/TikTok uslubi, 9:16, 30 fps).

Sahnalar:
  1. Hook (≈2.5 s) — Chorri sakrab chiqadi (hayron), so'zlar birin-ketin "urilib" chiqadi, whoosh.
  2. Savol (≈7 s) — savol kartasi sirpanib kiradi, variantlar pop bilan chiqadi, 3-2-1 halqa taymer,
     oxirgi soniyada ekran titraydi; Chorri pastda o'ylab turadi.
  3. Javob (≈3 s) — to'g'ri variant yashil bo'lib kattalashadi, konfetti, "ding", Chorri xursand.
  4. Chaqiriq (≈2.5 s) — Chorri ko'z qisadi, bot nomi katta "pill"da.
Hammasi Pillow + ffmpeg (bepul). Ovoz: edge-tts bo'lsa hook va savol o'qiladi; SFX ffmpeg'da yasaladi.
"""
from __future__ import annotations

import logging
import math
import random
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from . import mascot
from .quiz import Question
from .video import LETTERS, VideoPlan, VideoResult, _font, probe_duration, run_ffmpeg, wrap

log = logging.getLogger(__name__)

W, H, FPS = 1080, 1920, 30
ASPHALT = (14, 20, 34)
ASPHALT2 = (24, 33, 54)
YEL = (255, 184, 28)
INK = (19, 32, 58)
WHITE = (248, 250, 252)
GREEN = (46, 204, 113)
RED = (240, 84, 84)
CYAN = (120, 240, 255)


def ease_out_back(t: float, s: float = 1.9) -> float:
    t = min(max(t, 0.0), 1.0) - 1
    return 1 + (s + 1) * t ** 3 + s * t ** 2


def ease_out(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3


def clamp01(t: float) -> float:
    return min(max(t, 0.0), 1.0)


class HypeMaker:
    def __init__(self, root: Path, bot_username: str = "", site: str = "chorrahaa.uz",
                 tts_voice: str = "uz-UZ-MadinaNeural"):
        self.root = root
        self.bot = bot_username
        self.site = site.replace("https://", "").replace("http://", "").rstrip("/")
        self.tts_voice = tts_voice
        self._bg = self._background()

    # ------------------------------------------------------------ resurslar
    def font(self, size: int, bold: bool = True):
        return _font(size, bold, self.root)

    def _background(self) -> Image.Image:
        """Tungi asfalt + markazda yo'l chizig'i (kadrma-kadr pastga suriladi)."""
        bg = Image.new("RGB", (W, H), ASPHALT)
        d = ImageDraw.Draw(bg)
        for y in range(0, H, 4):
            t = y / H
            c = tuple(int(ASPHALT[i] + (ASPHALT2[i] - ASPHALT[i]) * t) for i in range(3))
            d.rectangle([0, y, W, y + 4], fill=c)
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse([W * 0.1, H * 0.12, W * 0.9, H * 0.62], fill=(255, 184, 28, 38))
        bg = bg.convert("RGBA")
        bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(120)))
        return bg.convert("RGB")

    @lru_cache(maxsize=64)
    def chorri(self, size: int, expr: str, mouth: int, blink: int, light: int) -> Image.Image:
        return mascot.draw(size, expr, mouth=mouth / 4, blink=blink / 4, light=light)

    def text_block(self, text: str, size: int, color=WHITE, max_w: int = W - 160, stroke: int = 0,
                   align="center") -> Image.Image:
        tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        f = self.font(size)
        lines = wrap(tmp, text, f, max_w)
        lh = int(size * 1.18)
        img = Image.new("RGBA", (max_w + 40, lh * len(lines) + 30), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        for i, ln in enumerate(lines):
            x = img.width // 2 if align == "center" else 20
            d.text((x, 15 + i * lh), ln, font=f, fill=color, anchor="ma" if align == "center" else "la",
                   stroke_width=stroke, stroke_fill=INK)
        return img

    # ---------------------------------------------------------------- audio
    # hissiyot → ovoz tezligi, balandligi va kuchi (edge-tts prosody)
    MOODS = {
        "hype": ("+18%", "+14Hz", "+15%"),     # hook: hayajon, shoshib
        "calm": ("+6%", "+4Hz", "+0%"),        # savol: aniq, tushunarli
        "tense": ("+12%", "+2Hz", "+5%"),
        "happy": ("+14%", "+22Hz", "+15%"),    # to'g'ri javob: quvonch
        "cta": ("+10%", "+10Hz", "+10%"),      # chaqiriq: do'stona, ishonchli
    }

    def _tts(self, text: str, out: Path, mood: str = "calm") -> float | None:
        exe = shutil.which("edge-tts")
        if not exe or not text.strip():
            return None
        rate, pitch, vol = self.MOODS.get(mood, self.MOODS["calm"])
        raw = out.with_suffix(".raw.mp3")
        try:
            subprocess.run([exe, "--voice", self.tts_voice, f"--rate={rate}", f"--pitch={pitch}", f"--volume={vol}",
                            "--text", text, "--write-media", str(raw)], check=True, capture_output=True, timeout=90)
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("TTS ishlamadi: %s", exc)
            return None
        return self._voice_fx(raw, out, mood)

    def _voice_fx(self, raw: Path, out: Path, mood: str) -> float | None:
        """Chorri ovozi: biroz yuqori va jarangli (tezlik o'zgarmaydi), siqilgan, aniq; VOICE_FX=none — o'chiradi."""
        import os
        fx = os.environ.get("VOICE_FX", "chorri")
        if fx == "none":
            shutil.copy(raw, out)
            return probe_duration(out)
        lift = 1.08 if mood in ("hype", "happy") else 1.05
        chain = (f"aresample=44100,asetrate={int(44100 * lift)},aresample=44100,atempo={1 / lift:.4f},"
                 "highpass=f=90,equalizer=f=3200:t=q:w=1.2:g=4,"
                 "aecho=0.85:0.5:9:0.18,acompressor=threshold=-18dB:ratio=3:attack=5:release=60,"
                 "loudnorm=I=-15:TP=-1.5:LRA=7")
        try:
            run_ffmpeg(["ffmpeg", "-y", "-i", str(raw), "-af", chain, "-ar", "44100", "-ac", "2", str(out)])
        except RuntimeError as exc:
            log.warning("Ovoz effekti ishlamadi: %s", exc)
            shutil.copy(raw, out)
        return probe_duration(out)

    def _sfx(self, tmp: Path) -> dict[str, Path]:
        specs = {
            "whoosh": "anoisesrc=d=0.45:c=pink:a=0.6,highpass=f=900,lowpass=f=6000,afade=t=in:d=0.12,afade=t=out:st=0.18:d=0.27",
            "pop": "aevalsrc='0.6*sin(2*PI*(380+2600*t)*t)*exp(-14*t)':d=0.18",
            "tick": "aevalsrc='0.5*sin(2*PI*1600*t)*exp(-60*t)':d=0.08",
            "ding": "aevalsrc='0.35*(sin(2*PI*1318*t)+0.7*sin(2*PI*1975*t))*exp(-4*t)':d=0.9",
            "boom": "aevalsrc='0.9*sin(2*PI*(110-60*t)*t)*exp(-6*t)':d=0.5",
        }
        out = {}
        for k, spec in specs.items():
            p = tmp / f"sfx_{k}.wav"
            run_ffmpeg(["ffmpeg", "-y", "-f", "lavfi", "-i", spec + ",aresample=44100", "-ac", "2", str(p)])
            out[k] = p
        return out

    def _audio(self, tmp: Path, total: float, events: list[tuple[float, str]], voice: list[tuple[float, Path]]) -> Path:
        sfx = self._sfx(tmp)
        inputs: list[str] = ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=44100:cl=stereo"]
        filt, labels = [], ["[0:a]"]
        idx = 1
        music = sorted((self.root / "assets" / "music").glob("*.mp3")) if (self.root / "assets" / "music").exists() else []
        if music:
            inputs += ["-stream_loop", "-1", "-i", str(random.choice(music))]
            filt.append(f"[{idx}:a]atrim=0:{total:.2f},volume=0.22,afade=t=out:st={max(0, total - 1.2):.2f}:d=1.2[m]")
            labels.append("[m]")
            idx += 1
        for t, path in voice:
            inputs += ["-i", str(path)]
            filt.append(f"[{idx}:a]aresample=44100,volume=1.2,adelay={int(t * 1000)}|{int(t * 1000)}[v{idx}]")
            labels.append(f"[v{idx}]")
            idx += 1
        for t, name in events:
            inputs += ["-i", str(sfx[name])]
            filt.append(f"[{idx}:a]adelay={int(t * 1000)}|{int(t * 1000)}[s{idx}]")
            labels.append(f"[s{idx}]")
            idx += 1
        filt.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:dropout_transition=0:normalize=0,"
                                       f"alimiter=limit=0.95[out]")
        out = tmp / "audio.m4a"
        run_ffmpeg(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filt), "-map", "[out]",
                    "-t", f"{total:.2f}", "-c:a", "aac", "-b:a", "160k", str(out)])
        return out

    # ---------------------------------------------------------------- kadr
    def _paste_scaled(self, frame: Image.Image, layer: Image.Image, cx: float, cy: float, scale: float,
                      alpha: float = 1.0, rot: float = 0.0) -> None:
        if scale <= 0.01 or alpha <= 0.01:
            return
        im = layer
        if abs(scale - 1) > 0.01:
            im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.BILINEAR)
        if rot:
            im = im.rotate(rot, resample=Image.BICUBIC, expand=True)
        if alpha < 0.99:
            a = im.getchannel("A").point(lambda v: int(v * alpha))
            im = im.copy()
            im.putalpha(a)
        frame.alpha_composite(im, (int(cx - im.width / 2), int(cy - im.height / 2)))

    def _road(self, t: float) -> Image.Image:
        fr = self._bg.copy().convert("RGBA")
        d = ImageDraw.Draw(fr)
        off = int((t * 900) % 240)
        for y in range(-240 + off, H, 240):  # markaziy yo'l chizig'i
            d.rectangle([W // 2 - 9, y, W // 2 + 9, y + 120], fill=(255, 184, 28, 34))
        for x in range(-120 + int(t * 300) % 120, W, 120):  # tepada ogohlantiruvchi chiziqlar
            d.polygon([(x, 0), (x + 60, 0), (x + 20, 26), (x - 40, 26)], fill=YEL)
        return fr

    def _option_cards(self, q: Question, size: int) -> list[Image.Image]:
        cards = []
        f = self.font(size)
        fl = self.font(int(size * 1.05))
        tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        for i, o in enumerate(q.options):
            lines = wrap(tmp, o, f, W - 300)[:3]
            hh = max(118, int(size * 1.25) * len(lines) + 46)
            card = Image.new("RGBA", (W - 120, hh), (0, 0, 0, 0))
            d = ImageDraw.Draw(card)
            d.rounded_rectangle([0, 0, card.width - 1, hh - 1], radius=30, fill=(28, 40, 68, 245), outline=(70, 90, 130), width=3)
            d.rounded_rectangle([18, (hh - 82) // 2, 100, (hh + 82) // 2], radius=22, fill=YEL)
            d.text((59, hh // 2), LETTERS[i], font=fl, fill=INK, anchor="mm")
            for j, ln in enumerate(lines):
                d.text((130, 23 + j * int(size * 1.25)), ln, font=f, fill=WHITE)
            cards.append(card)
        return cards

    def _recolor(self, card: Image.Image, fill, outline) -> Image.Image:
        c = card.copy()
        d = ImageDraw.Draw(c)
        d.rounded_rectangle([0, 0, c.width - 1, c.height - 1], radius=30, outline=outline, width=8)
        tint = Image.new("RGBA", c.size, fill + (0,))
        mask = c.getchannel("A").point(lambda v: int(v * 0.30))
        tint.putalpha(mask)
        c.alpha_composite(tint)
        return c

    # ---------------------------------------------------------------- make
    def make(self, q: Question, plan: VideoPlan, out_path: Path) -> VideoResult:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rng = random.Random(q.id)
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            v_hook = self._tts(plan.hook, tmp / "v_hook.mp3", "hype")
            v_q = self._tts(q.question, tmp / "v_q.mp3", "calm")
            has_voice = v_hook is not None and v_q is not None
            v_rev = self._tts(f"To'g'ri javob — {LETTERS[q.correct]}! {q.correct_text}.", tmp / "v_rev.mp3", "happy") if has_voice else None
            v_cta = self._tts(plan.cta, tmp / "v_cta.mp3", "cta") if has_voice else None

            hook_d = max(2.4, (v_hook or 0) + 0.3)
            read_d = max(3.2, (v_q or 0) + 0.4)
            count_n = 3
            q_d = read_d + count_n
            rev_d = max(3.4, (v_rev or 0) + 0.9)
            cta_d = max(2.8, (v_cta or 0) + 0.8)
            t_q, t_rev = hook_d, hook_d + q_d
            t_cta = t_rev + rev_d
            total = t_cta + cta_d
            frames = int(total * FPS)

            # oldindan tayyor qatlamlar
            hook_words = plan.hook.split()
            hook_size = 96 if len(plan.hook) < 40 else 80 if len(plan.hook) < 70 else 66
            hook_layers = []
            line_w, lines, cur = W - 160, [], []
            tmpd = ImageDraw.Draw(Image.new("RGB", (10, 10)))
            hf = self.font(hook_size)
            for wd in hook_words:  # so'zlarni qatorlarga ajratamiz (har so'z alohida chiqadi)
                trial = " ".join(cur + [wd])
                if tmpd.textlength(trial, font=hf) > line_w and cur:
                    lines.append(cur)
                    cur = [wd]
                else:
                    cur.append(wd)
            if cur:
                lines.append(cur)
            lh = int(hook_size * 1.2)
            hook_top = 330
            for li, ln in enumerate(lines):
                total_w = tmpd.textlength(" ".join(ln), font=hf)
                x = (W - total_w) / 2
                for wd in ln:
                    ww = tmpd.textlength(wd, font=hf)
                    lay = Image.new("RGBA", (int(ww) + 30, lh + 30), (0, 0, 0, 0))
                    color = YEL if wd.rstrip("?!.,").isdigit() or wd.endswith("?") else WHITE
                    ImageDraw.Draw(lay).text((15, 10), wd, font=hf, fill=color, stroke_width=6, stroke_fill=INK)
                    hook_layers.append((lay, x + ww / 2, hook_top + li * lh + lh / 2))
                    x += ww + tmpd.textlength(" ", font=hf)

            q_size = 70 if len(q.question) < 90 else 58 if len(q.question) < 160 else 48
            q_layer = self.text_block(q.question, q_size, WHITE, W - 160, stroke=0)
            q_card = Image.new("RGBA", (W - 80, q_layer.height + 60), (0, 0, 0, 0))
            ImageDraw.Draw(q_card).rounded_rectangle([0, 0, q_card.width - 1, q_card.height - 1], radius=40,
                                                     fill=(22, 32, 56, 240), outline=(255, 184, 28, 200), width=5)
            q_card.alpha_composite(q_layer, ((q_card.width - q_layer.width) // 2, 30))
            opt_size = 54 if max(len(o) for o in q.options) < 40 else 44
            cards = self._option_cards(q, opt_size)
            good = self._recolor(cards[q.correct], GREEN, GREEN)
            bad = [self._recolor(c, RED, (120, 60, 70)) for c in cards]
            gap = 26
            stage_h = q_card.height + 50 + sum(c.height for c in cards) + gap * (len(cards) - 1)
            q_top = max(150, int((H - 640 - stage_h) / 2) + 90)   # sahna markazda, pastda Chorri va taymerga joy
            opt_top = q_top + q_card.height + 50
            ys, y = [], opt_top
            for c in cards:
                ys.append(y + c.height / 2)
                y += c.height + gap
            stage_bottom = y

            pill_text = f"@{self.bot}" if self.bot else self.site
            pf = self.font(78)
            pw = int(tmpd.textlength(pill_text, font=pf)) + 120
            pill = Image.new("RGBA", (pw, 150), (0, 0, 0, 0))
            pd = ImageDraw.Draw(pill)
            pd.rounded_rectangle([0, 0, pw - 1, 149], radius=75, fill=YEL)
            pd.text((pw // 2, 75), pill_text, font=pf, fill=INK, anchor="mm")
            cta_layer = self.text_block(plan.cta, 64, WHITE, W - 160, stroke=5)
            expl = (q.explanation or "").strip()
            expl_layer = self.text_block(expl[:140] + ("…" if len(expl) > 140 else ""), 40, (210, 220, 235), W - 180) if expl else None
            ans_layer = self.text_block(f"To'g'ri javob: {LETTERS[q.correct]}", 70, GREEN, W - 160, stroke=6)
            site_layer = self.text_block(self.site, 36, (170, 180, 200), W - 160)
            confetti = [(rng.uniform(0.1, 0.9) * W, rng.uniform(-200, 200), rng.uniform(-1, 1), rng.uniform(2.5, 6),
                         rng.choice([YEL, GREEN, CYAN, (255, 120, 170), WHITE]), rng.uniform(10, 22)) for _ in range(90)]

            events: list[tuple[float, str]] = [(0.0, "whoosh"), (0.15, "boom")]
            for i in range(len(hook_layers)):
                events.append((0.35 + i * 0.12, "pop")) if i < 8 else None
            events.append((t_q, "whoosh"))
            for i in range(len(cards)):
                events.append((t_q + 0.5 + i * 0.22, "pop"))
            for k in range(count_n):
                events.append((t_q + read_d + k, "tick"))
            events += [(t_rev, "ding"), (t_rev + 0.05, "boom"), (t_cta, "whoosh"), (t_cta + 0.35, "pop")]
            voice = []
            if has_voice:
                voice = [(0.35, tmp / "v_hook.mp3"), (t_q + 0.3, tmp / "v_q.mp3")]
                if v_rev:
                    voice.append((t_rev + 0.35, tmp / "v_rev.mp3"))
                if v_cta:
                    voice.append((t_cta + 0.3, tmp / "v_cta.mp3"))
            audio = self._audio(tmp, total, events, voice)

            proc = subprocess.Popen(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                 "-r", str(FPS), "-i", "-", "-i", str(audio), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "copy", "-shortest", "-movflags", "+faststart",
                 str(out_path)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                for fi in range(frames):
                    t = fi / FPS
                    fr = self._road(t)
                    blink = 4 if (fi % 75) in (0, 1, 2) else 0
                    light = int(t * 2) % 3
                    shake = (0, 0)
                    if t < t_q:  # 1) HOOK
                        k = t / 0.55
                        s = ease_out_back(k)
                        expr = "shock" if t < 1.2 else "talk"
                        mouth = int(2 + 2 * abs(math.sin(t * 14))) if has_voice and t > 0.35 else 0
                        ch = self.chorri(560, expr, mouth, blink, light)
                        bob = math.sin(t * 5) * 12
                        self._paste_scaled(fr, ch, W / 2, 1290 + bob, s, 1.0, math.sin(t * 3) * 3)
                        for i, (lay, x, y) in enumerate(hook_layers):
                            st = 0.35 + i * 0.12
                            p = (t - st) / 0.28
                            if p > 0:
                                self._paste_scaled(fr, lay, x, y, 1.0 + 0.6 * (1 - ease_out(p)), clamp01(p * 2))
                        if t > hook_d - 0.25:  # o'tish: oq chaqnash
                            fl = Image.new("RGBA", (W, H), (255, 255, 255, int(200 * clamp01((t - (hook_d - 0.25)) / 0.25))))
                            fr.alpha_composite(fl)
                    elif t < t_rev:  # 2) SAVOL + TAYMER
                        lt = t - t_q
                        slide = ease_out(lt / 0.45)
                        cd_t = lt - read_d
                        if cd_t > count_n - 1:
                            shake = (int(math.sin(t * 90) * 10), int(math.cos(t * 77) * 8))
                        self._paste_scaled(fr, q_card, W / 2 + (1 - slide) * W, q_top + q_card.height / 2, 1.0, slide)
                        for i, c in enumerate(cards):
                            p = (lt - 0.5 - i * 0.22) / 0.3
                            if p > 0:
                                self._paste_scaled(fr, c, W / 2, ys[i], 0.6 + 0.4 * ease_out_back(p), clamp01(p * 1.8))
                        talking = has_voice and 0.3 < lt < read_d
                        expr = "talk" if talking else ("oops" if cd_t > count_n - 1 else "think")
                        mouth = int(1 + 3 * abs(math.sin(t * 15))) if talking else 0
                        ch = self.chorri(300, expr, mouth, blink, light)
                        cy_ch = max(stage_bottom + 230, H - 300)
                        self._paste_scaled(fr, ch, 210, cy_ch + math.sin(t * 4) * 8, 1.0)
                        # halqa taymer
                        cx_t, cy_t, r = W - 220, cy_ch - 10, 120
                        ring = Image.new("RGBA", (r * 2 + 40, r * 2 + 40), (0, 0, 0, 0))
                        rd = ImageDraw.Draw(ring)
                        rd.ellipse([20, 20, 20 + r * 2, 20 + r * 2], fill=(10, 16, 30, 230), outline=(60, 75, 110), width=14)
                        if cd_t >= 0:
                            n_left = count_n - int(cd_t)
                            frac = 1 - (cd_t % 1)
                            col = YEL if n_left > 1 else RED
                            rd.arc([20, 20, 20 + r * 2, 20 + r * 2], -90, -90 + 360 * frac, fill=col, width=14)
                            pulse = 1 + 0.25 * (1 - ease_out((cd_t % 1) / 0.3))
                            num = self.font(int(130 * pulse))
                            rd.text((20 + r, 20 + r), str(max(1, n_left)), font=num, fill=col, anchor="mm")
                        else:
                            rd.text((20 + r, 20 + r), "?", font=self.font(120), fill=YEL, anchor="mm")
                        fr.alpha_composite(ring, (int(cx_t - ring.width / 2), int(cy_t - ring.height / 2)))
                    elif t < t_cta:  # 3) JAVOB
                        lt = t - t_rev
                        self._paste_scaled(fr, q_card, W / 2, q_top + q_card.height / 2, 1.0, 1 - clamp01(lt / 0.4) * 0.6)
                        for i, c in enumerate(cards):
                            if i == q.correct:
                                s = 1 + 0.08 * ease_out_back(lt / 0.35)
                                self._paste_scaled(fr, good, W / 2, ys[i], s)
                            else:
                                self._paste_scaled(fr, bad[i], W / 2, ys[i], 0.94, 0.45)
                        self._paste_scaled(fr, ans_layer, W / 2, stage_bottom + 70, ease_out_back(lt / 0.35))
                        if expl_layer is not None:
                            self._paste_scaled(fr, expl_layer, W / 2, stage_bottom + 70 + ans_layer.height / 2 + expl_layer.height / 2 + 10,
                                               1.0, clamp01((lt - 0.4) / 0.4))
                        speaking = v_rev is not None and 0.35 < lt < 0.35 + v_rev
                        ch = self.chorri(300, "star" if lt < 0.9 else "happy",
                                         int(1 + 3 * abs(math.sin(t * 15))) if speaking and lt >= 0.9 else 0, 0, 2)
                        jump = abs(math.sin(lt * 7)) * 40 * max(0, 1 - lt / 2)
                        self._paste_scaled(fr, ch, 210, max(stage_bottom + 230, H - 300) - jump, 1.0)
                        cd = ImageDraw.Draw(fr)
                        for (x, y0, vx, vy, col, sz) in confetti:  # konfetti
                            px = x + vx * 260 * lt
                            py = 300 + y0 + vy * 380 * lt + 500 * lt * lt
                            if py < H:
                                a = lt * 9 + x
                                dx, dy = math.cos(a) * sz, math.sin(a) * sz * 0.5
                                cd.polygon([(px - dx, py - dy), (px + dy, py - dx), (px + dx, py + dy), (px - dy, py + dx)], fill=col)
                    else:  # 4) CHAQIRIQ
                        lt = t - t_cta
                        s = ease_out_back(lt / 0.5)
                        expr = "wink" if lt < 1.1 else "love"
                        cta_talk = v_cta is not None and 0.3 < lt < 0.3 + v_cta
                        ch = self.chorri(620, expr, int(1 + 3 * abs(math.sin(t * 15))) if cta_talk else 0, blink, light)
                        self._paste_scaled(fr, ch, W / 2, 760 + math.sin(t * 4) * 10, s, 1.0, math.sin(lt * 6) * 4 * max(0, 1 - lt))
                        self._paste_scaled(fr, cta_layer, W / 2, 1300, 1.0 + 0.4 * (1 - ease_out(lt / 0.3)), clamp01(lt / 0.25))
                        ps = ease_out_back((lt - 0.35) / 0.4) * (1 + 0.04 * math.sin(lt * 8))
                        self._paste_scaled(fr, pill, W / 2, 1300 + cta_layer.height / 2 + 120, ps)
                        self._paste_scaled(fr, site_layer, W / 2, H - 120, 1.0, clamp01((lt - 0.6) / 0.4))
                    rgb = fr.convert("RGB")
                    if shake != (0, 0):
                        rgb = rgb.transform(rgb.size, Image.AFFINE, (1, 0, shake[0], 0, 1, shake[1]), fillcolor=ASPHALT)
                    proc.stdin.write(rgb.tobytes())
                proc.stdin.close()
                err = proc.stderr.read().decode(errors="ignore")
                if proc.wait(timeout=600) != 0:
                    raise RuntimeError(f"ffmpeg xato: {err[-600:]}")
            except BaseException:
                proc.kill()
                raise
        return VideoResult(out_path, probe_duration(out_path) or total, has_voice)
