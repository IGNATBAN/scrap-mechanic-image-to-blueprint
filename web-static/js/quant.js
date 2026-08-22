// Подбор цвета и дизеринг. Порт core/quant.py — результат обязан совпадать
// с питоновской версией бит в бит, это проверяют эталонные векторы.
//
// Почему так, а не проще: палитра краскопульта «кривая» (10 оттенков на
// 4 яркости, без приглушённых тонов), и дизеринг в sRGB уводит тени в грязь.
// Здесь и подбор, и вся диффузия ошибки идут в OKLab, обход — змейкой,
// а вместо решётки Байера доступен синий шум.

import { toOklabArray } from './oklab.js';

const f = Math.fround;

/** Остаток как в Python: для отрицательных тоже неотрицательный. */
function mod(a, b) {
  const r = a % b;
  return r < 0 ? r + b : r;
}

// ── набор материалов с поиском ближайшего ────────────────────────────────

export class Palette {
  constructor({ rgb, paint, block, cells = null, period = 1 }) {
    this.rgb = rgb;
    this.size = rgb.length / 3;
    this.paint = paint || [];
    this.block = block || [];
    this.lab = toOklabArray(rgb, this.size);
    this._luts = new Map();
    this._steps = new Map();
    this._remaps = new Map();
    this._cellLab = null;
    // Текстура блока растянута на tiling блоков, поэтому один и тот же
    // вариант в разных местах постройки выглядит по-разному. cells хранит
    // настоящий цвет каждого варианта в каждой позиции: P*P*N*3.
    this.period = Math.max(1, period | 0);
    this.cells = null;
    if (cells && cells.length === this.period * this.period * this.size * 3) {
      this.cells = cells;
    } else {
      this.period = 1;
    }
  }

  /** Есть ли вообще разница между позициями. */
  get patterned() {
    return this.cells !== null && this.period > 1;
  }

  /** OKLab для таблицы позиций, считается один раз. */
  cellLab() {
    if (!this._cellLab) {
      const src = this.cells || this.rgb;
      this._cellLab = toOklabArray(src, src.length / 3);
    }
    return this._cellLab;
  }

  /**
   * Номера позиций для картинки. Строка 0 картинки — верх, а по игровому Z
   * верх это наибольшая координата, поэтому строки идут в обратную сторону.
   */
  residues(height, width, origin) {
    const p = this.period;
    const [ox, oz] = origin;
    const rows = new Int32Array(height);
    const cols = new Int32Array(width);
    for (let y = 0; y < height; y++) rows[y] = mod(height - 1 - y + oz, p);
    for (let x = 0; x < width; x++) cols[x] = mod(x + ox, p);
    return { rows, cols };
  }

  /** Что игра покажет: индексы -> цвет (Uint8Array n*3). */
  shown(keys, width, height, origin = [0, 0]) {
    const out = new Uint8Array(width * height * 3);
    if (!this.patterned) {
      for (let i = 0; i < width * height; i++) {
        const s = keys[i] * 3;
        out[i * 3] = this.rgb[s];
        out[i * 3 + 1] = this.rgb[s + 1];
        out[i * 3 + 2] = this.rgb[s + 2];
      }
      return out;
    }
    const { rows, cols } = this.residues(height, width, origin);
    const n = this.size;
    for (let y = 0; y < height; y++) {
      const base = rows[y] * this.period;
      for (let x = 0; x < width; x++) {
        const i = y * width + x;
        const s = ((base + cols[x]) * n + keys[i]) * 3;
        out[i * 3] = this.cells[s];
        out[i * 3 + 1] = this.cells[s + 1];
        out[i * 3 + 2] = this.cells[s + 2];
      }
    }
    return out;
  }

  /**
   * Ближайший вариант с учётом того, какая ячейка ляжет на клетку.
   * Перебор такой же, как в nearest, только набор берётся для позиции.
   */
  nearestAt(lab, width, height, origin, lumWeight = 1) {
    if (!this.patterned) return this.nearest(lab, width * height, lumWeight);
    const { rows, cols } = this.residues(height, width, origin);
    const pal = this.cellLab();
    const m = this.size;
    const wl = lumWeight;
    const res = new Int32Array(width * height);
    for (let y = 0; y < height; y++) {
      const base = rows[y] * this.period;
      for (let x = 0; x < width; x++) {
        const i = y * width + x;
        const L = lab[i * 3] * wl, A = lab[i * 3 + 1], B = lab[i * 3 + 2];
        const off = (base + cols[x]) * m * 3;
        let best = 0, bestD = Infinity;
        for (let j = 0; j < m; j++) {
          const dL = pal[off + j * 3] * wl - L;
          const dA = pal[off + j * 3 + 1] - A;
          const dB = pal[off + j * 3 + 2] - B;
          const d = dL * dL + dA * dA + dB * dB;
          if (d < bestD) { bestD = d; best = j; }
        }
        res[i] = best;
      }
    }
    return res;
  }

