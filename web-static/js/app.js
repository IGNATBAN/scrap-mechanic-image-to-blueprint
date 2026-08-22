// Интерфейс веб-версии. Считает всё локально — сервера нет вообще,
// ни один байт картинки никуда не уходит.

import * as pipeline from './pipeline.js';
import * as pal from './palette.js';
import { METHODS, loadBlueNoise } from './quant.js';
import * as tiles from './tiles.js';
import { descriptionJson, uuid4 } from './blueprint.js';
import { makeZip, download } from './zip.js';
import { Viewer } from './canvas.js';
import { I18N } from './i18n.js';

const t = (k, v) => I18N.t(k, v);

const $ = (id) => document.getElementById(id);
// разделитель разрядов по языку интерфейса: 14 385 против 14,385
const num = (n) => Number(n).toLocaleString(I18N.lang() === 'ru' ? 'ru-RU' : 'en-US');

const state = {
  source: null, grid: null, rects: null, modules: [], plan: null,
  edits: [], strokes: [], selected: null, timer: null, busy: false, pending: false,
  fileName: '', originalUrl: null,
};

// Значения выставлены по замерам на живых скриншотах, а не на глаз.
const PRESETS = {
  photo: { method: 'fs', strength: 1, lumWeight: 1.0, serpentine: true, useBlocks: true, dedupe: 0.012 },
  poster: { method: 'none', strength: 1, lumWeight: 1.2, serpentine: true, useBlocks: true, dedupe: 0.02 },
  pixel: { method: 'none', strength: 1, lumWeight: 1.0, serpentine: true, useBlocks: false, dedupe: 0.012 },
  max: { method: 'fs', strength: 1, lumWeight: 1.0, serpentine: true, useBlocks: true, dedupe: 0.004 },
};

function toast(msg, isErr) {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (t.className = 'toast'), isErr ? 6500 : 2800);
}

function segment(id, onChange) {
  const box = $(id);
  box.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    [...box.children].forEach((b) => b.classList.toggle('on', b === btn));
    onChange(btn.dataset.v);
  });
  return () => (box.querySelector('.on') || box.firstElementChild).dataset.v;
}

document.querySelectorAll('section.box > h2').forEach((h) =>
  h.addEventListener('click', () => h.parentElement.classList.toggle('closed')));

/* ── запуск ─────────────────────────────────────────────────────────── */

async function boot() {
  await Promise.all([pal.loadMaterials(), loadBlueNoise(), I18N.load('data/i18n.json')]);
  I18N.apply();
  I18N.mount($('langSwitch'));
  I18N.onChange(() => { refreshDynamic(); if (state.grid) schedule(0); });

  $('method').innerHTML = Object.entries(METHODS)
    .map(([id, title]) => `<option value="${id}"${id === 'fs' ? ' selected' : ''}>${title}</option>`)
    .join('');

  $('swatches').innerHTML = pal.swatches()
    .map((s) => `<i data-hex="${s.hex}" style="background:#${s.hex}" title="#${s.hex}"></i>`).join('');
  $('swatches').addEventListener('click', (e) => {
    const cell = e.target.closest('i');
    if (cell) setBrushColor(cell.dataset.hex);
  });

  const usable = pal.usableBlocks();
  $('blockList').innerHTML = usable.map((m) => `<label><input type="checkbox" value="${m.uuid}" checked>
    <span>${m.name.replace('blk_', '')}</span>
    <span class="keeps">${Math.round((1 - m.alpha) * 100)}%</span></label>`).join('');
  $('blockList').addEventListener('change', () => schedule());
  $('blocksNote').textContent = t('blocks.count', { n: usable.length });

  // блок-основа: показываем те же, что и в скачиваемой версии
  const all = [...pal.materials().values()].filter((o) => o.alpha <= 0.9);
  all.sort((a, b) => a.alpha - b.alpha);
  $('block').innerHTML = all
    .map((o) => `<option value="${o.uuid}"${o.uuid === pal.DEFAULT_BLOCK ? ' selected' : ''}>`
      + `${o.name.replace('blk_', '')}</option>`).join('');

  Viewer.init($('canvas'), $('stage'), {
    onZoom: (s) => { $('zoomLabel').textContent = Math.round(s * 100) + '%'; },
    onStroke: (stroke) => {
      state.strokes.push(stroke);
      state.edits.push(...stroke);
      schedule(0);
    },
    onCrop: () => { updateCropInfo(); schedule(0); },
    onModule: (tile) => selectModule(tile ? tile.label : null),
    onPick: (hex) => setBrushColor(hex),
  });

  updateColorHint();
}

