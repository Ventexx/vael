const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function cacheWith(io) {
  const context = { module: { exports: {} }, require(name) {
    if (name === 'fs') return { mkdtempSync: () => '/test-session-cache', promises: io };
    return require(name);
  } };
  vm.runInNewContext(fs.readFileSync(`${__dirname}/session-cache.js`, 'utf8'), context);
  return new context.module.exports.SessionCache();
}

test('shutdown drains writes, rejects late work, and removes the cache once with retries', async () => {
  let finishWrite, removals = 0;
  const cache = cacheWith({
    writeFile: () => new Promise(resolve => { finishWrite = resolve; }),
    async rm(directory, options) {
      removals++;
      assert.equal(directory, cache.directory);
      assert.equal(options.recursive, true);
      assert.equal(options.maxRetries, 5);
      assert.equal(options.retryDelay, 100);
    },
  });
  const write = cache.put({ source: 'image' });
  const closing = cache.close();
  assert.equal(cache.close(), closing);
  await Promise.resolve();
  assert.equal(removals, 0);
  await assert.rejects(cache.put({}), /closing/);
  await assert.rejects(cache.get('unused'), /closing/);
  await assert.rejects(cache.remove('unused'), /closing/);
  finishWrite();
  await write;
  await closing;
  assert.equal(removals, 1);
});

test('a failed in-flight write does not prevent shutdown cleanup', async () => {
  let removed = false;
  const cache = cacheWith({
    async writeFile() { throw new Error('Disk full'); },
    async rm() { removed = true; },
  });
  const write = cache.put({});
  const closing = cache.close();
  await assert.rejects(write, /Disk full/);
  await closing;
  assert.equal(removed, true);
});

test('quit waits for cleanup and still exits if temporary files remain locked', async () => {
  const source = fs.readFileSync(`${__dirname}/main.js`, 'utf8');
  const shutdown = source.slice(source.indexOf('let cacheCleanupFinished'), source.indexOf('let win;'));
  for (const fail of [false, true]) {
    let handler, complete, quits = 0, closes = 0, warnings = 0, prevented = 0;
    const cleanup = new Promise((resolve, reject) => { complete = () => fail ? reject(new Error('ENOTEMPTY')) : resolve(); });
    vm.runInNewContext(shutdown, {
      app: { on(name, fn) { assert.equal(name, 'will-quit'); handler = fn; }, quit() { quits++; handler(event); } },
      sessionCache: { close() { closes++; return cleanup; } },
      console: { warn() { warnings++; } },
    });
    const event = { preventDefault() { prevented++; } };
    handler(event);
    handler(event);
    assert.equal(quits, 0);
    assert.equal(closes, 1);
    complete();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(quits, 1);
    assert.equal(prevented, 2);
    assert.equal(warnings, Number(fail));
  }
});