  /**
   * «Что выбрала общая таблица -> что верно в этой позиции».
   * Диффузия ошибки идёт пиксель за пикселем, перебирать весь набор на
   * каждом шаге нельзя. Зато ответ таблицы можно переписать заранее:
   * у выбранного варианта берём его средний цвет как оценку того, чего
   * хотел пиксель, и ищем, кто ближе к ней настоящим цветом в этой позиции.
   * Кандидаты — сам вариант и его ближайшие соседи по среднему цвету.
   */
  remap(lumWeight = 1, neighbours = 16) {
    const key = Math.round(lumWeight * 1000);
    if (this._remaps.has(key)) return this._remaps.get(key);
    const n = this.size;
    const p = this.period;
    if (!this.patterned || n < 2) {
      const flat = new Int32Array(p * p * n);
      for (let c = 0; c < p * p; c++) for (let i = 0; i < n; i++) flat[c * n + i] = i;
      this._remaps.set(key, flat);
      return flat;
    }

    const want = this.lab;
    const wl = lumWeight;
    const m = Math.min(neighbours, n - 1);
    const cand = new Int32Array(n * (m + 1));
    const order = new Array(n);
    for (let i = 0; i < n; i++) order[i] = i;
    const dist = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      const L = want[i * 3] * wl, A = want[i * 3 + 1], B = want[i * 3 + 2];
      for (let j = 0; j < n; j++) {
        if (j === i) { dist[j] = Infinity; continue; }
        const dL = want[j * 3] * wl - L, dA = want[j * 3 + 1] - A, dB = want[j * 3 + 2] - B;
        dist[j] = dL * dL + dA * dA + dB * dB;
      }
      // устойчивая сортировка: при равных расстояниях выигрывает меньший
      // индекс — так же, как numpy.argsort(kind='stable')
      order.sort((a, b) => (dist[a] - dist[b]) || (a - b));
      cand[i * (m + 1)] = i;
      for (let k = 0; k < m; k++) cand[i * (m + 1) + k + 1] = order[k];
      for (let k = 0; k < n; k++) order[k] = k;
    }

