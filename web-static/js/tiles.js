// Дробление картинки на модули — отдельные чертежи, которые сваривают в игре.
// Порт core/tiles.py.
//
// Режем НЕ пересчитывая склейку заново, а разрезая готовые прямоугольники по
// границам модулей. Поэтому сумма модулей даёт ровно ту же картинку, что и
// цельный чертёж, а число деталей в каждом известно заранее.

export const TARGET_PARTS = 8000;
export const LIST_LIMIT = 48;

/** Границы модулей — куски максимально равные. */
export function edges(total, parts) {
  parts = Math.max(1, Math.min(parts | 0, total | 0));
  const out = [];
  for (let i = 0; i <= parts; i++) out.push(Math.round(i * total / parts));
  return out;
}

/** Границы под заданный размер модуля в блоках (последний может быть короче). */
export function edgesBySize(total, size) {
  size = Math.max(1, Math.min(size | 0, total | 0));
  const out = [];
  for (let v = 0; v < total; v += size) out.push(v);
  out.push(total);
  return out;
}

/** Проверить границы, пришедшие из интерфейса (их можно тянуть мышью). */
export function cleanEdges(total, raw, fallback) {
  if (!raw || !raw.length) return fallback;
  const set = new Set();
  for (const v of raw) {
    const n = Math.max(0, Math.min(total, v | 0));
    if (n > 0 && n < total) set.add(n);
  }
  return [0, ...[...set].sort((a, b) => a - b), total];
}

/** Метка модуля «ряд-столбец»: ряды снизу, столбцы слева. */
export function label(row, col, rows, cols) {
  const pad = Math.max(rows, cols) >= 10 ? 2 : 1;
  return `${String(rows - row).padStart(pad, '0')}-${String(col + 1).padStart(pad, '0')}`;
}

function findEdge(list, value) {
  // аналог bisect_right(list, value) - 1
  let lo = 0, hi = list.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (list[mid] <= value) lo = mid + 1; else hi = mid;
  }
  return lo - 1;
}

/** Разрезать прямоугольники по сетке модулей. Координаты внутри модуля — свои. */
export function cut(rects, gridW, gridH, cols, rows, edgesX = null, edgesY = null) {
  const ex = edgesX && edgesX.length ? edgesX : edges(gridW, cols);
  const ey = edgesY && edgesY.length ? edgesY : edges(gridH, rows);
  const nCols = ex.length - 1;
  const nRows = ey.length - 1;

  const grid = [];
  for (let r = 0; r < nRows; r++) {
    for (let c = 0; c < nCols; c++) {
      grid.push({
        col: c,
        row: r,
        order: (nRows - r - 1) * nCols + c + 1,
        x0: ex[c],
        y0: ey[r],
        width: ex[c + 1] - ex[c],
        height: ey[r + 1] - ey[r],
        rects: [],
      });
    }
  }

  for (const [x, y, w, h, key] of rects) {
    const c0 = Math.max(0, findEdge(ex, x));
    const c1 = Math.max(0, Math.min(nCols - 1, findEdge(ex, x + w - 1)));
    const r0 = Math.max(0, findEdge(ey, y));
    const r1 = Math.max(0, Math.min(nRows - 1, findEdge(ey, y + h - 1)));
    for (let r = r0; r <= r1; r++) {
      const ty0 = Math.max(y, ey[r]);
      const ty1 = Math.min(y + h, ey[r + 1]);
      for (let c = c0; c <= c1; c++) {
        const tx0 = Math.max(x, ex[c]);
        const tx1 = Math.min(x + w, ex[c + 1]);
        grid[r * nCols + c].rects.push([tx0 - ex[c], ty0 - ey[r], tx1 - tx0, ty1 - ty0, key]);
      }
    }
  }

  for (const t of grid) {
    t.parts = t.rects.length;
    t.label = label(t.row, t.col, nRows, nCols);
  }
  return grid;
}

