// Конвейер целиком в браузере: то же, что делает web/app.py на сервере.
// Картинка -> кадрирование -> масштаб -> коррекция -> альфа -> материалы ->
// склейка -> дробление -> текст чертежа.

import * as img from './imageproc.js';
import { toOklabArray } from './oklab.js';
import * as pal from './palette.js';
import { Palette, quantize, meanErrorRgb } from './quant.js';
import { mergeRects, splitRects, colorKeys, stats } from './mesh.js';
import * as tiles from './tiles.js';
import * as bp from './blueprint.js';
import { I18N } from './i18n.js';

// Бюджет на один расчёт. Больше — интерфейс начинает подвисать, а чертёж
// такого размера всё равно не собрать: упирается не в клетки, а в число
// деталей (на ширине 1200 их под два миллиона).
export const MAX_CELLS = 2_000_000;
export const PARTS_SOFT = 50_000;

const paletteCache = new Map();

function getPalette(baseBlock, extraBlocks, dedupe, withCells = true) {
  const key = `${baseBlock}|${extraBlocks.join(',')}|${dedupe}|${withCells ? 1 : 0}`;
  let p = paletteCache.get(key);
  if (!p) {
    p = new Palette(pal.buildPalette(pal.PALETTE_HEX, baseBlock, extraBlocks, dedupe, withCells));
    if (paletteCache.size > 8) paletteCache.clear();
    paletteCache.set(key, p);
  }
  return p;
}

/**
 * Посчитать сетку блоков.
 * source — то, что вернул imageproc.loadImage.
 */
export function buildGrid(source, params) {
  const {
    crop = null, width = 128, height = 0, keepRatio = true, resample = 'auto',
    colorMode = 'exact', method = 'none', strength = 1, lumWeight = 1, serpentine = true,
    baseBlock = pal.DEFAULT_BLOCK, extraBlocks = [], dedupe = 0.012,
    alphaMode = 'cutout', alphaThreshold = 128, background = 'FFFFFF',
    brightness = 1, contrast = 1, saturation = 1, gamma = 1, flipH = false,
    edits = null,
    // Узор текстуры блока. Он привязан к локальным координатам чертежа, а
    // они зависят от ориентации. При дроблении фазу не предсказать: у
    // сваренного тела своя система координат, и общий сдвиг останется
    // неизвестным — а неверная фаза хуже, чем никакой.
    pattern = true, patternKnown = true, orientation = 'vertical',
  } = params;

  let src = source;
  if (crop) {
    const box = cropBox(source.width, source.height, crop);
    if (box) src = img.crop(source, box);
  }

  let [w, h] = img.targetSize(src.width, src.height, width, height, keepRatio);

  // Не отказываем, а считаем в уменьшенном виде: запрет посреди работы
  // бесполезен, а картинка с честной пометкой — полезна.
  let clamped = null;
  if (w * h > MAX_CELLS) {
    const shrink = Math.sqrt(MAX_CELLS / (w * h));
    const safeW = Math.max(8, Math.floor(w * shrink));
    clamped = { requestedWidth: w, requestedHeight: h, requestedCells: w * h, usedWidth: safeW };
    [w, h] = img.targetSize(src.width, src.height, safeW, 0, true);
  }

  const filter = img.pickResample(resample, src.width, w);
  let rgb = img.resize(src.rgb, src.width, src.height, w, h, filter);
  const alpha = resizeAlpha(src.alpha, src.width, src.height, w, h, filter);
  if (flipH) {
    rgb = flipRgb(rgb, w, h);
    flipAlpha(alpha, w, h);
  }

  const n = w * h;
  const mask = new Uint8Array(n);
  if (alphaMode === 'flatten') {
    const bg = pal.hexToRgb(background);
    for (let i = 0; i < n; i++) {
      const a = alpha[i] / 255;
      for (let k = 0; k < 3; k++) {
        rgb[i * 3 + k] = Math.min(255, Math.max(0, Math.round(rgb[i * 3 + k] * a + bg[k] * (1 - a))));
      }
      mask[i] = 1;
    }
  } else {
    for (let i = 0; i < n; i++) {
      const on = alpha[i] >= alphaThreshold ? 1 : 0;
      mask[i] = on;
      if (!on) {                       // прозрачное не должно «протекать» в дизеринг
        rgb[i * 3] = 0; rgb[i * 3 + 1] = 0; rgb[i * 3 + 2] = 0;
      }
    }
  }

  rgb = img.adjust(rgb, n, { brightness, contrast, saturation, gamma });

  let keys;
  let palette = null;
  let shown = rgb;
  let error = 0;

  let origin = [0, 0];
  if (colorMode === 'palette') {
    origin = bp.originOf(w, h, orientation, true);
    // Ячейки нужны всегда, когда фаза известна: даже если подбор их не
    // использует, предпросмотр обязан показывать то, что покажет игра.
    palette = getPalette(baseBlock, extraBlocks, dedupe, patternKnown);
    keys = quantize(rgb, w, h, palette, method,
                    { strength, lumWeight, serpentine, mask, origin, usePattern: pattern });
    // предпросмотр показывает узор: цвет берётся для той позиции, в
    // которой блок окажется в постройке
    shown = palette.shown(keys, w, h, origin);
    error = meanErrorRgb(rgb, shown, n, mask);
  } else {
    keys = colorKeys(rgb, n);
  }

  const grid = { rgb: shown, keys, mask, width: w, height: h, palette, error, clamped,
                 origin, sourceWidth: source.width, sourceHeight: source.height };
  if (edits && edits.length) applyEdits(grid, edits);
  return grid;
}