    const pal = this.cellLab();
    const flat = new Int32Array(p * p * n);
    for (let c = 0; c < p * p; c++) {
      const off = c * n * 3;
      for (let i = 0; i < n; i++) {
        const L = want[i * 3] * wl, A = want[i * 3 + 1], B = want[i * 3 + 2];
        let best = cand[i * (m + 1)], bestD = Infinity;
        for (let k = 0; k <= m; k++) {
          const j = cand[i * (m + 1) + k];
          const dL = pal[off + j * 3] * wl - L;
          const dA = pal[off + j * 3 + 1] - A;
          const dB = pal[off + j * 3 + 2] - B;
          const d = dL * dL + dA * dA + dB * dB;
          if (d < bestD) { bestD = d; best = j; }
        }
        flat[c * n + i] = best;
      }
    }
    this._remaps.set(key, flat);
    return flat;
  }

  /**
   * Ближайшие цвета для массива OKLab. Перебор, а не дерево: палитра
   * маленькая (40-640), а перебор даёт тот же результат в любом браузере
   * и одинаково разрешает ничьи — берётся меньший индекс, как в Python.
   */
  nearest(lab, n, lumWeight = 1, out = null) {
    const res = out || new Int32Array(n);
    const pal = this.lab;
    const m = this.size;
    const wl = lumWeight;
    for (let i = 0; i < n; i++) {
      const L = lab[i * 3] * wl, A = lab[i * 3 + 1], B = lab[i * 3 + 2];
      let best = 0, bestD = Infinity;
      for (let j = 0; j < m; j++) {
        const dL = pal[j * 3] * wl - L;
        const dA = pal[j * 3 + 1] - A;
        const dB = pal[j * 3 + 2] - B;
        const d = dL * dL + dA * dA + dB * dB;
        if (d < bestD) { bestD = d; best = j; }
      }
      res[i] = best;
    }
    return res;
  }

  /** Характерное расстояние между соседними цветами набора. */
  typicalStep(lumWeight = 1) {
    const key = Math.round(lumWeight * 1000);
    if (this._steps.has(key)) return this._steps.get(key);
    const m = this.size;
    if (m < 2) return 0.1;
    const pal = this.lab;
    const dists = new Float64Array(m);
    for (let i = 0; i < m; i++) {
      let best = Infinity;
      for (let j = 0; j < m; j++) {
        if (i === j) continue;
        const dL = (pal[j * 3] - pal[i * 3]) * lumWeight;
        const dA = pal[j * 3 + 1] - pal[i * 3 + 1];
        const dB = pal[j * 3 + 2] - pal[i * 3 + 2];
        const d = dL * dL + dA * dA + dB * dB;
        if (d < best) best = d;
      }
      dists[i] = Math.sqrt(best);
    }
    const sorted = Array.from(dists).sort((a, b) => a - b);
    const mid = sorted.length >> 1;
    // numpy.median: при чётной длине усредняет два средних значения
    const med = sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
    this._steps.set(key, med);
    return med;
  }

  /** Таблица «точка OKLab -> индекс» для последовательных проходов. */
  lut(lumWeight = 1) {
    const key = Math.round(lumWeight * 1000);
    if (this._luts.has(key)) return this._luts.get(key);
    const N = LUT_N;
    const grid = new Float32Array(N * N * N * 3);
    let p = 0;
    for (let i = 0; i < N; i++) {
      const L = f(LUT_LO[0] + (LUT_HI[0] - LUT_LO[0]) * i / (N - 1));
      for (let j = 0; j < N; j++) {
        const A = f(LUT_LO[1] + (LUT_HI[1] - LUT_LO[1]) * j / (N - 1));
        for (let k = 0; k < N; k++) {
          grid[p++] = L;
          grid[p++] = A;
          grid[p++] = f(LUT_LO[2] + (LUT_HI[2] - LUT_LO[2]) * k / (N - 1));
        }
      }
    }
    const idx = this.nearest(grid, N * N * N, lumWeight);
    this._luts.set(key, idx);
    return idx;
  }
}

// куб OKLab, в который укладываются все реальные цвета sRGB
const LUT_LO = [0.0, -0.30, -0.32];
const LUT_HI = [1.0, 0.30, 0.20];
const LUT_N = 64;

// ── ядра диффузии ошибки ─────────────────────────────────────────────────
// [dx, dy, вес]. Ошибка уходит только вперёд по ходу обхода.

export const KERNELS = {
  fs: { off: [[1, 0, 7], [-1, 1, 3], [0, 1, 5], [1, 1, 1]], div: 16 },
  jarvis: {
    off: [[1, 0, 7], [2, 0, 5],
      [-2, 1, 3], [-1, 1, 5], [0, 1, 7], [1, 1, 5], [2, 1, 3],
      [-2, 2, 1], [-1, 2, 3], [0, 2, 5], [1, 2, 3], [2, 2, 1]], div: 48,
  },
  stucki: {
    off: [[1, 0, 8], [2, 0, 4],
      [-2, 1, 2], [-1, 1, 4], [0, 1, 8], [1, 1, 4], [2, 1, 2],
      [-2, 2, 1], [-1, 2, 2], [0, 2, 4], [1, 2, 2], [2, 2, 1]], div: 42,
  },
  burkes: {
    off: [[1, 0, 8], [2, 0, 4],
      [-2, 1, 2], [-1, 1, 4], [0, 1, 8], [1, 1, 4], [2, 1, 2]], div: 32,
  },
  sierra: {
    off: [[1, 0, 5], [2, 0, 3],
      [-2, 1, 2], [-1, 1, 4], [0, 1, 5], [1, 1, 4], [2, 1, 2],
      [-1, 2, 2], [0, 2, 3], [1, 2, 2]], div: 32,
  },
  atkinson: { off: [[1, 0, 1], [2, 0, 1], [-1, 1, 1], [0, 1, 1], [1, 1, 1], [0, 2, 1]], div: 8 },
};

export const KERNEL_TITLES = {
  fs: 'Флойд–Стейнберг — классика, мелкое зерно',
  jarvis: 'Джарвис — мягче, шире разброс',
  stucki: 'Стакки — чище Джарвиса',
  burkes: 'Бёркс — быстрый компромисс',
  sierra: 'Сьерра — спокойное зерно',
  atkinson: 'Аткинсон — контрастный, «маковский»',
};