/** Сколько деталей окажется в каждом модуле — без фактического разрезания. */
export function count(rects, gridW, gridH, cols, rows, edgesX = null, edgesY = null) {
  const ex = edgesX && edgesX.length ? edgesX : edges(gridW, cols);
  const ey = edgesY && edgesY.length ? edgesY : edges(gridH, rows);
  const nCols = ex.length - 1;
  const nRows = ey.length - 1;
  const flat = new Int32Array(nCols * nRows);
  let total = 0;

  for (const [x, y, w, h] of rects) {
    const c0 = Math.max(0, findEdge(ex, x));
    const c1 = Math.max(0, Math.min(nCols - 1, findEdge(ex, x + w - 1)));
    const r0 = Math.max(0, findEdge(ey, y));
    const r1 = Math.max(0, Math.min(nRows - 1, findEdge(ey, y + h - 1)));
    for (let r = r0; r <= r1; r++) {
      for (let c = c0; c <= c1; c++) { flat[r * nCols + c]++; total++; }
    }
  }
  return { counts: flat, total, cols: nCols, rows: nRows };
}

function squareLayout(modules, gridW, gridH) {
  modules = Math.max(1, modules | 0);
  const cols = Math.max(1, Math.min(gridW, Math.round(Math.sqrt(modules * gridW / Math.max(1, gridH)))));
  const rows = Math.max(1, Math.min(gridH, Math.ceil(modules / cols)));
  return [cols, rows];
}

/**
 * Разумный набор раскладок — вместо перебора миллионов вариантов.
 * Мелкие перебираем полностью, крупные берём по геометрической шкале и
 * добавляем ту, что вытекает прямо из потолка на модуль.
 */
function candidates(rectCount, gridW, gridH, target) {
  const out = new Set();
  const add = (c, r) => {
    if (c >= 1 && r >= 1 && c <= gridW && r <= gridH) out.add(`${c}x${r}`);
  };
  for (let c = 1; c <= Math.min(8, gridW); c++) {
    for (let r = 1; r <= Math.min(8, gridH); r++) add(c, r);
  }
  for (const m of [10, 12, 16, 20, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024, 2048, 4096, 8192, 16384]) {
    add(...squareLayout(m, gridW, gridH));
  }
  if (target > 0 && rectCount > target) {
    const need = Math.ceil(rectCount / target);
    for (const extra of [need, need + 1, Math.floor(need * 1.25) + 1, Math.floor(need * 1.6) + 1, need * 2]) {
      add(...squareLayout(extra, gridW, gridH));
    }
  }
  // порядок как в Python: sorted() по (столбцы, ряды) — от него зависит,
  // какой вариант останется при равной вытянутости
  return [...out].map((s) => s.split('x').map(Number))
    .sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));
}

/**
 * Посчитать варианты разбивки и выбрать рекомендуемый.
 * Из подходящих берём не просто самый малочисленный: длинная полоса формально
 * даёт меньше модулей, чем сетка, но ставить и сваривать её заметно неудобнее.
 */
