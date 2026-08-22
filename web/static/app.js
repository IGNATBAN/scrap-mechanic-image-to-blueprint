'use strict';

const $ = (id) => document.getElementById(id);
const t = (k, v) => I18N.t(k, v);
// разделитель разрядов по языку интерфейса: 14 385 против 14,385
const num = (n) => Number(n).toLocaleString(I18N.lang() === 'ru' ? 'ru-RU' : 'en-US');

const state = {
  cfg: null, imageId: null, srcW: 0, srcH: 0,
  originalUrl: null, timer: null, busy: false, pending: false,
  plan: null, palette: null, rectsUrl: null, selected: null,
  gridW: 0, gridH: 0,
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

/* ── сворачивание секций ────────────────────────────────────────────── */
document.querySelectorAll('section.box > h2').forEach((h) =>
  h.addEventListener('click', () => h.parentElement.classList.toggle('closed')));

/* ── конфигурация ───────────────────────────────────────────────────── */
async function loadConfig() {
  await I18N.load('/api/i18n');
  I18N.apply();
  I18N.mount($('langSwitch'));
  I18N.onChange(() => { updateColorHint(); renderPaths(); if (state.imageId) schedule(0); });
  state.cfg = await (await fetch('/api/config')).json();
  const c = state.cfg;

  $('formats').textContent = t('drop.formatsPrefix') + ' ' + c.supported.join(', ');

  const sel = $('block');
  let group = null;
  c.blocks.forEach((b) => {
    const label = b.glass ? t('blocks.groupGlass')
      : b.flat >= 3 ? t('blocks.groupBest') : b.flat === 2 ? t('blocks.groupGood') : t('blocks.groupTextured');
    if (!group || group.label !== label) {
      group = document.createElement('optgroup');
      group.label = label;
      sel.appendChild(group);
    }
    const o = document.createElement('option');
    o.value = b.uuid;
    o.textContent = b.title;
    if (b.uuid === c.defaultBlock) o.selected = true;
    group.appendChild(o);
  });

  $('method').innerHTML = c.methods
    .map((m) => `<option value="${m.id}"${m.id === 'fs' ? ' selected' : ''}>${m.title}</option>`)
    .join('');

  $('swatches').innerHTML = c.palette
    .map((s) => `<i data-hex="${s.hex}" style="background:#${s.hex}" title="#${s.hex}"></i>`)
    .join('');
  $('swatches').addEventListener('click', (e) => {
    const cell = e.target.closest('i');
    if (!cell) return;
    setBrushColor(cell.dataset.hex);
  });

  $('blockList').innerHTML = c.materials
    .map((m) => `<label><input type="checkbox" value="${m.uuid}" checked>
      <span>${m.name.replace('blk_', '')}</span>
      <span class="keeps">${Math.round(m.keeps * 100)}%</span></label>`)
    .join('');
  $('blockList').addEventListener('change', () => schedule());
  $('blocksNote').textContent = c.materialsReady ? t('blocks.count', { n: c.materials.length }) : '(таблица не собрана)';
  if (!c.materialsReady) $('useBlocks').disabled = true;

  $('target').value = c.target;
  $('targetOut').textContent = c.target;

  renderPaths();

  if (!c.blueprintsDir) {
    $('toGame').checked = false;
    $('toGame').disabled = true;
    $('toZip').checked = true;
  }
  updateColorHint();
}


/** Строка статуса в шапке: язык может смениться, поэтому рисуется отдельно. */
function renderPaths() {
  const c = state.cfg;
  if (!c) return;
  const fontsOk = Object.values(c.fonts || {}).filter(Boolean).length;
  const bits = [];
  bits.push(c.gameDir ? '<b>' + t('app.gameFound') + '</b>' : t('app.gameMissing'));
  if (c.blueprintsDir) bits.push(t('app.blueprints') + `: <code>…\\User_${c.steamId}\\Blueprints</code>`);
  else bits.push(t('app.blueprintsMissing'));
  if (c.paletteFromGame) bits.push(t('app.paletteFromGame') + ': ' + c.palette.length);
  if (fontsOk) bits.push(t('app.fontsFromGame') + ': ' + fontsOk);
  $('paths').innerHTML = bits.join(' · ');
}

/* ── загрузка картинки ──────────────────────────────────────────────── */
const drop = $('drop');
drop.addEventListener('click', () => $('file').click());
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', (e) => {
  e.preventDefault();
  drop.classList.remove('over');
  if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
});
$('file').addEventListener('change', (e) => e.target.files[0] && upload(e.target.files[0]));

