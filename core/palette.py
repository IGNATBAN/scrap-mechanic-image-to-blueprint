"""Палитра краскопульта Scrap Mechanic.

Первоисточник — файл самой игры:
    Data/Render/PaintPalette/primary.paintpalette
Это JSON с массивом из 64 значений RRGGBBAA; реальных цветов 40,
остальные 24 слота — "00000000" (пустые, в интерфейсе не показываются).
Сетка в краскопульте — 10 столбцов x 4 строки: строка = уровень яркости,
столбец = оттенок (серый, жёлтый, лайм, зелёный, циан, синий, фиолетовый,
пурпурный, красный, оранжевый).

ВАЖНО: сам blueprint.json НЕ ограничен палитрой — игра рисует любой hex.
Проверено на файлах игры: в её собственных чертежах 229 разных цветов,
из них 195 вне палитры (напр. 606469 встречается 55 896 раз). Палитра —
ограничение интерфейса краскопульта, а не движка.

Подбор цвета и дизеринг живут в quant.py, набор материалов — в materials.py.
"""

from __future__ import annotations

import json
import os

import numpy as np

from . import quant

PALETTE_HEX: list[str] = [
    "EEEEEE", "F5F071", "CBF66F", "68FF88", "7EEDED", "4C6FE3", "AE79F0", "EE7BF0", "F06767", "EEAF5C",
    "7F7F7F", "E2DB13", "A0EA00", "19E753", "2CE6E6", "0A3EE2", "7514ED", "CF11D2", "D02525", "DF7F00",
    "4A4A4A", "817C00", "577D07", "0E8031", "118787", "0F2E91", "500AA6", "720A74", "7C0000", "673B00",
    "222222", "323000", "375000", "064023", "0A4444", "0A1D5A", "35086C", "520653", "560202", "472800",
]

PALETTE_COLS = 10
PALETTE_ROWS = 4

_cache: dict[str, object] = {}


def load_from_game(game_dir: str | None) -> bool:
    """Перечитать палитру из установленной игры (учтёт моды и патчи)."""
    if not game_dir:
        return False
    path = os.path.join(game_dir, "Data", "Render", "PaintPalette", "primary.paintpalette")
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8-sig") as fh:
            colors = json.load(fh).get("colors") or []
    except (OSError, ValueError):
        return False

    real = [c[:6].upper() for c in colors if isinstance(c, str) and c[:8].lower() != "00000000"]
    if len(real) < 8:
        return False
    PALETTE_HEX[:] = real
    _cache.clear()
    return True


def palette_rgb() -> np.ndarray:
    """(N, 3) uint8 — палитра краскопульта в RGB."""
    if "rgb" not in _cache:
        _cache["rgb"] = np.array(
            [[int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)] for h in PALETTE_HEX],
            dtype=np.uint8,
        )
    return _cache["rgb"]


def base_palette() -> quant.Palette:
    """Набор материалов из одной только краски, без учёта блоков."""
    if "pal" not in _cache:
        _cache["pal"] = quant.Palette(palette_rgb(), list(PALETTE_HEX))
    return _cache["pal"]


def to_oklab(rgb: np.ndarray) -> np.ndarray:
    return quant.to_oklab(rgb)


def nearest_indices(rgb: np.ndarray) -> np.ndarray:
    """Индексы ближайших цветов палитры для картинки (H, W, 3)."""
    return base_palette().nearest(quant.to_oklab(rgb))


def quantize(rgb: np.ndarray, dither: str = "none", **kwargs) -> np.ndarray:
    """Быстрый путь «покрасить в палитру» — вернуть RGB, а не индексы."""
    method = {"floyd": "fs", "none": "none", "bayer": "bayer"}.get(dither, dither)
    pal = base_palette()
    idx = quant.quantize(rgb, pal, method, **kwargs)
    return pal.rgb[idx]


def swatches() -> list[dict]:
    """Палитра для отрисовки в интерфейсе."""
    return [
        {"hex": h, "row": i // PALETTE_COLS, "col": i % PALETTE_COLS}
        for i, h in enumerate(PALETTE_HEX)
    ]
