"""Слияние одноцветных пикселей в масштабированные блоки.

Scrap Mechanic умеет хранить блок растянутым: поле "bounds" в чертеже.
В собственных чертежах игры встречаются блоки с bounds до 892 — то есть
ограничения практически нет, и «одна деталь на пиксель» это чистая потеря.

Здесь — жадное разбиение на прямоугольники (greedy meshing): идём слева
направо и сверху вниз, из каждой свободной клетки тянем максимально
широкую полосу одного цвета, затем опускаем её вниз, пока строки под ней
совпадают. Картинка получается пиксель-в-пиксель та же, а деталей
становится в разы меньше.

Ни один из существующих конвертеров такого не делает: два веб-конвертера
пишут ровно 1x1x1 на пиксель, а единственный, который что-то склеивает
(SMBIC), склеивает только горизонтальные отрезки внутри одной строки.
"""

from __future__ import annotations

import numpy as np

# Прямоугольник: (x, y, ширина, высота, цвет как 0xRRGGBB)
Rect = tuple[int, int, int, int, int]

MAX_BOUND_DEFAULT = 255


def color_keys(rgb: np.ndarray) -> np.ndarray:
    """(H, W, 3) uint8 -> (H, W) uint32, один цвет = одно число."""
    a = rgb.astype(np.uint32)
    return (a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]


def as_keys(grid: np.ndarray) -> np.ndarray:
    """Принять либо готовые ключи (H, W), либо картинку (H, W, 3)."""
    arr = np.asarray(grid)
    return color_keys(arr) if arr.ndim == 3 else arr


def split_rects(keys: np.ndarray, mask: np.ndarray) -> list[Rect]:
    """Без слияния: каждая клетка — отдельный блок 1x1x1."""
    keys = as_keys(keys)
    ys, xs = np.nonzero(mask)
    vals = keys[ys, xs]
    return [(int(x), int(y), 1, 1, int(c)) for x, y, c in zip(xs, ys, vals)]


def merge_rects(keys: np.ndarray, mask: np.ndarray, max_bound: int = MAX_BOUND_DEFAULT) -> list[Rect]:
    """Жадное разбиение сетки на одинаковые по материалу прямоугольники.

    keys — целочисленный «что за блок и какого цвета» для каждой клетки.
    Соседи склеиваются только если ключ совпал полностью, поэтому режим с
    разными блоками работает тем же кодом, что и одноцветный.
    """
    key = np.ascontiguousarray(as_keys(keys), dtype=np.int64)
    h, w = key.shape
    max_bound = max(1, int(max_bound))
    if max_bound == 1:
        return split_rects(key, mask)

    # same_down[y, x] — клетка снизу того же цвета и тоже не пустая.
    if h > 1:
        same_down = mask[:-1] & mask[1:] & (key[:-1] == key[1:])
        # префиксные суммы -> проверка «вся полоса совпадает» за две операции
        csd = np.zeros((h - 1, w + 1), dtype=np.int32)
        np.cumsum(same_down, axis=1, out=csd[:, 1:], dtype=np.int32)
    else:
        csd = None

    rects: list[Rect] = []
    # Клетка занята <=> она попала в уже созданный прямоугольник. Так как
    # прямоугольники растут только вниз и мы идём сверху вниз, свободная
    # клетка гарантирует, что всё под ней в этом столбце тоже свободно.
    taken_until = np.zeros(w, dtype=np.int32)  # первая строка, с которой столбец снова свободен

    for y in range(h):
        row = key[y]
        rmask = mask[y]
        # границы одноцветных отрезков в строке — считаем один раз на строку
        change = np.empty(w, dtype=bool)
        change[0] = True
        np.not_equal(row[1:], row[:-1], out=change[1:])
        change[1:] |= rmask[1:] != rmask[:-1]
        starts = np.flatnonzero(change)
        ends = np.empty_like(starts)
        ends[:-1] = starts[1:]
        ends[-1] = w

        for run_start, run_end in zip(starts, ends):
            if not rmask[run_start]:
                continue
            x = int(run_start)
            stop = int(run_end)
            while x < stop:
                if taken_until[x] > y:
                    x += 1
                    continue
                seg_end = stop
                # столбцы правее могут быть заняты «свисающим» прямоугольником
                limit = min(seg_end, x + max_bound)
                width = 1
                while x + width < limit and taken_until[x + width] <= y:
                    width += 1

                height = 1
                if csd is not None:
                    max_h = min(h - y, max_bound)
                    while height < max_h:
                        yy = y + height - 1
                        if csd[yy, x + width] - csd[yy, x] != width:
                            break
                        height += 1

                rects.append((x, y, width, height, int(row[x])))
                taken_until[x:x + width] = y + height
                x += width

    return rects


def stats(rects: list[Rect]) -> dict:
    """Сводка для интерфейса."""
    if not rects:
        return {"parts": 0, "cells": 0, "colors": 0, "biggest": 0, "merged_ratio": 0.0}
    cells = sum(r[2] * r[3] for r in rects)
    colors = len({r[4] for r in rects})
    biggest = max(r[2] * r[3] for r in rects)
    return {
        "parts": len(rects),
        "cells": cells,
        "colors": colors,
        "biggest": biggest,
        "merged_ratio": round(1 - len(rects) / cells, 4) if cells else 0.0,
    }
