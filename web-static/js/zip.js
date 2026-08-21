// Сборка ZIP прямо в браузере, без единой сторонней библиотеки.
//
// Формат простой и старый: на каждый файл — локальный заголовок и данные,
// в конце — центральный каталог. Сжатие берём у самого браузера через
// CompressionStream('deflate-raw'); если его нет, кладём файлы без сжатия
// (метод 0) — архив всё равно останется корректным, только крупнее.
//
// Чертежи Scrap Mechanic — это текст JSON, он жмётся раз в десять, поэтому
// сжатие тут не роскошь: на модульной картине речь про десятки мегабайт.

const encoder = new TextEncoder();

// ── CRC32 ────────────────────────────────────────────────────────────────

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[i] = c >>> 0;
  }
  return table;
})();

function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

async function deflateRaw(bytes) {
  if (typeof CompressionStream === 'undefined') return null;
  try {
    const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('deflate-raw'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  } catch {
    return null;
  }
}

// ── запись полей ─────────────────────────────────────────────────────────

class Writer {
  constructor() {
    this.chunks = [];
    this.length = 0;
  }

  push(bytes) {
    this.chunks.push(bytes);
    this.length += bytes.length;
  }

  header(fields) {
    const size = fields.reduce((n, [w]) => n + w, 0);
    const buf = new Uint8Array(size);
    const view = new DataView(buf.buffer);
    let off = 0;
    for (const [width, value] of fields) {
      if (width === 2) view.setUint16(off, value, true);
      else view.setUint32(off, value >>> 0, true);
      off += width;
    }
    this.push(buf);
  }
}

/**
 * Собрать ZIP.
 * files: [{ name: 'путь/в/архиве', data: Uint8Array | string }]
 * Возвращает Blob.
 */
export async function makeZip(files) {
  const w = new Writer();
  const entries = [];

  for (const file of files) {
    const nameBytes = encoder.encode(file.name);
    const raw = typeof file.data === 'string' ? encoder.encode(file.data) : file.data;
    const crc = crc32(raw);

    const packed = await deflateRaw(raw);
    const useDeflate = packed && packed.length < raw.length;
    const body = useDeflate ? packed : raw;
    const method = useDeflate ? 8 : 0;

    const offset = w.length;
    w.header([
      [4, 0x04034b50],      // подпись локального заголовка
      [2, 20],              // нужная версия
      [2, 0x0800],          // флаг: имена в UTF-8
      [2, method],
      [2, 0], [2, 0],       // время и дата — нули, чтобы архив был воспроизводим
      [4, crc],
      [4, body.length],
      [4, raw.length],
      [2, nameBytes.length],
      [2, 0],               // extra
    ]);
    w.push(nameBytes);
    w.push(body);

    entries.push({ nameBytes, crc, method, packedSize: body.length, size: raw.length, offset });
  }

  const dirStart = w.length;
  for (const e of entries) {
    w.header([
      [4, 0x02014b50],      // подпись записи каталога
      [2, 20], [2, 20],
      [2, 0x0800],
      [2, e.method],
      [2, 0], [2, 0],
      [4, e.crc],
      [4, e.packedSize],
      [4, e.size],
      [2, e.nameBytes.length],
      [2, 0], [2, 0],       // extra, comment
      [2, 0], [2, 0],       // номер диска, внутренние атрибуты
      [4, 0],               // внешние атрибуты
      [4, e.offset],
    ]);
    w.push(e.nameBytes);
  }
  const dirSize = w.length - dirStart;

  w.header([
    [4, 0x06054b50],        // конец центрального каталога
    [2, 0], [2, 0],
    [2, entries.length], [2, entries.length],
    [4, dirSize],
    [4, dirStart],
    [2, 0],
  ]);

  return new Blob(w.chunks, { type: 'application/zip' });
}

/** Отдать пользователю файл на скачивание. */
export function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