/* ── загрузка ───────────────────────────────────────────────────────── */

const drop = $('drop');
drop.addEventListener('click', () => $('file').click());
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', (e) => {
  e.preventDefault();
  drop.classList.remove('over');
  if (e.dataTransfer.files[0]) openFile(e.dataTransfer.files[0]);
});
$('file').addEventListener('change', (e) => e.target.files[0] && openFile(e.target.files[0]));

async function openFile(file) {
  drop.querySelector('b').textContent = t('drop.reading');
  try {
    state.source = await pipeline.img.loadImage(file);
    state.edits = [];
    state.strokes = [];
    state.selected = null;
    Viewer.setCrop(null);
    Viewer.select(null);

    if (state.originalUrl) URL.revokeObjectURL(state.originalUrl);
    state.originalUrl = URL.createObjectURL(file);
    Viewer.setOriginal(state.originalUrl, state.source.width, state.source.height);

    state.fileName = file.name.replace(/\.[^.]+$/, '').slice(0, 60) || t('doc.defaultName');
    drop.querySelector('b').textContent = file.name;
    drop.querySelector('span').textContent = `${state.source.width}×${state.source.height} ${t('drop.replace')}`;
    $('controls').classList.remove('hidden');
    $('stageEmpty').classList.add('hidden');
    if (!$('name').value) $('name').value = state.fileName;

    const start = Math.max(8, Math.min(160, state.source.width));
    $('width').value = $('widthNum').value = start;
    syncHeight();
    updateOneHint();
    updateCropInfo();
    schedule(0);
  } catch (err) {
    drop.querySelector('b').textContent = t('drop.title');
    toast(t('err.readImage') + ': ' + (err.message || err), true);
  }
}

/* ── параметры ──────────────────────────────────────────────────────── */

const getColorMode = segment('colorMode', (v) => {
  $('paletteBox').classList.toggle('hidden', v !== 'palette');
  updateColorHint();
  schedule();
});
const getOrientation = segment('orientation', () => {});
const getAlphaMode = segment('alphaMode', (v) => {
  $('bgRow').classList.toggle('hidden', v !== 'flatten');
  $('alphaThRow').classList.toggle('hidden', v === 'flatten');
  schedule();
});
const getSplitMode = segment('splitMode', (v) => {
  $('byCount').classList.toggle('hidden', v !== 'count');
  $('bySize').classList.toggle('hidden', v !== 'size');
  schedule(0);
});
segment('preset', (v) => {
  const p = PRESETS[v];
  $('method').value = p.method;
  $('strength').value = p.strength; $('strengthOut').textContent = (+p.strength).toFixed(2);
  $('lumWeight').value = p.lumWeight; $('lumWeightOut').textContent = (+p.lumWeight).toFixed(1);
  $('serpentine').checked = p.serpentine;
  $('useBlocks').checked = p.useBlocks;
  $('blocksBox').classList.toggle('hidden', !p.useBlocks);
  $('dedupe').value = p.dedupe; $('dedupeOut').textContent = p.dedupe;
  schedule(0);
});

function updateColorHint() {
  $('colorHint').innerHTML = getColorMode() === 'palette'
    ? t('color.hintPalette')
    : t('color.hintExact');
}

function chosenBlocks() {
  if (!$('useBlocks').checked) return [];
  return [...$('blockList').querySelectorAll('input:checked')].map((i) => i.value);
}

