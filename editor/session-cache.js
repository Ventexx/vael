const fs = require('fs');
const os = require('os');
const path = require('path');
const { randomUUID } = require('crypto');
const v8 = require('v8');

// Private, session-only scratch files. Callers receive opaque keys, never paths.
class SessionCache {
  constructor() {
    this.directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vael-editor-cache-'));
  }
  file(key) {
    if (!/^[a-f0-9-]{36}$/.test(key)) throw new Error('Invalid cache key');
    return path.join(this.directory, key);
  }
  async put(value) {
    const key = randomUUID();
    await fs.promises.writeFile(this.file(key), v8.serialize(value), { flag: 'wx' });
    return key;
  }
  async get(key) { return v8.deserialize(await fs.promises.readFile(this.file(key))); }
  async remove(key) { await fs.promises.rm(this.file(key), { force: true }); }
  close() { fs.rmSync(this.directory, { recursive: true, force: true }); }
}

module.exports = { SessionCache };