export const ORDERED = {
  bayer: 'Байер 8×8 — регулярная сетка',
  bluenoise: 'Синий шум — без узора',
};

export const METHODS = {
  none: 'Без дизеринга — плоские заливки',
  ...KERNEL_TITLES,
  ...ORDERED,
};

const BAYER8 = [
  0, 32, 8, 40, 2, 34, 10, 42, 48, 16, 56, 24, 50, 18, 58, 26,
  12, 44, 4, 36, 14, 46, 6, 38, 60, 28, 52, 20, 62, 30, 54, 22,
  3, 35, 11, 43, 1, 33, 9, 41, 51, 19, 59, 27, 49, 17, 57, 25,
  15, 47, 7, 39, 13, 45, 5, 37, 63, 31, 55, 23, 61, 29, 53, 21,
].map((v) => v / 63);

let blueNoise = null;

/** Маска синего шума 64x64: считается один раз в Python, здесь только читается. */
export async function loadBlueNoise(url = 'data/bluenoise.json') {
  if (blueNoise) return blueNoise;
  const raw = await (await fetch(url)).json();
  blueNoise = Float32Array.from(raw.values);
  return blueNoise;
}

export function setBlueNoise(values) {
  blueNoise = Float32Array.from(values);
  return blueNoise;
}

function maskValue(name, x, y) {
  if (name === 'bayer') return BAYER8[(y % 8) * 8 + (x % 8)];
  return blueNoise ? blueNoise[(y % 64) * 64 + (x % 64)] : 0.5;
}

// ── основной вход ────────────────────────────────────────────────────────

/**
 * Вернуть Int32Array длиной w*h — индексы палитры для каждой клетки.
 * mask: Uint8Array, 0 = клетка пустая, её цвет не влияет на соседей.
 */
export function quantize(rgb, w, h, palette, method = 'fs', opts = {}) {
  // usePattern — учитывать ли узор ПРИ ПОДБОРЕ. Показывать его всё равно
  // надо: набор с ячейками рисует то, что игрок увидит.
  const { strength = 1, lumWeight = 1, serpentine = true, mask = null,
          origin = [0, 0], usePattern = true } = opts;
  const n = w * h;
  const lab = toOklabArray(rgb, n);

  if (method === 'none' || strength <= 0) {
    return usePattern ? palette.nearestAt(lab, w, h, origin, lumWeight)
                      : palette.nearest(lab, n, lumWeight);
  }
  if (method in ORDERED) {
    return ordered(lab, w, h, palette, method, strength, lumWeight, origin, usePattern);
  }
  if (!(method in KERNELS)) method = 'fs';
  return diffuse(lab, w, h, palette, method, strength, lumWeight, serpentine, mask, origin,
                 usePattern);
}

function ordered(lab, w, h, palette, method, strength, lumWeight, origin = [0, 0], usePattern = true) {
  const amp = palette.typicalStep(lumWeight) * 0.5 * strength;
  const shifted = new Float32Array(lab.length);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 3;
      const noise = (maskValue(method, x, y) - 0.5) * amp;
      shifted[i] = f(lab[i] + noise);
      shifted[i + 1] = f(lab[i + 1] + noise * 0.35);
      shifted[i + 2] = f(lab[i + 2] + noise * 0.35);
    }
  }
  return usePattern ? palette.nearestAt(shifted, w, h, origin, lumWeight)
                    : palette.nearest(shifted, w * h, lumWeight);
}

/**
 * Диффузия ошибки в OKLab, змейкой, с таблицей подстановки.
 *
 * Узор встроен так, чтобы не замедлить цикл: общая таблица даёт индекс как
 * раньше, а remap одним обращением переписывает его на верный для этой
 * позиции. Ошибка при этом считается от того, что игра ПОКАЖЕТ, — иначе
 * дизеринг размазывает не ту разницу.
 */
