// Запуск сверки JS с Python вне браузера: node web-static/tests/node.mjs
//
// В браузере то же самое делает tests/index.html. Здесь — чтобы это гоняла
// сборка на каждый коммит: расхождение между сайтом и программой должно
// падать в CI, а не всплывать у пользователя.

import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { runTests } from './run.js';

const here = dirname(fileURLToPath(import.meta.url));
const load = async (p) => JSON.parse(await readFile(join(here, p), 'utf8'));

const [vectors, materials, bluenoise] = await Promise.all([
  load('vectors.json'),
  load('../data/materials.json'),
  load('../data/bluenoise.json'),
]);

const started = Date.now();
const results = await runTests({ vectors, materials, bluenoise });

for (const r of results) {
  console.log((r.pass ? '  OK   ' : '  FAIL ') + r.name + (r.extra ? ' — ' + r.extra : ''));
}

const failed = results.filter((r) => !r.pass);
console.log('');
if (failed.length) {
  console.log(`ПРОВАЛЕНО ${failed.length} из ${results.length}`);
  process.exit(1);
}
console.log(`ВСЁ ЗЕЛЁНОЕ — ${results.length} проверок за ${Date.now() - started} мс`);
