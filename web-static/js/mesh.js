// Склейка одноцветных клеток в масштабированные блоки. Порт core/mesh.py.
//
// Scrap Mechanic умеет хранить блок растянутым (поле "bounds"), и в чертежах
// самой игры встречаются блоки с bounds до 892 — значит «одна деталь на
// пиксель» это чистая потеря. Здесь жадное разбиение на прямоугольники:
// из каждой свободной клетки тянем максимально широкую полосу одного
// материала, затем опускаем её вниз, пока строки под ней совпадают.
//
// Порядок обхода и содержимое прямоугольников обязаны совпадать с
// питоновской версией — это проверяют эталонные векторы.

export const MAX_BOUND_DEFAULT = 255;

/** Упаковать RGB в один ключ: 0xRRGGBB. */
export function colorKeys(rgb, n) {
  const keys = new Int32Array(n);
  for (let i = 0; i < n; i++) {
    keys[i] = (rgb[i * 3] << 16) | (rgb[i * 3 + 1] << 8) | rgb[i * 3 + 2];
  }
  return keys;
}

/** Без слияния: каждая клетка — отдельный блок 1x1x1. */
export function splitRects(keys, mask, w, h) {
  const rects = [];
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = y * w + x;
      if (mask && !mask[i]) continue;
      rects.push([x, y, 1, 1, keys[i]]);
    }
  }
  return rects;
}

/**
 * Жадное разбиение сетки на одинаковые по материалу прямоугольники.
 * keys — Int32Array w*h, mask — Uint8Array w*h (или null, если дыр нет).
 * Возвращает массив [x, y, ширина, высота, ключ].
 */
export function mergeRects(keys, mask, w, h, maxBound = MAX_BOUND_DEFAULT) {
  maxBound = Math.max(1, maxBound | 0);
  if (maxBound === 1) return splitRects(keys, mask, w, h);

  const at = (x, y) => (mask ? mask[y * w + x] : 1);

  // csd[y][x] — сколько подряд идущих столбцов до x совпали со строкой снизу.
  // Префиксные суммы дают проверку «вся полоса совпадает» за две операции.
  let csd = null;
  if (h > 1) {
    csd = new Int32Array((h - 1) * (w + 1));
    for (let y = 0; y < h - 1; y++) {
      const base = y * (w + 1);
      let acc = 0;
      for (let x = 0; x < w; x++) {
        const i = y * w + x;
        const same = at(x, y) && at(x, y + 1) && keys[i] === keys[i + w] ? 1 : 0;
        acc += same;
        csd[base + x + 1] = acc;
      }
    }
  }

  const rects = [];
  // Клетка занята <=> попала в уже созданный прямоугольник. Так как они
  // растут только вниз и мы идём сверху вниз, свободная клетка гарантирует,
  // что всё под ней в этом столбце тоже свободно.
  const takenUntil = new Int32Array(w);

  for (let y = 0; y < h; y++) {
    const rowOff = y * w;

    // границы одноцветных отрезков в строке — считаем один раз на строку
    let runStart = 0;
    while (runStart < w) {
      let runEnd = runStart + 1;
      while (runEnd < w
        && keys[rowOff + runEnd] === keys[rowOff + runEnd - 1]
        && at(runEnd, y) === at(runEnd - 1, y)) runEnd++;

      if (at(runStart, y)) {
        let x = runStart;
        while (x < runEnd) {
          if (takenUntil[x] > y) { x++; continue; }

          const limit = Math.min(runEnd, x + maxBound);
          let width = 1;
          while (x + width < limit && takenUntil[x + width] <= y) width++;

          let height = 1;
          if (csd) {
            const maxH = Math.min(h - y, maxBound);
            while (height < maxH) {
              const yy = y + height - 1;
              const base = yy * (w + 1);
              if (csd[base + x + width] - csd[base + x] !== width) break;
              height++;
            }
          }

          rects.push([x, y, width, height, keys[rowOff + x]]);
          for (let k = 0; k < width; k++) takenUntil[x + k] = y + height;
          x += width;
        }
      }
      runStart = runEnd;
    }
  }
  return rects;
}

/** Сводка для интерфейса. */
export function stats(rects) {
  if (!rects.length) return { parts: 0, cells: 0, colors: 0, biggest: 0, mergedRatio: 0 };
  let cells = 0, biggest = 0;
  const colors = new Set();
  for (const [, , w, h, key] of rects) {
    const area = w * h;
    cells += area;
    if (area > biggest) biggest = area;
    colors.add(key);
  }
  return {
    parts: rects.length,
    cells,
    colors: colors.size,
    biggest,
    mergedRatio: cells ? +(1 - rects.length / cells).toFixed(4) : 0,
  };
}
