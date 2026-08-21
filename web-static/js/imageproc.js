// Подготовка изображения: кадрирование, масштаб, коррекция, альфа, правки.
// Порт core/imageproc.py.
//
// Самое коварное место всего проекта — масштабирование. Если взять
// canvas.drawImage, результат будет зависеть от браузера и разойдётся с
// десктопной версией на Pillow. Поэтому здесь воспроизведён алгоритм самого
// Pillow: те же ядра фильтров, тот же способ подсчёта коэффициентов и та же
// арифметика с фиксированной точкой (22 бита). Совпадение проверяется
// эталонными векторами.

// ── фильтры Pillow ───────────────────────────────────────────────────────

const FILTERS = {
  nearest: { support: 0 },
  box: { support: 0.5, fn: (x) => (x > -0.5 && x <= 0.5 ? 1.0 : 0.0) },
  bilinear: { support: 1.0, fn: (x) => { const a = x < 0 ? -x : x; return a < 1.0 ? 1.0 - a : 0.0; } },
  lanczos: {
    support: 3.0,
    fn: (x) => {
      if (x <= -3.0 || x >= 3.0) return 0.0;
      return sinc(x) * sinc(x / 3);
    },
  },
};

function sinc(x) {
  if (x === 0.0) return 1.0;
  const t = x * Math.PI;
  return Math.sin(t) / t;
}

const PRECISION_BITS = 32 - 8 - 2;   // ровно как в Pillow

function clip8(v) {
  if (v < 0) return 0;
  if (v > (255 << PRECISION_BITS)) return 255;
  return v >> PRECISION_BITS;
}

/** Коэффициенты фильтра — построчно, как precompute_coeffs в Pillow. */
function precomputeCoeffs(inSize, outSize, filter) {
  const scale = inSize / outSize;
  const filterscale = scale < 1.0 ? 1.0 : scale;
  const support = filter.support * filterscale;
  const ksize = Math.ceil(support) * 2 + 1;

  const bounds = new Int32Array(outSize * 2);
  const kk = new Int32Array(outSize * ksize);
  const pre = new Float64Array(ksize);

  for (let xx = 0; xx < outSize; xx++) {
    const center = (xx + 0.5) * scale;
    const ss = 1.0 / filterscale;
    let xmin = Math.trunc(center - support + 0.5);
    if (xmin < 0) xmin = 0;
    let xmax = Math.trunc(center + support + 0.5);
    if (xmax > inSize) xmax = inSize;
    xmax -= xmin;

    let ww = 0.0;
    for (let x = 0; x < xmax; x++) {
      const w = filter.fn((x + xmin - center + 0.5) * ss);
      pre[x] = w;
      ww += w;
    }
    for (let x = 0; x < xmax; x++) if (ww !== 0.0) pre[x] /= ww;
    for (let x = xmax; x < ksize; x++) pre[x] = 0;

    bounds[xx * 2] = xmin;
    bounds[xx * 2 + 1] = xmax;
    // перевод в фиксированную точку — с тем же округлением, что в Pillow
    for (let x = 0; x < ksize; x++) {
      const v = pre[x] * (1 << PRECISION_BITS);
      kk[xx * ksize + x] = Math.trunc(v >= 0 ? v + 0.5 : v - 0.5);
    }
  }
  return { bounds, kk, ksize };
}

function resampleHorizontal(src, inW, inH, outW, filter) {
  const { bounds, kk, ksize } = precomputeCoeffs(inW, outW, filter);
  const out = new Uint8Array(outW * inH * 3);
  const half = 1 << (PRECISION_BITS - 1);
  for (let y = 0; y < inH; y++) {
    for (let xx = 0; xx < outW; xx++) {
      const xmin = bounds[xx * 2], xmax = bounds[xx * 2 + 1];
      let s0 = half, s1 = half, s2 = half;
      for (let x = 0; x < xmax; x++) {
        const k = kk[xx * ksize + x];
        const p = (y * inW + xmin + x) * 3;
        s0 += src[p] * k;
        s1 += src[p + 1] * k;
        s2 += src[p + 2] * k;
      }
      const o = (y * outW + xx) * 3;
      out[o] = clip8(s0);
      out[o + 1] = clip8(s1);
      out[o + 2] = clip8(s2);
    }
  }
  return out;
}