function cropBox(sw, sh, crop) {
  const [x, y, cw, ch] = crop;
  const x0 = Math.max(0, Math.min(sw - 1, Math.round(x)));
  const y0 = Math.max(0, Math.min(sh - 1, Math.round(y)));
  const x1 = Math.max(x0 + 1, Math.min(sw, Math.round(x + cw)));
  const y1 = Math.max(y0 + 1, Math.min(sh, Math.round(y + ch)));
  if (x0 === 0 && y0 === 0 && x1 === sw && y1 === sh) return null;
  return [x0, y0, x1, y1];
}

/** Альфу масштабируем тем же фильтром, что и цвет — иначе края «поедут». */
function resizeAlpha(alpha, inW, inH, outW, outH, filter) {
  const triple = new Uint8Array(inW * inH * 3);
  for (let i = 0; i < inW * inH; i++) {
    triple[i * 3] = alpha[i]; triple[i * 3 + 1] = alpha[i]; triple[i * 3 + 2] = alpha[i];
  }
  const scaled = img.resize(triple, inW, inH, outW, outH, filter);
  const out = new Uint8Array(outW * outH);
  for (let i = 0; i < outW * outH; i++) out[i] = scaled[i * 3];
  return out;
}

function flipRgb(rgb, w, h) {
  const out = new Uint8Array(rgb.length);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const s = (y * w + x) * 3, d = (y * w + (w - 1 - x)) * 3;
      out[d] = rgb[s]; out[d + 1] = rgb[s + 1]; out[d + 2] = rgb[s + 2];
    }
  }
  return out;
}

function flipAlpha(alpha, w, h) {
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w >> 1; x++) {
      const a = y * w + x, b = y * w + (w - 1 - x);
      const t = alpha[a]; alpha[a] = alpha[b]; alpha[b] = t;
    }
  }
}

