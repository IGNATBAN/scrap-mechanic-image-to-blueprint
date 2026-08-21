"""Пересобрать маску синего шума: py tools/build_bluenoise.py [размер]

Маска нужна для упорядоченного дизеринга без регулярного узора. Считается
один раз и кладётся в data/bluenoise.npy; без файла ядро посчитает её на
лету, но при каждом запуске заново.

Заодно печатает проверку «а синий ли шум»: доля энергии в низких частотах
должна быть порядка сотых долей процента, у белого шума — около четырёх.
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from core import bluenoise  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "bluenoise.npy")


def main() -> int:
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 64
    print(f"Считаю маску {size}x{size}…")
    mask = bluenoise.generate(size)

    unique = len(np.unique(mask))
    spectrum = np.fft.fftshift(np.abs(np.fft.fft2(mask - mask.mean())))
    mid = size // 2
    band = max(2, size // 10)
    low = spectrum[mid - band:mid + band + 1, mid - band:mid + band + 1].sum() / spectrum.sum()
    white = (2 * band + 1) ** 2 / (size * size)

    print(f"  уникальных значений: {unique} из {size * size}")
    print(f"  энергия в низких частотах: {low:.4%} (у белого шума было бы ~{white:.2%})")
    if low > white / 4:
        print("  ВНИМАНИЕ: маска не выглядит синим шумом")
        return 1

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    np.save(OUT, mask)
    print(f"Записано: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
