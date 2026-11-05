const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function environment(extra = {}) {
  const context = vm.createContext({
    setTimeout, clearTimeout,
    ImageData: class { constructor(data, width, height) { Object.assign(this, { data, width, height }); } },
    ...extra,
  });
  vm.runInContext(fs.readFileSync(`${__dirname}/image-preparation.js`, 'utf8'), context);
  return context;
}

test('canvas installation yields between bounded uploads and preserves every pixel', async () => {
  let yields = 0;
  const context = environment({ scheduler: { postTask: async () => { yields++; } } });
  const width = 1024, height = 150;
  const pixels = Uint8ClampedArray.from({ length: width * height * 4 }, (_, i) => i % 251);
  const restored = new Uint8ClampedArray(pixels.length);
  const bands = [];
  const ctx = { putImageData(band, x, y) {
    assert.equal(x, 0);
    assert.ok(band.width * band.height <= 65536);
    assert.equal(yields, bands.length + 1);
    restored.set(band.data, y * width * 4);
    bands.push(band);
  } };
  assert.equal(await context.installImagePixels(ctx, pixels, width, height, () => false), true);
  assert.equal(bands.length, 3);
  assert.deepEqual(restored, pixels);
});

test('disposing an image during a yield stops subsequent uploads', async () => {
  let cancelled = false, uploads = 0;
  const context = environment({ scheduler: { postTask: async () => { cancelled = true; } } });
  assert.equal(await context.installImagePixels({ putImageData() { uploads++; } },
    new Uint8ClampedArray(16), 2, 2, () => cancelled), false);
  assert.equal(uploads, 0);
});

test('worker success and decode failure both release the worker', async () => {
  for (const response of [{ w: 1, h: 1, pixels: new Uint8ClampedArray(4) }, { error: 'Invalid image' }]) {
    let terminated = false;
    const context = environment({ Worker: class {
      postMessage() { queueMicrotask(() => this.onmessage({ data: response })); }
      terminate() { terminated = true; }
    } });
    const job = context.prepareImagePixels('data:image/png;base64,', 1024);
    if (response.error) await assert.rejects(job, /Invalid image/);
    else assert.equal(await job, response);
    assert.equal(terminated, true);
  }
});

test('an in-flight background upload pauses for edits and resumes afterward', async () => {
  let editing = true, uploads = 0, waits = 0;
  const context = environment({
    scheduler: { postTask: async () => {} },
    setTimeout(resolve) {
      assert.equal(uploads, 0);
      waits++;
      editing = false;
      queueMicrotask(resolve);
    },
  });
  assert.equal(await context.installImagePixels({ putImageData() { uploads++; } },
    new Uint8ClampedArray(16), 2, 2, () => false, () => editing), true);
  assert.equal(waits, 1);
  assert.equal(uploads, 1);
});

// Exercise the real filter entry point with cache I/O held indefinitely. This
// catches both pre-filter waits and waits in finally (including template-save).
test('blur finishes while background cache work is unresolved', { timeout: 1000 }, async () => {
  const html = fs.readFileSync(`${__dirname}/editor.html`, 'utf8');
  const extract = (start, end) => html.slice(html.indexOf(start), html.indexOf(end, html.indexOf(start)));
  const img = { w: 2, h: 2, mask: null };
  let blurred = false, history = false, scheduled = 0, cacheWaits = 0;
  const context = vm.createContext({
    state: { images: [img], activeIdx: 0, multiSelected: new Set() },
    BLUR_COOLDOWN_MS: 500, _lastBlurAt: 0, BOKEH_POOL_SIZE: 4,
    OPERATION_MEMORY_LIMIT: 1024 * 1024 * 1024,
    setFiltersBusy(busy) { context.state.filterBusy = busy; },
    makeMask: (w, h) => new Uint8Array(w * h),
    setStatus() {}, renderGallery() {}, updateStatusBar() {}, filterLabel: () => 'Blur',
    scheduleMemoryCheck() { scheduled++; },
    enforceMemoryBudget() { cacheWaits++; return new Promise(() => {}); },
    async bokehBlurSelection() { blurred = true; },
    pushHistory() { history = true; }, console,
  });
  vm.runInContext(extract('async function reserveFilterMemory(', 'function releaseImage('), context);
  vm.runInContext(extract('async function runFilter(', 'async function runFilterBatch('), context);
  assert.equal(await context.runFilter('blur', 3), true);
  assert.equal(blurred, true);
  assert.equal(history, true);
  assert.equal(context.state.filterBusy, false);
  assert.equal(img._pinned, false);
  assert.equal(cacheWaits, 0);
  assert.ok(scheduled > 0);
  await assert.rejects(context.reserveFilterMemory({ w: 100000, h: 100000 }, 'blur', 3), /too large/);
});