function params() {
  const crop = Viewer.crop();
  return {
    crop: crop ? [crop.x, crop.y, crop.w, crop.h] : null,
    width: +$('widthNum').value,
    height: +$('heightNum').value,
    keepRatio: $('keepRatio').checked,
    resample: $('resample').value,
    colorMode: getColorMode(),
    method: $('method').value,
    strength: +$('strength').value,
    lumWeight: +$('lumWeight').value,
    serpentine: $('serpentine').checked,
    baseBlock: $('block').value,
    extraBlocks: chosenBlocks(),
    dedupe: +$('dedupe').value,
    alphaMode: getAlphaMode(),
    alphaThreshold: +$('alphaThreshold').value,
    background: $('background').value.replace('#', ''),
    brightness: +$('brightness').value,
    contrast: +$('contrast').value,
    saturation: +$('saturation').value,
    gamma: +$('gamma').value,
    flipH: $('flipH').checked,
    edits: state.edits,
  };
}

function splitParams(gridW, gridH) {
  if (!$('split').checked) return { cols: 1, rows: 1 };
  if (getSplitMode() === 'size') {
    const ex = tiles.edgesBySize(gridW, +$('moduleW').value);
    const ey = tiles.edgesBySize(gridH, +$('moduleH').value);
    return { cols: ex.length - 1, rows: ey.length - 1, edgesX: ex, edgesY: ey };
  }
  const custom = Viewer.edges();
  return {
    cols: +$('cols').value, rows: +$('rows').value,
    edgesX: custom && custom.x && custom.x.length ? custom.x : null,
    edgesY: custom && custom.y && custom.y.length ? custom.y : null,
  };
}

/* ── расчёт ─────────────────────────────────────────────────────────── */

function schedule(delay = 220) {
  clearTimeout(state.timer);
  state.timer = setTimeout(run, delay);
}

function run() {
  if (!state.source) return;
  if (state.busy) { state.pending = true; return; }
  state.busy = true;
  $('stage').classList.add('busy');

  // Отдаём управление браузеру, чтобы «считаю» успело отрисоваться.
  // Именно setTimeout, а не requestAnimationFrame: в фоновой вкладке кадры
  // не выдаются вовсе, и расчёт просто не начинался бы до возврата.
  setTimeout(() => {
    const t0 = performance.now();
    try {
      const grid = pipeline.buildGrid(state.source, params());
      const { rects, summary } = pipeline.meshGrid(grid, {
        merge: $('merge').checked, maxBound: +$('maxBound').value,
      });
      const sp = splitParams(grid.width, grid.height);
      const modules = pipeline.splitGrid(rects, grid, sp);
      const planned = tiles.plan(rects, grid.width, grid.height, +$('target').value);

      state.grid = grid;
      state.rects = rects;
      state.modules = modules;
      state.plan = planned;

      render(grid, rects, modules);
      showStats(grid, summary, modules, Math.round(performance.now() - t0));
      renderPlan(planned, summary, modules);
    } catch (err) {
      toast(t('err.compute') + ': ' + (err.message || err), true);
      console.error(err);
    } finally {
      state.busy = false;
      $('stage').classList.remove('busy');
      if (state.pending) { state.pending = false; run(); }
    }
  }, 0);
}

function gridToRgba(grid) {
  const n = grid.width * grid.height;
  const rgba = new Uint8ClampedArray(n * 4);
  for (let i = 0; i < n; i++) {
    rgba[i * 4] = grid.rgb[i * 3];
    rgba[i * 4 + 1] = grid.rgb[i * 3 + 1];
    rgba[i * 4 + 2] = grid.rgb[i * 3 + 2];
    rgba[i * 4 + 3] = grid.mask[i] ? 255 : 0;
  }
  return rgba;
}

function render(grid, rects, modules) {
  Viewer.setGridPixels(gridToRgba(grid), grid.width, grid.height);
  Viewer.setTiles(modules.map((t) => ({
    x0: t.x0, y0: t.y0, width: t.width, height: t.height, label: t.label, parts: t.parts,
  })));

  if ($('showRects').classList.contains('on') && rects.length <= 400000) {
    const buf = new Int32Array(rects.length * 4);
    rects.forEach((r, i) => { buf[i * 4] = r[0]; buf[i * 4 + 1] = r[1]; buf[i * 4 + 2] = r[2]; buf[i * 4 + 3] = r[3]; });
    Viewer.setRects(buf);
  } else {
    Viewer.setRects(null);
  }

  const sp = splitParams(grid.width, grid.height);
  if ($('split').checked && getSplitMode() === 'count' && !Viewer.edges()) {
    Viewer.setEdges({ x: tiles.edges(grid.width, sp.cols), y: tiles.edges(grid.height, sp.rows) });
  }
}

