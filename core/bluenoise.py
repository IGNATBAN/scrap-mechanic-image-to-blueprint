"""Маска синего шума методом «пустот и скоплений» (void-and-cluster).

Зачем она. Упорядоченный дизеринг решёткой Байера даёт регулярный узор:
на блоках он читается как клетчатая рябь и сразу выдаёт «компьютерность».
Синий шум распределён так же равномерно, но без периодичности — глаз
воспринимает его как ровный тон.

Алгоритм классический (Ulichney, 1993): берём случайный набор точек,
итеративно переносим самую «тесную» точку в самую большую пустоту, а
затем ранжируем все ячейки, поочерёдно убирая скопления и заполняя
пустоты. Плотность считается свёрткой с гауссианом по тору — через БПФ,
иначе на 64x64 это заметно дольше.
"""

from __future__ import annotations

import numpy as np


def _density_kernel(size: int, sigma: float = 1.9) -> np.ndarray:
    """Гауссиан, свёрнутый по кругу (тор) — готовый множитель в частотах."""
    axis = np.arange(size)
    axis = np.minimum(axis, size - axis)          # расстояние с учётом заворота
    dy, dx = np.meshgrid(axis, axis, indexing="ij")
    kernel = np.exp(-(dx ** 2 + dy ** 2) / (2.0 * sigma ** 2))
    return np.fft.rfft2(kernel)


def _filter(binary: np.ndarray, kernel_f: np.ndarray, size: int) -> np.ndarray:
    return np.fft.irfft2(np.fft.rfft2(binary.astype(np.float64)) * kernel_f, s=(size, size))


def _tightest(density: np.ndarray, binary: np.ndarray) -> tuple[int, int]:
    masked = np.where(binary, density, -np.inf)
    return np.unravel_index(int(np.argmax(masked)), density.shape)


def _largest_void(density: np.ndarray, binary: np.ndarray) -> tuple[int, int]:
    masked = np.where(binary, np.inf, density)
    return np.unravel_index(int(np.argmin(masked)), density.shape)


def generate(size: int = 64, seed: int = 7) -> np.ndarray:
    """Вернуть маску size x size со значениями 0..1 (равномерное распределение)."""
    total = size * size
    kernel_f = _density_kernel(size)

    rng = np.random.default_rng(seed)
    binary = np.zeros((size, size), dtype=bool)
    start = max(1, total // 10)
    flat = rng.permutation(total)[:start]
    binary.flat[flat] = True

    # 1. развести стартовые точки: скопление -> пустота, пока не устаканится
    for _ in range(total):
        density = _filter(binary, kernel_f, size)
        cy, cx = _tightest(density, binary)
        binary[cy, cx] = False
        density = _filter(binary, kernel_f, size)
        vy, vx = _largest_void(density, binary)
        if (vy, vx) == (cy, cx):
            binary[cy, cx] = True
            break
        binary[vy, vx] = True

    initial = binary.copy()
    rank = np.full((size, size), -1, dtype=np.int32)

    # 2. убираем точки по одной — это младшие ранги
    work = initial.copy()
    for r in range(int(work.sum()) - 1, -1, -1):
        density = _filter(work, kernel_f, size)
        y, x = _tightest(density, work)
        work[y, x] = False
        rank[y, x] = r

    # 3. заполняем пустоты — средние ранги
    work = initial.copy()
    for r in range(int(initial.sum()), total // 2):
        density = _filter(work, kernel_f, size)
        y, x = _largest_void(density, work)
        work[y, x] = True
        rank[y, x] = r

    # 4. вторая половина: теперь «скопление» ищется по нулям
    for r in range(total // 2, total):
        density = _filter(~work, kernel_f, size)
        y, x = _tightest(density, ~work)
        work[y, x] = True
        rank[y, x] = r

    return (rank.astype(np.float32) + 0.5) / total
