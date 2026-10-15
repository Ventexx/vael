const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { Worker } = require('node:worker_threads');
const naturalCompare = require('./natural-sort');
const { scanLibrary } = require('./scanner');

const expected = [];
for (const [letter, count] of [['A', 8], ['B', 16], ['C', 24], ['D', 8], ['E', 16]]) {
  if (letter === 'A' || letter === 'D') expected.push(`base_${letter}!_000${letter === 'A' ? '67' : '68'}_.png`);
  for (let i = 1; i <= count; i++) expected.push(`base_${letter}_${i}_00001_.png`);
}

test('requested filename order, including punctuation and multi-digit numbers', () => {
  assert.deepEqual([...expected].reverse().sort(naturalCompare), expected);
  assert.equal(naturalCompare('BASE_01.png', 'base_1.png'), 0);
  assert.ok(naturalCompare('image_9007199254740992.png', 'image_9007199254740993.png') < 0);
  assert.ok(naturalCompare('base_A!', 'base_A_1') < 0);
});

test('browser and Node use the same ordering; packaged app includes the helper', () => {
  const context = vm.createContext({});
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'natural-sort.js'), 'utf8'), context);
  assert.deepEqual([...expected].reverse().sort(context.naturalCompare), expected);
  const html = fs.readFileSync(path.join(__dirname, 'reviewer.html'), 'utf8');
  assert.ok(html.indexOf('<script src="natural-sort.js"></script>') < html.indexOf('<script>'));
  assert.ok(require('./package.json').build.files.includes('natural-sort.js'));
});

test('scanner and background worker preserve grouping and filtering with the new order', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'reviewer-sorting-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  for (const name of [...expected].reverse()) fs.writeFileSync(path.join(dir, name), 'fixture');
  fs.writeFileSync(path.join(dir, 'base_A_1_00002_.png'), 'fixture');
  fs.writeFileSync(path.join(dir, 'ignore.txt'), 'fixture');
  fs.mkdirSync(path.join(dir, '!excluded'));
  fs.writeFileSync(path.join(dir, '!excluded', 'image.png'), 'fixture');
  const config = { folders: [dir, dir], hiddenFolders: [] };
  const result = scanLibrary(config);
  const names = [...expected];
  names.splice(2, 0, 'base_A_1_00002_.png');
  assert.equal(result.all.length, 1);
  assert.deepEqual(result.all[0].order.map(entry => entry.name), names);
  assert.deepEqual(Object.keys(result.all[0].groups).sort(naturalCompare), expected.map(name => name.replace(/_\d+_\.png$/, '')));
  assert.deepEqual(Object.keys(result.repeated[0].groups), ['base_A_1']);
  assert.deepEqual(result.repeated[0].groups.base_A_1.map(entry => entry.iter), [1, 2]);
  assert.deepEqual(result.warnings, []);
  assert.deepEqual(scanLibrary({ folders: [dir], hiddenFolders: [dir] }).all, []);
  const worker = new Worker(path.join(__dirname, 'scanner.js'), { workerData: config });
  t.after(() => worker.terminate());
  const background = await new Promise((resolve, reject) => {
    worker.once('message', resolve);
    worker.once('error', reject);
    worker.once('exit', code => { if (code) reject(new Error(`Worker exited with ${code}`)); });
  });
  assert.deepEqual(background.all[0].order.map(entry => entry.name), names);
});
