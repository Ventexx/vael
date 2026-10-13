const { Worker } = require('worker_threads');
const path = require('path');

// Keep hashing, metadata processing and atomic writes out of Electron's
// window process too. Worker failures reject jobs so their snapshots survive.
class SaveService {
  constructor() { this.worker = null; this.pending = new Map(); this.serial = 0; }
  save(data) {
    if (!this.worker) {
      const worker = this.worker = new Worker(path.join(__dirname, 'save-file-worker.js'));
      const fail = error => {
        if (this.worker !== worker) return;
        this.worker = null;
        for (const request of this.pending.values()) request.reject(error);
        this.pending.clear();
        worker.terminate();
      };
      worker.on('message', result => {
        const request = this.pending.get(result.id);
        if (!request) return;
        this.pending.delete(result.id);
        if (result.error) request.reject(new Error(result.error));
        else request.resolve(result.version);
      });
      worker.on('error', fail);
      worker.on('exit', () => fail(new Error('The save worker stopped. Retry the save.')));
    }
    const id = ++this.serial;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      // IPC buffers can share a backing allocation; transfer a dedicated copy.
      const bytes = Uint8Array.from(data.bytes);
      try { this.worker.postMessage({ ...data, id, bytes }, [bytes.buffer]); }
      catch (error) { this.pending.delete(id); reject(error); }
    });
  }
  close() { this.worker?.terminate(); }
}
module.exports = { SaveService };