function showStats(grid, s, modules, ms) {
  $('stats').classList.remove('hidden');
  $('stGrid').textContent = `${grid.width}×${grid.height}`;
  $('stMeters').textContent = `${(grid.width * 0.25).toFixed(1)}×${(grid.height * 0.25).toFixed(1)} м`;
  $('stColors').textContent = num(s.colors);
  $('stMs').textContent = ms;

  const parts = $('stParts');
  parts.textContent = num(s.parts);
  parts.className = s.parts < 10000 ? 'ok' : s.parts < 50000 ? 'mid' : 'bad';
  $('stSaved').textContent = s.cells ? Math.round(s.mergedRatio * 100) + '%' : '—';

  $('stErrorBox').classList.toggle('hidden', !grid.palette);
  if (grid.palette) $('stError').textContent = grid.error.toFixed(4);

  const on = modules.length > 1;
  $('stModulesBox').classList.toggle('hidden', !on);
  $('stHeaviestBox').classList.toggle('hidden', !on);
  if (on) {
    const worst = Math.max(...modules.map((t) => t.parts));
    $('stModules').textContent = num(modules.length);
    const h = $('stHeaviest');
    h.textContent = num(worst);
    h.className = worst < 10000 ? 'ok' : worst < 50000 ? 'mid' : 'bad';
  }

  const used = new Map();
  if (grid.palette) {
    for (const r of state.rects) {
      const b = grid.palette.block[r[4]] || '';
      used.set(b, (used.get(b) || 0) + 1);
    }
  }
  $('blocksUsed').innerHTML = used.size > 1
    ? t('blocks.used') + ': ' + [...used.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8)
      .map(([u, n]) => `${(pal.materials().get(u) || { name: u }).name.replace('blk_', '')} — ${num(n)}`).join(' · ')
    : '';

  const warn = $('warn');
  const worst = modules.length > 1 ? Math.max(...modules.map((t) => t.parts)) : s.parts;
  const lines = [];

  if (grid.clamped) {
    const c = grid.clamped;
    lines.push(t('warn.clamped', {
      w: c.requestedWidth, h: c.requestedHeight, cells: num(c.requestedCells), used: c.usedWidth,
    }) + `<span class="acts">
        <button type="button" class="ghost" data-act="clamp" data-w="${c.usedWidth}">`
      + t('warn.clampFix', { w: c.usedWidth }) + '</button></span>');
  }
  if (worst >= 50000) {
    lines.push(t('warn.heavy', {
      parts: num(worst),
      where: t(modules.length > 1 ? 'warn.heavyModule' : 'warn.heavyWhole'),
    }) + `<span class="acts">
        <button type="button" class="ghost" data-act="split">${t('warn.autoSplit')}</button>
        <button type="button" class="ghost" data-act="blocks">${t('warn.enableBlocks')}</button>
      </span>`);
  } else if (worst >= 10000) {
    lines.push(t('warn.mid', { parts: num(worst) })
      + `<span class="acts"><button type="button" class="ghost" data-act="split">${t('warn.autoSplit')}</button></span>`);
  }

  warn.className = lines.length ? 'warn ' + (grid.clamped || worst >= 50000 ? 'bad' : 'mid') : 'warn hidden';
  warn.innerHTML = lines.join('<hr>');
}

$('warn').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  if (btn.dataset.act === 'clamp') {
    $('widthNum').value = btn.dataset.w;
    $('width').value = Math.min(+$('width').max, +btn.dataset.w);
    syncHeight();
    schedule(0);
  } else if (btn.dataset.act === 'blocks') {
    document.querySelector('#colorMode button[data-v="palette"]').click();
    $('useBlocks').checked = true;
    $('blocksBox').classList.remove('hidden');
    schedule(0);
  } else if (btn.dataset.act === 'split') {
    if (!$('split').checked) { $('split').checked = true; $('split').dispatchEvent(new Event('change')); }
    else if (state.plan && state.plan.recommended) {
      applySplit(state.plan.recommended.cols, state.plan.recommended.rows);
      schedule(0);
    }
    document.querySelector('section.box[data-key="split"]').classList.remove('closed');
  }
});

