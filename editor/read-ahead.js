// One full-resolution preparation at a time. Re-evaluate the route after
// every image, so a jump never leaves a long obsolete queue ahead of it.
function workflowImages(images, categories) {
  const buckets = new Map([[null, []], ...categories.map(cat => [cat.id, []])]);
  for (const img of images) {
    const id = img.categoryId ?? null;
    if (!buckets.has(id)) buckets.set(id, []);
    buckets.get(id).push(img);
  }
  return [...buckets.values()].flat();
}

function readAheadPlan(order, active) {
  if (!active || !order.includes(active)) return [];
  return order.filter(img => !img._disposed &&
    (img.categoryId ?? null) === (active.categoryId ?? null));
}

function nextCategoryPlan(images, categories, active) {
  const current = readAheadPlan(images, active);
  if (!current.length) return [];
  const order = workflowImages(images, categories);
  const last = order.indexOf(current[current.length - 1]);
  const first = order[last + 1];
  if (!first) return [];
  return readAheadPlan(images, first);
}

// Finish a category before exposing its first working frame. Recheck the
// route between images so rapid category changes cancel obsolete work.
async function prepareCategory({ plan, ready, prepare, cancelled, progress }) {
  const failed = new Set(plan().filter(img => img._readAheadError));
  while (!cancelled()) {
    const images = plan();
    progress(images.filter(ready).length, images.length, failed.size);
    const next = images.find(img => !ready(img) && !failed.has(img));
    if (!next) return failed;
    try { await prepare(next); }
    catch (error) { next._readAheadError = error.message; failed.add(next); }
  }
  return failed;
}

class ImageReadAhead {
  constructor({ plan, ready, prepare, paused, progress, delay = 30 }) {
    Object.assign(this, { plan, ready, prepare, paused, progress, delay });
    this.failed = new WeakSet();
    this.running = false;
    this.closed = false;
    this.timer = null;
  }
  request(delay = this.delay) {
    if (this.closed || this.running || this.timer !== null) return;
    this.timer = setTimeout(() => { this.timer = null; this.run(); }, delay);
  }
  async run() {
    if (this.closed) return;
    if (this.paused()) { this.request(Math.max(50, this.delay)); return; }
    const plan = this.plan().filter(img => !img._disposed);
    this.progress(plan.filter(this.ready).length, plan.length);
    const next = plan.find(img => !this.ready(img) && !this.failed.has(img));
    if (!next) return;
    this.running = true;
    try { await this.prepare(next); }
    catch (error) { this.failed.add(next); next._readAheadError = error.message; }
    finally {
      this.running = false;
      this.request();
    }
  }
  close() { this.closed = true; clearTimeout(this.timer); this.timer = null; }
}

if (typeof module !== 'undefined') module.exports = { workflowImages, readAheadPlan, nextCategoryPlan, prepareCategory, ImageReadAhead };
