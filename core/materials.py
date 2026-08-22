"""Расширение палитры за счёт разных блоков.

Краскопульт даёт 40 цветов, и этого мало: в палитре нет ни приглушённых
тонов, ни тёмно-серых с оттенком. Но текстура блока накладывается поверх
краски, и один и тот же цвет на пластике, дереве и металле выглядит
по-разному. Модель наложения снята с файлов игры (см. tools/build_materials.py):

    итог_линейный = краска_линейная * (1 - alpha) + tint

где alpha — сила наложения текстуры, tint — её собственный тон.
Проверенные значения: у стекла alpha = 0.00 (краска видна как есть),
у пластика 0.05, у бетона 0.06, у дерева-1 0.16, у дерева-2 0.36,
у металла-2 0.57, у «предупреждающего» 0.55.

Одного числа на блок мало
-------------------------
Развёртка в игре идёт по телу постройки, а не по каждому блоку: текстура
растянута ровно на `tiling` блоков, и блок в локальной позиции (x, z)
показывает СВОЮ ячейку (x mod tiling, z mod tiling). Значит стена из
одинаковых блоков одной краски — не ровная заливка, а повторяющийся узор.

Проверено в игре: собран чертёж из пятнадцати полей 16x16 с разными
смещениями, снимок разобран машинно — фаза у всех пятнадцати совпала с
локальными координатами без поправки. Заодно выяснилось, что склейка
блоков в прямоугольники узор не меняет: поле, собранное по блоку,
строками и одной деталью, выглядит одинаково.

Поэтому у каждого годного блока есть таблица ячеек: alpha и tint на
каждую позицию. Строка 0 — низ постройки.

Блеск
-----
Канал A файла asg — сила отражения. Блоки с большим значением отражают
небо и краску почти не показывают. Замерено по снимку из игры: у
blk_concrete1 (отражение 0.08) расчёт сходится с точностью 4 из 255, у
blk_metal2 (0.50) он ошибается на +82 из 255 — в игре блок бледный и
почти одинаковый при любой краске. Поэтому блестящие блоки в набор
автоматически не берутся.
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

# Граница по отражению. Замерены две точки: 0.08 сходится, 0.50 врёт на
# +82 из 255. Между ними замеров нет, порог выбран по разрыву в самих
# данных — выше него только три зеркальных металла и стекло.
SHINY_LIMIT = 0.30

# Сетки и решётки: сквозные, картинку из них не собрать.
SKIP_NAMES = {"blk_metalnet", "blk_crossnet", "blk_tryponet", "blk_stripednet",
              "blk_squarenet", "blk_placeholderblock_sticky"}

# Размах узора, начиная с которого блок стоит показывать с предупреждением:
# ровной заливки из него не выйдет. В единицах 0..255 по светлоте.
NOISY_SPAN = 12.0

_LUM = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


@dataclass(frozen=True)
class Overlay:
    uuid: str
    name: str
    alpha: float
    tint: tuple[float, float, float]
    glass: bool
    tiling: int = 8
    spec: float = 0.0
    cells: np.ndarray | None = None      # (n, n, 4): alpha, tint r, g, b

    @property
    def n(self) -> int:
        return int(self.cells.shape[0]) if self.cells is not None else 1

    @property
    def span(self) -> float:
        """Насколько блок пятнистый: разброс светлоты по позициям, 0..255.

        Мерить на одной краске мало: текстура, которая гасит, ярче всего
        видна на белой, а текстура со светлым собственным тоном (гипсокартон)
        на белой пропадает и вылезает на тёмной. Поэтому берём худший случай
        из трёх проб. Светлота — по sRGB: пятнистость это про глаз, а не
        про физику.
        """
        if self.cells is None:
            return 0.0
        probes = np.array([[238, 238, 238], [127, 127, 127], [34, 34, 34]], dtype=np.uint8)
        shown = apply_cells(probes, self).astype(np.float32) / 255.0   # (n, n, 3, 3)
        lum = shown @ _LUM                                             # (n, n, 3)
        flat = lum.reshape(-1, probes.shape[0])
        return float((flat.max(axis=0) - flat.min(axis=0)).max() * 255.0)


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
        cells = None
        block_cells = entry.get("cells")
        if block_cells:
            n = int(block_cells.get("n") or 1)
            a = np.asarray(block_cells.get("a") or [], dtype=np.float32).reshape(n, n, 1)
            t = np.asarray(block_cells.get("t") or [], dtype=np.float32).reshape(n, n, 3)
            cells = np.concatenate([a, t], axis=2)
        _overlays[uuid] = Overlay(
            uuid=uuid,
            name=str(entry.get("name") or ""),
            alpha=float(entry.get("alpha") or 0.0),
            tint=(float(tint[0]), float(tint[1]), float(tint[2])),
            glass=bool(entry.get("glass")),
            tiling=int(entry.get("tiling") or 8),
            spec=float(entry.get("spec") or 0.0),
            cells=cells,
        )
    return _overlays


def available() -> bool:
    return bool(load())


def apply(paint_rgb: np.ndarray, overlay: Overlay) -> np.ndarray:
    """Как будет выглядеть краска на этом блоке. (N,3) uint8 -> (N,3) uint8."""
    lin = quant.srgb_to_linear(paint_rgb)
    out = lin * (1.0 - overlay.alpha) + np.array(overlay.tint, dtype=np.float32)
    return quant.linear_to_srgb(out)


def apply_cells(paint_rgb: np.ndarray, overlay: Overlay) -> np.ndarray:
    """Цвет краски в каждой позиции блока. (N,3) uint8 -> (n, n, N, 3) uint8."""
    if overlay.cells is None:
        shown = apply(paint_rgb, overlay)
        return shown.reshape(1, 1, -1, 3)
    lin = quant.srgb_to_linear(paint_rgb)                       # (N, 3)
    alpha = overlay.cells[..., 0:1][:, :, None, :]              # (n, n, 1, 1)
    tint = overlay.cells[..., 1:4][:, :, None, :]               # (n, n, 1, 3)
    return quant.linear_to_srgb(lin[None, None, :, :] * (1.0 - alpha) + tint)


def usable_blocks(include_glass: bool = False, include_shiny: bool = False) -> list[Overlay]:
    """Блоки, годные для расширения палитры, от самых «чистых» к плотным."""
    out = [
        o for o in load().values()
        if o.alpha <= OPAQUE_LIMIT
        and o.name not in SKIP_NAMES
        and (include_glass or not o.glass)
        and (include_shiny or o.spec <= SHINY_LIMIT)
    ]
    return sorted(out, key=lambda o: (o.alpha, o.name))


def catalog(include_glass: bool = False, include_shiny: bool = True) -> list[dict]:
    """Для интерфейса: чем каждый блок хорош и чем плох."""
    return [
        {"uuid": o.uuid, "name": o.name, "alpha": round(o.alpha, 3),
         "keeps": round(1 - o.alpha, 3), "glass": o.glass,
         "spec": round(o.spec, 3), "shiny": o.spec > SHINY_LIMIT,
         "span": round(o.span, 1), "noisy": o.span >= NOISY_SPAN}
        for o in usable_blocks(include_glass, include_shiny)
    ]


def _lcm(a: int, b: int) -> int:
    x, y = a, b
    while y:
        x, y = y, x % y
    return a * b // x if x else 1


def build_palette(
    paint_hex: list[str],
    base_block: str,
    extra_blocks: list[str] | None = None,
    *,
    dedupe: float = 0.012,
    with_cells: bool = True,
) -> quant.Palette:
    """Собрать набор материалов «цвет краски + блок».

    dedupe — минимальное расстояние в OKLab между соседями набора. Без него
    половина комбинаций дублирует друг друга и только замедляет подбор.

    with_cells — приложить таблицу «как этот вариант выглядит в каждой
    позиции». Она нужна и подбору, и предпросмотру: без неё конвертер
    рисует ровные заливки там, где в игре будет узор.
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
    keep = list(range(len(rgb)))
    if dedupe > 0:
        # жадно оставляем только заметно различающиеся цвета, приоритет — порядок выше
        lab = quant.to_oklab(rgb)
        keep = []
        kept_lab = np.zeros((0, 3), dtype=np.float32)
        limit = dedupe ** 2
        for i in range(len(lab)):
            if kept_lab.shape[0]:
                if ((kept_lab - lab[i]) ** 2).sum(axis=1).min() < limit:
                    continue
            keep.append(i)
            kept_lab = np.vstack([kept_lab, lab[i]])

    cells = None
    period = 1
    if with_cells:
        period = 1
        for overlay in chosen:
            period = _lcm(period, max(1, overlay.n))
        full = np.zeros((period, period, len(rgb), 3), dtype=np.uint8)
        for j, overlay in enumerate(chosen):
            block = apply_cells(paints, overlay)                  # (n, n, N, 3)
            reps = period // max(1, overlay.n)
            block = np.tile(block, (reps, reps, 1, 1))
            full[:, :, j * len(paints):(j + 1) * len(paints), :] = block
        cells = np.ascontiguousarray(full[:, :, keep, :])

    return quant.Palette(
        rgb[keep],
        [paint_of[i] for i in keep],
        [block_of[i] for i in keep],
        cells=cells,
        period=period,
    )