function resampleVertical(src, inW, inH, outH, filter) {
  const { bounds, kk, ksize } = precomputeCoeffs(inH, outH, filter);
  const out = new Uint8Array(inW * outH * 3);
  const half = 1 << (PRECISION_BITS - 1);
  for (let yy = 0; yy < outH; yy++) {
    const ymin = bounds[yy * 2], ymax = bounds[yy * 2 + 1];
    for (let x = 0; x < inW; x++) {
      let s0 = half, s1 = half, s2 = half;
      for (let y = 0; y < ymax; y++) {
        const k = kk[yy * ksize + y];
        const p = ((ymin + y) * inW + x) * 3;
        s0 += src[p] * k;
        s1 += src[p + 1] * k;
        s2 += src[p + 2] * k;
      }
      const o = (yy * inW + x) * 3;
      out[o] = clip8(s0);
      out[o + 1] = clip8(s1);
      out[o + 2] = clip8(s2);
    }
  }
  return out;
}

function resampleNearest(src, inW, inH, outW, outH) {
  const out = new Uint8Array(outW * outH * 3);
  const sx = inW / outW, sy = inH / outH;
  for (let yy = 0; yy < outH; yy++) {
    const y = Math.min(inH - 1, Math.trunc((yy + 0.5) * sy));
    for (let xx = 0; xx < outW; xx++) {
      const x = Math.min(inW - 1, Math.trunc((xx + 0.5) * sx));
      const p = (y * inW + x) * 3;
      const o = (yy * outW + xx) * 3;
      out[o] = src[p];
      out[o + 1] = src[p + 1];
      out[o + 2] = src[p + 2];
    }
  }
  return out;
}

/** Масштабирование, совпадающее с Pillow. rgb — Uint8Array inW*inH*3. */
export function resize(rgb, inW, inH, outW, outH, name = 'box') {
  if (name === 'nearest' || !FILTERS[name] || !FILTERS[name].fn) {
    if (outW === inW && outH === inH) return rgb.slice();
    return resampleNearest(rgb, inW, inH, outW, outH);
  }
  const filter = FILTERS[name];
  let cur = rgb, curW = inW, curH = inH;
  if (outW !== curW) {                       // Pillow пропускает проход, если размер не меняется
    cur = resampleHorizontal(cur, curW, curH, outW, filter);
    curW = outW;
  }
  if (outH !== curH) {
    cur = resampleVertical(cur, curW, curH, outH, filter);
    curH = outH;
  }
  return cur === rgb ? rgb.slice() : cur;
}

/** auto: пиксель-арт увеличиваем «ближайшим», фото уменьшаем плавно. */
export function pickResample(name, inW, outW) {
  if (name in FILTERS) return name;
  if (outW >= inW) return 'nearest';
  return inW / outW < 2.5 ? 'box' : 'lanczos';
}

// ── коррекция: точная арифметика Pillow ImageEnhance ─────────────────────

/**
 * Image.blend из Pillow. Тонкость, из-за которой результат расходился на
 * единицу: в C параметр alpha объявлен как float, поэтому ВСЁ выражение
 * считается в одинарной точности. Например 1.3f * 90 даёт 116.99998, а не
 * 117.0 — и Pillow отбрасывает дробную часть до 116. Здесь то же самое
 * через Math.fround.
 */
function blend(a, b, alpha) {
  const af = Math.fround(alpha);
  const v = Math.fround(a + Math.fround(af * (b - a)));
  if (alpha >= 0 && alpha <= 1.0) return Math.trunc(v) & 255;
  const t = Math.trunc(v);
  return t < 0 ? 0 : (t > 255 ? 255 : t);
}

