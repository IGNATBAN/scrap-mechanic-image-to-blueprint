// Палитра краскопульта и наборы материалов. Порт core/palette.py и core/materials.py.
//
// Первоисточник палитры — файл игры Data/Render/PaintPalette/primary.paintpalette:
// 40 цветов, сетка 10 столбцов на 4 строки. В веб-версии он зашит, потому что
// браузер не может прочитать установленную игру. Для ванильной игры это то же
// самое; моды, меняющие палитру, подхватит только скачанная версия.

import { linearToByte, srgbToLinear, toOklabArray } from './oklab.js';

export const PALETTE_HEX = [
  'EEEEEE', 'F5F071', 'CBF66F', '68FF88', '7EEDED', '4C6FE3', 'AE79F0', 'EE7BF0', 'F06767', 'EEAF5C',
  '7F7F7F', 'E2DB13', 'A0EA00', '19E753', '2CE6E6', '0A3EE2', '7514ED', 'CF11D2', 'D02525', 'DF7F00',
  '4A4A4A', '817C00', '577D07', '0E8031', '118787', '0F2E91', '500AA6', '720A74', '7C0000', '673B00',
  '222222', '323000', '375000', '064023', '0A4444', '0A1D5A', '35086C', '520653', '560202', '472800',
];

export const PALETTE_COLS = 10;
export const PALETTE_ROWS = 4;

// Блок по умолчанию — пластиковая панель: самая ровная матовая поверхность.
export const DEFAULT_BLOCK = 'e981c337-1c8a-449c-8602-1dd990cbba3a';

// Краска почти не видна — такие блоки в наборе бесполезны.
const OPAQUE_LIMIT = 0.9;
const SKIP_NAMES = new Set([
  'blk_metalnet', 'blk_crossnet', 'blk_tryponet', 'blk_stripednet',
  'blk_squarenet', 'blk_placeholderblock_sticky',
]);

let overlays = null;

/** Загрузить таблицу наложений (data/materials.json), собранную из файлов игры. */
export async function loadMaterials(url = 'data/materials.json') {
  if (overlays) return overlays;
  const raw = await (await fetch(url)).json();
  setMaterials(raw);
  return overlays;
}

/** Для тестов в Node: подсунуть уже прочитанный JSON. */
export function setMaterials(raw) {
  overlays = new Map();
  for (const [uuid, e] of Object.entries(raw.blocks || {})) {
    overlays.set(uuid, {
      uuid,
      name: e.name || '',
      alpha: +e.alpha || 0,
      tint: e.tint || [0, 0, 0],
      glass: !!e.glass,
      tiling: e.tiling,
    });
  }
  return overlays;
}

export function materials() {
  return overlays || new Map();
}

/** Блоки, годные для расширения палитры, от самых «чистых» к плотным. */
export function usableBlocks(includeGlass = false) {
  const out = [];
  for (const o of materials().values()) {
    if (o.alpha > OPAQUE_LIMIT || SKIP_NAMES.has(o.name)) continue;
    if (!includeGlass && o.glass) continue;
    out.push(o);
  }
  out.sort((a, b) => (a.alpha - b.alpha) || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  return out;
}

export function hexToRgb(hex) {
  return [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16)];
}

export function rgbToHex(r, g, b) {
  return ((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1).toUpperCase();
}

/**
 * Как будет выглядеть краска на этом блоке.
 * Модель снята с файлов игры: итог = краска x (1 - alpha) + тон.
 */
export function applyOverlay(rgb, overlay) {
  const out = new Uint8Array(rgb.length);
  const keep = 1 - overlay.alpha;
  for (let i = 0; i < rgb.length; i += 3) {
    out[i] = linearToByte(srgbToLinear(rgb[i]) * keep + overlay.tint[0]);
    out[i + 1] = linearToByte(srgbToLinear(rgb[i + 1]) * keep + overlay.tint[1]);
    out[i + 2] = linearToByte(srgbToLinear(rgb[i + 2]) * keep + overlay.tint[2]);
  }
  return out;
}

/**
 * Собрать набор «цвет краски + блок».
 * dedupe — минимальное расстояние в OKLab между соседями набора: без него
 * половина комбинаций дублирует друг друга и только замедляет подбор.
 */
export function buildPalette(paintHex, baseBlock, extraBlocks = [], dedupe = 0.012) {
  const paints = new Uint8Array(paintHex.length * 3);
  paintHex.forEach((h, i) => {
    const [r, g, b] = hexToRgb(h);
    paints[i * 3] = r; paints[i * 3 + 1] = g; paints[i * 3 + 2] = b;
  });

  const all = materials();
  const chosen = [];
  const base = all.get(baseBlock);
  if (base) chosen.push(base);
  for (const uuid of extraBlocks) {
    const o = all.get(uuid);
    if (o && o.uuid !== baseBlock && o.alpha <= OPAQUE_LIMIT) chosen.push(o);
  }

  if (!chosen.length) {
    return { rgb: paints, paint: [...paintHex], block: paintHex.map(() => baseBlock) };
  }

  const rgbList = [];
  const paintOf = [];
  const blockOf = [];
  for (const overlay of chosen) {           // базовый блок первым — он в приоритете
    const shown = applyOverlay(paints, overlay);
    for (let i = 0; i < paintHex.length; i++) {
      rgbList.push(shown[i * 3], shown[i * 3 + 1], shown[i * 3 + 2]);
      paintOf.push(paintHex[i]);
      blockOf.push(overlay.uuid);
    }
  }

  const rgb = Uint8Array.from(rgbList);
  if (dedupe <= 0) return { rgb, paint: paintOf, block: blockOf };

  // жадно оставляем только заметно различающиеся цвета, приоритет — порядок выше
  const n = rgb.length / 3;
  const lab = toOklabArray(rgb, n);
  const limit = dedupe * dedupe;
  const keep = [];
  const keptLab = [];
  for (let i = 0; i < n; i++) {
    const L = lab[i * 3], A = lab[i * 3 + 1], B = lab[i * 3 + 2];
    let tooClose = false;
    for (let k = 0; k < keptLab.length; k += 3) {
      const dL = keptLab[k] - L, dA = keptLab[k + 1] - A, dB = keptLab[k + 2] - B;
      if (dL * dL + dA * dA + dB * dB < limit) { tooClose = true; break; }
    }
    if (tooClose) continue;
    keep.push(i);
    keptLab.push(L, A, B);
  }

  const outRgb = new Uint8Array(keep.length * 3);
  keep.forEach((src, i) => {
    outRgb[i * 3] = rgb[src * 3];
    outRgb[i * 3 + 1] = rgb[src * 3 + 1];
    outRgb[i * 3 + 2] = rgb[src * 3 + 2];
  });
  return {
    rgb: outRgb,
    paint: keep.map((i) => paintOf[i]),
    block: keep.map((i) => blockOf[i]),
  };
}

/** Палитра для отрисовки в интерфейсе. */
export function swatches() {
  return PALETTE_HEX.map((hex, i) => ({
    hex, row: Math.floor(i / PALETTE_COLS), col: i % PALETTE_COLS,
  }));
}
