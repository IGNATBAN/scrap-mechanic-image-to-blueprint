"""Расширение палитры за счёт разных блоков.

Краскопульт даёт 40 цветов, и этого мало: в палитре нет ни приглушённых
тонов, ни тёмно-серых с оттенком. Но текстура блока накладывается поверх
краски, и один и тот же цвет на пластике, дереве и металле выглядит
по-разному. Модель наложения снята с файлов игры (см. tools/build_materials.py):

    итог_линейный = краска_линейная * (1 - alpha) + tint

где alpha — средняя сила наложения текстуры, tint — её собственный тон.
Проверенные значения: у стекла alpha = 0.00 (краска видна как есть),
у пластика 0.05, у бетона 0.06, у дерева-1 0.16, у дерева-2 0.36,
у металла-2 0.57, у «предупреждающего» 0.55. То есть блоки дают ту самую
недостающую тёмную и приглушённую часть палитры.

Это приближение среднего вида блока: настоящий рендер добавляет затенение
по нормалям и блики, а вблизи видна сама текстура. Поэтому режим включается
отдельно и всегда виден в предпросмотре.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import numpy as np

from . import quant

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "materials.json")

# Блоки, у которых краска почти не видна (alpha близка к 1) — в наборе бесполезны.
OPAQUE_LIMIT = 0.9

# Сетки и решётки: сквозные, картинку из них не собрать.
SKIP_NAMES = {"blk_metalnet", "blk_crossnet", "blk_tryponet", "blk_stripednet",
              "blk_squarenet", "blk_placeholderblock_sticky"}


@dataclass(frozen=True)
class Overlay:
    uuid: str
    name: str
    alpha: float
    tint: tuple[float, float, float]
    glass: bool


_overlays: dict[str, Overlay] = {}
_loaded = False


def load() -> dict[str, Overlay]:
    global _loaded
    if _loaded:
        return _overlays
    _loaded = True
    try:
        with open(DATA, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return _overlays
    for uuid, entry in (raw.get("blocks") or {}).items():
        tint = entry.get("tint") or [0.0, 0.0, 0.0]
        _overlays[uuid] = Overlay(
            uuid=uuid,
            name=str(entry.get("name") or ""),
            alpha=float(entry.get("alpha") or 0.0),
            tint=(float(tint[0]), float(tint[1]), float(tint[2])),
            glass=bool(entry.get("glass")),
        )
    return _overlays


def available() -> bool:
    return bool(load())


def apply(paint_rgb: np.ndarray, overlay: Overlay) -> np.ndarray:
    """Как будет выглядеть краска на этом блоке. (N,3) uint8 -> (N,3) uint8."""
    lin = quant.srgb_to_linear(paint_rgb)
    out = lin * (1.0 - overlay.alpha) + np.array(overlay.tint, dtype=np.float32)
    return quant.linear_to_srgb(out)


def usable_blocks(include_glass: bool = False) -> list[Overlay]:
    """Блоки, годные для расширения палитры, от самых «чистых» к плотным."""
    out = [
        o for o in load().values()
        if o.alpha <= OPAQUE_LIMIT and o.name not in SKIP_NAMES and (include_glass or not o.glass)
    ]
    return sorted(out, key=lambda o: (o.alpha, o.name))


def catalog(include_glass: bool = False) -> list[dict]:
    """Для интерфейса: насколько каждый блок гасит краску."""
    return [
        {"uuid": o.uuid, "name": o.name, "alpha": round(o.alpha, 3),
         "keeps": round(1 - o.alpha, 3), "glass": o.glass}
        for o in usable_blocks(include_glass)
    ]


def build_palette(
    paint_hex: list[str],
    base_block: str,
    extra_blocks: list[str] | None = None,
    *,
    dedupe: float = 0.012,
) -> quant.Palette:
    """Собрать набор материалов «цвет краски + блок».

    dedupe — минимальное расстояние в OKLab между соседями набора. Без него
    половина комбинаций дублирует друг друга и только замедляет подбор.
    """
    paints = np.array(
        [[int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)] for h in paint_hex],
        dtype=np.uint8,
    )

    overlays = load()
    chosen: list[Overlay] = []
    base = overlays.get(base_block)
    if base is not None:
        chosen.append(base)
    for uuid in extra_blocks or []:
        o = overlays.get(uuid)
        if o is not None and o.uuid != base_block and o.alpha <= OPAQUE_LIMIT:
            chosen.append(o)

    if not chosen:
        return quant.Palette(paints, list(paint_hex), [base_block] * len(paint_hex))

    colors: list[np.ndarray] = []
    paint_of: list[str] = []
    block_of: list[str] = []
    for overlay in chosen:                      # базовый блок идёт первым — он в приоритете
        shown = apply(paints, overlay)
        for i in range(len(paints)):
            colors.append(shown[i])
            paint_of.append(paint_hex[i])
            block_of.append(overlay.uuid)

    rgb = np.array(colors, dtype=np.uint8)
    if dedupe <= 0:
        return quant.Palette(rgb, paint_of, block_of)

    # жадно оставляем только заметно различающиеся цвета, приоритет — порядок выше
    lab = quant.to_oklab(rgb)
    keep: list[int] = []
    kept_lab = np.zeros((0, 3), dtype=np.float32)
    limit = dedupe ** 2
    for i in range(len(lab)):
        if kept_lab.shape[0]:
            if ((kept_lab - lab[i]) ** 2).sum(axis=1).min() < limit:
                continue
        keep.append(i)
        kept_lab = np.vstack([kept_lab, lab[i]])

    return quant.Palette(
        rgb[keep],
        [paint_of[i] for i in keep],
        [block_of[i] for i in keep],
    )
