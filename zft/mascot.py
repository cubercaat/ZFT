"""«Chorri» — Chorraha qahramoni: yo'l belgisi boshli kichik robot.

Sariq romb (ogohlantiruvchi yo'l belgisi) — bosh, ichida LED-ekran yuz, tepasida svetofor-antenna
(qizil → sariq → yashil almashib yonadi), pastda belgining ustuni — bo'yni. 0.5 soniyada taniladi:
sariq romb + ikki katta yorug' ko'z.

draw(size, expr, ...) RGBA rasm qaytaradi; videoda har kadrda chaqiriladi.
Ifodalar: idle, happy, shock, think, wink, cool, sad, talk, love, star, oops.
Yuz — zumrad-yashil shisha ekran, katta yalpiz-oq ko'zlar (qorachiq, uchqun), qoshlar va yonoqlar bilan.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

SIGN = (255, 184, 28)
SIGN_DARK = (214, 140, 0)
INK = (19, 32, 58)
SCREEN_TOP = (18, 128, 96)      # zumrad
SCREEN_BOT = (5, 58, 46)        # to'q archa yashili
EYE = (236, 255, 245)           # yalpiz-oq
EYE_GLOW = (80, 255, 170)
IRIS = (36, 196, 128)
PUPIL = (4, 34, 26)
BROW = (190, 255, 222)
BLUSH = (255, 120, 150, 120)
HEART = (255, 82, 120)
STAR = (255, 214, 60)
MOUTH_IN = (8, 40, 32)
TONGUE = (255, 120, 140)
POLE = (150, 162, 182)
LIGHTS = [(255, 72, 72), (255, 190, 40), (60, 220, 120)]
EXPRESSIONS = ("idle", "happy", "shock", "think", "wink", "cool", "sad", "talk", "love", "star", "oops")


def _rr(d: ImageDraw.ImageDraw, box, r, **kw):
    d.rounded_rectangle([int(v) for v in box], radius=int(r), **kw)


def draw(size: int = 512, expr: str = "idle", mouth: float = 0.0, blink: float = 0.0,
         look: tuple[float, float] = (0.0, 0.0), light: int = 0, pole: bool = True) -> Image.Image:
    """size — rasm kengligi (balandligi 1.35×). mouth 0..1 (gapirish), blink 0..1, look −1..1."""
    S = 2  # antialiasing uchun 2× chizib, keyin kichraytiramiz
    w, h = size * S, int(size * 1.35) * S
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = w / 2, w * 0.56          # romb markazi
    R = w * 0.40                      # markazdan burchakkacha

    # ustun (belgi tayog'i) va asos
    if pole:
        pw = w * 0.07
        _rr(d, (cx - pw / 2, cy + R * 0.8, cx + pw / 2, h - w * 0.06), pw / 2, fill=POLE, outline=INK, width=int(S * 5))
        _rr(d, (cx - w * 0.17, h - w * 0.085, cx + w * 0.17, h - w * 0.02), w * 0.03, fill=INK)

    # antenna + svetofor chirog'i
    ax = cx + R * 0.42
    d.line([(ax - R * 0.08, cy - R * 0.62), (ax + R * 0.10, cy - R * 1.05)], fill=INK, width=int(S * 9))
    lc = LIGHTS[light % 3]
    lr = w * 0.045
    lx, ly = ax + R * 0.12, cy - R * 1.10
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([lx - lr * 2.6, ly - lr * 2.6, lx + lr * 2.6, ly + lr * 2.6], fill=lc + (110,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(w * 0.02)))
    d.ellipse([lx - lr, ly - lr, lx + lr, ly + lr], fill=lc, outline=INK, width=int(S * 5))

    # bosh: romb (yumaloq burchakli) — tashqi siyoh, sariq, ichki ingichka chiziq
    def diamond(r, fill, width=0):
        pts = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.polygon(pts, fill=fill)
        if width:  # yumaloq burchaklar uchun qalin chiziq
            ld.line(pts + [pts[0], pts[1]], fill=fill, width=width, joint="curve")
        return layer

    img.alpha_composite(diamond(R, INK, int(S * 26)))
    img.alpha_composite(diamond(R * 0.93, SIGN, int(S * 14)))
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))   # pastki yarmi biroz to'qroq — hajm
    sd = ImageDraw.Draw(shade)
    sd.polygon([(cx - R * 0.93, cy), (cx + R * 0.93, cy), (cx, cy + R * 0.93)], fill=SIGN_DARK + (90,))
    img.alpha_composite(shade)
    hl = Image.new("RGBA", (w, h), (0, 0, 0, 0))      # yaltiroq
    ImageDraw.Draw(hl).polygon([(cx - R * 0.55, cy - R * 0.25), (cx, cy - R * 0.80), (cx + R * 0.10, cy - R * 0.70),
                                (cx - R * 0.42, cy - R * 0.17)], fill=(255, 255, 255, 70))
    img.alpha_composite(hl)
    inner = [(cx, cy - R * 0.83), (cx + R * 0.83, cy), (cx, cy + R * 0.83), (cx - R * 0.83, cy)]
    d.line(inner + [inner[0]], fill=INK, width=int(S * 5), joint="curve")

    # yuz-ekran: zumrad-yashil shisha
    vw, vh = R * 1.06, R * 0.72
    vx0, vy0 = cx - vw / 2, cy - vh / 2 + R * 0.03
    box = (vx0, vy0, vx0 + vw, vy0 + vh)
    _rr(d, (vx0 - S * 7, vy0 - S * 7, vx0 + vw + S * 7, vy0 + vh + S * 7), vh * 0.34, fill=INK)
    grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for k in range(int(vh)):
        t = k / vh
        c = tuple(int(SCREEN_TOP[i] + (SCREEN_BOT[i] - SCREEN_TOP[i]) * t) for i in range(3))
        gd.line([(vx0, vy0 + k), (vx0 + vw, vy0 + k)], fill=c + (255,))
    mask = Image.new("L", (w, h), 0)
    _rr(ImageDraw.Draw(mask), box, vh * 0.30, fill=255)
    grad.putalpha(mask)
    img.alpha_composite(grad)
    shine = Image.new("RGBA", (w, h), (0, 0, 0, 0))     # shisha yaltirog'i
    ImageDraw.Draw(shine).ellipse([vx0 + vw * 0.04, vy0 - vh * 0.55, vx0 + vw * 0.80, vy0 + vh * 0.32], fill=(255, 255, 255, 34))
    sh_m = Image.new("L", (w, h), 0)
    _rr(ImageDraw.Draw(sh_m), box, vh * 0.30, fill=255)
    from PIL import ImageChops
    shine.putalpha(ImageChops.multiply(shine.getchannel("A"), sh_m))
    img.alpha_composite(shine)
    _draw_face(img, S, cx, vx0, vy0, vw, vh, expr, mouth, blink, look)
    hl2 = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(hl2).arc([vx0 + vw * 0.06, vy0 + vh * 0.06, vx0 + vw * 0.94, vy0 + vh * 1.4], 200, 250,
                            fill=(255, 255, 255, 70), width=int(S * 6))
    img.alpha_composite(hl2)
    return img.resize((size, int(size * 1.35)), Image.LANCZOS)


def _draw_face(img, S, cx, vx0, vy0, vw, vh, expr, mouth, blink, look):
    w, h = img.size
    face = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    fd = ImageDraw.Draw(face)
    lw = int(S * 9)
    er = vh * 0.21                        # ko'z radiusi
    ex, ey = vw * 0.23, vy0 + vh * 0.46
    lx_, ly_ = look[0] * er * 0.35, look[1] * er * 0.3
    if expr == "think" and look == (0.0, 0.0):
        lx_, ly_ = er * 0.32, -er * 0.30
    eyes = [(cx - ex, ey), (cx + ex, ey)]

    def brow(x, y, side, lift=0.0, tilt=0.0):  # side −1 chap, +1 o'ng; tilt>0 ichki tomoni pastda (jahl)
        bw = er * 0.95
        y0 = y - er * (1.28 + lift * 0.6)
        ix = x - side * bw * 0.85          # ichki uchi (burun tomoni)
        ox = x + side * bw * 0.85
        inner = (ix, y0 + tilt * er * 0.40)   # tilt<0 → ichki uchi tepada (afsus), >0 → pastda (jahl)
        outer = (ox, y0 - tilt * er * 0.40)
        fd.line([inner, outer], fill=BROW, width=int(S * 8))
        for px, py in (inner, outer):
            r = S * 4
            fd.ellipse([px - r, py - r, px + r, py + r], fill=BROW)

    def eye(x, y, kind, side):
        if kind == "closed":
            fd.arc([x - er, y - er * 0.6, x + er, y + er * 0.9], 200, 340, fill=EYE, width=lw)
            return
        if kind == "happy":
            fd.arc([x - er, y - er * 0.2, x + er, y + er * 1.4], 195, 345, fill=EYE, width=int(lw * 1.2))
            return
        if kind == "heart":
            r = er * 0.62
            fd.ellipse([x - r * 1.55, y - r * 1.1, x - r * 0.05, y + r * 0.35], fill=HEART)
            fd.ellipse([x + r * 0.05, y - r * 1.1, x + r * 1.55, y + r * 0.35], fill=HEART)
            fd.polygon([(x - r * 1.45, y - r * 0.15), (x + r * 1.45, y - r * 0.15), (x, y + r * 1.45)], fill=HEART)
            fd.ellipse([x - r * 1.1, y - r * 0.85, x - r * 0.7, y - r * 0.45], fill=(255, 255, 255))
            return
        if kind == "star":
            pts = []
            for k in range(10):
                a = -math.pi / 2 + k * math.pi / 5
                rr = er * (1.05 if k % 2 == 0 else 0.45)
                pts.append((x + math.cos(a) * rr, y + math.sin(a) * rr))
            fd.polygon(pts, fill=STAR)
            return
        if kind == "cool":
            _rr(fd, (x - er * 1.25, y - er * 0.65, x + er * 1.25, y + er * 0.75), er * 0.45, fill=(6, 20, 16))
            fd.line([(x - er * 0.8, y - er * 0.35), (x - er * 0.2, y - er * 0.35)], fill=EYE_GLOW, width=int(S * 5))
            return
        sx, sy = er, er * (1.12 if kind != "wide" else 1.25)
        sq = max(0.08, 1 - blink)
        top = y - sy * sq
        fd.ellipse([x - sx, top, x + sx, y + sy], fill=EYE)            # oq qismi
        pr = er * (0.60 if kind != "wide" else 0.38)
        px, py = x + lx_, y + ly_ + er * 0.08
        fd.ellipse([px - pr, py - pr, px + pr, py + pr], fill=IRIS)    # qorachiq (yashil halqa)
        fd.ellipse([px - pr * 0.62, py - pr * 0.62, px + pr * 0.62, py + pr * 0.62], fill=PUPIL)
        s1 = pr * 0.36
        fd.ellipse([px - pr * 0.55 - s1, py - pr * 0.55 - s1, px - pr * 0.55 + s1, py - pr * 0.55 + s1], fill=(255, 255, 255))
        s2 = pr * 0.17
        fd.ellipse([px + pr * 0.35 - s2, py + pr * 0.30 - s2, px + pr * 0.35 + s2, py + pr * 0.30 + s2], fill=(255, 255, 255))

    kinds = {"idle": ("open", "open"), "talk": ("open", "open"), "happy": ("happy", "happy"),
             "shock": ("wide", "wide"), "think": ("open", "open"), "wink": ("closed", "open"),
             "cool": ("cool", "cool"), "sad": ("open", "open"), "love": ("heart", "heart"),
             "star": ("star", "star"), "oops": ("wide", "open")}.get(expr, ("open", "open"))
    if blink > 0.85:
        kinds = tuple("closed" if k in ("open", "wide") else k for k in kinds)
    for (x, y), k, side in zip(eyes, kinds, (-1, 1)):
        eye(x, y, k, side)
    # qoshlar — reaksiyaning asosiy qismi
    brows = {"idle": (0.0, 0.0, 0.0, 0.0), "talk": (0.05, 0.0, 0.05, 0.0), "happy": (0.15, -0.3, 0.15, -0.3),
             "shock": (0.55, -0.2, 0.55, -0.2), "think": (-0.05, 0.4, 0.35, -0.3), "wink": (0.0, 0.3, 0.2, -0.2),
             "sad": (0.1, -0.9, 0.1, -0.9), "oops": (0.3, -0.6, 0.15, -0.6), "love": (0.25, -0.4, 0.25, -0.4),
             "star": (0.4, -0.3, 0.4, -0.3)}
    if expr in brows:
        l1, t1, l2, t2 = brows[expr]
        brow(eyes[0][0], eyes[0][1], -1, l1, t1)
        brow(eyes[1][0], eyes[1][1], 1, l2, t2)
    # yonoqlar
    if expr in ("happy", "love", "wink", "star"):
        for x, _ in eyes:
            r = er * 0.42
            fd.ellipse([x - r * 1.2, ey + er * 1.05 - r * 0.6, x + r * 1.2, ey + er * 1.05 + r * 0.6], fill=BLUSH)
    # og'iz
    my = vy0 + vh * 0.86
    mw = vw * 0.13
    if expr == "shock":
        r = mw * 0.55
        fd.ellipse([cx - r, my - r * 1.25, cx + r, my + r * 0.9], fill=MOUTH_IN, outline=EYE, width=int(lw * 0.8))
    elif expr == "sad":
        fd.arc([cx - mw, my - vh * 0.01, cx + mw, my + vh * 0.20], 205, 335, fill=EYE, width=lw)
    elif expr == "oops":
        pts = [(cx - mw + k * mw / 4, my + (S * 6 if k % 2 else -S * 6)) for k in range(9)]
        fd.line(pts, fill=EYE, width=int(lw * 0.8), joint="curve")
        dx, dy = eyes[1][0] + er * 1.5, eyes[1][1] - er * 1.0   # ter tomchisi
        fd.polygon([(dx, dy - er * 0.55), (dx - er * 0.32, dy + er * 0.05), (dx + er * 0.32, dy + er * 0.05)], fill=(140, 220, 255))
        fd.ellipse([dx - er * 0.33, dy - er * 0.2, dx + er * 0.33, dy + er * 0.45], fill=(140, 220, 255))
    elif expr == "think":
        fd.line([(cx - mw * 0.55, my + S * 2), (cx + mw * 0.75, my - S * 8)], fill=EYE, width=lw)
    elif mouth > 0.05 or expr == "talk":
        o = max(mouth, 0.4 if expr == "talk" else 0)
        hh = vh * 0.16 * o + S * 6
        _rr(fd, (cx - mw * 0.75, my - hh / 2, cx + mw * 0.75, my + hh / 2), min(mw * 0.5, hh / 2), fill=MOUTH_IN,
            outline=EYE, width=int(lw * 0.7))
    elif expr in ("happy", "love", "star"):
        fd.chord([cx - mw * 1.1, my - vh * 0.20, cx + mw * 1.1, my + vh * 0.10], 0, 180, fill=MOUTH_IN, outline=EYE, width=int(lw * 0.8))
        fd.chord([cx - mw * 0.55, my - vh * 0.02, cx + mw * 0.55, my + vh * 0.09], 0, 180, fill=TONGUE)
    else:
        fd.arc([cx - mw, my - vh * 0.18, cx + mw, my + vh * 0.05], 20, 160, fill=EYE, width=lw)
    from PIL import ImageChops
    vmask = Image.new("L", (w, h), 0)
    _rr(ImageDraw.Draw(vmask), (vx0, vy0, vx0 + vw, vy0 + vh), vh * 0.30, fill=255)
    face.putalpha(ImageChops.multiply(face.getchannel("A"), vmask))
    glow = face.filter(ImageFilter.GaussianBlur(S * 8))
    tint = Image.new("RGBA", (w, h), EYE_GLOW + (0,))
    tint.putalpha(glow.getchannel("A").point(lambda v: int(v * 0.55)))
    img.alpha_composite(tint)
    img.alpha_composite(face)


def sheet(path, size: int = 360) -> None:
    """Barcha ifodalar bitta rasmda (ko'rib chiqish uchun)."""
    exprs = ["idle", "happy", "shock", "think", "wink", "cool", "sad", "talk", "love", "star", "oops"]
    cols = 4
    cell_w, cell_h = size + 40, int(size * 1.35) + 90
    bg = Image.new("RGB", (cell_w * cols, cell_h * ((len(exprs) + cols - 1) // cols)), (14, 23, 41))
    from .video import _font
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    f = _font(34, True, root)
    dd = ImageDraw.Draw(bg)
    names = {"idle": "oddiy", "happy": "xursand", "shock": "hayron", "think": "o'ylayapti",
             "wink": "ko'z qisdi", "cool": "zo'r", "sad": "afsus", "talk": "gapiryapti",
             "love": "yoqdi", "star": "hayajon", "oops": "voy"}
    for i, e in enumerate(exprs):
        m = draw(size, e, mouth=0.8 if e == "talk" else 0, light=i)
        x, y = (i % cols) * cell_w + 20, (i // cols) * cell_h + 10
        bg.paste(m, (x, y), m)
        dd.text((x + size // 2, y + int(size * 1.35) + 40), names[e], font=f, fill=(255, 184, 28), anchor="mm")
    bg.save(path)