async function upload(file) {
  const fd = new FormData();
  fd.append('file', file);
  drop.querySelector('b').textContent = t('drop.loading');
  try {
    const res = await fetch('/api/upload', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || t('err.upload'));

    state.imageId = data.id;
    state.srcW = data.width;
    state.srcH = data.height;
    if (state.originalUrl) URL.revokeObjectURL(state.originalUrl);
    state.originalUrl = URL.createObjectURL(file);
    Viewer.setOriginal(state.originalUrl, data.width, data.height);
    Viewer.setCrop(null);

    drop.querySelector('b').textContent = file.name;
    drop.querySelector('span').textContent = `${data.width}×${data.height} ${t('drop.replace')}`;
    $('controls').classList.remove('hidden');
    $('stageEmpty').classList.add('hidden');
    if (!$('name').value) $('name').value = data.name;

    const start = Math.max(8, Math.min(160, data.width));
    $('width').value = $('widthNum').value = start;
    syncHeight();
    updateOneHint();
    updateCropInfo();
    schedule(0);
  } catch (err) {
    drop.querySelector('b').textContent = t('drop.title');
    toast(String(err.message || err), true);
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
    id: state.imageId,
    crop: crop ? [crop.x, crop.y, crop.w, crop.h] : null,
    width: +$('widthNum').value,
    height: +$('heightNum').value,
    keep_ratio: $('keepRatio').checked,
    resample: $('resample').value,
    color_mode: getColorMode(),
    method: $('method').value,
    strength: +$('strength').value,
    lum_weight: +$('lumWeight').value,
    serpentine: $('serpentine').checked,
    block: $('block').value,
    extra_blocks: chosenBlocks(),
    dedupe: +$('dedupe').value,
    alpha_mode: getAlphaMode(),
    alpha_threshold: +$('alphaThreshold').value,
    background: $('background').value.replace('#', ''),
    brightness: +$('brightness').value,
    contrast: +$('contrast').value,
    saturation: +$('saturation').value,
    gamma: +$('gamma').value,
    flip_h: $('flipH').checked,
    merge: $('merge').checked,
    max_bound: +$('maxBound').value,
    split: $('split').checked,
    split_mode: getSplitMode(),
    cols: +$('cols').value,
    rows: +$('rows').value,
    module_w: +$('moduleW').value,
    module_h: +$('moduleH').value,
    target: +$('target').value,
  };
}

/* ── предпросмотр ───────────────────────────────────────────────────── */
function schedule(delay = 300) {
  clearTimeout(state.timer);
  state.timer = setTimeout(run, delay);
}