export function plan(rects, gridW, gridH, target = TARGET_PARTS) {
  const bestByCount = new Map();

  for (const [cols, rows] of candidates(rects.length, gridW, gridH, target)) {
    const modules = cols * rows;
    const tileW = gridW / cols, tileH = gridH / rows;
    const skew = Math.max(tileW, tileH) / Math.max(1e-6, Math.min(tileW, tileH));
    const prev = bestByCount.get(modules);
    if (prev && prev.skew <= skew) continue;
    bestByCount.set(modules, { cols, rows, skew });
  }

  // Прореживать надо ПОСЛЕ выбора лучшей формы, а не до: иначе из пары
  // 3x1 / 1x3 может остаться вытянутый вариант. Сам выбор формы стоит
  // копейки, дорог только проход по деталям в count().
  let moduleCounts = [...bestByCount.keys()].sort((a, b) => a - b);
  const budget = Math.max(6, Math.min(48, Math.floor(48 * 200000 / Math.max(1, rects.length))));
  if (moduleCounts.length > budget) {
    const step = moduleCounts.length / budget;
    const keep = new Set();
    for (let i = 0; i < budget; i++) {
      keep.add(moduleCounts[Math.min(moduleCounts.length - 1, Math.trunc(i * step))]);
    }
    keep.add(moduleCounts[0]);
    if (target) {
      const need = Math.ceil(rects.length / Math.max(1, target));
      let closest = moduleCounts[0];
      for (const m of moduleCounts) {
        if (Math.abs(m - need) < Math.abs(closest - need)) closest = m;
      }
      keep.add(closest);
    }
    moduleCounts = [...keep].sort((a, b) => a - b);
  }

  let options = moduleCounts.map((modules) => {
    const cand = bestByCount.get(modules);
    const { counts } = count(rects, gridW, gridH, cand.cols, cand.rows);
    const ex = edges(gridW, cand.cols), ey = edges(gridH, cand.rows);
    let maxParts = 0, minParts = Infinity, total = 0;
    for (const v of counts) {
      if (v > maxParts) maxParts = v;
      if (v < minParts) minParts = v;
      total += v;
    }
    let tw = 0, th = 0;
    for (let i = 0; i < ex.length - 1; i++) tw = Math.max(tw, ex[i + 1] - ex[i]);
    for (let i = 0; i < ey.length - 1; i++) th = Math.max(th, ey[i + 1] - ey[i]);
    return {
      cols: cand.cols, rows: cand.rows, modules: counts.length,
      maxParts, minParts: minParts === Infinity ? 0 : minParts, totalParts: total,
      tileWidth: tw, tileHeight: th,
      overhead: total - rects.length,
      skew: +cand.skew.toFixed(2),
    };
  });

  const fits = options.filter((o) => o.maxParts <= target);
  let recommended = null;
  if (fits.length) {
    recommended = fits.reduce((a, b) => (score(a) <= score(b) ? a : b));
  } else if (options.length) {
    recommended = options.reduce((a, b) => (a.maxParts <= b.maxParts ? a : b));
  }

  // список не должен разрастаться: при тысячах модулей он бесполезен
  if (options.length > LIST_LIMIT) {
    const half = LIST_LIMIT >> 1;
    const keep = new Set(options.slice(0, half));
    const tail = options.slice(half);
    const step = Math.max(1, Math.floor(tail.length / half));
    for (let i = 0; i < tail.length; i += step) keep.add(tail[i]);
    if (recommended) keep.add(recommended);
    options = options.filter((o) => keep.has(o));
  }

  return {
    options,
    target,
    recommended: recommended ? { cols: recommended.cols, rows: recommended.rows } : null,
    wholeParts: rects.length,
  };
}

function score(o) {
  return o.modules * (1 + 0.35 * (o.skew - 1));
}

/** Памятка по сборке — на основе того, что пишет о сварке сама игра. */
export function instructions(name, cols, rows, counts, orientation) {
  const total = counts.reduce((a, b) => a + b, 0);
  const vertical = orientation !== 'horizontal';
  const step3 = vertical
    ? '3. Ставьте соседние модули вплотную, ряд за рядом: сначала весь нижний\r\n   ряд слева направо, потом следующий — и так вверх.'
    : '3. Ставьте соседние модули вплотную, ряд за рядом: сначала ближний\r\n   ряд слева направо, потом следующий — и так от себя.';

  const lines = [
    `СБОРКА «${name}» — ${cols}×${rows} = ${cols * rows} модулей, ${total} деталей всего`,
    '',
    'Модули названы «ряд-столбец»: ряды считаются СНИЗУ, столбцы — слева направо.',
    `1-1 — левый нижний угол картинки, ${rows}-${cols} — правый верхний.`,
    '',
    'ПОРЯДОК:',
    '1. Возьмите сварочный аппарат (Weld Tool).',
    '2. Поставьте подъёмник и разместите на нём модуль 1-1.',
    step3,
    '4. Соединяйте сварочным аппаратом: наведите на одну несоединённую конструкцию,',
    '   ЛКМ, затем на соседнюю — ЛКМ. Игра пишет об этом так:',
    '   «Также можно соединять соприкасающиеся детали, которые находятся на подъёмнике».',
    '   То есть на подъёмнике стыковка проще всего — собирайте там.',
    '',
    'РАЗМЕР МОДУЛЕЙ (деталей):',
  ];

  for (let r = rows; r >= 1; r--) {
    const rowCounts = counts.slice((rows - r) * cols, (rows - r + 1) * cols);
    lines.push(`  ряд ${r}:  ` + rowCounts.map((n, c) => `${r}-${c + 1}: ${n}`).join('   '));
  }

  lines.push(
    '',
    'Стыки ровные, внахлёст ставить не нужно — модули граничат встык.',
    'Если какой-то модуль не грузится — он слишком тяжёлый: пересоберите с',
    'большим числом модулей или меньшей шириной картинки.',
  );
  return lines.join('\r\n');
}
