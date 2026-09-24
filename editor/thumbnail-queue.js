// Two jobs at a time bound file reads and decoder allocations. Pick priority
// again for every job so switching categories takes effect without restarting.
class ThumbnailQueue {
  constructor({ read, priority, apply, progress }) {
    Object.assign(this, { read, priority, apply, progress });
    this.pending = [];
    this.slots = Array.from({ length: 2 }, () => ({ worker: null, busy: false }));
    this.total = this.done = this.failed = 0;
    this.closed = false;
  }
  add(images) {
    if (this.closed) return;
    if (!this.pending.length && this.slots.every(slot => !slot.busy)) {
      this.total = this.done = this.failed = 0;
    }
    for (const img of images) this.pending.push(img);
    this.total += images.length;
    this.report();
    this.pump();
  }
  report() { this.progress({ total: this.total, done: this.done, failed: this.failed }); }
  pump() {
    for (const slot of this.slots) {
      if (slot.busy || this.closed || !this.pending.length) continue;
      const preferred = this.pending.findIndex(img => !img._disposed && this.priority(img));
      const [img] = this.pending.splice(preferred < 0 ? 0 : preferred, 1);
      slot.busy = true;
      this.run(slot, img);
    }
  }
  async run(slot, img) {
    try {
      if (img._disposed) return;
      const bytes = await this.read(img.path);
      if (this.closed || img._disposed) return;
      const result = await new Promise((resolve, reject) => {
        const worker = slot.worker || (slot.worker = new Worker('thumbnail-worker.js'));
        const timer = setTimeout(() => {
          worker.terminate(); slot.worker = null;
          reject(new Error('Thumbnail timed out'));
        }, 120000);
        slot.cancel = () => { clearTimeout(timer); reject(new Error('Import closed')); };
        worker.onmessage = ({ data }) => { clearTimeout(timer); resolve(data); };
        worker.onerror = event => {
          clearTimeout(timer); event.preventDefault();
          worker.terminate(); slot.worker = null;
          reject(new Error(event.message || 'Thumbnail worker failed'));
        };
        const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
        worker.postMessage({ bytes: buffer }, [buffer]);
      });
      try {
        if (result.error) throw new Error(result.error);
        if (!this.closed && !img._disposed) this.apply(img, result);
      } finally { result.bitmap?.close(); }
    } catch (error) {
      if (!this.closed && !img._disposed) {
        img._thumbnailError = error.message;
        if (img._thumbWrap) img._thumbWrap.title = `${img.name} — Preview unavailable: ${error.message}`;
        this.failed++;
      }
    } finally {
      slot.busy = false;
      slot.cancel = null;
      this.done++;
      if (!this.closed) { this.report(); this.pump(); }
    }
  }
  close() {
    this.closed = true;
    this.pending.length = 0;
    for (const slot of this.slots) {
      slot.worker?.terminate();
      slot.cancel?.();
    }
  }
}
