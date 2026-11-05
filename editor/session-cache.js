const fs = require('fs');
const os = require('os');
const path = require('path');
const { randomUUID } = require('crypto');
const v8 = require('v8');

// Private, session-only scratch files. Callers receive opaque keys, never paths.
class SessionCache {
  constructor() {
    this.directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vael-editor-cache-'));
    this.pending = new Set();
    this.closing = false;
    this.closePromise = null;
  }
  file(key) {
    if (!/^[a-f0-9-]{36}$/.test(key)) throw new Error('Invalid cache key');
    return path.join(this.directory, key);
  }
  run(operation) {
    if (this.closing) return Promise.reject(new Error('Session cache is closing'));
    const pending = Promise.resolve().then(operation);
    this.pending.add(pending);
    pending.then(() => this.pending.delete(pending), () => this.pending.delete(pending));
    return pending;
  }
  put(value) {
    return this.run(async () => {
      const key = randomUUID();
      await fs.promises.writeFile(this.file(key), v8.serialize(value), { flag: 'wx' });
      return key;
    });
  }
  get(key) { return this.run(async () => v8.deserialize(await fs.promises.readFile(this.file(key)))); }
  remove(key) { return this.run(() => fs.promises.rm(this.file(key), { force: true })); }
  close() {
    if (this.closePromise) return this.closePromise;
    this.closing = true;
    this.closePromise = (async () => {
      // Stop accepting work before waiting: a late write must not recreate a
      // file while recursive removal is walking the directory.
      await Promise.allSettled([...this.pending]);
      await fs.promises.rm(this.directory, {
        recursive: true, force: true, maxRetries: 5, retryDelay: 100,
      });
    })();
    return this.closePromise;
  }
}

module.exports = { SessionCache };
