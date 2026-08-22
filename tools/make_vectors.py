"""Эталонные тест-векторы: py tools/make_vectors.py

Зачем. Веб-версия считает то же самое на JavaScript. Две реализации
неизбежно разойдутся после первой же правки, если их не связать. Этот файл
фиксирует вход и ожидаемый выход для каждого слоя ядра, а проверяют его
обе стороны: `tools/verify.py` на Python и `web-static/tests/run.js` в браузере
и в CI. Разошлись — падает сборка, а не пользователь.

Граница фиксации выбрана осознанно. Декодирование PNG и масштабирование в
Pillow и в браузере дают разные байты, поэтому векторы начинаются НЕ с файла,
а с готовой сетки RGB. Всё, что ниже — подбор цвета, дизеринг, склейка,
дробление, текст чертежа — обязано совпадать бит в бит.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from core import blocks, blueprint, materials, mesh, palette, quant, tiles  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "vectors.json")

# Блоки для расширенной палитры берём фиксированным списком, а не «все подряд»:
# добавится новый блок в игре — векторы не должны молча поменяться.
EXTRA_BLOCKS = [
    "5f41af56-df4c-4837-9b3c-10781335757f",  # стекло, alpha 0.00
    "628b2d61-5ceb-43e9-8334-a4135566df7a",  # пластик, 0.05
    "a6c6ce30-dd47-4587-b475-085d55c6a3b4",  # бетон 1, 0.06
    "df953d9c-234f-4ac2-af5e-f0490b223e71",  # дерево 1, 0.16
    "1897ee42-0291-43e4-9645-8c5a5d310398",  # дерево 2, 0.36
    "1016cafc-9f6b-40c9-8713-9019d399783f",  # металл 2, 0.57
]


def digest(arr: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()[:16]


def make_image(w: int = 24, h: int = 16) -> np.ndarray:
    """Детерминированная картинка: градиент, чистые цвета и шум в одном кадре."""
    rng = np.random.default_rng(20260821)
    img = np.zeros((h, w, 3), dtype=np.uint8)
    xs = np.linspace(0, 255, w)
    ys = np.linspace(0, 255, h)
    img[..., 0] = xs[None, :]
    img[..., 1] = ys[:, None]
    img[..., 2] = 128
    img[:4, :6] = (255, 0, 0)
    img[:4, 6:12] = (0, 255, 0)
    img[:4, 12:18] = (0, 0, 255)
    img[:4, 18:] = (16, 16, 16)
    img[-4:] = rng.integers(0, 256, (4, w, 3), dtype=np.uint8)
    return img


def main() -> int:
    img = make_image()
    h, w = img.shape[:2]
    mask = np.ones((h, w), dtype=bool)
    mask[8:11, 3:7] = False           # дырка, чтобы проверить вырезание

    vectors: dict = {
        "note": "Эталон для Python и JavaScript. Создаётся tools/make_vectors.py",
        "image": {"width": w, "height": h,
                  "rgb": base64.b64encode(img.tobytes()).decode(),
                  "mask": base64.b64encode(np.packbits(mask).tobytes()).decode()},
        "paletteHex": list(palette.PALETTE_HEX),
        "extraBlocks": EXTRA_BLOCKS,
    }

    # 1. Цветовое пространство
    probe = np.array([[0, 0, 0], [255, 255, 255], [223, 127, 0], [16, 16, 16],
                      [1, 2, 3], [128, 64, 200]], dtype=np.uint8)
    lab = quant.to_oklab(probe.reshape(1, -1, 3))[0]
    vectors["oklab"] = {"rgb": probe.tolist(),
                        "lab": [[round(float(v), 6) for v in row] for row in lab]}

    # 2. Наложение текстуры блока на краску
    over = materials.load()
    vectors["materials"] = {
        uuid: {"alpha": round(over[uuid].alpha, 5),
               "tint": [round(v, 5) for v in over[uuid].tint],
               "onOrange": materials.apply(np.array([[223, 127, 0]], np.uint8), over[uuid])[0].tolist()}
        for uuid in EXTRA_BLOCKS if uuid in over
    }

    # 2б. Узор: таблица ячеек блока. Развёртка в игре идёт по телу постройки,
    # поэтому у блока не один оттенок, а tiling x tiling.
    vectors["cells"] = {
        uuid: {"n": over[uuid].n,
               "span": round(over[uuid].span, 3),
               "hash": digest(np.round(over[uuid].cells * 1e5).astype(np.int64)),
               "corner": [round(float(v), 5) for v in over[uuid].cells[0, 0]],
               "onOrange": materials.apply_cells(
                   np.array([[223, 127, 0]], np.uint8), over[uuid])[:, :, 0, :].reshape(-1, 3)[:6].tolist()}
        for uuid in EXTRA_BLOCKS if uuid in over and over[uuid].cells is not None
    }

    # 3. Наборы материалов
    base_pal = materials.build_palette(palette.PALETTE_HEX, blocks.DEFAULT_BLOCK)
    wide_pal = materials.build_palette(palette.PALETTE_HEX, blocks.DEFAULT_BLOCK, EXTRA_BLOCKS)
    vectors["palettes"] = {
        "base": {"size": len(base_pal), "rgbHash": digest(base_pal.rgb)},
        "wide": {"size": len(wide_pal), "rgbHash": digest(wide_pal.rgb),
                 "rgb": wide_pal.rgb.tolist(), "paint": wide_pal.paint, "block": wide_pal.block},
    }

    # 4. Квантование каждым способом
    vectors["quantize"] = {}
    for method in ("none", "fs", "jarvis", "stucki", "burkes", "sierra", "atkinson", "bayer", "bluenoise"):
        for lw in (1.0, 1.8):
            keys = quant.quantize(img, base_pal, method, strength=1.0, lum_weight=lw,
                                  serpentine=True, mask=mask)
            vectors["quantize"][f"{method}@{lw}"] = {
                "hash": digest(keys.astype(np.int32)),
                "unique": int(len(np.unique(keys[mask]))),
                "first": keys[0, :8].tolist(),
            }

    # 4б. Квантование с учётом узора и ненулевого начала координат.
    # Фаза берётся из локальных координат чертежа — сдвиг обязан совпадать
    # с веб-версией, иначе узор разъедется.
    vectors["quantizePattern"] = {}
    for origin in ((0, 0), (-12, 0), (5, 3)):
        for method in ("none", "fs", "bluenoise"):
            keys = quant.quantize(img, wide_pal, method, strength=1.0, lum_weight=1.0,
                                  serpentine=True, mask=mask, origin=origin)
            vectors["quantizePattern"][f"{method}@{origin[0]},{origin[1]}"] = {
                "hash": digest(keys.astype(np.int32)),
                "unique": int(len(np.unique(keys[mask]))),
                "first": keys[0, :8].tolist(),
                "shownHash": digest(wide_pal.shown(keys, origin)),
            }
    vectors["remap"] = {
        "period": wide_pal.period,
        "size": len(wide_pal),
        "hash": digest(wide_pal.remap(1.0)),
        "row0": wide_pal.remap(1.0)[0, 0, :12].tolist(),
        "cellsHash": digest(wide_pal.cells),
    }

    # 5. Склейка
    vectors["mesh"] = {}
    for name, keys_src in (("packed", (img[..., 0].astype(np.int32) << 16)
                            | (img[..., 1].astype(np.int32) << 8) | img[..., 2]),
                           ("palette", quant.quantize(img, base_pal, "none", mask=mask).astype(np.int32))):
        for bound in (255, 4):
            rects = mesh.merge_rects(keys_src, mask, bound)
            flat = np.array([[r[0], r[1], r[2], r[3], r[4]] for r in rects], dtype=np.int64)
            vectors["mesh"][f"{name}@{bound}"] = {
                "count": len(rects), "hash": digest(flat), "first": rects[0] if rects else None,
            }

    # 6. Дробление
    keys = quant.quantize(img, base_pal, "none", mask=mask).astype(np.int32)
    rects = mesh.merge_rects(keys, mask, 255)
    vectors["tiles"] = {}
    for cols, rows in ((1, 1), (2, 2), (3, 2), (5, 4)):
        cut = tiles.cut(rects, w, h, cols, rows)
        vectors["tiles"][f"{cols}x{rows}"] = {
            "modules": len(cut),
            "parts": [t.parts for t in sorted(cut, key=lambda t: t.order)],
            "labels": [t.label(rows, cols) for t in sorted(cut, key=lambda t: t.order)],
        }
    plan = tiles.plan(rects, w, h, target=40)
    vectors["plan"] = {"recommended": plan["recommended"],
                       "options": [{k: o[k] for k in ("cols", "rows", "modules", "maxParts", "totalParts")}
                                   for o in plan["options"][:8]]}

    # 7. Текст чертежа — здесь расхождение недопустимо совсем
    vectors["blueprint"] = {}
    for orient in ("vertical", "horizontal"):
        for depth in (1, 3):
            text = blueprint.build_json(rects, w, h, blueprint.rgb_resolver(blocks.DEFAULT_BLOCK),
                                        orient, True, depth)
            vectors["blueprint"][f"{orient}@{depth}"] = {
                "sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
                "length": len(text),
                "head": text[:220],
            }
    desc = blueprint.description_json("Тест", "11111111-2222-3333-4444-555555555555", "заметка")
    vectors["description"] = {"sha256": hashlib.sha256(desc.encode()).hexdigest()[:16], "text": desc}

    # 8. Масштабирование и коррекция — здесь Pillow и браузер расходятся легче
    #    всего, поэтому фиксируем и их. Источник отдельный: важно, чтобы
    #    размеры не делились нацело, иначе половина фильтров совпадёт случайно.
    from PIL import Image, ImageEnhance

    rng2 = np.random.default_rng(777)
    src = rng2.integers(0, 256, (23, 37, 3), dtype=np.uint8)
    src[5:9, 3:20] = (255, 40, 0)
    vectors["resizeSource"] = {"width": 37, "height": 23,
                               "rgb": base64.b64encode(src.tobytes()).decode()}
    vectors["resize"] = {}
    filters = {"nearest": Image.Resampling.NEAREST, "box": Image.Resampling.BOX,
               "bilinear": Image.Resampling.BILINEAR, "lanczos": Image.Resampling.LANCZOS}
    img_src = Image.fromarray(src, "RGB")
    for fname, f in filters.items():
        for size in ((12, 8), (60, 40), (37, 23), (19, 5)):
            out = np.array(img_src.resize(size, f), dtype=np.uint8)
            vectors["resize"][f"{fname}@{size[0]}x{size[1]}"] = {
                "hash": digest(out), "first": out[0, :4].tolist(),
            }

    vectors["adjust"] = {}
    small = Image.fromarray(src[:8, :12], "RGB")
    for label, (b, c, s, g) in {
        "gamma0.8": (1.0, 1.0, 1.0, 0.8),
        "bright1.3": (1.3, 1.0, 1.0, 1.0),
        "contrast1.4": (1.0, 1.4, 1.0, 1.0),
        "sat0.5": (1.0, 1.0, 0.5, 1.0),
        "all": (1.15, 1.25, 1.4, 0.9),
    }.items():
        arr = np.array(small, dtype=np.uint8)
        if g != 1.0:
            lut = np.clip(((np.arange(256) / 255.0) ** (1.0 / g)) * 255.0, 0, 255).astype(np.uint8)
            arr = lut[arr]
        im = Image.fromarray(arr, "RGB")
        if b != 1.0:
            im = ImageEnhance.Brightness(im).enhance(b)
        if c != 1.0:
            im = ImageEnhance.Contrast(im).enhance(c)
        if s != 1.0:
            im = ImageEnhance.Color(im).enhance(s)
        got = np.array(im, dtype=np.uint8)
        vectors["adjust"][label] = {"hash": digest(got), "first": got[0, :4].tolist()}
    vectors["adjustSource"] = {"width": 12, "height": 8,
                               "rgb": base64.b64encode(np.ascontiguousarray(src[:8, :12]).tobytes()).decode()}

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(vectors, fh, ensure_ascii=False, indent=1)

    size = os.path.getsize(OUT)
    print(f"Записано {OUT} — {size} байт")
    print(f"  квантование: {len(vectors['quantize'])} вариантов")
    print(f"  узор:        {len(vectors['cells'])} блоков, {len(vectors['quantizePattern'])} прогонов")
    print(f"  склейка:     {len(vectors['mesh'])} вариантов")
    print(f"  дробление:   {len(vectors['tiles'])} раскладок")
    print(f"  чертёж:      {len(vectors['blueprint'])} вариантов")
    print(f"  масштаб:     {len(vectors['resize'])} вариантов")
    print(f"  коррекция:   {len(vectors['adjust'])} вариантов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
