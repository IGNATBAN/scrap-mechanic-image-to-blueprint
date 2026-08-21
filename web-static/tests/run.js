// Сверка JavaScript-ядра с эталонными векторами, общими с Python.
//
// Работает и в браузере (tests/index.html), и в Node (CI). Если правка
// меняет результат хоть на один блок — здесь становится красно, а не у
// пользователя, который заметит разницу между сайтом и программой.

import { toOklabArray } from '../js/oklab.js';
import * as pal from '../js/palette.js';
import { Palette, quantize, setBlueNoise } from '../js/quant.js';
import { mergeRects, colorKeys } from '../js/mesh.js';
import * as tiles from '../js/tiles.js';
import * as bp from '../js/blueprint.js';
import * as img from '../js/imageproc.js';

/** sha256 первых 16 знаков — тот же приём, что в Python. */
async function digest(bytes) {
  const buf = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 16);
}

function b64ToBytes(b64) {
  if (typeof atob === 'function') {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  return new Uint8Array(Buffer.from(b64, 'base64'));
}

/** Python пишет int32 в little-endian — так же упаковываем здесь. */
function int32Bytes(values) {
  const out = new Int32Array(values.length);
  out.set(values);
  return new Uint8Array(out.buffer);
}

function int64Bytes(rows) {
  const out = new BigInt64Array(rows.length * 5);
  rows.forEach((r, i) => {
    for (let k = 0; k < 5; k++) out[i * 5 + k] = BigInt(r[k]);
  });
  return new Uint8Array(out.buffer);
}

export async function runTests({ vectors, materials, bluenoise }) {
  const results = [];
  const ok = (name, pass, extra = '') => results.push({ name, pass, extra: String(extra) });

  pal.setMaterials(materials);
  setBlueNoise(bluenoise.values);

  const v = vectors;
  const w = v.image.width, h = v.image.height;
  const rgb = b64ToBytes(v.image.rgb);
  const packedMask = b64ToBytes(v.image.mask);
  const mask = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) {
    mask[i] = (packedMask[i >> 3] >> (7 - (i & 7))) & 1;
  }

  // ── палитра и цветовое пространство ────────────────────────────────────
  ok('палитра совпадает', JSON.stringify(pal.PALETTE_HEX) === JSON.stringify(v.paletteHex));

  const probe = Uint8Array.from(v.oklab.rgb.flat());
  const lab = toOklabArray(probe, v.oklab.rgb.length);
  let labOk = true, labWorst = 0;
  v.oklab.lab.forEach((exp, i) => {
    for (let k = 0; k < 3; k++) {
      const d = Math.abs(lab[i * 3 + k] - exp[k]);
      if (d > labWorst) labWorst = d;
      if (d > 1e-5) labOk = false;
    }
  });
  ok('OKLab совпадает с Python', labOk, `худшее расхождение ${labWorst.toExponential(2)}`);

  // ── наложения блоков ──────────────────────────────────────────────────
  let overlayBad = [];
  for (const [uuid, exp] of Object.entries(v.materials)) {
    const o = pal.materials().get(uuid);
    if (!o || Math.abs(o.alpha - exp.alpha) > 1e-4) { overlayBad.push(uuid); continue; }
    const got = pal.applyOverlay(Uint8Array.from([223, 127, 0]), o);
    if (got[0] !== exp.onOrange[0] || got[1] !== exp.onOrange[1] || got[2] !== exp.onOrange[2]) {
      overlayBad.push(uuid + ' цвет');
    }
  }
  ok('наложение текстуры на краску', !overlayBad.length, overlayBad.slice(0, 2).join(', '));

  // ── наборы материалов ─────────────────────────────────────────────────
  const basePal = new Palette(pal.buildPalette(v.paletteHex, pal.DEFAULT_BLOCK));
  const widePal = new Palette(pal.buildPalette(v.paletteHex, pal.DEFAULT_BLOCK, v.extraBlocks));
  ok('набор из одной краски', await digest(basePal.rgb) === v.palettes.base.rgbHash,
    `${basePal.size} против ${v.palettes.base.size}`);
  ok('набор с блоками', await digest(widePal.rgb) === v.palettes.wide.rgbHash,
    `${widePal.size} против ${v.palettes.wide.size}`);

  // ── квантование ───────────────────────────────────────────────────────
  const badQuant = [];
  for (const [name, exp] of Object.entries(v.quantize)) {
    const [method, lw] = name.split('@');
    const keys = quantize(rgb, w, h, basePal, method,
      { strength: 1, lumWeight: parseFloat(lw), serpentine: true, mask });
    if (await digest(int32Bytes(keys)) !== exp.hash) badQuant.push(name);
  }
  ok('квантование во всех режимах', !badQuant.length,
    badQuant.length ? `разошлись: ${badQuant.join(', ')}` : `${Object.keys(v.quantize).length} вариантов`);

  // ── склейка ───────────────────────────────────────────────────────────
  const packed = colorKeys(rgb, w * h);
  const palKeys = quantize(rgb, w, h, basePal, 'none', { mask });
  const badMesh = [];
  for (const [name, exp] of Object.entries(v.mesh)) {
    const [src, bound] = name.split('@');
    const rects = mergeRects(src === 'packed' ? packed : palKeys, mask, w, h, parseInt(bound, 10));
    if (rects.length !== exp.count || await digest(int64Bytes(rects)) !== exp.hash) {
      badMesh.push(`${name} (${rects.length} против ${exp.count})`);
    }
  }
  ok('склейка совпадает', !badMesh.length, badMesh.join('; '));

  // ── дробление ─────────────────────────────────────────────────────────
  const rects = mergeRects(palKeys, mask, w, h, 255);
  const badTiles = [];
  for (const [name, exp] of Object.entries(v.tiles)) {
    const [cols, rows] = name.split('x').map(Number);
    const cutTiles = tiles.cut(rects, w, h, cols, rows).sort((a, b) => a.order - b.order);
    const parts = cutTiles.map((t) => t.parts);
    const labels = cutTiles.map((t) => t.label);
    if (JSON.stringify(parts) !== JSON.stringify(exp.parts)) badTiles.push(name + ' детали');
    else if (JSON.stringify(labels) !== JSON.stringify(exp.labels)) badTiles.push(name + ' метки');
  }
  ok('дробление совпадает', !badTiles.length, badTiles.join(', '));

  const planned = tiles.plan(rects, w, h, 40);
  ok('рекомендация дробления', JSON.stringify(planned.recommended) === JSON.stringify(v.plan.recommended),
    `${JSON.stringify(planned.recommended)} против ${JSON.stringify(v.plan.recommended)}`);

  // ── текст чертежа ─────────────────────────────────────────────────────
  const enc = new TextEncoder();
  const badBp = [];
  for (const [name, exp] of Object.entries(v.blueprint)) {
    const [orient, depth] = name.split('@');
    const text = bp.buildJson(rects, w, h, bp.rgbResolver(pal.DEFAULT_BLOCK),
      orient, true, parseInt(depth, 10));
    const hash = await digest(enc.encode(text));
    if (hash !== exp.sha256) {
      badBp.push(`${name}: ${text.length} против ${exp.length} знаков`);
    }
  }
  ok('текст чертежа бит в бит', !badBp.length, badBp.join('; '));

  // ── масштабирование и коррекция ───────────────────────────────────────
  if (v.resize) {
    const sw = v.resizeSource.width, sh = v.resizeSource.height;
    const srcRgb = b64ToBytes(v.resizeSource.rgb);
    const badResize = [];
    for (const [name, exp] of Object.entries(v.resize)) {
      const [fname, size] = name.split('@');
      const [ow, oh] = size.split('x').map(Number);
      const got = img.resize(srcRgb, sw, sh, ow, oh, fname);
      if (await digest(got) !== exp.hash) {
        badResize.push(`${name} (первые ${[...got.slice(0, 4)]} вместо ${exp.first[0]})`);
      }
    }
    ok('масштабирование как в Pillow', !badResize.length,
      badResize.length ? badResize.slice(0, 3).join('; ') : `${Object.keys(v.resize).length} вариантов`);
  }

  if (v.adjust) {
    const aw = v.adjustSource.width, ah = v.adjustSource.height;
    const aRgb = b64ToBytes(v.adjustSource.rgb);
    const params = {
      'gamma0.8': { gamma: 0.8 },
      'bright1.3': { brightness: 1.3 },
      'contrast1.4': { contrast: 1.4 },
      'sat0.5': { saturation: 0.5 },
      all: { brightness: 1.15, contrast: 1.25, saturation: 1.4, gamma: 0.9 },
    };
    const badAdjust = [];
    for (const [name, exp] of Object.entries(v.adjust)) {
      const got = img.adjust(aRgb, aw * ah, params[name]);
      if (await digest(got) !== exp.hash) badAdjust.push(name);
    }
    ok('коррекция как в Pillow', !badAdjust.length,
      badAdjust.length ? badAdjust.join(', ') : `${Object.keys(v.adjust).length} вариантов`);
  }

  const desc = bp.descriptionJson('Тест', '11111111-2222-3333-4444-555555555555', 'заметка');
  ok('description.json совпадает', await digest(enc.encode(desc)) === v.description.sha256,
    desc === v.description.text ? '' : 'текст отличается');

  return results;
}