/* ── дробление ──────────────────────────────────────────────────────── */

function applySplit(cols, rows) {
  $('cols').value = cols;
  $('rows').value = rows;
  Viewer.setEdges(null);
}

function renderPlan(plan, summary, modules) {
  const rec = plan.recommended;
  const target = +$('target').value;
  const whole = plan.wholeParts;
  const advice = $('advice');

  if (!rec || (rec.cols === 1 && rec.rows === 1)) {
    advice.className = 'advice';
    advice.innerHTML = t('split.fitsOne', { parts: num(whole), target: num(target) });
  } else {
    const o = plan.options.find((x) => x.cols === rec.cols && x.rows === rec.rows);
    const fits = o.maxParts <= target;
    advice.className = 'advice' + (fits ? '' : ' over');
    advice.innerHTML = fits
      ? t('split.recommend', {
        parts: num(whole), cols: rec.cols, rows: rec.rows, modules: o.modules,
        max: num(o.maxParts), tw: o.tileWidth, th: o.tileHeight,
      })
      : t('split.tooHeavy', {
        cols: rec.cols, rows: rec.rows, max: num(o.maxParts), target: num(target),
      });
  }

  const sel = $('splitPreset');
  const cur = `${$('cols').value}x${$('rows').value}`;
  sel.innerHTML = plan.options.map((o) => {
    const tag = rec && o.cols === rec.cols && o.rows === rec.rows ? ' ' + t('split.recommended') : '';
    const label = o.modules === 1
      ? t('split.noSplit', { parts: num(o.totalParts) })
      : t('split.option', {
        cols: o.cols, rows: o.rows, modules: o.modules, max: num(o.maxParts),
      }) + tag;
    return `<option value="${o.cols}x${o.rows}">${label}</option>`;
  }).join('');
  if (plan.options.some((o) => `${o.cols}x${o.rows}` === cur)) sel.value = cur;

  $('moduleList').innerHTML = [...modules].sort((a, b) => a.order - b.order)
    .map((m) => `<i class="${m.parts > target ? 'heavy' : ''}${state.selected === m.label ? ' on' : ''}"
      data-label="${m.label}" title="${m.width}×${m.height} блоков">${m.label}: ${num(m.parts)}</i>`).join('');
}

$('moduleList').addEventListener('click', (e) => {
  const cell = e.target.closest('i');
  if (cell) selectModule(cell.dataset.label);
});

function selectModule(label) {
  state.selected = state.selected === label ? null : label;
  Viewer.select(state.selected);
  $('onlyRow').classList.toggle('hidden', !state.selected);
  $('onlyName').textContent = state.selected || '';
  [...$('moduleList').children].forEach((el) =>
    el.classList.toggle('on', el.dataset.label === state.selected));
}

/* ── инструменты ────────────────────────────────────────────────────── */

document.querySelectorAll('[data-tool]').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('[data-tool]').forEach((b) => b.classList.toggle('on', b === btn));
    Viewer.setTool(btn.dataset.tool);
  });
});

$('cropBtn').addEventListener('click', () => {
  document.querySelectorAll('[data-tool]').forEach((b) => b.classList.remove('on'));
  Viewer.setTool('crop');
  toast(t('size.cropHint'));
});
$('cropReset').addEventListener('click', () => {
  Viewer.setCrop(null);
  updateCropInfo();
  schedule(0);
});

function updateCropInfo() {
  const c = Viewer.crop();
  $('cropInfo').textContent = c
    ? t('size.cropped', {
      w: Math.round(c.w), h: Math.round(c.h), sw: state.source.width, sh: state.source.height,
    })
    : (state.source ? t('size.whole', { w: state.source.width, h: state.source.height }) : '');
}

$('zoomIn').addEventListener('click', () => Viewer.zoom(1.25));
$('zoomOut').addEventListener('click', () => Viewer.zoom(1 / 1.25));
$('zoomFit').addEventListener('click', () => Viewer.fit());
$('showOriginal').addEventListener('click', (e) => {
  const on = e.target.classList.toggle('on');
  Viewer.setShowOriginal(on);
});
$('showRects').addEventListener('click', (e) => {
  e.target.classList.toggle('on');
  if (state.grid) render(state.grid, state.rects, state.modules);
});

