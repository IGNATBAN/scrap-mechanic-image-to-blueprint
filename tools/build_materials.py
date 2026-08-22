"""Собрать таблицу «как блок тонирует краску»: py tools/build_materials.py

Зачем. В Scrap Mechanic краска красит блок, а текстура блока даёт рисунок и
собственную яркость. Один и тот же цвет на пластиковой панели, картоне и
металле выглядит по-разному. Если разрешить конвертеру ставить разные блоки,
палитра из 40 цветов превращается в несколько сотен оттенков — как раз тех
приглушённых и тёмных, которых в палитре краскопульта нет.

Как устроена текстура. Файл из поля "dif" в .shapeset — это НЕ альбедо, а
наложение поверх краски: RGB задаёт собственный тон материала, альфа — силу
наложения. У бетона RGB чёрный, а альфа доходит до 34/255 — текстура просто
слегка притемняет краску грязью и швами. У картона альфа до 255 и коричневый
RGB — там краска почти не видна.

Значит цвет блока, покрашенного краской P:

    итог = P x (1 - alpha) + tint,   где tint = RGB x alpha

Обе величины считаются в линейном свете (усреднять sRGB нельзя).

Но одного числа на блок мало. Развёртка в игре идёт по телу постройки, а не
по каждому блоку: текстура растянута ровно на `tiling` блоков, и блок в
локальной позиции (x, z) показывает ячейку (x mod tiling, z mod tiling).
Поэтому кроме среднего пишется таблица ячеек tiling x tiling — свои alpha и
tint для каждой позиции. Проверено снимком из игры: у всех пятнадцати полей
калибровочной стены фаза совпала с локальными координатами без поправки.

Ось V текстуры смотрит вниз, а игровая Z — вверх, поэтому таблица ячеек
переворачивается по вертикали: строка 0 — самый низ постройки.

Ещё пишется spec — средняя сила отражения из канала A файла "asg". Блоки с
большим spec в игре отражают небо и краску почти не показывают: снимок
калибровки дал у blk_metal2 отклонение +82 из 255 от расчёта. Такие блоки
конвертер в набор не берёт (см. core/materials.py).

Результат кладётся в data/materials.json, чтобы не читать при каждом запуске
десятки TGA по 2048x2048.
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from core import paths  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "materials.json")

# Таблицу ячеек пишем не для всех: у полностью непрозрачных блоков краска не
# видна и в набор они всё равно не попадают, а tiling 32 раздул бы файл.
CELLS_ALPHA_LIMIT = 0.95
CELLS_TILING_LIMIT = 16


def resolve(path: str, game_dir: str) -> str | None:
    """$GAME_DATA/... и $SURVIVAL_DATA/... -> реальный путь."""
    if not path:
        return None
    for token, sub in (("$GAME_DATA", "Data"), ("$SURVIVAL_DATA", "Survival"), ("$CHALLENGE_DATA", "ChallengeData")):
        if path.startswith(token):
            real = os.path.join(game_dir, sub, path[len(token) + 1:].replace("/", os.sep))
            if os.path.isfile(real):
                return real
    return None


def _to_linear(arr: np.ndarray) -> np.ndarray:
    return np.where(arr <= 0.04045, arr / 12.92, ((arr + 0.055) / 1.055) ** 2.4)


def _fold(arr: np.ndarray, n: int) -> np.ndarray:
    """Свернуть текстуру в сетку n x n усреднением по ячейкам.

    Сторона почти всегда делится нацело: у большинства блоков на блок
    приходится ровно 128 текселей. Остальные случаи — карты-заглушки 4x4
    у каутиона и стекла — режутся по ближайшим целым границам.
    """
    if arr.ndim == 2:
        arr = arr[..., None]
    h, w, ch = arr.shape
    if h % n == 0 and w % n == 0:
        return arr.reshape(n, h // n, n, w // n, ch).mean(axis=(1, 3))
    ys = np.linspace(0, h, n + 1)
    xs = np.linspace(0, w, n + 1)
    out = np.empty((n, n, ch), dtype=np.float32)
    for j in range(n):
        y0 = min(int(ys[j]), h - 1)
        y1 = min(max(int(np.ceil(ys[j + 1])), y0 + 1), h)
        for i in range(n):
            x0 = min(int(xs[i]), w - 1)
            x1 = min(max(int(np.ceil(xs[i + 1])), x0 + 1), w)
            out[j, i] = arr[y0:y1, x0:x1].reshape(-1, ch).mean(0)
    return out


def read_texture(path: str) -> np.ndarray | None:
    try:
        img = Image.open(path).convert("RGBA")
    except (OSError, ValueError) as exc:
        print("  не прочитал", os.path.basename(path), exc)
        return None
    return np.asarray(img, dtype=np.float32) / 255.0


def overlay(dif_path: str, tiling: int):
    """Таблица ячеек и среднее по ней."""
    arr = read_texture(dif_path)
    if arr is None:
        return None
    n = max(1, int(tiling))
    alpha = arr[..., 3:4]
    tint = _to_linear(arr[..., :3]) * alpha
    cells_a = _fold(alpha, n)[::-1, :, 0]              # V вниз, Z вверх
    cells_t = _fold(tint, n)[::-1, :, :]
    # среднее берём из ячеек, чтобы оно ровно совпадало с их средним
    return cells_a, cells_t, float(cells_a.mean()), [float(v) for v in cells_t.reshape(-1, 3).mean(0)]


def specular(asg_path: str | None) -> float:
    """Средняя сила отражения — канал A файла asg."""
    if not asg_path:
        return 0.0
    arr = read_texture(asg_path)
    return float(arr[..., 3].mean()) if arr is not None else 0.0


def main() -> int:
    game = paths.find_game_dir()
    if not game:
        print("Игра не найдена — таблицу не собрать")
        return 1

    shapesets = [
        os.path.join(game, "Data", "Objects", "Database", "ShapeSets", "blocks.shapeset"),
        os.path.join(game, "Survival", "Objects", "Database", "ShapeSets", "blocks.shapeset"),
    ]

    entries: dict[str, dict] = {}
    for shapeset in shapesets:
        if not os.path.isfile(shapeset):
            continue
        with open(shapeset, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        for raw in data.get("blockList") or []:
            uuid, name = raw.get("uuid"), raw.get("name")
            if not uuid or uuid in entries:
                continue
            tiling = int(raw.get("tiling") or 8)
            dif = resolve(str(raw.get("dif") or ""), game)
            result = overlay(dif, tiling) if dif else None
            if result is None:
                continue
            cells_a, cells_t, alpha, tint = result
            entry = {
                "name": name,
                "alpha": round(alpha, 5),
                "tint": [round(v, 5) for v in tint],
                "tiling": tiling,
                "spec": round(specular(resolve(str(raw.get("asg") or ""), game)), 5),
                "glass": str(raw.get("physicsMaterial") or "").lower() == "glass",
            }
            if alpha <= CELLS_ALPHA_LIMIT and tiling <= CELLS_TILING_LIMIT:
                entry["cells"] = {
                    "n": tiling,
                    "a": [round(float(v), 5) for v in cells_a.reshape(-1)],
                    "t": [round(float(v), 5) for v in cells_t.reshape(-1)],
                }
            entries[uuid] = entry
            print("  %-26s наложение=%.3f отражение=%.3f ячеек=%s"
                  % (name, alpha, entry["spec"], tiling * tiling if "cells" in entry else "-"))

    if not entries:
        print("Ни одной текстуры прочитать не удалось")
        return 1

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"model": "final_linear = paint_linear * (1 - alpha) + tint",
                   "cellModel": "ячейка блока в позиции (x mod n, z mod n), строка 0 — низ",
                   "blocks": entries}, fh, ensure_ascii=False, indent=1)
    size = os.path.getsize(OUT) / 1024
    print(f"\nЗаписано {len(entries)} блоков в {OUT} ({size:.0f} КБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