/** Яркость по ITU-R 601-2 — ровно как convert("L") в Pillow.
 *  Слагаемое 0x8000 даёт округление, а не отбрасывание: без него (1,2,3)
 *  превращается в 1 вместо 2, и контраст с насыщенностью уезжают. */
function luma(r, g, b) {
  return (r * 19595 + g * 38470 + b * 7471 + 0x8000) >> 16;
}

export function adjust(rgb, n, { brightness = 1, contrast = 1, saturation = 1, gamma = 1 } = {}) {
  let out = rgb;

  if (gamma !== 1) {
    const lut = new Uint8Array(256);
    for (let i = 0; i < 256; i++) {
      const v = Math.pow(i / 255, 1 / Math.max(gamma, 1e-3)) * 255;
      lut[i] = Math.min(255, Math.max(0, Math.trunc(v)));
    }
    out = out === rgb ? rgb.slice() : out;
    for (let i = 0; i < out.length; i++) out[i] = lut[out[i]];
  }

  if (brightness !== 1) {
    out = out === rgb ? rgb.slice() : out;
    for (let i = 0; i < out.length; i++) out[i] = blend(0, out[i], brightness);
  }

  if (contrast !== 1) {
    out = out === rgb ? rgb.slice() : out;
    let sum = 0;
    for (let i = 0; i < n; i++) sum += luma(out[i * 3], out[i * 3 + 1], out[i * 3 + 2]);
    const mean = Math.trunc(sum / n + 0.5);        // ImageStat.Stat(...).mean[0] + 0.5
    for (let i = 0; i < out.length; i++) out[i] = blend(mean, out[i], contrast);
  }

  if (saturation !== 1) {
    out = out === rgb ? rgb.slice() : out;
    for (let i = 0; i < n; i++) {
      const p = i * 3;
      const L = luma(out[p], out[p + 1], out[p + 2]);
      out[p] = blend(L, out[p], saturation);
      out[p + 1] = blend(L, out[p + 1], saturation);
      out[p + 2] = blend(L, out[p + 2], saturation);
    }
  }

  return out === rgb ? rgb.slice() : out;
}

// ── загрузка картинки в браузере ─────────────────────────────────────────

/** Файл -> {rgb, alpha, width, height}. Декодирование делает браузер. */
export async function loadImage(file) {
  const bitmap = await createImageBitmap(file);
  const { width, height } = bitmap;
  const canvas = new OffscreenCanvas(width, height);
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(bitmap, 0, 0);
  bitmap.close();
  const data = ctx.getImageData(0, 0, width, height).data;

  const rgb = new Uint8Array(width * height * 3);
  const alpha = new Uint8Array(width * height);
  for (let i = 0, p = 0; i < width * height; i++, p += 4) {
    rgb[i * 3] = data[p];
    rgb[i * 3 + 1] = data[p + 1];
    rgb[i * 3 + 2] = data[p + 2];
    alpha[i] = data[p + 3];
  }
  return { rgb, alpha, width, height };
}

/** Вырезать прямоугольник из исходника. */
export function crop(src, box) {
  const [x0, y0, x1, y1] = box;
  const w = x1 - x0, h = y1 - y0;
  const rgb = new Uint8Array(w * h * 3);
  const alpha = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const s = ((y0 + y) * src.width + x0 + x);
      const d = y * w + x;
      rgb[d * 3] = src.rgb[s * 3];
      rgb[d * 3 + 1] = src.rgb[s * 3 + 1];
      rgb[d * 3 + 2] = src.rgb[s * 3 + 2];
      alpha[d] = src.alpha[s];
    }
  }
  return { rgb, alpha, width: w, height: h };
}

export function targetSize(srcW, srcH, width, height, keepRatio) {
  const w = Math.max(1, width | 0);
  if (keepRatio || !height) return [w, Math.max(1, Math.round(w * srcH / srcW))];
  return [w, Math.max(1, height | 0)];
}