function setBrushColor(hex) {
  Viewer.setBrush(hex);
  [...$('swatches').children].forEach((el) => el.classList.toggle('on', el.dataset.hex === hex));
  $('brushSizeLabel').textContent = t('tool.brushSize', { n: Viewer.brush().size }) + ` · #${hex}`;
}

window.addEventListener('keydown', (e) => {
  if (e.target.matches('input, select, textarea')) return;
  if (e.key === '[' || e.key === ']') {
    const b = Viewer.setBrush(null, Viewer.brush().size + (e.key === ']' ? 1 : -1));
    $('brushSizeLabel').textContent = t('tool.brushSize', { n: b.size }) + ` · #${b.color}`;
  }
});

$('undo').addEventListener('click', () => {
  const last = state.strokes.pop();
  if (!last) { toast(t('tool.nothingToUndo')); return; }
  state.edits.length = Math.max(0, state.edits.length - last.length);
  schedule(0);
});
$('clearEdits').addEventListener('click', () => {
  state.edits = [];
  state.strokes = [];
  schedule(0);
});

/* ── связки контролов ───────────────────────────────────────────────── */

function syncHeight() {
  const keep = $('keepRatio').checked;
  $('height').disabled = $('heightNum').disabled = keep;
  if (keep && state.source) {
    const src = Viewer.crop() || { w: state.source.width, h: state.source.height };
    const h = Math.max(1, Math.round((+$('widthNum').value * src.h) / src.w));
    $('height').value = Math.min(+$('height').max, h);
    $('heightNum').value = h;
  }
}

function pair(rangeId, numId, after) {
  const r = $(rangeId), n = $(numId);
  r.addEventListener('input', () => { n.value = r.value; after && after(); schedule(); });
  n.addEventListener('input', () => { r.value = Math.min(r.max, Math.max(r.min, n.value)); after && after(); schedule(); });
}
pair('width', 'widthNum', syncHeight);
pair('height', 'heightNum');

function slider(id, fmt) {
  const el = $(id), out = $(id + 'Out');
  const upd = () => { if (out) out.textContent = fmt ? fmt(el.value) : el.value; };
  el.addEventListener('input', () => { upd(); schedule(); });
  upd();
}
['brightness', 'contrast', 'saturation', 'gamma', 'strength'].forEach((id) => slider(id, (v) => (+v).toFixed(2)));
slider('lumWeight', (v) => (+v).toFixed(1));
slider('dedupe', (v) => (+v).toFixed(3));
['alphaThreshold', 'maxBound', 'depth', 'target'].forEach((id) => slider(id));

['resample', 'method', 'background', 'flipH', 'merge', 'serpentine', 'block'].forEach((id) =>
  $(id).addEventListener('input', schedule));
$('keepRatio').addEventListener('change', () => { syncHeight(); schedule(); });
$('merge').addEventListener('change', () => $('boundRow').classList.toggle('hidden', !$('merge').checked));
$('useBlocks').addEventListener('change', () => {
  $('blocksBox').classList.toggle('hidden', !$('useBlocks').checked);
  schedule(0);
});
$('blocksAll').addEventListener('click', () => {
  $('blockList').querySelectorAll('input').forEach((i) => { i.checked = true; });
  schedule(0);
});
$('blocksNone').addEventListener('click', () => {
  $('blockList').querySelectorAll('input').forEach((i) => { i.checked = false; });
  schedule(0);
});
$('split').addEventListener('change', () => {
  const on = $('split').checked;
  $('splitBox').classList.toggle('hidden', !on);
  if (on && state.plan && state.plan.recommended) {
    applySplit(state.plan.recommended.cols, state.plan.recommended.rows);
  }
  if (!on) Viewer.setEdges(null);
  schedule(0);
});
$('splitPreset').addEventListener('change', () => {
  const [c, r] = $('splitPreset').value.split('x').map(Number);
  applySplit(c, r);
  schedule(0);
});
['cols', 'rows', 'moduleW', 'moduleH'].forEach((id) => $(id).addEventListener('input', () => schedule()));