/** Ручные правки кистью: [[x, y, "RRGGBB"], [x, y, null], ...]. */
export function applyEdits(grid, edits) {
  const lookup = new Map();
  for (const item of edits) {
    const x = item[0] | 0, y = item[1] | 0, value = item[2];
    if (x < 0 || y < 0 || x >= grid.width || y >= grid.height) continue;
    const i = y * grid.width + x;

    if (value === null || value === undefined) { grid.mask[i] = 0; continue; }

    const hex = String(value).replace('#', '').slice(0, 6).toUpperCase();
    if (hex.length !== 6) continue;
    let key = lookup.get(hex);
    if (key === undefined) { key = keyFor(grid, hex); lookup.set(hex, key); }
    grid.keys[i] = key;
    grid.mask[i] = 1;
    let r, g, b;
    if (!grid.palette) {
      r = (key >> 16) & 255; g = (key >> 8) & 255; b = key & 255;
    } else if (grid.palette.patterned) {
      // цвет зависит от места: та же краска рядом ляжет иначе
      const p = grid.palette.period;
      const [ox, oz] = grid.origin || [0, 0];
      const cz = ((grid.height - 1 - y + oz) % p + p) % p;
      const cx = ((x + ox) % p + p) % p;
      const s = ((cz * p + cx) * grid.palette.size + key) * 3;
      r = grid.palette.cells[s]; g = grid.palette.cells[s + 1]; b = grid.palette.cells[s + 2];
    } else {
      r = grid.palette.rgb[key * 3];
      g = grid.palette.rgb[key * 3 + 1];
      b = grid.palette.rgb[key * 3 + 2];
    }
    grid.rgb[i * 3] = r; grid.rgb[i * 3 + 1] = g; grid.rgb[i * 3 + 2] = b;
  }
}

function keyFor(grid, hex) {
  const [r, g, b] = pal.hexToRgb(hex);
  if (!grid.palette) return (r << 16) | (g << 8) | b;
  // Кисть красит цветом из палитры, но пользователь мог взять пипеткой
  // произвольный оттенок — берём ближайший материал в OKLab, как и везде.
  const lab = toOklabArray(Uint8Array.from([r, g, b]), 1);
  return grid.palette.nearest(lab, 1)[0];
}

/** Склейка + сводка. */
export function meshGrid(grid, { merge = true, maxBound = 255 } = {}) {
  const rects = merge
    ? mergeRects(grid.keys, grid.mask, grid.width, grid.height, maxBound)
    : splitRects(grid.keys, grid.mask, grid.width, grid.height);
  return { rects, summary: stats(rects) };
}

/** Разбивка на модули. */
export function splitGrid(rects, grid, { cols = 1, rows = 1, edgesX = null, edgesY = null } = {}) {
  if (cols * rows <= 1 && !edgesX && !edgesY) return [];
  return tiles.cut(rects, grid.width, grid.height, cols, rows, edgesX, edgesY);
}

/** Собрать чертежи: один или по модулю на каждый. */
export function makeBlueprints(grid, rects, opts) {
  const {
    name = '', orientation = 'vertical', depth = 1,
    baseBlock = pal.DEFAULT_BLOCK, moduleList = [], onlyModule = null,
  } = opts;

  const resolve = grid.palette
    ? bp.paletteResolver(grid.palette, baseBlock)
    : bp.rgbResolver(baseBlock);

  if (!moduleList.length) {
    return [{
      name,
      text: bp.buildJson(rects, grid.width, grid.height, resolve, orientation, true, depth),
      note: I18N.t('doc.blueprintNote', { w: grid.width, h: grid.height, parts: rects.length }),
      tile: null,
    }];
  }

  const rows = Math.max(...moduleList.map((t) => t.row)) + 1;
  const cols = Math.max(...moduleList.map((t) => t.col)) + 1;
  let chosen = [...moduleList].sort((a, b) => a.order - b.order);
  if (onlyModule) chosen = chosen.filter((t) => t.label === onlyModule);

  return chosen.map((tile) => ({
    name: `${name} ${tile.label}`,
    text: bp.buildJson(tile.rects, tile.width, tile.height, resolve, orientation, true, depth),
    note: I18N.t('doc.moduleNote', {
      label: tile.label, modules: cols * rows,
      w: tile.width, h: tile.height, parts: tile.parts,
    }),
    tile,
  }));
}

export { tiles, bp, pal, img };