async function run() {
  if (!state.imageId) return;
  if (state.busy) { state.pending = true; return; }
  state.busy = true;
  $('stage').classList.add('busy');
  try {
    const res = await fetch('/api/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params()),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || t('err.compute'));

    state.gridW = data.stats.gridWidth;
    state.gridH = data.stats.gridHeight;
    state.rectsUrl = data.rects;
    if (data.palette) {
      state.palette = data.palette;
      $('paletteInfo').textContent = t('palette.available', { n: data.palette.length });
    }
    await Viewer.setGrid(data.image, state.gridW, state.gridH);
    Viewer.setTiles(data.stats.modules || []);
    Viewer.setEdges(data.stats.edgesX ? { x: data.stats.edgesX.slice(1, -1), y: (data.stats.edgesY || []).slice(1, -1) } : null);
    if ($('showRects').classList.contains('on')) loadRects();
    showStats(data.stats);
    if (data.plan) renderPlan(data.plan, data.stats);
  } catch (err) {
    toast(String(err.message || err), true);
  } finally {
    state.busy = false;
    $('stage').classList.remove('busy');
    if (state.pending) { state.pending = false; run(); }
  }
}

async function loadRects() {
  if (!state.rectsUrl) return;
  try {
    const buf = await (await fetch(state.rectsUrl)).arrayBuffer();
    Viewer.setRects(buf.byteLength ? buf : null);
  } catch { Viewer.setRects(null); }
}

function showStats(s) {
  $('stats').classList.remove('hidden');
  $('stGrid').textContent = `${s.gridWidth}×${s.gridHeight}`;
  $('stMeters').textContent = `${s.meters[0]}×${s.meters[1]} ` + t('unit.m');
  $('stColors').textContent = num(s.colors);
  $('stMs').textContent = num(s.ms);

  const parts = $('stParts');
  parts.textContent = num(s.parts);
  parts.className = s.parts < 10000 ? 'ok' : s.parts < 50000 ? 'mid' : 'bad';
  $('stSaved').textContent = s.cells ? Math.round(s.merged_ratio * 100) + '%' : '—';

  const errBox = $('stErrorBox');
  errBox.classList.toggle('hidden', s.colorMode !== 'palette');
  if (s.colorMode === 'palette') {
    const e = $('stError');
    e.textContent = s.error.toFixed(4);
    e.className = s.error < 0.02 ? 'ok' : s.error < 0.04 ? 'mid' : 'bad';
  }

  const mods = s.modules || [];
  $('stModulesBox').classList.toggle('hidden', mods.length < 2);
  $('stHeaviestBox').classList.toggle('hidden', mods.length < 2);
  if (mods.length > 1) {
    $('stModules').textContent = num(mods.length);
    const h = $('stHeaviest');
    h.textContent = num(s.moduleMax);
    h.className = s.moduleMax < 10000 ? 'ok' : s.moduleMax < 50000 ? 'mid' : 'bad';
  }

  $('blocksUsed').innerHTML = (s.blocksUsed || []).length > 1
    ? t('blocks.used') + ': ' + s.blocksUsed.slice(0, 8).map((b) => `${b.title} — ${num(b.parts)}`).join(' · ')
      + (s.blocksUsed.length > 8 ? ' ' + t('blocks.more', { n: s.blocksUsed.length - 8 }) : '')
    : '';

  const warn = $('warn');
  const worst = mods.length > 1 ? s.moduleMax : s.parts;
  const lines = [];

  // Запрошенный размер не влез в бюджет расчёта — показываем, что именно
  // урезано, и сразу даём чем это лечится. Раньше здесь был отказ, из-за
  // которого работать было нельзя вообще.
  if (s.clamped) {
    const c = s.clamped;
    lines.push(`<b>Показано в уменьшенном виде.</b> Вы запросили
      ${c.requestedWidth}×${c.requestedHeight} = ${num(c.requestedCells)} блоков —
      столько за раз не посчитать. Считаю на ширине <b>${c.usedWidth}</b>.
      <span class="acts">
        <button type="button" class="ghost" data-act="clamp" data-w="${c.usedWidth}">Зафиксировать ${c.usedWidth}</button>
        <button type="button" class="ghost" data-act="estimate">Во что обойдётся 1:1</button>
      </span>`);
  }

  if (worst >= 50000) {
    lines.push(`<b>${num(worst)}</b> деталей ${mods.length > 1 ? t('warn.heavyModule') : t('warn.heavyWhole')} —
      игра будет сильно тормозить.
      <span class="acts">
        <button type="button" class="ghost" data-act="split">Разбить автоматически</button>
        <button type="button" class="ghost" data-act="blocks">Включить разные блоки</button>
      </span>`);
  } else if (worst >= 10000) {
    lines.push(`<b>${num(worst)}</b> деталей — построится, но тяжеловато. Комфортный потолок около 10 000.
      <span class="acts"><button type="button" class="ghost" data-act="split">Разбить автоматически</button></span>`);
  }

  if (!lines.length) {
    warn.className = 'warn hidden';
    warn.innerHTML = '';
  } else {
    warn.className = 'warn ' + (s.clamped || worst >= 50000 ? 'bad' : 'mid');
    warn.innerHTML = lines.join('<hr>');
  }
}

/* ── кнопки прямо в предупреждении ──────────────────────────────────── */
$('warn').addEventListener('click', async (e) => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  const act = btn.dataset.act;

  if (act === 'clamp') {
    $('widthNum').value = btn.dataset.w;
    $('width').value = Math.min(+$('width').max, +btn.dataset.w);
    syncHeight();
    schedule(0);
    return;
  }
  if (act === 'blocks') {
    document.querySelector('#colorMode button[data-v="palette"]').click();
    $('useBlocks').checked = true;
    $('blocksBox').classList.remove('hidden');
    schedule(0);
    return;
  }
  if (act === 'split') {
    if (!$('split').checked) {
      $('split').checked = true;
      $('split').dispatchEvent(new Event('change'));
    } else if (state.plan && state.plan.recommended) {
      applySplit(state.plan.recommended.cols, state.plan.recommended.rows);
      schedule(0);
    }
    document.querySelector('section.box[data-key="split"]').classList.remove('closed');
    return;
  }
  if (act === 'estimate') {
    btn.textContent = t('estimate.working');
    btn.disabled = true;
    try {
      const res = await fetch('/api/estimate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.assign(params(), { width: state.srcW, keep_ratio: true })),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || t('err.generic'));
      showEstimate(d);
    } catch (err) {
      toast(String(err.message || err), true);
    } finally {
      btn.disabled = false;
      btn.textContent = t('estimate.button');
    }
  }
});

function showEstimate(d) {
  const gb = d.bytesEstimate / 1073741824;
  $('warn').insertAdjacentHTML('beforeend', `<hr>
    <b>Оценка 1:1 (${d.requestedWidth}×${d.requestedHeight}):</b>
    примерно <b>${num(d.parts)}</b> деталей и ${gb >= 1 ? gb.toFixed(1) + ' ' + t('unit.gb') : Math.round(d.bytesEstimate / 1048576) + ' ' + t('unit.mb')} чертежа.
    Это <b>${num(d.modules)}</b> модулей по ${num(+$('target').value)} деталей — столько папок в игре бессмысленно.<br>
    Разумные ориентиры для этой картинки:
    <span class="acts">
      <button type="button" class="ghost" data-act="clamp" data-w="${d.widthForComfort}">
        ${d.widthForComfort} — один чертёж без тормозов</button>
      <button type="button" class="ghost" data-act="clamp" data-w="${d.widthForOneBlueprint}">
        ${d.widthForOneBlueprint} — целиком в один чертёж</button>
      <button type="button" class="ghost" data-act="clamp" data-w="${d.maxComputableWidth}">
        ${d.maxComputableWidth} — максимум расчёта, ${num(d.modulesAtMax)} модулей</button>
    </span>`);
}

/* ── план дробления ─────────────────────────────────────────────────── */
function renderPlan(plan, stats) {
  state.plan = plan;
  const rec = plan.recommended;
  const target = plan.target;
  const whole = plan.wholeParts;

  const advice = $('advice');
  if (!rec || (rec.cols === 1 && rec.rows === 1)) {
    advice.className = 'advice';
    advice.innerHTML = `Помещается в <b>один чертёж</b>: ${num(whole)} деталей, потолок ${num(target)}. Дробить не нужно.`;
  } else {
    const o = plan.options.find((x) => x.cols === rec.cols && x.rows === rec.rows) || {};
    const fits = o.maxParts <= target;
    advice.className = 'advice' + (fits ? '' : ' over');
    advice.innerHTML = fits
      ? `Целиком ${num(whole)} деталей — многовато. Рекомендую <b>${rec.cols}×${rec.rows} = ${num(o.modules)} модулей</b>:
         тяжелейший ${num(o.maxParts)}, модуль ${o.tileWidth}×${o.tileHeight} блоков.`
      : `Даже при ${rec.cols}×${rec.rows} тяжелейший модуль ${num(o.maxParts)} — выше потолка ${num(target)}.
         Уменьшите ширину, поднимите потолок или дробите мельче.`;
  }

  const sel = $('splitPreset');
  const cur = `${$('cols').value}x${$('rows').value}`;
  sel.innerHTML = plan.options.map((o) => {
    const tag = rec && o.cols === rec.cols && o.rows === rec.rows ? ' ' + t('split.recommended') : '';
    const label = o.modules === 1
      ? t('split.noSplit', { parts: num(o.totalParts) })
      : `${o.cols}×${o.rows} = ${num(o.modules)} мод. — макс ${num(o.maxParts)}${tag}`;
    return `<option value="${o.cols}x${o.rows}">${label}</option>`;
  }).join('');
  if (plan.options.some((o) => `${o.cols}x${o.rows}` === cur)) sel.value = cur;

  const mods = stats.modules || [];
  $('moduleList').innerHTML = mods.map((m) =>
    `<i data-label="${m.label}" class="${m.parts > target ? 'heavy' : ''}${state.selected === m.label ? ' on' : ''}"
        title="${m.width}×${m.height} блоков">${m.label}: ${num(m.parts)}</i>`).join('');
}

$('moduleList').addEventListener('click', (e) => {
  const cell = e.target.closest('i');
  if (!cell) return;
  selectModule(cell.dataset.label);
});

function selectModule(label) {
  state.selected = state.selected === label ? null : label;
  Viewer.select(state.selected);
  $('onlyRow').classList.toggle('hidden', !state.selected);
  $('onlyName').textContent = state.selected || '';
  document.querySelectorAll('#moduleList i').forEach((i) =>
    i.classList.toggle('on', i.dataset.label === state.selected));
}

/* ── холст ──────────────────────────────────────────────────────────── */
Viewer.init($('canvas'), $('stage'), {
  onZoom: (s) => ($('zoomLabel').textContent = Math.round(s * 100) + '%'),
  onPick: (hex) => { setBrushColor(hex); toast(t('pick.taken') + ': #' + hex); },
  onModule: (tile) => tile && selectModule(tile.label),
  onCrop: (c) => { updateCropInfo(); schedule(0); },
  onStroke: async (stroke) => {
    try {
      const res = await fetch('/api/edits', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: state.imageId, add: stroke }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'ошибка правки');
      schedule(120);
    } catch (err) { toast(String(err.message || err), true); }
  },
  onEdges: async (edges) => {
    try {
      await fetch('/api/edges', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: state.imageId, x: edges.x, y: edges.y }),
      });
      schedule(0);
    } catch (err) { toast(String(err.message || err), true); }
  },
});

