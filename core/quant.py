"""Квантование цвета: подбор ближайшего блока и дизеринг.

Почему старый вариант выглядел плохо. Палитра краскопульта — 40 цветов,
и они «кривые»: сетка 10 оттенков x 4 яркости, где нет ни приглушённых
тонов, ни тёмно-серых с оттенком. Дизеринг Флойда из Pillow работает в
sRGB, а сумма ошибок в sRGB не соответствует тому, что видит глаз: тени
уходят в грязь, а светлые участки — в кислотный цвет.

Что здесь сделано иначе:

* весь подбор и вся диффузия ошибки идут в **OKLab**, где евклидово
  расстояние близко к воспринимаемой разнице цвета;
* ось яркости можно взвесить отдельно (`lum_weight`) — глаз прощает сдвиг
  оттенка, но не прощает сдвиг светлоты, и при весе 1.5-2 картинка
  становится заметно «честнее»;
* диффузия ошибки идёт **змейкой** (нечётные строки справа налево), что
  убирает характерные диагональные «червячки» Флойда;
* кроме Флойда есть ядра Джарвиса, Стакки, Аткинсона, Сьерры и Бёркса —
  они распределяют ошибку шире и на сложных фото дают меньше шума;
* вместо решётки Байера доступен **синий шум**: та же упорядоченная маска,
  но без регулярного узора, который в блоках читается как клетка;
* сила дизеринга регулируется — на 0.6-0.8 шума заметно меньше, а
  полутона ещё держатся.

Ближайший цвет ищется через cKDTree, а для последовательных проходов
(диффузия ошибки — она по своей природе пиксель за пикселем) заранее
строится таблица подстановки по сетке OKLab: тогда внутренний цикл
делает одно обращение по индексу вместо перебора всей палитры.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

try:
    from scipy.spatial import cKDTree
except ImportError:  # без scipy обойдёмся перебором
    cKDTree = None

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# ── цветовое пространство ────────────────────────────────────────────────────


def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    c = np.asarray(rgb, dtype=np.float32) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(lin: np.ndarray) -> np.ndarray:
    lin = np.clip(lin, 0.0, 1.0)
    out = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055)
    return np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)


_M1 = np.array(
    [
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005],
    ],
    dtype=np.float32,
)
_M2 = np.array(
    [
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660],
    ],
    dtype=np.float32,
)
_M2_INV = np.linalg.inv(_M2).astype(np.float32)
_M1_INV = np.linalg.inv(_M1).astype(np.float32)


def linear_to_oklab(lin: np.ndarray) -> np.ndarray:
    lms = lin @ _M1.T
    return np.cbrt(np.maximum(lms, 0.0)) @ _M2.T


def oklab_to_linear(lab: np.ndarray) -> np.ndarray:
    lms = lab @ _M2_INV.T
    return (lms ** 3) @ _M1_INV.T


def to_oklab(rgb: np.ndarray) -> np.ndarray:
    """sRGB uint8 (..., 3) -> OKLab float32."""
    return linear_to_oklab(srgb_to_linear(rgb)).astype(np.float32)


def oklab_to_srgb(lab: np.ndarray) -> np.ndarray:
    return linear_to_srgb(oklab_to_linear(lab.astype(np.float32)))


# ── палитра ──────────────────────────────────────────────────────────────────


@dataclass
class Palette:
    """Набор доступных «материалов»: цвет + чем он получается в игре.

    `rgb` — средний цвет варианта. Но текстура блока растянута на `tiling`
    блоков, поэтому один и тот же вариант в разных местах постройки
    выглядит по-разному. `cells` хранит настоящий цвет каждого варианта в
    каждой позиции: (P, P, N, 3), где P — общий период набора в блоках.
    Позиция берётся по ЛОКАЛЬНЫМ координатам чертежа, а не по клетке
    картинки, — проверено снимком из игры.
    """

    rgb: np.ndarray                      # (N, 3) uint8 — средний цвет
    paint: list[str] = field(default_factory=list)   # hex краски
    block: list[str] = field(default_factory=list)   # uuid блока
    cells: np.ndarray | None = None      # (P, P, N, 3) uint8 — цвет по позициям
    period: int = 1                      # P

    def __post_init__(self) -> None:
        self.rgb = np.asarray(self.rgb, dtype=np.uint8).reshape(-1, 3)
        if not self.paint:
            self.paint = ["%02X%02X%02X" % tuple(c) for c in self.rgb]
        if not self.block:
            self.block = [""] * len(self.rgb)
        self._lab = to_oklab(self.rgb)
        self._trees: dict[float, object] = {}
        self._luts: dict[tuple, np.ndarray] = {}
        self._cell_trees: dict[float, list] = {}
        self._cell_lab: np.ndarray | None = None
        self._remaps: dict[float, np.ndarray] = {}
        self.period = max(1, int(self.period))
        if self.cells is not None:
            self.cells = np.ascontiguousarray(self.cells, dtype=np.uint8)
            if self.cells.shape != (self.period, self.period, len(self.rgb), 3):
                self.cells = None
                self.period = 1

    def __len__(self) -> int:
        return len(self.rgb)

    # ── узор: цвет варианта в конкретной позиции ─────────────────────────

    @property
    def patterned(self) -> bool:
        """Есть ли вообще разница между позициями."""
        return self.cells is not None and self.period > 1

    def cell_lab(self) -> np.ndarray:
        """(P, P, N, 3) в OKLab. Считается один раз."""
        if self._cell_lab is None:
            src = self.cells if self.cells is not None else self.rgb.reshape(1, 1, -1, 3)
            self._cell_lab = to_oklab(src.reshape(-1, 3)).reshape(src.shape)
        return self._cell_lab

    def residues(self, height: int, width: int, origin: tuple[int, int]):
        """Номера позиций для картинки: (строки, столбцы).

        Строка 0 картинки — верх, а по игровому Z верх это наибольшая
        координата, поэтому строки идут в обратную сторону.
        """
        p = self.period
        ox, oz = origin
        rows = ((np.arange(height - 1, -1, -1, dtype=np.int64) + oz) % p).astype(np.int32)
        cols = ((np.arange(width, dtype=np.int64) + ox) % p).astype(np.int32)
        return rows, cols

    def shown(self, keys: np.ndarray, origin: tuple[int, int] = (0, 0)) -> np.ndarray:
        """Что игра покажет: (H, W) индексы -> (H, W, 3) uint8."""
        if not self.patterned:
            return self.rgb[keys]
        h, w = keys.shape
        rows, cols = self.residues(h, w, origin)
        return self.cells[rows[:, None], cols[None, :], keys]

    def lab(self, lum_weight: float = 1.0) -> np.ndarray:
        """OKLab со взвешенной осью светлоты."""
        scaled = self._lab.copy()
        scaled[:, 0] *= lum_weight
        return scaled

    def _tree(self, lum_weight: float):
        key = round(float(lum_weight), 3)
        if key not in self._trees:
            self._trees[key] = cKDTree(self.lab(key)) if cKDTree is not None else None
        return self._trees[key]

    def nearest(self, lab: np.ndarray, lum_weight: float = 1.0) -> np.ndarray:
        """Индексы ближайших цветов для массива OKLab (..., 3)."""
        flat = lab.reshape(-1, 3).copy()
        flat[:, 0] *= lum_weight
        tree = self._tree(lum_weight)
        if tree is not None:
            return tree.query(flat, workers=-1)[1].astype(np.int32).reshape(lab.shape[:-1])

        pal = self.lab(lum_weight)
        out = np.empty(flat.shape[0], dtype=np.int32)
        step = 1 << 16
        for start in range(0, flat.shape[0], step):
            chunk = flat[start:start + step]
            d = ((chunk[:, None, :] - pal[None, :, :]) ** 2).sum(axis=2)
            out[start:start + step] = np.argmin(d, axis=1)
        return out.reshape(lab.shape[:-1])

    # ── подбор с учётом позиции ─────────────────────────────────────────

    def _cell_scaled(self, lum_weight: float) -> np.ndarray:
        """(P*P, N, 3) в OKLab со взвешенной светлотой."""
        key = round(float(lum_weight), 3)
        got = self._cell_trees.get(key)
        if got is None:
            lab = self.cell_lab().copy()
            lab[..., 0] *= key
            got = lab.reshape(-1, len(self.rgb), 3)
            self._cell_trees[key] = got
        return got

    def nearest_at(self, lab: np.ndarray, origin: tuple[int, int],
                   lum_weight: float = 1.0) -> np.ndarray:
        """Ближайший вариант с учётом того, какая ячейка ляжет на клетку.

        Дерева здесь нет намеренно: позиций до 256, на каждую пришлось бы
        своё, а строятся они дольше, чем считается сам ответ. Внутри одной
        позиции набор фиксирован, поэтому расстояния берутся матрицей —
        |t|² - 2·t·p + |p|², где второе слагаемое это обычное умножение
        матриц. Оно упирается в BLAS и обгоняет дерево на таких размерах.
        """
        if not self.patterned:
            return self.nearest(lab, lum_weight)

        h, w = lab.shape[:2]
        rows, cols = self.residues(h, w, origin)
        flat = np.ascontiguousarray(lab.reshape(-1, 3), dtype=np.float32)
        flat = flat * np.array([lum_weight, 1.0, 1.0], dtype=np.float32)
        cls = (rows[:, None] * self.period + cols[None, :]).reshape(-1)

        # Четвёртая координата складывает |p|² прямо в умножение матриц:
        # без неё пришлось бы отдельным проходом прибавлять норму к матрице
        # расстояний, а она на больших картинках весит гигабайты.
        pal = self._cell_scaled(lum_weight)                    # (C, N, 3)
        aug = np.concatenate([-2.0 * pal, (pal ** 2).sum(-1, keepdims=True)], axis=-1)
        aug = np.ascontiguousarray(np.swapaxes(aug, 1, 2), dtype=np.float32)   # (C, 4, N)
        ones = np.ones((flat.shape[0], 1), dtype=np.float32)
        flat4 = np.concatenate([flat, ones], axis=1)

        out = np.empty(flat.shape[0], dtype=np.int32)
        order = np.argsort(cls, kind="stable")
        bounds = np.searchsorted(cls[order], np.arange(self.period ** 2 + 1))
        chunk = max(1, (1 << 22) // max(1, len(self.rgb)))     # держим матрицу в разумных мегабайтах
        for c in range(self.period ** 2):
            lo, hi = bounds[c], bounds[c + 1]
            if lo == hi:
                continue
            take = order[lo:hi]
            here = aug[c]
            for start in range(0, take.size, chunk):
                part = take[start:start + chunk]
                out[part] = np.argmin(flat4[part] @ here, axis=1)
        return out.reshape(h, w)

    def remap(self, lum_weight: float = 1.0, neighbours: int = 16) -> np.ndarray:
        """(P, P, N): «что выбрала общая таблица -> что верно в этой позиции».

        Диффузия ошибки идёт пиксель за пикселем, и позволить себе поиск по
        всему набору на каждом шаге нельзя. Зато можно заранее переписать
        ответ общей таблицы: у выбранного варианта берём его средний цвет
        как оценку того, чего хотел пиксель, и ищем, кто ближе к этой
        оценке настоящим цветом в этой позиции. Кандидаты — сам вариант и
        его ближайшие соседи по среднему цвету: дальше ответа не бывает.
        """
        key = round(float(lum_weight), 3)
        if key in self._remaps:
            return self._remaps[key]

        n = len(self.rgb)
        if not self.patterned or n < 2:
            table = np.tile(np.arange(n, dtype=np.int32), (self.period, self.period, 1))
            self._remaps[key] = table
            return table

        want = self.lab(key)                                   # (N, 3)
        m = min(int(neighbours), n - 1)
        # Соседей ищем перебором, а не деревом: набор маленький, зато порядок
        # получается тот же в Python и в браузере — ничьи разрешаются по
        # меньшему индексу. Веб-версия обязана давать те же индексы.
        d = ((want[:, None, :] - want[None, :, :]) ** 2).sum(-1)
        np.fill_diagonal(d, np.inf)
        near = np.argsort(d, axis=1, kind="stable")[:, :m].astype(np.int32)
        cand = np.concatenate([np.arange(n, dtype=np.int32)[:, None], near], axis=1)

        lab = self.cell_lab().copy()
        lab[..., 0] *= key
        rows = np.arange(n)
        table = np.zeros((self.period, self.period, n), dtype=np.int32)
        for cz in range(self.period):
            for cx in range(self.period):
                here = lab[cz, cx][cand]                       # (N, m+1, 3)
                d = ((here - want[:, None, :]) ** 2).sum(-1)
                table[cz, cx] = cand[rows, np.argmin(d, axis=1)]
        self._remaps[key] = table
        return table


# границы куба OKLab, в который укладываются все реальные цвета sRGB
_LUT_LO = np.array([0.0, -0.30, -0.32], dtype=np.float32)
_LUT_HI = np.array([1.0, 0.30, 0.20], dtype=np.float32)
_LUT_N = 64


def _lut(pal: Palette, lum_weight: float) -> np.ndarray:
    """Таблица «точка OKLab -> индекс палитры» для последовательных проходов."""
    key = ("lut", round(float(lum_weight), 3), _LUT_N)
    if key in pal._luts:
        return pal._luts[key]

    axes = [np.linspace(_LUT_LO[i], _LUT_HI[i], _LUT_N, dtype=np.float32) for i in range(3)]
    grid = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
    idx = pal.nearest(grid, lum_weight).astype(np.int32).reshape(_LUT_N, _LUT_N, _LUT_N)
    pal._luts[key] = idx
    return idx


# ── ядра диффузии ошибки ─────────────────────────────────────────────────────
# (dx, dy, вес). Ошибка уходит только вперёд по ходу обхода.

KERNELS: dict[str, tuple[list[tuple[int, int, float]], float]] = {
    "fs": ([(1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1)], 16),
    "jarvis": (
        [(1, 0, 7), (2, 0, 5),
         (-2, 1, 3), (-1, 1, 5), (0, 1, 7), (1, 1, 5), (2, 1, 3),
         (-2, 2, 1), (-1, 2, 3), (0, 2, 5), (1, 2, 3), (2, 2, 1)], 48),
    "stucki": (
        [(1, 0, 8), (2, 0, 4),
         (-2, 1, 2), (-1, 1, 4), (0, 1, 8), (1, 1, 4), (2, 1, 2),
         (-2, 2, 1), (-1, 2, 2), (0, 2, 4), (1, 2, 2), (2, 2, 1)], 42),
    "burkes": (
        [(1, 0, 8), (2, 0, 4),
         (-2, 1, 2), (-1, 1, 4), (0, 1, 8), (1, 1, 4), (2, 1, 2)], 32),
    "sierra": (
        [(1, 0, 5), (2, 0, 3),
         (-2, 1, 2), (-1, 1, 4), (0, 1, 5), (1, 1, 4), (2, 1, 2),
         (-1, 2, 2), (0, 2, 3), (1, 2, 2)], 32),
    "atkinson": ([(1, 0, 1), (2, 0, 1), (-1, 1, 1), (0, 1, 1), (1, 1, 1), (0, 2, 1)], 8),
}

KERNEL_TITLES = {
    "fs": "Флойд–Стейнберг — классика, мелкое зерно",
    "jarvis": "Джарвис — мягче, шире разброс",
    "stucki": "Стакки — чище Джарвиса",
    "burkes": "Бёркс — быстрый компромисс",
    "sierra": "Сьерра — спокойное зерно",
    "atkinson": "Аткинсон — контрастный, «маковский»",
}

ORDERED = {"bayer": "Байер 8×8 — регулярная сетка", "bluenoise": "Синий шум — без узора"}
METHODS = {"none": "Без дизеринга — плоские заливки", **KERNEL_TITLES, **ORDERED}


# ── упорядоченные маски ──────────────────────────────────────────────────────

_BAYER8 = np.array(
    [[0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26],
     [12, 44, 4, 36, 14, 46, 6, 38], [60, 28, 52, 20, 62, 30, 54, 22],
     [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
     [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]],
    dtype=np.float32,
) / 63.0

_bluenoise_cache: np.ndarray | None = None


def bluenoise_mask() -> np.ndarray:
    """Маска синего шума 64x64 со значениями 0..1.

    Берётся из data/bluenoise.npy (её кладёт tools/build_bluenoise.py).
    Если файла нет — считается на лету и запоминается в памяти.
    """
    global _bluenoise_cache
    if _bluenoise_cache is not None:
        return _bluenoise_cache

    path = os.path.join(DATA_DIR, "bluenoise.npy")
    if os.path.isfile(path):
        try:
            _bluenoise_cache = np.load(path).astype(np.float32)
            return _bluenoise_cache
        except (OSError, ValueError):
            pass

    from .bluenoise import generate

    _bluenoise_cache = generate(64)
    return _bluenoise_cache


def _mask(name: str, height: int, width: int) -> np.ndarray:
    base = _BAYER8 if name == "bayer" else bluenoise_mask()
    bh, bw = base.shape
    return np.tile(base, (height // bh + 1, width // bw + 1))[:height, :width]


# ── основной вход ────────────────────────────────────────────────────────────


def quantize(
    rgb: np.ndarray,
    palette: Palette,
    method: str = "fs",
    *,
    strength: float = 1.0,
    lum_weight: float = 1.0,
    serpentine: bool = True,
    mask: np.ndarray | None = None,
    origin: tuple[int, int] = (0, 0),
    use_pattern: bool = True,
) -> np.ndarray:
    """Вернуть (H, W) int32 — индексы палитры для каждой клетки.

    mask: где False — клетка пустая, её цвет не влияет на соседей.
    origin: сдвиг картинки в локальных координатах чертежа (x, z). От него
    зависит, какая ячейка текстуры ляжет на какую клетку.
    use_pattern: учитывать ли узор ПРИ ПОДБОРЕ. Показывать его всё равно
    надо: набор с ячейками рисует то, что игрок увидит, независимо от того,
    подстраивался под узор подбор или нет.
    """
    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    lab = to_oklab(rgb)

    if method == "none" or strength <= 0:
        return palette.nearest_at(lab, origin, lum_weight) if use_pattern             else palette.nearest(lab, lum_weight)

    if method in ORDERED:
        return _ordered(lab, palette, method, strength, lum_weight, origin, use_pattern)

    if method not in KERNELS:
        method = "fs"
    return _diffuse(lab, palette, method, strength, lum_weight, serpentine, mask, origin,
                    use_pattern)


def _typical_step(palette: Palette, lum_weight: float) -> float:
    """Характерное расстояние между соседними цветами палитры в OKLab."""
    pal = palette.lab(lum_weight)
    if len(pal) < 2:
        return 0.1
    tree = palette._tree(lum_weight)
    if tree is not None:
        d = tree.query(pal, k=2, workers=-1)[0][:, 1]
    else:
        dist = np.sqrt(((pal[:, None, :] - pal[None, :, :]) ** 2).sum(axis=2))
        np.fill_diagonal(dist, np.inf)
        d = dist.min(axis=1)
    return float(np.median(d))


def _ordered(lab, palette, method, strength, lum_weight, origin=(0, 0), use_pattern=True):
    """Упорядоченный дизеринг: сдвигаем цвет маской и берём ближайший."""
    h, w = lab.shape[:2]
    amp = _typical_step(palette, lum_weight) * 0.5 * float(strength)
    noise = (_mask(method, h, w) - 0.5) * amp
    shifted = lab.copy()
    # шум по светлоте даёт смешение соседних блоков без цветных крапин
    shifted[..., 0] += noise
    shifted[..., 1] += noise * 0.35
    shifted[..., 2] += noise * 0.35
    return palette.nearest_at(shifted, origin, lum_weight) if use_pattern         else palette.nearest(shifted, lum_weight)


def _diffuse(lab, palette, method, strength, lum_weight, serpentine, mask, origin=(0, 0),
             use_pattern=True):
    """Диффузия ошибки в OKLab, змейкой, с таблицей подстановки.

    Внутренний цикл принципиально последовательный: цвет пикселя зависит от
    ошибки предыдущих. Поэтому он написан на обычных числах Python, а не на
    numpy: операция над массивом из трёх элементов стоит около микросекунды,
    и на сотнях тысяч пикселей это превращается в секунды. Скалярная версия
    быстрее примерно вчетверо. Векторизуются только границы строк.

    Узор встроен так, чтобы не замедлить этот цикл: общая таблица даёт
    индекс как раньше, а `palette.remap` одним обращением переписывает его
    на верный для этой позиции. Ошибка при этом считается от того, что
    игра ПОКАЖЕТ, а не от среднего цвета варианта, — иначе дизеринг
    размазывает не ту разницу.
    """
    h, w = lab.shape[:2]
    offsets, divisor = KERNELS[method]
    weights = [(dx, dy, wt / divisor * float(strength)) for dx, dy, wt in offsets]
    depth = max(dy for _, dy, _ in offsets) + 1

    n = _LUT_N
    lut_flat = _lut(palette, lum_weight).reshape(-1).tolist()
    lo0, lo1, lo2 = (float(v) for v in _LUT_LO)
    sc0, sc1, sc2 = ((n - 1) / (float(hi) - float(lo))
                     for hi, lo in zip(_LUT_HI, _LUT_LO))
    top = n - 1

    pal = palette._lab
    pal_l = pal[:, 0].tolist()
    pal_a = pal[:, 1].tolist()
    pal_b = pal[:, 2].tolist()

    # узор: переписывание индекса и настоящий цвет ячейки, плоскими списками
    patterned = palette.patterned and use_pattern
    if patterned:
        size = len(palette.rgb)
        period = palette.period
        remap_flat = palette.remap(lum_weight).reshape(-1).tolist()
        cell_flat = palette.cell_lab().reshape(-1).tolist()
        ox, oz = origin
        col_of = [((x + ox) % period) for x in range(w)]

    lab_l, lab_a, lab_b = (np.ascontiguousarray(lab[..., i], dtype=np.float64) for i in range(3))
    out = np.zeros((h, w), dtype=np.int32)

    zeros = [0.0] * w
    err_l = [list(zeros) for _ in range(depth)]
    err_a = [list(zeros) for _ in range(depth)]
    err_b = [list(zeros) for _ in range(depth)]
    has_mask = mask is not None

    for y in range(h):
        row_l = (lab_l[y] + err_l[0]).tolist()
        row_a = (lab_a[y] + err_a[0]).tolist()
        row_b = (lab_b[y] + err_b[0]).tolist()
        row_mask = mask[y].tolist() if has_mask else None
        reverse = serpentine and (y & 1)
        order = range(w - 1, -1, -1) if reverse else range(w)
        flip = -1 if reverse else 1
        row_out = [0] * w
        if patterned:
            # строка 0 картинки — верх, а по игровому Z верх это наибольшая
            row_class = ((h - 1 - y + oz) % period) * period

        for x in order:
            if has_mask and not row_mask[x]:
                continue
            pl, pa, pb = row_l[x], row_a[x], row_b[x]

            i0 = int((pl - lo0) * sc0)
            i1 = int((pa - lo1) * sc1)
            i2 = int((pb - lo2) * sc2)
            if i0 < 0: i0 = 0
            elif i0 > top: i0 = top
            if i1 < 0: i1 = 0
            elif i1 > top: i1 = top
            if i2 < 0: i2 = 0
            elif i2 > top: i2 = top

            idx = lut_flat[(i0 * n + i1) * n + i2]

            if patterned:
                base = (row_class + col_of[x]) * size
                idx = remap_flat[base + idx]
                spot = (base + idx) * 3
                got_l, got_a, got_b = cell_flat[spot], cell_flat[spot + 1], cell_flat[spot + 2]
            else:
                got_l, got_a, got_b = pal_l[idx], pal_a[idx], pal_b[idx]
            row_out[x] = idx

            # без ограничения ошибка на насыщенных краях «взрывается»
            el = pl - got_l
            ea = pa - got_a
            eb = pb - got_b
            if el > 0.35: el = 0.35
            elif el < -0.35: el = -0.35
            if ea > 0.35: ea = 0.35
            elif ea < -0.35: ea = -0.35
            if eb > 0.35: eb = 0.35
            elif eb < -0.35: eb = -0.35

            for dx, dy, wt in weights:
                nx = x + dx * flip
                if nx < 0 or nx >= w:
                    continue
                if dy:
                    err_l[dy][nx] += el * wt
                    err_a[dy][nx] += ea * wt
                    err_b[dy][nx] += eb * wt
                else:
                    row_l[nx] += el * wt
                    row_a[nx] += ea * wt
                    row_b[nx] += eb * wt

        out[y] = row_out
        err_l.append(err_l.pop(0)); err_l[-1][:] = zeros
        err_a.append(err_a.pop(0)); err_a[-1][:] = zeros
        err_b.append(err_b.pop(0)); err_b[-1][:] = zeros

    return out


# ── автоподгонка картинки под палитру ────────────────────────────────────────


def fit_to_palette(rgb: np.ndarray, palette: Palette, lum_weight: float = 1.0) -> dict:
    """Подобрать гамму / насыщенность / контраст, чтобы цвета легли ближе.

    Палитра игры не покрывает приглушённые тона: если картинка бледная,
    любой цвет улетает в ближайший кислотный. Небольшое поднятие
    насыщенности и правка гаммы уменьшают среднюю ошибку в разы.
    Перебор грубый, но честный — считается настоящая ошибка в OKLab.
    """
    small = rgb
    if max(rgb.shape[:2]) > 160:                       # для оценки хватает эскиза
        step = max(1, max(rgb.shape[:2]) // 160)
        small = rgb[::step, ::step]

    lin0 = srgb_to_linear(small)
    best = {"gamma": 1.0, "saturation": 1.0, "contrast": 1.0, "error": None}

    for gamma in (0.75, 0.85, 1.0, 1.15, 1.3):
        lin_g = np.clip(lin0 ** (1.0 / gamma), 0, 1)
        for contrast in (0.9, 1.0, 1.1, 1.25):
            lin_c = np.clip((lin_g - 0.5) * contrast + 0.5, 0, 1)
            for saturation in (0.85, 1.0, 1.2, 1.4, 1.6):
                lum = lin_c @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
                lin_s = np.clip(lum[..., None] + (lin_c - lum[..., None]) * saturation, 0, 1)
                lab = linear_to_oklab(lin_s).astype(np.float32)
                idx = palette.nearest(lab, lum_weight)
                diff = lab - palette._lab[idx]
                diff[..., 0] *= lum_weight
                err = float(np.sqrt((diff ** 2).sum(axis=-1)).mean())
                if best["error"] is None or err < best["error"]:
                    best = {"gamma": gamma, "saturation": saturation,
                            "contrast": contrast, "error": err}

    base_lab = linear_to_oklab(lin0).astype(np.float32)
    base_idx = palette.nearest(base_lab, lum_weight)
    base_diff = base_lab - palette._lab[base_idx]
    base_diff[..., 0] *= lum_weight
    best["baseError"] = float(np.sqrt((base_diff ** 2).sum(axis=-1)).mean())
    return best


def perceived_error(src_rgb: np.ndarray, out_rgb: np.ndarray,
                    mask: np.ndarray | None = None, radius: int = 2) -> float:
    """Ошибка «как видно с расстояния».

    Считать ошибку по каждому пикселю для дизеринга бессмысленно: он нарочно
    ставит рядом два неточных цвета, чтобы глаз смешал их в верный. Поэтому
    честный замер — усреднить и оригинал, и результат по небольшому окну
    (примерно так их смешивает глаз, когда постройка не в упор) и только
    потом сравнить в OKLab.
    """
    try:
        from scipy.ndimage import uniform_filter
    except ImportError:
        return mean_error_rgb(src_rgb, out_rgb, mask)

    size = radius * 2 + 1
    a = linear_to_oklab(srgb_to_linear(src_rgb))
    b = linear_to_oklab(srgb_to_linear(out_rgb))
    if mask is not None:
        weight = uniform_filter(mask.astype(np.float32), size=size, mode="nearest")
        a = np.stack([uniform_filter(a[..., i] * mask, size=size, mode="nearest") for i in range(3)], -1)
        b = np.stack([uniform_filter(b[..., i] * mask, size=size, mode="nearest") for i in range(3)], -1)
        good = weight > 0.2
        a = a[good] / weight[good][:, None]
        b = b[good] / weight[good][:, None]
    else:
        a = np.stack([uniform_filter(a[..., i], size=size, mode="nearest") for i in range(3)], -1)
        b = np.stack([uniform_filter(b[..., i], size=size, mode="nearest") for i in range(3)], -1)

    dist = np.sqrt(((a - b) ** 2).sum(axis=-1))
    return float(dist.mean()) if dist.size else 0.0


def mean_error_rgb(src_rgb: np.ndarray, out_rgb: np.ndarray, mask: np.ndarray | None = None) -> float:
    a = to_oklab(src_rgb)
    b = to_oklab(out_rgb)
    dist = np.sqrt(((a - b) ** 2).sum(axis=-1))
    if mask is not None:
        dist = dist[mask]
    return float(dist.mean()) if dist.size else 0.0


def mean_error(rgb: np.ndarray, palette: Palette, indices: np.ndarray,
               mask: np.ndarray | None = None, origin: tuple[int, int] = (0, 0)) -> float:
    """Средняя ошибка цвета в OKLab — число, по которому видно, стало ли лучше.

    Сравнивать надо с тем, что игра ПОКАЖЕТ в этой позиции, а не со средним
    цветом варианта, иначе узор в замер не попадёт.
    """
    lab = to_oklab(rgb)
    if palette.patterned:
        h, w = indices.shape
        rows, cols = palette.residues(h, w, origin)
        shown = palette.cell_lab()[rows[:, None], cols[None, :], indices]
    else:
        shown = palette._lab[indices]
    diff = lab - shown
    dist = np.sqrt((diff ** 2).sum(axis=-1))
    if mask is not None:
        dist = dist[mask]
    return float(dist.mean()) if dist.size else 0.0
