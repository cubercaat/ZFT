"""«Chorri» — Chorraha qahramoni: yo'l belgisi boshli kichik robot.

Sariq romb (ogohlantiruvchi yo'l belgisi) — bosh, ichida LED-ekran yuz, tepasida svetofor-antenna
(qizil → sariq → yashil almashib yonadi), pastda belgining ustuni — bo'yni. 0.5 soniyada taniladi:
sariq romb + ikki katta yorug' ko'z.

draw(size, expr, ...) RGBA rasm qaytaradi; videoda har kadrda chaqiriladi.
Ifodalar: idle, happy, shock, think, wink, cool, sad, talk.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

SIGN = (255, 184, 28)
SIGN_DARK = (214, 140, 0)
INK = (19, 32, 58)
VISOR = (10, 18, 36)
LED = (120, 240, 255)
LED_GLOW = (60, 200, 255)
POLE = (150, 162, 182)
LIGHTS = [(255, 72, 72), (255, 190, 40), (60, 220, 120)]
EXPRESSIONS = ("idle", "happy", "shock", "think", "wink", "cool", "sad", "talk")


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

    # LED-ekran (yuz)
    vw, vh = R * 0.98, R * 0.62
    vx0, vy0 = cx - vw / 2, cy - vh / 2 + R * 0.02
    _rr(d, (vx0, vy0, vx0 + vw, vy0 + vh), vh * 0.32, fill=VISOR, outline=INK, width=int(S * 6))

    face = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    fd = ImageDraw.Draw(face)
    ex, ey = vw * 0.22, vy0 + vh * 0.40
    lx_, ly_ = look[0] * vw * 0.05, look[1] * vh * 0.06
    eyes = [(cx - ex + lx_, ey + ly_), (cx + ex + lx_, ey + ly_)]
    er_w, er_h = vw * 0.085, vh * 0.23
    lw = int(S * 11)
    for i, (x, y) in enumerate(eyes):
        e = expr
        if e == "wink" and i == 0:
            e = "closed"
        if blink > 0.6 and e not in ("happy", "cool"):
            e = "closed"
        if e in ("happy",):
            fd.arc([x - er_w * 1.2, y - er_h * 0.4, x + er_w * 1.2, y + er_h * 1.1], 200, 340, fill=LED, width=lw)
        elif e == "closed":
            fd.line([(x - er_w, y + er_h * 0.25), (x + er_w, y + er_h * 0.25)], fill=LED, width=lw)
        elif e == "shock":
            r = er_w * 1.25
            fd.ellipse([x - r, y - r, x + r, y + r], outline=LED, width=lw)
            fd.ellipse([x - r * 0.35, y - r * 0.35, x + r * 0.35, y + r * 0.35], fill=LED)
        elif e == "cool":  # quyosh ko'zoynak
            _rr(fd, (x - er_w * 1.6, y - er_h * 0.45, x + er_w * 1.6, y + er_h * 0.55), er_h * 0.3, fill=LED)
        elif e == "sad":
            _rr(fd, (x - er_w * 0.8, y - er_h * 0.2, x + er_w * 0.8, y + er_h * 0.9), er_w * 0.8, fill=LED)
            tilt = 1 if i == 0 else -1
            fd.line([(x - er_w * 1.2, y - er_h * 0.55 + tilt * er_h * 0.25), (x + er_w * 1.2, y - er_h * 0.55 - tilt * er_h * 0.25)],
                    fill=LED, width=int(lw * 0.7))
        elif e == "think" and i == 1:
            fd.line([(x - er_w * 1.1, y), (x + er_w * 1.1, y - er_h * 0.15)], fill=LED, width=lw)
        else:  # idle, talk, think(chap ko'z)
            sq = max(0.15, 1 - blink)
            _rr(fd, (x - er_w, y - er_h * sq, x + er_w, y + er_h * sq), er_w, fill=LED)
            fd.ellipse([x - er_w * 0.45, y - er_h * sq * 0.65, x - er_w * 0.05, y - er_h * sq * 0.25], fill=(235, 255, 255))

    # og'iz
    my = vy0 + vh * 0.80
    mw = vw * 0.17
    if expr in ("shock",):
        r = vw * 0.06
        fd.ellipse([cx - r, my - r * 1.1, cx + r, my + r * 0.9], outline=LED, width=lw)
    elif expr == "sad":
        fd.arc([cx - mw, my - vh * 0.02, cx + mw, my + vh * 0.22], 200, 340, fill=LED, width=lw)
    elif expr == "think":
        fd.line([(cx - mw * 0.6, my), (cx + mw * 0.9, my - vh * 0.04)], fill=LED, width=lw)
    elif mouth > 0.05 or expr == "talk":
        o = max(mouth, 0.35 if expr == "talk" else 0)
        _rr(fd, (cx - mw * 0.7, my - vh * 0.10 * o, cx + mw * 0.7, my + vh * 0.10 * o + S * 4), mw * 0.4, fill=LED)
    else:
        fd.arc([cx - mw, my - vh * 0.22, cx + mw, my + vh * 0.04], 20, 160, fill=LED, width=lw)
    glow = face.filter(ImageFilter.GaussianBlur(S * 9))
    tint = Image.new("RGBA", (w, h), LED_GLOW + (0,))
    tint.putalpha(glow.getchannel("A").point(lambda v: int(v * 0.9)))
    img.alpha_composite(tint)
    img.alpha_composite(face)
    return img.resize((size, int(size * 1.35)), Image.LANCZOS)


def sheet(path, size: int = 360) -> None:
    """Barcha ifodalar bitta rasmda (ko'rib chiqish uchun)."""
    exprs = ["idle", "happy", "shock", "think", "wink", "cool", "sad", "talk"]
    cols = 4
    cell_w, cell_h = size + 40, int(size * 1.35) + 90
    bg = Image.new("RGB", (cell_w * cols, cell_h * 2), (14, 23, 41))
    from .video import _font
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    f = _font(34, True, root)
    dd = ImageDraw.Draw(bg)
    names = {"idle": "oddiy", "happy": "xursand", "shock": "hayron", "think": "o'ylayapti",
             "wink": "ko'z qisdi", "cool": "zo'r", "sad": "afsus", "talk": "gapiryapti"}
    for i, e in enumerate(exprs):
        m = draw(size, e, mouth=0.8 if e == "talk" else 0, light=i)
        x, y = (i % cols) * cell_w + 20, (i // cols) * cell_h + 10
        bg.paste(m, (x, y), m)
        dd.text((x + size // 2, y + int(size * 1.35) + 40), names[e], font=f, fill=(255, 184, 28), anchor="mm")
    bg.save(path)
