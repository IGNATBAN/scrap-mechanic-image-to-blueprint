// Сборка чертежа Scrap Mechanic. Порт core/blueprint.py.
//
// Формат сверен с файлами, которые пишет сама игра:
//   {"bodies":[{"childs":[ ... ]}],"version":4}
//   child = {"bounds":{...},"color":"RRGGBB","pos":{...},
//            "shapeId":"<uuid>","xaxis":1,"zaxis":3}
// Цвет — 6 знаков в ВЕРХНЕМ регистре без решётки: сторонние конвертеры
// пишут "#FFFFFF", игра так не делает.
//
// Система координат игры — Z вверх, поэтому «картина» ложится в плоскость
// X-Z, а «мозаика на земле» — в X-Y.

export const VERTICAL = 'vertical';
export const HORIZONTAL = 'horizontal';

/** Ключ = упакованный цвет 0xRRGGBB, блок один на всю постройку. */
export function rgbResolver(shapeId) {
  return (key) => [((key >>> 0) & 0xffffff).toString(16).toUpperCase().padStart(6, '0'), shapeId];
}

/** Ключ = индекс в наборе материалов: у каждого свой цвет и свой блок. */
export function paletteResolver(palette, fallbackBlock) {
  return (key) => [
    (palette.paint[key] || '').toUpperCase(),
    palette.block[key] || fallbackBlock,
  ];
}

/**
 * Собрать текст blueprint.json.
 * Строки клеим вручную: на десятках тысяч деталей это на порядок быстрее и
 * экономнее по памяти, чем JSON.stringify по объектам.
 */
export function buildJson(rects, gridW, gridH, resolve, orientation = VERTICAL, center = true, depth = 1) {
  const offX = center ? -Math.floor(gridW / 2) : 0;
  depth = Math.max(1, depth | 0);

  const cache = new Map();
  const material = (key) => {
    let got = cache.get(key);
    if (!got) { got = resolve(key); cache.set(key, got); }
    return got;
  };

  const parts = new Array(rects.length);
  if (orientation === HORIZONTAL) {
    const offY = center ? -Math.floor(gridH / 2) : 0;
    for (let i = 0; i < rects.length; i++) {
      const [x, y, w, h, key] = rects[i];
      const [color, shape] = material(key);
      // строка 0 — верх картинки; сверху вниз = уменьшение Y
      parts[i] = `{"bounds":{"x":${w},"y":${h},"z":${depth}},"color":"${color}",`
        + `"pos":{"x":${x + offX},"y":${gridH - (y + h) + offY},"z":0},`
        + `"shapeId":"${shape}","xaxis":1,"zaxis":3}`;
    }
  } else {
    for (let i = 0; i < rects.length; i++) {
      const [x, y, w, h, key] = rects[i];
      const [color, shape] = material(key);
      // строка 0 — верх картинки; сверху вниз = уменьшение Z
      parts[i] = `{"bounds":{"x":${w},"y":${depth},"z":${h}},"color":"${color}",`
        + `"pos":{"x":${x + offX},"y":0,"z":${gridH - (y + h)}},`
        + `"shapeId":"${shape}","xaxis":1,"zaxis":3}`;
    }
  }

  return '{"bodies":[{"childs":[' + parts.join(',') + ']}],"version":4}';
}

/**
 * description.json ровно в том виде, в каком его пишет игра:
 * отступ в три пробела, порядок ключей, type = Blueprint.
 * Имя папки чертежа обязано совпадать с localId.
 */
export function descriptionJson(name, localId, note = '') {
  return JSON.stringify({
    description: note || 'Сделано в SM_Pixel — конвертер картинки в чертёж',
    localId,
    name,
    type: 'Blueprint',
    version: 0,
  }, null, 3);
}

/** Сколько деталей какого блока — для сводки в интерфейсе. */
export function usedBlocks(rects, resolve) {
  const counts = new Map();
  const cache = new Map();
  for (const [, , , , key] of rects) {
    let block = cache.get(key);
    if (block === undefined) { block = resolve(key)[1]; cache.set(key, block); }
    counts.set(block, (counts.get(block) || 0) + 1);
  }
  return counts;
}

/** UUID версии 4 — им называется папка чертежа. */
export function uuid4() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  const b = new Uint8Array(16);
  (typeof crypto !== 'undefined' ? crypto : { getRandomValues: (a) => a.forEach((_, i) => { a[i] = (Math.random() * 256) | 0; }) })
    .getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const hex = [...b].map((v) => v.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
