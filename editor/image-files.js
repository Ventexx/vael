const path = require('path');
const fs = require('fs');
const { createHash, randomUUID } = require('crypto');

function outputFormat(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  const format = { '.png': 'png', '.jpg': 'jpeg', '.jpeg': 'jpeg', '.webp': 'webp' }[ext];
  if (!format) throw new Error('Choose a .png, .jpg, .jpeg, or .webp filename.');
  return format;
}

function decodeExport(filePath, src) {
  const expected = outputFormat(filePath);
  const match = /^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)$/.exec(src);
  if (!match || match[1] !== expected) throw new Error('The image encoding does not match the filename.');
  const bytes = Buffer.from(match[2], 'base64');
  const valid = expected === 'png'
    ? bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
    : expected === 'jpeg'
      ? bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255
      : bytes.toString('ascii', 0, 4) === 'RIFF' && bytes.toString('ascii', 8, 12) === 'WEBP';
  if (!valid) throw new Error('The encoded image is not valid for the chosen format.');
  return bytes;
}

function versionOf(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
async function fileVersion(filePath) {
  try {
    const info = await fs.promises.lstat(filePath);
    if (!info.isFile()) throw new Error('Choose a regular file destination, not a link or directory.');
    return versionOf(await fs.promises.readFile(filePath));
  } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}
async function readSnapshot(filePath) {
  const file = await fs.promises.open(filePath, 'r');
  try {
    const before = await file.stat();
    const bytes = await file.readFile();
    const after = await file.stat();
    if (before.size !== after.size || before.mtimeMs !== after.mtimeMs || before.ctimeMs !== after.ctimeMs) {
      throw new Error('The image changed while it was being read. Open it again.');
    }
    const ext = path.extname(filePath).slice(1).toLowerCase();
    const mime = ext === 'jpg' ? 'jpeg' : ext;
    return { src: `data:image/${mime};base64,${bytes.toString('base64')}`, version: versionOf(bytes) };
  } finally { await file.close(); }
}

// Serialize writers within this app. The version is per opened image, so two
// copies of the same path cannot silently overwrite one another's edits.
const pendingWrites = new Map();
function atomicSave(filePath, bytes, expectedVersion) {
  const destination = path.resolve(filePath);
  const key = process.platform === 'win32' ? destination.toLowerCase() : destination;
  const previous = pendingWrites.get(key) || Promise.resolve();
  const operation = previous.catch(() => {}).then(async () => {
    const checkVersion = async () => {
      if (expectedVersion === undefined || await fileVersion(destination) !== expectedVersion) {
        throw new Error('The destination changed outside this image session. Use Save As to keep a separate copy, or reopen the file.');
      }
    };
    await checkVersion();
    const temporary = path.join(path.dirname(destination), `.${path.basename(destination)}.${randomUUID()}.tmp`);
    let handle;
    try {
      const mode = expectedVersion === null ? 0o666 : (await fs.promises.stat(destination)).mode;
      handle = await fs.promises.open(temporary, 'wx', mode);
      await handle.writeFile(bytes);
      await handle.sync();
      await handle.close(); handle = null;
      await checkVersion();
      if (expectedVersion === null) {
        // Create-only publication also guards a file created after the check.
        await fs.promises.link(temporary, destination);
      } else {
        await fs.promises.rename(temporary, destination);
      }
      return versionOf(bytes);
    } finally {
      if (handle) await handle.close();
      await fs.promises.rm(temporary, { force: true }).catch(() => {});
    }
  });
  pendingWrites.set(key, operation);
  operation.finally(() => { if (pendingWrites.get(key) === operation) pendingWrites.delete(key); }).catch(() => {});
  return operation;
}

module.exports = { outputFormat, decodeExport, versionOf, fileVersion, readSnapshot, atomicSave };
