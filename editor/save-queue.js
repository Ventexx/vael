// Immutable snapshots stay owned by the queue until their write succeeds.
// One worker job at a time avoids competing encoders during rapid editing.
class BackgroundSaveQueue {
  constructor({ execute, changed = () => {}, limit = 512 * 1024 * 1024 }) {
    Object.assign(this, { execute, changed, limit });
    this.jobs = [];
    this.bytes = 0;
    this.running = false;
    this.serial = 0;
  }
  has(owner) { return this.jobs.some(job => job.owner === owner); }
  get pending() { return this.jobs.filter(job => job.status !== 'failed').length; }
  get failed() { return this.jobs.filter(job => job.status === 'failed'); }
  checkCapacity(bytes) {
    if (this.bytes + bytes > this.limit) throw new Error('Save queue is full. Wait for pending saves or retry failed saves, then save this image again.');
  }
  add(data) {
    this.checkCapacity(data.bytes);
    const job = { ...data, id: ++this.serial, status: 'pending' };
    this.bytes += job.bytes;
    this.jobs.push(job);
    this.changed();
    // Yield so the next image paints before background preparation begins.
    setTimeout(() => this.pump(), 0);
    return job;
  }
  retry(job) {
    if (!this.jobs.includes(job) || job.status !== 'failed') return;
    job.status = 'pending'; job.error = null;
    this.changed(); this.pump();
  }
  discard(job) {
    if (!this.jobs.includes(job) || job.status !== 'failed') return;
    this.jobs.splice(this.jobs.indexOf(job), 1);
    this.bytes -= job.bytes;
    job.status = 'discarded';
    job.dispose?.();
    this.changed(); this.pump();
  }
  async pump() {
    if (this.running) return;
    const job = this.jobs.find((candidate, index) => candidate.status === 'pending' &&
      !this.jobs.slice(0, index).some(prior => prior.owner === candidate.owner || prior.destinationKey === candidate.destinationKey));
    if (!job) return;
    this.running = true;
    job.status = 'running'; this.changed();
    try {
      await this.execute(job);
      this.jobs.splice(this.jobs.indexOf(job), 1);
      this.bytes -= job.bytes;
      job.status = 'done';
      job.dispose?.();
    } catch (error) {
      job.status = 'failed'; job.error = error.message || String(error);
    } finally {
      this.running = false;
      this.changed();
      setTimeout(() => this.pump(), 0);
    }
  }
}
if (typeof module !== 'undefined') module.exports = { BackgroundSaveQueue };
