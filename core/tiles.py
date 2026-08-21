"""Разбивка картинки на модули — отдельные чертежи, которые сваривают в игре.

Зачем: один чертёж на 60 000 деталей игра либо не загрузит, либо будет
загружать минуту и тормозить. Разрезав картинку на модули, каждый из которых
влезает в комфортные ~8 000 деталей, вы ставите их по очереди и соединяете
сварочным аппаратом.

Как режем: НЕ пересчитываем склейку заново для каждого куска, а разрезаем уже
готовые прямоугольники по границам модулей. Из-за этого сумма модулей
гарантированно даёт ровно ту же картинку, что и цельный чертёж, а число
деталей в каждом модуле известно заранее — без повторного счёта.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

import numpy as np

from .mesh import Rect

# Комфортный потолок на один модуль: до 10 000 деталей игра держит бодро.
TARGET_PARTS = 8000

# Жёстких пределов нет: модулей может быть хоть тысячи. Ограничение одно и
# естественное — модуль не может быть меньше одного блока. Эти числа влияют
# только на то, сколько вариантов показать в списке.
MAX_SIDE = 4096
MAX_MODULES = 1 << 20
LIST_LIMIT = 48        # столько вариантов показываем в выпадающем списке


@dataclass
class Tile:
    col: int                       # 0..cols-1, слева направо
    row: int                       # 0..rows-1, сверху вниз (как на картинке)
    order: int                     # порядок сборки: 1 — ставится первым (нижний ряд)
    x0: int
    y0: int
    width: int
    height: int
    rects: list[Rect] = field(default_factory=list)

    @property
    def parts(self) -> int:
        return len(self.rects)

    def label(self, rows: int, cols: int = 1) -> str:
        """Метка модуля «ряд-столбец»: ряды снизу, столбцы слева.

        При десяти и более модулях по стороне номера дополняются нулём,
        иначе список чертежей в игре отсортируется как 1, 10, 2.
        """
        pad = 2 if max(rows, cols) >= 10 else 1
        return f"{rows - self.row:0{pad}d}-{self.col + 1:0{pad}d}"

    def as_dict(self, rows: int, cols: int) -> dict:
        return {
            "x0": self.x0, "y0": self.y0,
            "width": self.width, "height": self.height,
            "label": self.label(rows, cols),
            "parts": self.parts,
            "order": self.order,
        }


def edges(total: int, parts: int) -> list[int]:
    """Границы модулей — куски максимально равные."""
    parts = max(1, min(int(parts), int(total)))
    return [round(i * total / parts) for i in range(parts + 1)]


def edges_by_size(total: int, size: int) -> list[int]:
    """Границы под заданный размер модуля в блоках (последний может быть короче)."""
    size = max(1, min(int(size), int(total)))
    out = list(range(0, total, size))
    out.append(total)
    return out


def clean_edges(total: int, raw, fallback: list[int]) -> list[int]:
    """Проверить границы, пришедшие из интерфейса (их можно тянуть мышью)."""
    if not raw:
        return fallback
    try:
        vals = sorted({max(0, min(int(total), int(v))) for v in raw})
    except (TypeError, ValueError):
        return fallback
    vals = [v for v in vals if 0 < v < total]
    out = [0] + vals + [total]
    # соседние границы не должны совпадать
    return [v for i, v in enumerate(out) if i == 0 or v > out[i - 1]]


def cut(
    rects: list[Rect],
    grid_w: int,
    grid_h: int,
    cols: int,
    rows: int,
    edges_x: list[int] | None = None,
    edges_y: list[int] | None = None,
) -> list[Tile]:
    """Разрезать прямоугольники по сетке модулей. Координаты внутри модуля — свои.

    edges_x / edges_y позволяют задать неравные модули — например, чтобы шов
    не проходил по лицу.
    """
    ex = list(edges_x) if edges_x else edges(grid_w, cols)
    ey = list(edges_y) if edges_y else edges(grid_h, rows)
    cols, rows = len(ex) - 1, len(ey) - 1

    grid = [
        [
            Tile(
                col=c, row=r, order=(rows - r - 1) * cols + c + 1,
                x0=ex[c], y0=ey[r], width=ex[c + 1] - ex[c], height=ey[r + 1] - ey[r],
            )
            for c in range(cols)
        ]
        for r in range(rows)
    ]

    for x, y, w, h, color in rects:
        c0 = bisect_right(ex, x) - 1
        c1 = bisect_right(ex, x + w - 1) - 1
        r0 = bisect_right(ey, y) - 1
        r1 = bisect_right(ey, y + h - 1) - 1
        for r in range(r0, r1 + 1):
            ty0, ty1 = max(y, ey[r]), min(y + h, ey[r + 1])
            for c in range(c0, c1 + 1):
                tx0, tx1 = max(x, ex[c]), min(x + w, ex[c + 1])
                grid[r][c].rects.append((tx0 - ex[c], ty0 - ey[r], tx1 - tx0, ty1 - ty0, color))

    return [t for row in grid for t in row]


def _as_arrays(rects: list[Rect]):
    if not rects:
        z = np.zeros(0, dtype=np.int64)
        return z, z, z, z
    a = np.asarray(rects, dtype=np.int64)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def count(rects, grid_w: int, grid_h: int, cols: int, rows: int, arrays=None,
          edges_x=None, edges_y=None) -> tuple[np.ndarray, int]:
    """Сколько деталей окажется в каждом модуле — без фактического разрезания."""
    x, y, w, h = arrays if arrays is not None else _as_arrays(rects)
    ex = np.array(edges_x if edges_x else edges(grid_w, cols), dtype=np.int64)
    ey = np.array(edges_y if edges_y else edges(grid_h, rows), dtype=np.int64)
    cols, rows = len(ex) - 1, len(ey) - 1

    if x.size == 0:
        return np.zeros((rows, cols), dtype=np.int64), 0

    c0 = np.searchsorted(ex, x, "right") - 1
    c1 = np.searchsorted(ex, x + w - 1, "right") - 1
    r0 = np.searchsorted(ey, y, "right") - 1
    r1 = np.searchsorted(ey, y + h - 1, "right") - 1
    np.clip(c0, 0, cols - 1, out=c0)
    np.clip(c1, 0, cols - 1, out=c1)
    np.clip(r0, 0, rows - 1, out=r0)
    np.clip(r1, 0, rows - 1, out=r1)

    # подавляющее большинство прямоугольников целиком внутри одного модуля
    simple = (c0 == c1) & (r0 == r1)
    # bincount, а не np.add.at: на миллионах деталей разница в разы
    flat = np.bincount(r0[simple] * cols + c0[simple], minlength=rows * cols).astype(np.int64)

    rest = np.flatnonzero(~simple)
    if rest.size:
        for i in rest:
            for r in range(r0[i], r1[i] + 1):
                flat[r * cols + c0[i]:r * cols + c1[i] + 1] += 1

    return flat.reshape(rows, cols), int(flat.sum())


def _square_layout(modules: int, grid_w: int, grid_h: int) -> tuple[int, int]:
    """Раскладка на заданное число модулей, при которой они ближе к квадрату."""
    modules = max(1, int(modules))
    cols = max(1, min(grid_w, round((modules * grid_w / max(1, grid_h)) ** 0.5)))
    rows = max(1, min(grid_h, -(-modules // cols)))
    return cols, rows


def _candidates(rects_count: int, grid_w: int, grid_h: int, target: int) -> set[tuple[int, int]]:
    """Разумный набор раскладок — вместо перебора миллионов вариантов.

    Мелкие разбивки перебираем полностью, крупные берём по геометрической
    шкале и добавляем ту, что вытекает прямо из потолка на модуль.
    """
    out: set[tuple[int, int]] = set()
    for cols in range(1, min(8, grid_w) + 1):
        for rows in range(1, min(8, grid_h) + 1):
            out.add((cols, rows))

    scale = [10, 12, 16, 20, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512,
             768, 1024, 2048, 4096, 8192, 16384]
    for modules in scale:
        out.add(_square_layout(modules, grid_w, grid_h))

    # сколько модулей нужно, если делить ровно по потолку
    if target > 0 and rects_count > target:
        need = -(-rects_count // target)
        for extra in (need, need + 1, int(need * 1.25) + 1, int(need * 1.6) + 1, need * 2):
            out.add(_square_layout(extra, grid_w, grid_h))

    return {(c, r) for c, r in out if 1 <= c <= grid_w and 1 <= r <= grid_h}


def plan(
    rects: list[Rect],
    grid_w: int,
    grid_h: int,
    target: int = TARGET_PARTS,
    max_side: int = MAX_SIDE,
    max_modules: int = MAX_MODULES,
) -> dict:
    """Посчитать варианты разбивки и выбрать рекомендуемый.

    Возвращает {"options": [...], "recommended": {cols, rows} или None}.
    Для каждого числа модулей берём ту раскладку, где модули получаются
    ближе всего к квадрату — такие удобнее ставить и сваривать.
    """
    arrays = _as_arrays(rects)
    best_by_count: dict[int, dict] = {}

    # Каждый вариант стоит один проход по всем деталям. На миллионах деталей
    # полный список вариантов считался бы полминуты, поэтому чем тяжелее
    # картинка, тем короче список — рекомендация от этого не страдает,
    # страдает только длина выпадающего меню.
    budget = max(6, min(48, int(48 * 200_000 / max(1, len(rects)))))
    candidates = sorted(_candidates(len(rects), grid_w, grid_h, target),
                        key=lambda cr: cr[0] * cr[1])
    if len(candidates) > budget:
        step = len(candidates) / budget
        keep = {candidates[min(len(candidates) - 1, int(i * step))] for i in range(budget)}
        need = -(-len(rects) // max(1, target)) if target else 1
        keep.add(_square_layout(need, grid_w, grid_h))
        keep.add((1, 1))
        candidates = sorted(keep, key=lambda cr: cr[0] * cr[1])

    for cols, rows in candidates:
        modules = cols * rows
        if modules > max_modules or cols > max_side or rows > max_side:
            continue
        tile_w, tile_h = grid_w / cols, grid_h / rows
        # штраф за вытянутость модуля
        skew = max(tile_w, tile_h) / max(1e-6, min(tile_w, tile_h))
        prev = best_by_count.get(modules)
        if prev and prev["_skew"] <= skew:
            continue
        best_by_count[modules] = {"cols": cols, "rows": rows, "_skew": skew}

    options = []
    for modules in sorted(best_by_count):
        cand = best_by_count[modules]
        counts, total = count(rects, grid_w, grid_h, cand["cols"], cand["rows"], arrays)
        ex, ey = edges(grid_w, cand["cols"]), edges(grid_h, cand["rows"])
        options.append(
            {
                "cols": cand["cols"],
                "rows": cand["rows"],
                "modules": int(counts.size),
                "maxParts": int(counts.max()),
                "minParts": int(counts.min()),
                "totalParts": total,
                "tileWidth": max(ex[i + 1] - ex[i] for i in range(len(ex) - 1)),
                "tileHeight": max(ey[i + 1] - ey[i] for i in range(len(ey) - 1)),
                "overhead": total - len(rects),
                "skew": round(cand["_skew"], 2),
            }
        )

    # Из подходящих вариантов берём не просто самый малочисленный: длинная
    # полоса 43x195 формально даёт меньше модулей, чем сетка 4x2, но ставить
    # и сваривать её заметно неудобнее. Поэтому вытянутость модуля штрафуется.
    fits = [o for o in options if o["maxParts"] <= target]
    if fits:
        recommended = min(fits, key=lambda o: o["modules"] * (1 + 0.35 * (o["skew"] - 1)))
    elif options:
        recommended = min(options, key=lambda o: o["maxParts"])
    else:
        recommended = None

    # список не должен разрастаться: оставляем начало шкалы, рекомендацию и
    # прореженный хвост, иначе при тысячах модулей выпадающий список бесполезен
    if len(options) > LIST_LIMIT:
        keep = {id(o) for o in options[:LIST_LIMIT // 2]}
        tail = options[LIST_LIMIT // 2:]
        step = max(1, len(tail) // (LIST_LIMIT // 2))
        keep |= {id(o) for o in tail[::step]}
        if recommended:
            keep.add(id(recommended))
        options = [o for o in options if id(o) in keep]

    return {
        "options": options,
        "target": target,
        "recommended": {"cols": recommended["cols"], "rows": recommended["rows"]} if recommended else None,
        "wholeParts": len(rects),
    }


def instructions(name: str, cols: int, rows: int, counts: list[int], orientation: str) -> str:
    """Памятка по сборке — на основе того, что пишет сама игра о сварке."""
    total = sum(counts)
    vertical = orientation != "horizontal"
    step3 = (
        "3. Ставьте соседние модули вплотную, ряд за рядом: сначала весь нижний\r\n"
        "   ряд слева направо, потом следующий — и так вверх."
        if vertical
        else "3. Ставьте соседние модули вплотную, ряд за рядом: сначала ближний\r\n"
        "   ряд слева направо, потом следующий — и так от себя."
    )
    lines = [
        f"СБОРКА «{name}» — {cols}×{rows} = {cols * rows} модулей, {total} деталей всего",
        "",
        "Модули названы «ряд-столбец»: ряды считаются СНИЗУ, столбцы — слева направо.",
        f"1-1 — левый нижний угол картинки, {rows}-{cols} — правый верхний.",
        "",
        "ПОРЯДОК:",
        "1. Возьмите сварочный аппарат (Weld Tool).",
        "2. Поставьте подъёмник и разместите на нём модуль 1-1.",
        step3,
        "4. Соединяйте сварочным аппаратом: наведите на одну несоединённую конструкцию,",
        "   ЛКМ, затем на соседнюю — ЛКМ. Игра пишет об этом так:",
        "   «Также можно соединять соприкасающиеся детали, которые находятся на подъёмнике».",
        "   То есть на подъёмнике стыковка проще всего — собирайте там.",
        "",
        "РАЗМЕР МОДУЛЕЙ (деталей):",
    ]
    for r in range(rows, 0, -1):
        row_counts = counts[(rows - r) * cols:(rows - r + 1) * cols]
        lines.append("  ряд %d:  %s" % (r, "   ".join(f"{r}-{c + 1}: {n}" for c, n in enumerate(row_counts))))
    lines += [
        "",
        "Стыки ровные, внахлёст ставить не нужно — модули граничат встык.",
        "Если какой-то модуль не грузится — он слишком тяжёлый: пересоберите с",
        "большим числом модулей или меньшей шириной картинки.",
    ]
    return "\r\n".join(lines)
