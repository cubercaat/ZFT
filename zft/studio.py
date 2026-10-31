"""Montaj studiyasi: AI sahnalar (video yoki rasm) + o'zbekcha ovoz + subtitr + musiqa + brend yakuni.

Natija: 1080×1920, 30 fps, H.264 + AAC — Instagram Reels va TikTok talablariga mos.
"""
from __future__ import annotations

import logging
import random
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .director import Concept
from .video import ACCENT, BG_BOTTOM, BG_TOP, H, MUTED, W, WHITE, _font, fit_text, probe_duration, run_ffmpeg

log = logging.getLogger(__name__)
FPS = 30


@dataclass
class StudioResult:
    path: Path
    duration: float
    has_voice: bool
    has_music: bool


class Studio:
    def __init__(self, root: Path, bot_username: str, site: str, tts_voice: str):
        self.root = root
        self.bot_username = bot_username
        self.site = site.replace("https://", "").replace("http://", "").rstrip("/")
        self.tts_voice = tts_voice
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg topilmadi")

    # ------------------------------------------------------------ overlays
    def caption_overlay(self, text: str, hook: str | None, path: Path) -> Path:
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if hook:
            f, lines, size = fit_text(d, hook, W - 160, 360, 76, 48, root=self.root)
            y = 230
            for ln in lines:
                d.text((W // 2, y), ln, font=f, fill=WHITE, anchor="ma", stroke_width=6, stroke_fill=(0, 0, 0, 220))
                y += int(size * 1.2)
        if text:
            f, lines, size = fit_text(d, text, W - 200, 240, 58, 40, root=self.root)
            block_h = int(len(lines) * size * 1.25) + 40
            top = H - 560
            widest = max(d.textlength(ln, font=f) for ln in lines)
            pad = 36
            d.rounded_rectangle([W // 2 - widest / 2 - pad, top, W // 2 + widest / 2 + pad, top + block_h],
                                radius=26, fill=(10, 12, 20, 170))
            y = top + 20
            for ln in lines:
                d.text((W // 2, y), ln, font=f, fill=ACCENT, anchor="ma")
                y += int(size * 1.25)
        img.save(path)
        return path

    def end_card(self, c: Concept, bg_frame: Path | None, path: Path) -> Path:
        if bg_frame and bg_frame.exists():
            base = Image.open(bg_frame).convert("RGB").resize((W, H)).filter(ImageFilter.GaussianBlur(18))
            shade = Image.new("RGB", (W, H), BG_TOP)
            img = Image.blend(base, shade, 0.62)
        else:
            img = Image.new("RGB", (W, H), BG_TOP)
            dd = ImageDraw.Draw(img)
            for y in range(H):
                t = y / H
                dd.line([(0, y), (W, y)], fill=tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)))
        d = ImageDraw.Draw(img)
        # brend qahramoni «Chorri» (har videoda bir xil yuz)
        from . import mascot
        ch = mascot.draw(420, "wink", light=2)
        img = img.convert("RGBA")
        img.alpha_composite(ch, (W // 2 - ch.width // 2, 150))
        img = img.convert("RGB")
        d = ImageDraw.Draw(img)
        d.text((W // 2, 760), "CHORRAHA", font=_font(84, True, self.root), fill=WHITE, anchor="mm")
        f, lines, size = fit_text(d, c.card_title, W - 160, 260, 70, 46, root=self.root)
        y = 880
        for ln in lines:
            d.text((W // 2, y), ln, font=f, fill=WHITE, anchor="ma")
            y += int(size * 1.2)
        if c.card_sub:
            d.text((W // 2, y + 30), c.card_sub, font=_font(42, False, self.root), fill=MUTED, anchor="ma")
            y += 90
        if self.bot_username:
            d.rounded_rectangle([130, y + 70, W - 130, y + 200], radius=65, fill=ACCENT)
            d.text((W // 2, y + 135), f"@{self.bot_username}", font=_font(62, True, self.root), fill=BG_TOP, anchor="mm")
            y += 200
        d.text((W // 2, y + 110), self.site, font=_font(40, False, self.root), fill=MUTED, anchor="mm")
        img.save(path)
        return path

    # ---------------------------------------------------------------- audio
    def tts(self, text: str, out: Path) -> float | None:
        exe = shutil.which("edge-tts")
        if not exe or not text.strip():
            return None
        try:
            subprocess.run([exe, "--voice", self.tts_voice, "--rate", "+6%", "--text", text, "--write-media", str(out)],
                           check=True, capture_output=True, timeout=90)
            return probe_duration(out)
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("TTS ishlamadi: %s", exc)
            return None

    def pick_music(self) -> Path | None:
        folder = self.root / "assets" / "music"
        tracks = sorted([*folder.glob("*.mp3"), *folder.glob("*.m4a")]) if folder.exists() else []
        single = self.root / "assets" / "music.mp3"
        if single.exists():
            tracks.append(single)
        return random.choice(tracks) if tracks else None

    # ----------------------------------------------------------- segments
    def _segment_from_video(self, src: Path, dur: float, overlay: Path, out: Path) -> None:
        run_ffmpeg(["ffmpeg", "-y", "-i", str(src), "-loop", "1", "-i", str(overlay),
                    "-filter_complex",
                    f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1,"
                    f"tpad=stop_mode=clone:stop_duration={dur:.2f},trim=0:{dur:.3f},setpts=PTS-STARTPTS[v];"
                    f"[v][1:v]overlay=0:0:shortest=1,format=yuv420p[out]",
                    "-map", "[out]", "-an", "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    str(out)])

    def _segment_from_image(self, src: Path, dur: float, overlay: Path, out: Path, zoom_in: bool) -> None:
        frames = int(dur * FPS)
        z = "min(zoom+0.0009,1.14)" if zoom_in else "if(eq(on,0),1.14,max(zoom-0.0009,1.0))"
        run_ffmpeg(["ffmpeg", "-y", "-loop", "1", "-i", str(src), "-loop", "1", "-i", str(overlay),
                    "-filter_complex",
                    f"[0:v]scale={int(W * 1.2)}:{int(H * 1.2)}:force_original_aspect_ratio=increase,"
                    f"crop={int(W * 1.2)}:{int(H * 1.2)},"
                    f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
                    f"setsar=1[v];[v][1:v]overlay=0:0:shortest=1,format=yuv420p[out]",
                    "-map", "[out]", "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    str(out)])

    def _segment_still(self, img: Path, dur: float, out: Path) -> None:
        frames = int(dur * FPS)
        run_ffmpeg(["ffmpeg", "-y", "-loop", "1", "-i", str(img), "-vf",
                    f"scale={int(W * 1.06)}:{int(H * 1.06)},zoompan=z='min(zoom+0.0006,1.05)':x='iw/2-(iw/zoom/2)':"
                    f"y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},setsar=1,format=yuv420p",
                    "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)])

    # ----------------------------------------------------------------- make
    def make(self, c: Concept, media: list[Path], kind: str, out: Path) -> StudioResult:
        """kind: 'video' — media mp4 kliplar, 'image' — rasmlar (Ken Burns harakati)."""
        if len(media) < len(c.scenes):
            raise ValueError("Har bir sahna uchun media kerak")
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            voices: list[tuple[Path | None, float]] = []
            durs: list[float] = []
            for i, s in enumerate(c.scenes):
                vp = tmp / f"v{i}.mp3"
                vd = self.tts(s.voice, vp)
                voices.append((vp if vd else None, vd or 0))
                clip_len = (probe_duration(media[i]) or s.seconds) if kind == "video" else s.seconds
                durs.append(max(3.0, min(12.0, max(clip_len, (vd or 0) + 0.5))))
            end_vp = tmp / "v_end.mp3"
            end_vd = self.tts(c.reveal_voice, end_vp)
            end_d = max(3.5, (end_vd or 0) + 0.8)

            segs = []
            last_frame = None
            for i, s in enumerate(c.scenes):
                ov = self.caption_overlay(s.caption, c.hook_text if i == 0 else None, tmp / f"ov{i}.png")
                seg = tmp / f"seg{i}.mp4"
                if kind == "video":
                    self._segment_from_video(media[i], durs[i], ov, seg)
                else:
                    self._segment_from_image(media[i], durs[i], ov, seg, zoom_in=i % 2 == 0)
                segs.append(seg)
            # oxirgi sahnadan bitta kadr — yakuniy kartaning foni uchun
            last_frame: Path | None = tmp / "last.png"
            try:  # subtitrsiz asl kadr
                src = media[len(c.scenes) - 1]
                if kind == "video":
                    run_ffmpeg(["ffmpeg", "-y", "-sseof", "-0.3", "-i", str(src), "-frames:v", "1", str(last_frame)])
                else:
                    Image.open(src).convert("RGB").save(last_frame)
            except (RuntimeError, OSError):
                last_frame = None
            card = self.end_card(c, last_frame, tmp / "card.png")
            end_seg = tmp / "seg_end.mp4"
            self._segment_still(card, end_d, end_seg)
            segs.append(end_seg)
            durs.append(end_d)
            voices.append((end_vp if end_vd else None, end_vd or 0))

            # video qismlarini birlashtirish
            lst = tmp / "list.txt"
            lst.write_text("".join(f"file '{Path(p).as_posix()}'\n" for p in segs), encoding="utf-8")
            vid = tmp / "video.mp4"
            run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(vid)])
            total = sum(durs)

            # audio: ovozlar o'z sahnasi boshida + musiqa (pastroq ovozda)
            inputs, filters, labels = [], [], []
            t = 0.0
            n = 0
            for (vp, _), d in zip(voices, durs):
                if vp:
                    inputs += ["-i", str(vp)]
                    ms = int((t + 0.25) * 1000)
                    filters.append(f"[{n}:a]aresample=44100,adelay={ms}|{ms},volume=1.6[a{n}]")
                    labels.append(f"[a{n}]")
                    n += 1
                t += d
            has_voice = n > 0
            music = self.pick_music()
            if music:
                inputs += ["-stream_loop", "-1", "-i", str(music)]
                vol = 0.16 if has_voice else 0.5
                filters.append(f"[{n}:a]aresample=44100,atrim=0:{total:.3f},volume={vol},"
                               f"afade=t=out:st={max(0, total - 1.5):.2f}:d=1.5[m]")
                labels.append("[m]")
                n += 1
            audio = tmp / "audio.m4a"
            if labels:
                filters.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest,"
                               f"apad,atrim=0:{total:.3f},loudnorm=I=-15:TP=-1.5:LRA=11[out]")
                run_ffmpeg(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters), "-map", "[out]",
                            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", str(audio)])
            else:
                run_ffmpeg(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{total:.3f}",
                            "-c:a", "aac", "-b:a", "64k", str(audio)])
            run_ffmpeg(["ffmpeg", "-y", "-i", str(vid), "-i", str(audio), "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(out)])
        return StudioResult(out, probe_duration(out) or total, has_voice, bool(music))