function diffuse(lab, w, h, palette, method, strength, lumWeight, serpentine, mask, origin = [0, 0], usePattern = true) {
  const { off, div } = KERNELS[method];
  const weights = off.map(([dx, dy, wt]) => [dx, dy, (wt / div) * strength]);
  const depth = Math.max(...off.map(([, dy]) => dy)) + 1;

  const lut = palette.lut(lumWeight);
  const palLab = palette.lab;
  const scale = [
    (LUT_N - 1) / (LUT_HI[0] - LUT_LO[0]),
    (LUT_N - 1) / (LUT_HI[1] - LUT_LO[1]),
    (LUT_N - 1) / (LUT_HI[2] - LUT_LO[2]),
  ];

  const out = new Int32Array(w * h);
  const row = new Float32Array(w * 3);
  // ошибка копится на несколько строк вперёд, как в numpy-версии
  const err = new Float32Array(depth * w * 3);

  const patterned = palette.patterned && usePattern;
  const size = palette.size;
  const period = palette.period;
  const remapFlat = patterned ? palette.remap(lumWeight) : null;
  const cellLab = patterned ? palette.cellLab() : null;
  const colOf = new Int32Array(w);
  if (patterned) for (let x = 0; x < w; x++) colOf[x] = mod(x + origin[0], period);

  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w * 3; x++) row[x] = f(lab[y * w * 3 + x] + err[x]);

    const reverse = serpentine && (y % 2 === 1);
    const flip = reverse ? -1 : 1;
    // строка 0 картинки — верх, а по игровому Z верх это наибольшая
    const rowClass = patterned ? mod(h - 1 - y + origin[1], period) * period : 0;

    for (let step = 0; step < w; step++) {
      const x = reverse ? w - 1 - step : step;
      if (mask && !mask[y * w + x]) continue;

      const px = x * 3;
      const L = row[px], A = row[px + 1], B = row[px + 2];

      const gi0 = clampIdx((L - LUT_LO[0]) * scale[0]);
      const gi1 = clampIdx((A - LUT_LO[1]) * scale[1]);
      const gi2 = clampIdx((B - LUT_LO[2]) * scale[2]);
      let idx = lut[(gi0 * LUT_N + gi1) * LUT_N + gi2];
      let gotL, gotA, gotB;
      if (patterned) {
        const base = (rowClass + colOf[x]) * size;
        idx = remapFlat[base + idx];
        const spot = (base + idx) * 3;
        gotL = cellLab[spot]; gotA = cellLab[spot + 1]; gotB = cellLab[spot + 2];
      } else {
        gotL = palLab[idx * 3]; gotA = palLab[idx * 3 + 1]; gotB = palLab[idx * 3 + 2];
      }
      out[y * w + x] = idx;

      // без ограничения ошибка на насыщенных краях «взрывается»
      const eL = clampErr(L - gotL);
      const eA = clampErr(A - gotA);
      const eB = clampErr(B - gotB);

      for (let k = 0; k < weights.length; k++) {
        const dx = weights[k][0], dy = weights[k][1], wt = weights[k][2];
        const nx = x + dx * flip;
        if (nx < 0 || nx >= w) continue;
        if (dy === 0) {
          const t = nx * 3;
          row[t] = f(row[t] + eL * wt);
          row[t + 1] = f(row[t + 1] + eA * wt);
          row[t + 2] = f(row[t + 2] + eB * wt);
        } else {
          const t = dy * w * 3 + nx * 3;
          err[t] = f(err[t] + eL * wt);
          err[t + 1] = f(err[t + 1] + eA * wt);
          err[t + 2] = f(err[t + 2] + eB * wt);
        }
      }
    }

    err.copyWithin(0, w * 3);
    err.fill(0, (depth - 1) * w * 3);
  }
  return out;
}

function clampIdx(v) {
  // numpy: clip(..., 0, N-1).astype(int32) — отбрасывание дробной части
  const c = v < 0 ? 0 : (v > LUT_N - 1 ? LUT_N - 1 : v);
  return Math.trunc(c);
}

function clampErr(v) {
  return v < -0.35 ? -0.35 : (v > 0.35 ? 0.35 : v);
}

// ── метрики ──────────────────────────────────────────────────────────────

/** Ошибка «в упор»: по каждому блоку отдельно. Дизеринг её ухудшает. */
export function meanErrorRgb(srcRgb, outRgb, n, mask = null) {
  const a = toOklabArray(srcRgb, n);
  const b = toOklabArray(outRgb, n);
  let sum = 0, count = 0;
  for (let i = 0; i < n; i++) {
    if (mask && !mask[i]) continue;
    const dL = a[i * 3] - b[i * 3];
    const dA = a[i * 3 + 1] - b[i * 3 + 1];
    const dB = a[i * 3 + 2] - b[i * 3 + 2];
    sum += Math.sqrt(dL * dL + dA * dA + dB * dB);
    count++;
  }
  return count ? sum / count : 0;
}