document.querySelectorAll('.toolbar [data-tool]').forEach((btn) =>
  btn.addEventListener('click', () => {
    document.querySelectorAll('.toolbar [data-tool]').forEach((b) => b.classList.toggle('on', b === btn));
    $('cropBtn').classList.remove('on');
    $('edgesBtn').classList.remove('on');
    Viewer.setTool(btn.dataset.tool);
  }));

function pickTool(tool, btn) {
  document.querySelectorAll('.toolbar [data-tool]').forEach((b) => b.classList.remove('on'));
  $('cropBtn').classList.toggle('on', btn === $('cropBtn'));
  $('edgesBtn').classList.toggle('on', btn === $('edgesBtn'));
  Viewer.setTool(tool);
}

$('cropBtn').addEventListener('click', () => {
  const on = !$('cropBtn').classList.contains('on');
  pickTool(on ? 'crop' : 'pan', on ? $('cropBtn') : null);
  if (!on) document.querySelector('.toolbar [data-tool="pan"]').classList.add('on');
  toast(on ? 'Растяните рамку по оригиналу' : 'Обрезка выключена');
});
$('cropReset').addEventListener('click', () => {
  Viewer.setCrop(null);
  updateCropInfo();
  schedule(0);
});
$('edgesBtn').addEventListener('click', () => {
  if (!$('split').checked) { toast('Сначала включите дробление', true); return; }
  const on = !$('edgesBtn').classList.contains('on');
  pickTool(on ? 'edges' : 'pan', on ? $('edgesBtn') : null);
  if (!on) document.querySelector('.toolbar [data-tool="pan"]').classList.add('on');
  toast(on ? 'Тяните линии швов мышью' : 'Швы зафиксированы');
});
$('edgesReset').addEventListener('click', async () => {
  await fetch('/api/edges', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: state.imageId, reset: true }),
  });
  schedule(0);
});

