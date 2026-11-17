"""Regenerate assets/app_icon.ico and assets/app_icon.png (needs Pillow).

Design: dark rounded tile with three concentric usage gauges in the provider
colours (Codex, Claude Code, Antigravity). Run: python tools/make_icon.py
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent.parent / 'assets'
SS = 1024  # supersampled canvas, downscaled for crisp edges
BG = '#101626'
TRACK = '#2B3850'
# (ring radius, stroke, colour, filled sweep in degrees)
RINGS = [(372, 92, '#52B59A', 290), (262, 92, '#DF906E', 235), (152, 92, '#6B92EB', 180)]


def render(size: int = 256) -> Image.Image:
    im = Image.new('RGBA', (SS, SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, SS - 1, SS - 1), radius=int(SS * 0.22), fill=BG)
    c = SS // 2
    for r, w, color, sweep in RINGS:
        box = (c - r, c - r, c + r, c + r)
        d.arc(box, 0, 360, fill=TRACK, width=w)
        start = -90
        d.arc(box, start, start + sweep, fill=color, width=w)
        for ang in (start, start + sweep):  # round caps
            a = math.radians(ang)
            x, y = c + (r - w / 2) * math.cos(a), c + (r - w / 2) * math.sin(a)
            d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=color)
    return im.resize((size, size), Image.Resampling.LANCZOS)


if __name__ == '__main__':
    big = render(256)
    big.save(ASSETS / 'app_icon.png')
    sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    big.save(ASSETS / 'app_icon.ico', sizes=sizes)
    print('wrote', ASSETS / 'app_icon.ico')