$('reset').addEventListener('click', () => {
  ['brightness', 'contrast', 'saturation', 'gamma'].forEach((id) => {
    $(id).value = 1;
    $(id + 'Out').textContent = '1.00';
  });
  schedule(0);
});

$('one2one').addEventListener('click', () => {
  if (!state.source) return;
  const src = Viewer.crop() || { w: state.source.width };
  const max = +$('widthNum').max;
  const w = Math.min(max, Math.round(src.w));
  if (w < src.w) toast(t('size.limited', { max, src: Math.round(src.w) }));
  $('keepRatio').checked = true;
  $('widthNum').value = w;
  $('width').value = Math.min(+$('width').max, w);
  syncHeight();
  schedule(0);
});

function updateOneHint() {
  $('oneHint').textContent = state.source
    ? t('size.original', {
      w: state.source.width, h: state.source.height,
      cells: num(state.source.width * state.source.height),
    })
    : '';
}

/** Перерисовать то, что подставляется из кода, а не из разметки. */
function refreshDynamic() {
  updateColorHint();
  updateOneHint();
  updateCropInfo();
  const usable = pal.usableBlocks();
  $('blocksNote').textContent = t('blocks.count', { n: usable.length });
  $('export').textContent = t('export.button');
  const b = Viewer.brush();
  $('brushSizeLabel').textContent = t('tool.brushSize', { n: b.size }) + ` · #${b.color}`;
}

const CRLF = String.fromCharCode(13, 10);

/* ── сборка ZIP ─────────────────────────────────────────────────────── */

$('export').addEventListener('click', async () => {
  if (!state.grid) return;
  const btn = $('export');
  btn.disabled = true;
  btn.textContent = t('export.working');
  try {
    const name = ($('name').value.trim() || state.fileName || t('doc.defaultName')).slice(0, 60);
    const onlyModule = $('onlyModule').checked ? state.selected : null;
    const items = pipeline.makeBlueprints(state.grid, state.rects, {
      name,
      orientation: getOrientation(),
      depth: +$('depth').value,
      baseBlock: $('block').value,
      moduleList: state.modules,
      onlyModule,
    });

    const files = [];
    for (const item of items) {
      const id = uuid4();
      files.push({ name: `${id}/blueprint.json`, data: item.text });
      files.push({ name: `${id}/description.json`, data: descriptionJson(item.name, id, item.note) });
      const icon = await iconPng(item.tile);
      if (icon) files.push({ name: `${id}/icon.png`, data: icon });
    }

    const many = items.length > 1;
    files.push({
      name: t('doc.whereName'),
      data: (many ? t('doc.whereMany', { n: items.length }) : t('doc.whereOne')) + CRLF
        + '%APPDATA%\\Axolot Games\\Scrap Mechanic\\User\\User_<SteamID>\\Blueprints\\' + CRLF
        + t('doc.whereTail') + CRLF
        + (many ? CRLF + t('doc.whereGuide') + CRLF : ''),
    });

    let guide = '';
    if (many && !onlyModule) {
      const rows = Math.max(...state.modules.map((t) => t.row)) + 1;
      const cols = Math.max(...state.modules.map((t) => t.col)) + 1;
      const counts = [...state.modules].sort((a, b) => (a.row - b.row) || (a.col - b.col)).map((t) => t.parts);
      guide = tiles.instructions(name, cols, rows, counts, getOrientation(), I18N.lang());
      files.push({ name: t('doc.assemblyName'), data: guide });
      const map = await assemblyMap(name, cols, rows);
      if (map) files.push({ name: t('doc.mapName'), data: map });
    }

    const blob = await makeZip(files);
    download(blob, `${name}.zip`);

    const bytes = items.reduce((n, i) => n + i.text.length, 0);
    $('exportResult').innerHTML =
      t(many ? 'export.doneMany' : 'export.doneOne', { n: items.length, mb: (bytes / 1048576).toFixed(2) })
      + '<br>' + t('export.unpack')
      + ' <code>…\\User_&lt;SteamID&gt;\\Blueprints\\</code>';
    $('guide').classList.toggle('hidden', !guide);
    $('guide').textContent = guide;
    toast(many ? t('export.builtModules', { n: items.length }) : t('export.builtOne'));
  } catch (err) {
    toast(t('export.failed') + ': ' + (err.message || err), true);
    console.error(err);
  } finally {
    btn.disabled = false;
    btn.textContent = t('export.button');
  }
});