function updateCropInfo() {
  const c = Viewer.crop();
  $('cropInfo').textContent = c ? `кадр ${c.w}×${c.h} px` : `весь кадр ${state.srcW}×${state.srcH}`;
}

$('zoomIn').addEventListener('click', () => Viewer.zoom(1.35));
$('zoomOut').addEventListener('click', () => Viewer.zoom(1 / 1.35));
$('zoomFit').addEventListener('click', () => Viewer.fit());
$('zoom1').addEventListener('click', () => Viewer.setZoom(1));

$('showOriginal').addEventListener('click', () => {
  const on = !$('showOriginal').classList.contains('on');
  $('showOriginal').classList.toggle('on', on);
  Viewer.setShowOriginal(on);
});
$('showRects').addEventListener('click', () => {
  const on = !$('showRects').classList.contains('on');
  $('showRects').classList.toggle('on', on);
  Viewer.setShowRects(on);
  if (on) loadRects();
});

$('undo').addEventListener('click', async () => {
  await fetch('/api/edits', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: state.imageId, undo: 1 }),
  });
  schedule(0);
});
$('clearEdits').addEventListener('click', async () => {
  await fetch('/api/edits', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: state.imageId, clear: true }),
  });
  toast('Правки сброшены');
  schedule(0);
});

