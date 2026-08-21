"""Эталонная мишень для проверки цветопередачи: py tools/make_testchart.py

Собирает картинку, на которой сразу видно все слабые места конвертера:

* 24 поля цветовой шкалы (значения ColorChecker в sRGB) — по ним считается
  средняя ошибка в OKLab, то есть объективное «насколько похоже»;
* серый клин 21 ступень — видно, не рвётся ли светлота;
* развёртка тонов при полной и половинной насыщенности — видно, куда уводит
  оттенки бедная палитра;
* тона кожи — самое заметное для глаза;
* мелкая сетка и тонкие линии — проверка, не съедает ли деталь склейка;
* тёмный участок с неоновым свечением — случай «тёмное фото с подсветкой»,
  на котором палитра краскопульта ломается сильнее всего.

Результат кладётся в tests/testchart.png.
"""

from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "testchart.png")

# Классическая 24-польная шкала, значения sRGB (ряд за рядом сверху вниз).
CHECKER = [
    (115, 82, 68), (194, 150, 130), (98, 122, 157), (87, 108, 67), (133, 128, 177), (103, 189, 170),
    (214, 126, 44), (80, 91, 166), (193, 90, 99), (94, 60, 108), (157, 188, 64), (224, 163, 46),
    (56, 61, 150), (70, 148, 73), (175, 54, 60), (231, 199, 31), (187, 86, 149), (8, 133, 161),
    (243, 243, 242), (200, 200, 200), (160, 160, 160), (122, 122, 121), (85, 85, 85), (52, 52, 52),
]

SKIN = [(255, 224, 196), (240, 200, 170), (222, 176, 142), (198, 148, 112),
        (166, 118, 88), (128, 88, 66), (94, 62, 46), (66, 42, 32)]

W, H = 960, 720


def hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]
    return int(r * 255), int(g * 255), int(b * 255)


def main() -> int:
    img = Image.new("RGB", (W, H), (24, 24, 24))
    d = ImageDraw.Draw(img)

    # 1. цветовая шкала 6x4
    pw, ph, pad = 130, 78, 8
    for i, color in enumerate(CHECKER):
        cx, cy = i % 6, i // 6
        x0 = 20 + cx * (pw + pad)
        y0 = 20 + cy * (ph + pad)
        d.rectangle([x0, y0, x0 + pw, y0 + ph], fill=color)

    # 2. серый клин
    top = 20 + 4 * (ph + pad) + 10
    for i in range(21):
        v = int(round(i / 20 * 255))
        x0 = 20 + i * (920 // 21)
        d.rectangle([x0, top, x0 + 920 // 21 - 2, top + 46], fill=(v, v, v))

    # 3. развёртка тонов: сверху насыщенные, снизу приглушённые
    top += 54
    for x in range(920):
        hue = x / 920
        d.line([(20 + x, top), (20 + x, top + 40)], fill=hsv_to_rgb(hue, 1.0, 1.0))
        d.line([(20 + x, top + 42), (20 + x, top + 82)], fill=hsv_to_rgb(hue, 0.45, 0.85))

    # 4. тона кожи
    top += 90
    for i, color in enumerate(SKIN):
        x0 = 20 + i * 115
        d.rectangle([x0, top, x0 + 110, top + 44], fill=color)

    # 5. мелкая сетка и тонкие линии — проверка детализации
    top += 52
    for x in range(0, 300, 4):
        d.line([(20 + x, top), (20 + x, top + 60)], fill=(255, 255, 255))
    for y in range(0, 60, 4):
        d.line([(330, top + y), (630, top + y)], fill=(255, 255, 255))
    for i in range(12):
        d.line([(660 + i * 24, top), (660 + i * 24 + 12, top + 60)], fill=(255, 255, 255), width=1)

    # 6. тёмный участок с неоновым свечением
    top += 68
    dark = np.zeros((H - top - 10, 920, 3), dtype=np.float32)
    hh, ww = dark.shape[:2]
    yy, xx = np.mgrid[0:hh, 0:ww]
    for cx, tint in ((160, (0.1, 0.9, 1.0)), (460, (1.0, 0.25, 0.5)), (760, (0.5, 0.4, 1.0))):
        dist = np.sqrt((xx - cx) ** 2 + ((yy - hh / 2) * 2.2) ** 2)
        glow = np.exp(-dist / 55.0)
        for c in range(3):
            dark[..., c] += glow * tint[c] * 255
    dark += 14
    img.paste(Image.fromarray(np.clip(dark, 0, 255).astype(np.uint8), "RGB"), (20, top))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img.save(OUT)
    print(f"Мишень записана: {OUT} ({W}x{H})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