/** Иконка чертежа: игра использует 128x128 RGBA. */
async function iconPng(tile) {
  const g = state.grid;
  const w = tile ? tile.width : g.width;
  const h = tile ? tile.height : g.height;
  const x0 = tile ? tile.x0 : 0;
  const y0 = tile ? tile.y0 : 0;

  const src = document.createElement('canvas');
  src.width = w; src.height = h;
  const rgba = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const s = (y0 + y) * g.width + x0 + x;
      const d = y * w + x;
      rgba[d * 4] = g.rgb[s * 3];
      rgba[d * 4 + 1] = g.rgb[s * 3 + 1];
      rgba[d * 4 + 2] = g.rgb[s * 3 + 2];
      rgba[d * 4 + 3] = g.mask[s] ? 255 : 0;
    }
  }
  src.getContext('2d').putImageData(new ImageData(rgba, w, h), 0, 0);

  const out = document.createElement('canvas');
  out.width = 128; out.height = 128;
  const k = Math.min(128 / w, 128 / h);
  const dw = Math.max(1, Math.round(w * k)), dh = Math.max(1, Math.round(h * k));
  const ctx = out.getContext('2d');
  ctx.imageSmoothingEnabled = k < 1;
  ctx.drawImage(src, (128 - dw) / 2, (128 - dh) / 2, dw, dh);
  return canvasBytes(out);
}

/** Схема сборки: картинка с сеткой модулей и их номерами. */
async function assemblyMap(name, cols, rows) {
  const g = state.grid;
  const scale = Math.max(1, Math.min(Math.floor(1000 / Math.max(g.width, g.height)), 16));
  const head = 46, foot = 30, pad = 12;
  const cv = document.createElement('canvas');
  cv.width = g.width * scale + pad * 2;
  cv.height = g.height * scale + head + foot;
  const ctx = cv.getContext('2d');
  ctx.fillStyle = '#12151a';
  ctx.fillRect(0, 0, cv.width, cv.height);

  const src = document.createElement('canvas');
  src.width = g.width; src.height = g.height;
  src.getContext('2d').putImageData(new ImageData(gridToRgba(g), g.width, g.height), 0, 0);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(src, pad, head, g.width * scale, g.height * scale);

  ctx.lineWidth = 2;
  for (const t of state.modules) {
    const x = pad + t.x0 * scale, y = head + t.y0 * scale;
    const w = t.width * scale, h = t.height * scale;
    ctx.strokeStyle = 'rgba(255,255,255,.92)';
    ctx.strokeRect(x, y, w, h);
    const fs = Math.max(11, Math.min(26, Math.min(w, h) / 4));
    ctx.font = `600 ${fs}px sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const tw = ctx.measureText(t.label).width;
    ctx.fillStyle = 'rgba(0,0,0,.7)';
    ctx.fillRect(x + w / 2 - tw / 2 - 5, y + h / 2 - fs * 0.7, tw + 10, fs * 1.4);
    ctx.fillStyle = '#fff';
    ctx.fillText(t.label, x + w / 2, y + h / 2);
  }

  ctx.textAlign = 'left';
  ctx.fillStyle = '#f0f4fa';
  ctx.font = '600 20px sans-serif';
  ctx.fillText(t('doc.mapTitle', { name }), pad, 26);
  ctx.fillStyle = '#96a2b3';
  ctx.font = '14px sans-serif';
  const total = state.modules.reduce((n, t) => n + t.parts, 0);
  const worst = Math.max(...state.modules.map((t) => t.parts));
  ctx.fillText(t('doc.mapNote', {
    w: g.width, h: g.height, cols, rows, modules: cols * rows, parts: total, max: worst,
  }), pad, cv.height - foot + 16);

  return canvasBytes(cv);
}

async function canvasBytes(canvas) {
  const blob = await new Promise((r) => canvas.toBlob(r, 'image/png'));
  return blob ? new Uint8Array(await blob.arrayBuffer()) : null;
}

boot().catch((e) => toast('Could not start: ' + e.message, true));