function setBrushColor(hex) {
  Viewer.setBrush(hex);
  document.querySelectorAll('#swatches i').forEach((i) => i.classList.toggle('on', i.dataset.hex === hex));
}

window.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  if (e.key === '[') { const b = Viewer.setBrush(null, Viewer.brush().size - 1); $('brushSizeLabel').textContent = 'кисть ' + b.size; }
  if (e.key === ']') { const b = Viewer.setBrush(null, Viewer.brush().size + 1); $('brushSizeLabel').textContent = 'кисть ' + b.size; }
  if (e.key === 'b') document.querySelector('.toolbar [data-tool="brush"]').click();
  if (e.key === 'h') document.querySelector('.toolbar [data-tool="pan"]').click();
  if (e.key === 'e') document.querySelector('.toolbar [data-tool="erase"]').click();
  if (e.key === 'f') Viewer.fit();
});

/* ── связки контролов ───────────────────────────────────────────────── */
function syncHeight() {
  const keep = $('keepRatio').checked;
  $('height').disabled = $('heightNum').disabled = keep;
  const crop = Viewer.crop();
  const sw = crop ? crop.w : state.srcW;
  const sh = crop ? crop.h : state.srcH;
  if (keep && sw) {
    const h = Math.max(1, Math.round((+$('widthNum').value * sh) / sw));
    $('height').value = Math.min(+$('height').max, h);
    $('heightNum').value = h;
  }
}

