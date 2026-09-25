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

function readAheadPlan(order, active, budget, estimate) {
  const at = order.indexOf(active);
  if (at < 0) return [];
  const candidates = [...order.slice(at + 1, at + 9), ...order.slice(Math.max(0, at - 2), at).reverse()];
  const plan = [];
  let used = estimate(active);
  for (const img of candidates) {
    const bytes = estimate(img);
    // Leave room for encoded sources, thumbnails and transient allocations.
    if (used + bytes > budget * 0.75) break;
    plan.push(img); used += bytes;
  }
  return plan;
}

class ImageReadAhead {
  constructor({ plan, ready, prepare, paused, progress }) {
    Object.assign(this, { plan, ready, prepare, paused, progress });
    this.failed = new WeakSet();
    this.running = false;
    this.closed = false;
    this.timer = null;
  }
  request() {
    if (this.closed || this.running || this.timer !== null) return;
    this.timer = setTimeout(() => { this.timer = null; this.run(); }, 30);
  }
  async run() {
    if (this.closed) return;
    if (this.paused()) { this.request(); return; }
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

if (typeof module !== 'undefined') module.exports = { workflowImages, readAheadPlan, ImageReadAhead };
