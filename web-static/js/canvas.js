'use strict';

/* Холст предпросмотра: зум, панорама, кисть, обрезка, выбор модуля и
   перетаскивание швов. Всё рисование идёт в один <canvas>; сетка блоков
   лежит в отдельном невидимом холсте 1:1, поэтому мазок кистью виден
   мгновенно, не дожидаясь пересчёта на сервере. */

const Viewer = (() => {
  const st = {
    canvas: null, ctx: null, stage: null, hooks: {},
    grid: null,              // холст 1:1 с текущей сеткой блоков
    gridW: 0, gridH: 0,
    original: null,          // Image оригинала (для режима обрезки)
    srcW: 0, srcH: 0,
    scale: 1, ox: 0, oy: 0,
    tool: 'pan',
    brush: { size: 1, color: 'EEEEEE' },
    tiles: [], rects: null, showRects: false, showOriginal: false,
    crop: null, cropDrag: null,
    edges: null, edgeDrag: null,
    selected: null,
    stroke: null, painting: false, panning: false, last: null,
    pointer: null,
  };

  /* ── преобразования координат ─────────────────────────────────────── */
  const view = () => (st.showOriginal || st.tool === 'crop' ? 'src' : 'grid');
  const sizeOf = () => (view() === 'src' ? [st.srcW, st.srcH] : [st.gridW, st.gridH]);

  function toWorld(ev) {
    const r = st.canvas.getBoundingClientRect();
    return { x: (ev.clientX - r.left - st.ox) / st.scale, y: (ev.clientY - r.top - st.oy) / st.scale };
  }

  function cellAt(ev) {
    const p = toWorld(ev);
    return { x: Math.floor(p.x), y: Math.floor(p.y) };
  }

  /* ── масштаб ──────────────────────────────────────────────────────── */
  function resize() {
    if (!st.canvas) return;
    const r = st.stage.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    st.canvas.width = Math.round(r.width * dpr);
    st.canvas.height = Math.round(r.height * dpr);
    st.canvas.style.width = r.width + 'px';
    st.canvas.style.height = r.height + 'px';
    st.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    st.viewW = r.width;
    st.viewH = r.height;
    draw();
  }

  function fit() {
    const [w, h] = sizeOf();
    if (!w || !h) return;
    const k = Math.min(st.viewW / w, st.viewH / h) * 0.94;
    st.scale = Math.max(0.02, k);
    st.ox = (st.viewW - w * st.scale) / 2;
    st.oy = (st.viewH - h * st.scale) / 2;
    draw();
    report();
  }

  function zoomAt(factor, cx, cy) {
    const before = { x: (cx - st.ox) / st.scale, y: (cy - st.oy) / st.scale };
    st.scale = Math.max(0.02, Math.min(64, st.scale * factor));
    st.ox = cx - before.x * st.scale;
    st.oy = cy - before.y * st.scale;
    draw();
    report();
  }

  function report() {
    st.hooks.onZoom && st.hooks.onZoom(st.scale);
  }

  /* ── отрисовка ────────────────────────────────────────────────────── */
  function draw() {
    const ctx = st.ctx;
    if (!ctx) return;
    ctx.clearRect(0, 0, st.viewW, st.viewH);

    const src = view() === 'src' ? st.original : st.grid;
    if (!src) return;
    const [w, h] = sizeOf();

    ctx.imageSmoothingEnabled = st.scale < 1;
    ctx.drawImage(src, st.ox, st.oy, w * st.scale, h * st.scale);

    if (view() === 'grid') {
      if (st.showRects) drawRects(ctx);
      drawTiles(ctx);
    }
    if (st.tool === 'crop') drawCrop(ctx);
    drawCursor(ctx);
  }

  function drawRects(ctx) {
    if (!st.rects || st.scale < 3) return;
    const n = st.rects.length / 4;
    ctx.strokeStyle = 'rgba(0,0,0,.45)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    // рисуем только то, что попало в окно — иначе на 50 000 деталей всё встанет
    const x0 = -st.ox / st.scale, y0 = -st.oy / st.scale;
    const x1 = x0 + st.viewW / st.scale, y1 = y0 + st.viewH / st.scale;
    for (let i = 0; i < n; i++) {
      const x = st.rects[i * 4], y = st.rects[i * 4 + 1];
      const rw = st.rects[i * 4 + 2], rh = st.rects[i * 4 + 3];
      if (x > x1 || y > y1 || x + rw < x0 || y + rh < y0) continue;
      ctx.rect(Math.round(st.ox + x * st.scale) + 0.5, Math.round(st.oy + y * st.scale) + 0.5,
               Math.round(rw * st.scale) - 1, Math.round(rh * st.scale) - 1);
    }
    ctx.stroke();
  }

  function drawTiles(ctx) {
    if (!st.tiles.length) return;
    ctx.save();
    ctx.lineWidth = 2;
    for (const t of st.tiles) {
      const x = st.ox + t.x0 * st.scale, y = st.oy + t.y0 * st.scale;
      const w = t.width * st.scale, h = t.height * st.scale;
      const on = st.selected === t.label;
      ctx.strokeStyle = on ? '#f8a808' : 'rgba(255,255,255,.85)';
      ctx.strokeRect(x, y, w, h);
      if (on) { ctx.fillStyle = 'rgba(248,168,8,.14)'; ctx.fillRect(x, y, w, h); }

      const fs = Math.max(10, Math.min(26, Math.min(w, h) / 4));
      if (fs >= 10 && w > 26 && h > 18) {
        ctx.font = `600 ${fs}px 'SM Digits','SM UI',sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const text = t.label;
        const tw = ctx.measureText(text).width;
        ctx.fillStyle = 'rgba(0,0,0,.68)';
        ctx.fillRect(x + w / 2 - tw / 2 - 5, y + h / 2 - fs * 0.7, tw + 10, fs * 1.4);
        ctx.fillStyle = on ? '#f8a808' : '#fff';
        ctx.fillText(text, x + w / 2, y + h / 2);
      }
    }
    ctx.restore();
  }

  function drawCrop(ctx) {
    const c = st.cropDrag || st.crop;
    if (!c) return;
    const x = st.ox + c.x * st.scale, y = st.oy + c.y * st.scale;
    const w = c.w * st.scale, h = c.h * st.scale;
    ctx.save();
    ctx.fillStyle = 'rgba(0,0,0,.55)';
    ctx.fillRect(0, 0, st.viewW, st.viewH);
    ctx.clearRect(x, y, w, h);
    ctx.drawImage(st.original, c.x, c.y, c.w, c.h, x, y, w, h);
    ctx.strokeStyle = '#f8a808';
    ctx.lineWidth = 2;
    ctx.strokeRect(x, y, w, h);
    ctx.restore();
  }

  function drawCursor(ctx) {
    if (!st.pointer || (st.tool !== 'brush' && st.tool !== 'erase')) return;
    const s = st.brush.size;
    const half = Math.floor(s / 2);
    const x = st.ox + (st.pointer.x - half) * st.scale;
    const y = st.oy + (st.pointer.y - half) * st.scale;
    ctx.save();
    ctx.strokeStyle = st.tool === 'erase' ? '#d02525' : '#' + st.brush.color;
    ctx.lineWidth = 2;
    ctx.strokeRect(x, y, s * st.scale, s * st.scale);
    ctx.restore();
  }

  /* ── правка кистью ────────────────────────────────────────────────── */
  function paintCell(cx, cy) {
    const s = st.brush.size, half = Math.floor(s / 2);
    const ctx = st.grid.getContext('2d');
    const erase = st.tool === 'erase';
    ctx.fillStyle = erase ? 'rgba(0,0,0,0)' : '#' + st.brush.color;
    for (let dy = 0; dy < s; dy++) {
      for (let dx = 0; dx < s; dx++) {
        const x = cx - half + dx, y = cy - half + dy;
        if (x < 0 || y < 0 || x >= st.gridW || y >= st.gridH) continue;
        if (erase) ctx.clearRect(x, y, 1, 1);
        else ctx.fillRect(x, y, 1, 1);
        st.stroke.push([x, y, erase ? null : st.brush.color]);
      }
    }
  }

  function line(a, b) {
    // Брезенхэм: без него быстрый мазок оставляет пунктир
    let x0 = a.x, y0 = a.y;
    const dx = Math.abs(b.x - x0), sx = x0 < b.x ? 1 : -1;
    const dy = -Math.abs(b.y - y0), sy = y0 < b.y ? 1 : -1;
    let err = dx + dy;
    for (;;) {
      paintCell(x0, y0);
      if (x0 === b.x && y0 === b.y) break;
      const e2 = 2 * err;
      if (e2 >= dy) { err += dy; x0 += sx; }
      if (e2 <= dx) { err += dx; y0 += sy; }
    }
  }

  /* ── швы модулей ──────────────────────────────────────────────────── */
  function nearestEdge(p) {
    if (!st.edges) return null;
    const tol = 8 / st.scale;
    let best = null;
    (st.edges.x || []).forEach((v, i) => {
      if (v > 0 && v < st.gridW && Math.abs(p.x - v) < tol) best = { axis: 'x', index: i, value: v };
    });
    if (best) return best;
    (st.edges.y || []).forEach((v, i) => {
      if (v > 0 && v < st.gridH && Math.abs(p.y - v) < tol) best = { axis: 'y', index: i, value: v };
    });
    return best;
  }

  function tileAt(p) {
    return st.tiles.find((t) => p.x >= t.x0 && p.y >= t.y0 && p.x < t.x0 + t.width && p.y < t.y0 + t.height);
  }

  /* ── события ──────────────────────────────────────────────────────── */
  function bind() {
    const el = st.stage;

    el.addEventListener('wheel', (e) => {
      e.preventDefault();
      const r = st.canvas.getBoundingClientRect();
      zoomAt(e.deltaY < 0 ? 1.18 : 1 / 1.18, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });

    el.addEventListener('pointerdown', (e) => {
      if (!st.grid && !st.original) return;
      el.setPointerCapture(e.pointerId);
      const p = toWorld(e);
      const cell = cellAt(e);

      if (e.button === 1 || e.shiftKey || st.tool === 'pan') {
        st.panning = true;
        st.last = { x: e.clientX, y: e.clientY };
        el.classList.add('dragging');
        return;
      }
      if (st.tool === 'crop') {
        st.cropDrag = { x: Math.max(0, p.x), y: Math.max(0, p.y), w: 0, h: 0, ax: p.x, ay: p.y };
        return;
      }
      if (st.tool === 'edges') {
        st.edgeDrag = nearestEdge(p);
        return;
      }
      if (st.tool === 'module') {
        const t = tileAt(p);
        st.selected = t ? t.label : null;
        draw();
        st.hooks.onModule && st.hooks.onModule(t || null);
        return;
      }
      if (st.tool === 'pick') {
        const d = st.grid.getContext('2d').getImageData(cell.x, cell.y, 1, 1).data;
        if (d[3] > 0) {
          const hex = [d[0], d[1], d[2]].map((v) => v.toString(16).padStart(2, '0').toUpperCase()).join('');
          st.hooks.onPick && st.hooks.onPick(hex);
        }
        return;
      }
      if (st.tool === 'brush' || st.tool === 'erase') {
        st.painting = true;
        st.stroke = [];
        st.last = cell;
        paintCell(cell.x, cell.y);
        draw();
      }
    });

    el.addEventListener('pointermove', (e) => {
      const p = toWorld(e);
      st.pointer = { x: Math.floor(p.x), y: Math.floor(p.y) };

      if (st.panning) {
        st.ox += e.clientX - st.last.x;
        st.oy += e.clientY - st.last.y;
        st.last = { x: e.clientX, y: e.clientY };
        draw();
        return;
      }
      if (st.cropDrag) {
        const c = st.cropDrag;
        c.x = Math.max(0, Math.min(c.ax, p.x));
        c.y = Math.max(0, Math.min(c.ay, p.y));
        c.w = Math.min(st.srcW, Math.max(c.ax, p.x)) - c.x;
        c.h = Math.min(st.srcH, Math.max(c.ay, p.y)) - c.y;
        draw();
        return;
      }
      if (st.edgeDrag) {
        const arr = st.edges[st.edgeDrag.axis];
        const limit = st.edgeDrag.axis === 'x' ? st.gridW : st.gridH;
        const v = Math.round(st.edgeDrag.axis === 'x' ? p.x : p.y);
        arr[st.edgeDrag.index] = Math.max(1, Math.min(limit - 1, v));
        st.hooks.onEdgePreview && st.hooks.onEdgePreview(st.edges);
        draw();
        return;
      }
      if (st.painting) {
        const cell = cellAt(e);
        if (cell.x !== st.last.x || cell.y !== st.last.y) {
          line(st.last, cell);
          st.last = cell;
        }
      }
      draw();
    });

    const finish = () => {
      el.classList.remove('dragging');
      if (st.panning) { st.panning = false; return; }
      if (st.cropDrag) {
        const c = st.cropDrag;
        st.cropDrag = null;
        if (c.w > 4 && c.h > 4) {
          st.crop = { x: Math.round(c.x), y: Math.round(c.y), w: Math.round(c.w), h: Math.round(c.h) };
          st.hooks.onCrop && st.hooks.onCrop(st.crop);
        }
        draw();
        return;
      }
      if (st.edgeDrag) {
        st.edgeDrag = null;
        st.hooks.onEdges && st.hooks.onEdges(st.edges);
        return;
      }
      if (st.painting) {
        st.painting = false;
        const stroke = st.stroke;
        st.stroke = null;
        if (stroke && stroke.length) st.hooks.onStroke && st.hooks.onStroke(stroke);
      }
    };

    el.addEventListener('pointerup', finish);
    el.addEventListener('pointercancel', finish);
    el.addEventListener('pointerleave', () => { st.pointer = null; draw(); });
    window.addEventListener('resize', resize);
  }

  /* ── внешний интерфейс ────────────────────────────────────────────── */
  return {
    init(canvas, stage, hooks) {
      st.canvas = canvas;
      st.ctx = canvas.getContext('2d');
      st.stage = stage;
      st.hooks = hooks || {};
      bind();
      resize();
    },

    setGrid(url, w, h) {
      return new Promise((resolve) => {
        const img = new Image();
        img.onload = () => {
          const first = st.gridW !== w || st.gridH !== h;
          st.gridW = w; st.gridH = h;
          st.grid = document.createElement('canvas');
          st.grid.width = w; st.grid.height = h;
          const c = st.grid.getContext('2d');
          c.imageSmoothingEnabled = false;
          c.drawImage(img, 0, 0);
          if (first || !st.scale) fit(); else draw();
          resolve();
        };
        img.onerror = () => resolve();
        img.src = url;
      });
    },

    /** Отдать сетку готовыми пикселями RGBA — так делает веб-версия:
     *  кодировать PNG на каждый пересчёт незачем, всё уже в памяти. */
    setGridPixels(rgba, w, h) {
      const first = st.gridW !== w || st.gridH !== h;
      st.gridW = w;
      st.gridH = h;
      st.grid = document.createElement('canvas');
      st.grid.width = w;
      st.grid.height = h;
      st.grid.getContext('2d').putImageData(new ImageData(rgba, w, h), 0, 0);
      if (first || !st.scale) fit(); else draw();
    },

    setOriginal(url, w, h) {
      const img = new Image();
      img.onload = () => { st.original = img; st.srcW = w; st.srcH = h; draw(); };
      img.src = url;
    },

    setTiles(tiles) { st.tiles = tiles || []; draw(); },
    setRects(buf) {
      st.rects = buf ? new Int32Array(buf) : null;
      draw();
    },
    setEdges(edges) { st.edges = edges ? { x: [...(edges.x || [])], y: [...(edges.y || [])] } : null; draw(); },
    edges: () => st.edges,

    setTool(tool) {
      st.tool = tool;
      st.stage.className = 'stage tool-' + tool;
      if (tool === 'crop' && st.original) fit();
      draw();
    },
    tool: () => st.tool,

    setBrush(color, size) {
      if (color) st.brush.color = color.replace('#', '').toUpperCase();
      if (size) st.brush.size = Math.max(1, Math.min(64, size));
      draw();
      return st.brush;
    },
    brush: () => st.brush,

    setShowRects(on) { st.showRects = on; draw(); },
    setShowOriginal(on) { st.showOriginal = on; fit(); },
    showingOriginal: () => st.showOriginal,

    crop: () => st.crop,
    setCrop(c) { st.crop = c; draw(); },
    selected: () => st.selected,
    select(label) { st.selected = label; draw(); },

    zoom(factor) { zoomAt(factor, st.viewW / 2, st.viewH / 2); },
    setZoom(v) { zoomAt(v / st.scale, st.viewW / 2, st.viewH / 2); },
    fit,
    resize,
    scale: () => st.scale,
  };
})();

export { Viewer };