function pair(rangeId, numId, after) {
  const r = $(rangeId), n = $(numId);
  r.addEventListener('input', () => { n.value = r.value; after && after(); schedule(); });
  n.addEventListener('input', () => { r.value = Math.min(+r.max, Math.max(+r.min, +n.value)); after && after(); schedule(); });
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

['resample', 'method', 'background', 'flipH', 'merge', 'serpentine', 'block']
  .forEach((id) => $(id).addEventListener('input', schedule));

$('keepRatio').addEventListener('change', () => { syncHeight(); schedule(); });
$('merge').addEventListener('change', () => $('boundRow').classList.toggle('hidden', !$('merge').checked));
$('useBlocks').addEventListener('change', () => {
  $('blocksBox').classList.toggle('hidden', !$('useBlocks').checked);
  schedule(0);
});
$('blocksAll').addEventListener('click', () => {
  $('blockList').querySelectorAll('input').forEach((i) => (i.checked = true));
  schedule(0);
});
$('blocksNone').addEventListener('click', () => {
  $('blockList').querySelectorAll('input').forEach((i) => (i.checked = false));
  schedule(0);
});
$('blocksFlat').addEventListener('click', () => {
  // «ровные» = те, что почти не гасят краску: у них текстура слабее всего
  const keep = new Set(state.cfg.materials.filter((m) => m.keeps >= 0.8).map((m) => m.uuid));
  $('blockList').querySelectorAll('input').forEach((i) => (i.checked = keep.has(i.value)));
  schedule(0);
});

$('reset').addEventListener('click', () => {
  ['brightness', 'contrast', 'saturation', 'gamma'].forEach((id) => {
    $(id).value = 1;
    $(id + 'Out').textContent = '1.00';
  });
  schedule(0);
});

$('one2one').addEventListener('click', () => {
  const crop = Viewer.crop();
  const w = crop ? crop.w : state.srcW;
  if (!w) return;
  const max = +$('widthNum').max;
  const val = Math.min(max, w);
  if (val < w) toast(`Ограничил ширину ${max} блоками — исходник ${w} px`);
  $('keepRatio').checked = true;
  $('widthNum').value = val;
  $('width').value = Math.min(+$('width').max, val);
  syncHeight();
  schedule(0);
});

function updateOneHint() {
  const crop = Viewer.crop();
  const w = crop ? crop.w : state.srcW;
  const h = crop ? crop.h : state.srcH;
  $('oneHint').textContent = w ? `${w}×${h} = ${num(w * h)} блоков` : '';
}

/* ── пресеты режима палитры ─────────────────────────────────────────── */
const PRESETS = {
  // Значения выставлены по замерам на живых скриншотах (tools/quality.py),
  // а не на глаз: Флойд даёт лучшую ошибку «издали» из всех ядер, а вес
  // яркости выше 1.0 всегда ухудшает общую точность — это обмен, не улучшение.
  photo: { method: 'fs', strength: 1, lumWeight: 1.0, serpentine: true, useBlocks: true, dedupe: 0.012 },
  poster: { method: 'none', strength: 1, lumWeight: 1.2, serpentine: true, useBlocks: true, dedupe: 0.02 },
  pixel: { method: 'none', strength: 1, lumWeight: 1.0, serpentine: true, useBlocks: false, dedupe: 0.012 },
  max: { method: 'fs', strength: 1, lumWeight: 1.0, serpentine: true, useBlocks: true, dedupe: 0.004 },
};

segment('preset', (v) => {
  const p = PRESETS[v];
  if (!p) return;
  $('method').value = p.method;
  $('strength').value = p.strength; $('strengthOut').textContent = (+p.strength).toFixed(2);
  $('lumWeight').value = p.lumWeight; $('lumWeightOut').textContent = (+p.lumWeight).toFixed(1);
  $('serpentine').checked = p.serpentine;
  $('dedupe').value = p.dedupe; $('dedupeOut').textContent = (+p.dedupe).toFixed(3);
  if ($('useBlocks').disabled) p.useBlocks = false;
  $('useBlocks').checked = p.useBlocks;
  $('blocksBox').classList.toggle('hidden', !p.useBlocks);
  schedule(0);
});

$('autofitBtn').addEventListener('click', async () => {
  if (!state.imageId) return;
  $('autofitBtn').disabled = true;
  try {
    const res = await fetch('/api/autofit', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params()),
    });
    const fit = await res.json();
    if (!res.ok) throw new Error(fit.detail || 'не вышло');
    $('gamma').value = fit.gamma; $('gammaOut').textContent = (+fit.gamma).toFixed(2);
    $('contrast').value = fit.contrast; $('contrastOut').textContent = (+fit.contrast).toFixed(2);
    $('saturation').value = fit.saturation; $('saturationOut').textContent = (+fit.saturation).toFixed(2);
    const gain = fit.baseError ? Math.round((1 - fit.error / fit.baseError) * 100) : 0;
    $('fitInfo').textContent = `ошибка ${fit.baseError.toFixed(3)} → ${fit.error.toFixed(3)} (−${gain}%)`;
    schedule(0);
  } catch (err) {
    toast(String(err.message || err), true);
  } finally {
    $('autofitBtn').disabled = false;
  }
});

