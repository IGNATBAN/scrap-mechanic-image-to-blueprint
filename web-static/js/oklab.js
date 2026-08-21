// Цветовые пространства. Порт core/quant.py.
//
// Важно: питоновская версия считает во float32, а JavaScript по умолчанию
// работает во float64. Чтобы обе реализации давали одинаковые индексы
// палитры, все промежуточные массивы здесь — Float32Array, а одиночные
// вычисления округляются через Math.fround. Совпадение проверяется
// эталонными векторами (tests/vectors.json), общими с Python.

const f = Math.fround;

export function srgbToLinear(v) {
  const c = f(v / 255);
  return f(c <= 0.04045 ? f(c / 12.92) : f(Math.pow(f((c + 0.055) / 1.055), 2.4)));
}

export function linearToSrgb(v) {
  const c = Math.min(1, Math.max(0, v));
  const out = c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;
  return Math.min(255, Math.max(0, Math.round(out * 255 + 0.5) - (out * 255 + 0.5 >= 0 ? 0 : 0))) | 0;
}

// Точное повторение numpy: clip(out * 255 + 0.5, 0, 255).astype(uint8) — то есть
// прибавили половину и отбросили дробную часть, а не «округлили».
export function linearToByte(v) {
  const c = Math.min(1, Math.max(0, v));
  const out = c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;
  return Math.min(255, Math.max(0, Math.floor(out * 255 + 0.5))) | 0;
}

// Матрицы из работы Björn Ottosson. Значения совпадают с core/quant.py.
const M1 = [
  0.4122214708, 0.5363325363, 0.0514459929,
  0.2119034982, 0.6806995451, 0.1073969566,
  0.0883024619, 0.2817188376, 0.6299787005,
];
const M2 = [
  0.2104542553, 0.7936177850, -0.0040720468,
  1.9779984951, -2.4285922050, 0.4505937099,
  0.0259040371, 0.7827717662, -0.8086757660,
];

/** Линейный RGB (три числа) -> OKLab (три числа), с точностью float32. */
export function linearToOklab(r, g, b, out) {
  const l = f(f(f(M1[0] * r) + f(M1[1] * g)) + f(M1[2] * b));
  const m = f(f(f(M1[3] * r) + f(M1[4] * g)) + f(M1[5] * b));
  const s = f(f(f(M1[6] * r) + f(M1[7] * g)) + f(M1[8] * b));
  const l_ = f(Math.cbrt(Math.max(l, 0)));
  const m_ = f(Math.cbrt(Math.max(m, 0)));
  const s_ = f(Math.cbrt(Math.max(s, 0)));
  out[0] = f(f(f(M2[0] * l_) + f(M2[1] * m_)) + f(M2[2] * s_));
  out[1] = f(f(f(M2[3] * l_) + f(M2[4] * m_)) + f(M2[5] * s_));
  out[2] = f(f(f(M2[6] * l_) + f(M2[7] * m_)) + f(M2[8] * s_));
  return out;
}

const M2_INV = invert3(M2);
const M1_INV = invert3(M1);

function invert3(m) {
  const [a, b, c, d, e, g, h, i, j] = m;
  const det = a * (e * j - g * i) - b * (d * j - g * h) + c * (d * i - e * h);
  return [
    (e * j - g * i) / det, (c * i - b * j) / det, (b * g - c * e) / det,
    (g * h - d * j) / det, (a * j - c * h) / det, (c * d - a * g) / det,
    (d * i - e * h) / det, (b * h - a * i) / det, (a * e - b * d) / det,
  ];
}

/** OKLab -> линейный RGB. Нужен для обратного пересчёта после дизеринга. */
export function oklabToLinear(L, a, b, out) {
  const l_ = M2_INV[0] * L + M2_INV[1] * a + M2_INV[2] * b;
  const m_ = M2_INV[3] * L + M2_INV[4] * a + M2_INV[5] * b;
  const s_ = M2_INV[6] * L + M2_INV[7] * a + M2_INV[8] * b;
  const l = l_ * l_ * l_, m = m_ * m_ * m_, s = s_ * s_ * s_;
  out[0] = M1_INV[0] * l + M1_INV[1] * m + M1_INV[2] * s;
  out[1] = M1_INV[3] * l + M1_INV[4] * m + M1_INV[5] * s;
  out[2] = M1_INV[6] * l + M1_INV[7] * m + M1_INV[8] * s;
  return out;
}

/** Массив sRGB (Uint8Array длиной n*3) -> Float32Array OKLab длиной n*3. */
export function toOklabArray(rgb, n) {
  const out = new Float32Array(n * 3);
  const tmp = new Float32Array(3);
  for (let i = 0; i < n; i++) {
    linearToOklab(
      srgbToLinear(rgb[i * 3]),
      srgbToLinear(rgb[i * 3 + 1]),
      srgbToLinear(rgb[i * 3 + 2]),
      tmp,
    );
    out[i * 3] = tmp[0];
    out[i * 3 + 1] = tmp[1];
    out[i * 3 + 2] = tmp[2];
  }
  return out;
}
