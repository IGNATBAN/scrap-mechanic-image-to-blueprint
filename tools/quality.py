r"""Сравнение режимов подбора цвета на живых картинках.

    py tools/quality.py "путь\к\фото.png" [ещё файлы...]
    py tools/quality.py --nvidia          # взять записи NVIDIA ShadowPlay

Печатает две ошибки в OKLab и складывает лист сравнения в
%TEMP%\sm_pixel_quality_<имя>.png:

  «издали»  — картинка размывается по-гауссу, как её видит глаз с
              расстояния. Именно эту величину улучшает дизеринг: отдельные
              блоки разного цвета сливаются в промежуточный тон.
  «в упор»  — ошибка каждого блока по отдельности. Дизеринг её ухудшает
              (соседние блоки специально разные), заливки — улучшают.

Смотреть надо на обе: хороший режим держит «издали» низкой, не разгоняя
«в упор» до пестроты.
"""

from __future__ import annotations

import glob
import io
import os
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from core import blocks, imageproc, materials, mesh, quant  # noqa: E402

WIDTH = 190
NVIDIA = os.path.join(os.environ.get("USERPROFILE", ""), "Videos", "NVIDIA")


def variants() -> list[tuple[str, dict]]:
    extra = [o.uuid for o in materials.usable_blocks()]
    return [
        ("Оригинал", None),
        ("Старый способ: Pillow, диффузия в sRGB", "OLD"),
        ("Флойд в OKLab, 40 цветов",
         dict(color_mode="palette", method="fs", lum_weight=1.0, strength=1.0, serpentine=True)),
        ("Синий шум, сила 0.7",
         dict(color_mode="palette", method="bluenoise", lum_weight=1.8, strength=0.7)),
        ("Флойд + блоки как доп. цвета",
         dict(color_mode="palette", method="fs", lum_weight=1.0, strength=1.0,
              serpentine=True, extra_blocks=extra, base_block=blocks.DEFAULT_BLOCK)),
        ("Без дизеринга + блоки (плакат)",
         dict(color_mode="palette", method="none", lum_weight=1.8,
              extra_blocks=extra, base_block=blocks.DEFAULT_BLOCK)),
    ]


def font(size: int):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def old_pillow(raw: bytes, width: int):
    """Как это делалось раньше: палитра из 40 цветов, диффузия ошибки в sRGB.

    Оставлено ради честного сравнения — так работает Pillow и так работают
    все найденные сторонние конвертеры.
    """
    from core import palette as pal

    base = imageproc.build_grid(raw, width=width, color_mode="exact")
    pal_img = Image.new("P", (1, 1))
    flat: list[int] = []
    for h in pal.PALETTE_HEX:
        flat += [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]
    while len(flat) < 768:
        flat += flat[-3:]
    pal_img.putpalette(flat[:768])
    out = np.array(
        Image.fromarray(base.rgb, "RGB")
        .quantize(palette=pal_img, dither=Image.Dither.FLOYDSTEINBERG)
        .convert("RGB"),
        dtype=np.uint8,
    )
    grid = imageproc.Grid(rgb=out, keys=imageproc._pack(out), mask=base.mask,
                          width=base.width, height=base.height, source_size=base.source_size)
    grid.error = quant.perceived_error(base.rgb, out, base.mask)
    grid.error_close = quant.mean_error_rgb(base.rgb, out, base.mask)
    return grid


def run(path: str) -> None:
    raw = open(path, "rb").read()
    name = os.path.splitext(os.path.basename(path))[0]
    print(f"\n=== {name} ===")
    print("%-38s %-10s %-10s %-9s %s" % ("режим", "издали", "в упор", "цветов", "деталей"))

    base = imageproc.build_grid(raw, width=WIDTH, color_mode="exact")
    tiles_img: list[tuple[str, Image.Image, str]] = []

    for title, params in variants():
        if params == "OLD":
            grid = old_pillow(raw, WIDTH)
            rects = mesh.merge_rects(grid.keys, grid.mask)
            line = "%-38s %-10.4f %-10.4f %-9s %d" % (
                title, grid.error, grid.error_close, len(np.unique(grid.keys[grid.mask])), len(rects))
            note = f"издали {grid.error:.4f} · в упор {grid.error_close:.4f} · {len(rects)} деталей"
            print(line)
            tiles_img.append((title, Image.open(io.BytesIO(imageproc.raw_png(grid))).convert("RGB"), note))
            continue
        if params is None:
            grid = base
            line = "%-38s %-10s %-10s %-9s %s" % (title, "—", "—", "—", "—")
            note = "исходник, приведённый к сетке"
        else:
            t0 = time.time()
            grid = imageproc.build_grid(raw, width=WIDTH, **params)
            rects = mesh.merge_rects(grid.keys, grid.mask)
            dt = time.time() - t0
            colors = int(len(np.unique(grid.keys[grid.mask]))) if grid.mask.any() else 0
            line = "%-38s %-10.4f %-10.4f %-9d %d" % (
                title, grid.error, grid.error_close, colors, len(rects))
            note = f"издали {grid.error:.4f} · в упор {grid.error_close:.4f} · {len(rects)} деталей · {dt:.2f} с"
        print(line)
        img = Image.open(io.BytesIO(imageproc.raw_png(grid))).convert("RGB")
        tiles_img.append((title, img, note))

    sheet(name, tiles_img)


def sheet(name: str, items: list[tuple[str, Image.Image, str]]) -> None:
    scale = 3
    w, h = items[0][1].size
    cw, ch = w * scale, h * scale
    cols = 3
    rows = (len(items) + cols - 1) // cols
    pad, head = 10, 40

    canvas = Image.new("RGB", (cols * (cw + pad) + pad, rows * (ch + head + pad) + pad), (16, 16, 16))
    draw = ImageDraw.Draw(canvas)
    f1, f2 = font(15), font(12)

    for i, (title, img, note) in enumerate(items):
        x = pad + (i % cols) * (cw + pad)
        y = pad + (i // cols) * (ch + head + pad)
        canvas.paste(img.resize((cw, ch), Image.Resampling.NEAREST), (x, y + head))
        draw.text((x, y + 3), title, font=f1, fill=(248, 168, 8))
        draw.text((x, y + 22), note, font=f2, fill=(150, 150, 150))

    out = os.path.join(os.environ.get("TEMP", "."), f"sm_pixel_quality_{name[:40]}.png")
    canvas.save(out)
    print("лист сравнения:", out)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--nvidia" in sys.argv:
        picks = []
        for folder in ("Zenless Zone Zero", "Need for Speed Unbound", "Scrap Mechanic", "Teardown"):
            found = sorted(glob.glob(os.path.join(NVIDIA, folder, "*.png")), key=os.path.getsize, reverse=True)
            if found:
                picks.append(found[0])
        args = picks

    if not args:
        print(__doc__)
        return 1
    for path in args:
        if os.path.isfile(path):
            run(path)
        else:
            print("нет файла:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