/* ── дробление ──────────────────────────────────────────────────────── */
$('split').addEventListener('change', () => {
  const on = $('split').checked;
  $('splitBox').classList.toggle('hidden', !on);
  if (on && state.plan && state.plan.recommended) {
    $('cols').value = state.plan.recommended.cols;
    $('rows').value = state.plan.recommended.rows;
  }
  if (!on) selectModule(null);
  schedule(0);
});
$('splitPreset').addEventListener('change', () => {
  const [c, r] = $('splitPreset').value.split('x').map(Number);
  $('cols').value = c;
  $('rows').value = r;
  schedule(0);
});
['cols', 'rows', 'moduleW', 'moduleH'].forEach((id) => $(id).addEventListener('input', () => schedule()));

/* ── экспорт ────────────────────────────────────────────────────────── */
$('export').addEventListener('click', async () => {
  if (!state.imageId) return;
  if (!$('toGame').checked && !$('toZip').checked) {
    toast('Отметьте, куда сохранить: в игру или ZIP', true);
    return;
  }
  const btn = $('export');
  btn.disabled = true;
  btn.textContent = 'Собираю…';
  try {
    const body = Object.assign(params(), {
      name: $('name').value.trim(),
      orientation: getOrientation(),
      depth: +$('depth').value,
      to_game: $('toGame').checked,
      to_zip: $('toZip').checked,
      lang: I18N.lang(),
      replace: $('replace').checked,
      only_module: $('onlyModule').checked ? state.selected : null,
    });
    const res = await fetch('/api/export', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'ошибка сборки');

    const many = data.modules > 1;
    const lines = [`Готово: <b>${num(data.parts)}</b> деталей` +
      (many ? ` в <b>${num(data.modules)}</b> модулях` : '') +
      `, ${(data.bytes / 1048576).toFixed(2)} МБ`];
    if (data.path) {
      lines.push(many
        ? `Положено ${num(data.written)} чертежей: <code>${data.path}</code>`
        : `Положено в игру: <code>${data.path}</code>`);
    }
    if (data.download) lines.push(`<a href="${data.download}" download>Скачать ZIP</a>`);
    if (data.map) lines.push(`<a href="${data.map}" download>Скачать схему сборки</a>`);
    $('exportResult').innerHTML = lines.join('<br>');
    $('guide').classList.toggle('hidden', !data.guide);
    $('guide').textContent = data.guide || '';
    if (data.download) window.location.href = data.download;
    toast(many ? `Собрано ${data.modules} модулей` : 'Чертёж собран');
  } catch (err) {
    toast(String(err.message || err), true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Собрать чертёж';
  }
});

loadConfig().catch((e) => toast('Не удалось прочитать настройки: ' + e, true));
