const fs = require('node:fs');
const path = require('node:path');
const { isMainThread, parentPort, workerData } = require('node:worker_threads');

const extensions = new Set(['.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif']);
const iteration = /^(.+)_(\d{3,})_?\.(png|jpe?g|webp|bmp|gif)$/i;
const naturalCompare = (a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
const directoryKey = dir => process.platform === 'win32' ? path.resolve(dir).toLowerCase() : path.resolve(dir);
function canonicalRoot(dir) {
  try { return fs.realpathSync.native(dir); }
  catch { return path.resolve(dir); }
}

function scanLibrary(config) {
  const all = [], warnings = [], seen = new Set();
  const hidden = new Set(config.hiddenFolders.map(folder => directoryKey(canonicalRoot(folder))));
  const pending = config.folders.map(canonicalRoot).reverse();
  while (pending.length) {
    const dir = pending.pop(), identity = directoryKey(dir);
    if (seen.has(identity) || hidden.has(identity)) continue;
    seen.add(identity);
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); }
    catch (error) { warnings.push({ path: dir, error: error.message }); continue; }
    const groups = Object.create(null);
    for (const entry of entries) {
      if (entry.isDirectory()) {
        if (!entry.name.startsWith('.') && !entry.name.startsWith('!')) pending.push(path.join(dir, entry.name));
      } else if (entry.isFile() && extensions.has(path.extname(entry.name).toLowerCase())) {
        const match = entry.name.match(iteration);
        const base = match ? match[1] : entry.name.replace(/\.[^.]+$/, '');
        (groups[base] ||= []).push({ name: entry.name, iter: match ? Number(match[2]) : 1, iteration: !!match });
      }
    }
    const order = [];
    for (const base of Object.keys(groups).sort(naturalCompare)) {
      groups[base].sort((a, b) => a.iter - b.iter || naturalCompare(a.name, b.name));
      for (const entry of groups[base]) order.push({ base, ...entry });
    }
    if (order.length) all.push({ dir, groups, order });
  }
  all.sort((a, b) => naturalCompare(a.dir, b.dir));
  const repeated = [];
  for (const folder of all) {
    const groups = Object.create(null);
    for (const [base, entries] of Object.entries(folder.groups)) {
      const iterations = entries.filter(entry => entry.iteration);
      if (iterations.length > 1) groups[base] = iterations;
    }
    if (Object.keys(groups).length) repeated.push({ dir: folder.dir, groups });
  }
  return { all, repeated, warnings };
}

module.exports = { scanLibrary };
if (!isMainThread) parentPort.postMessage(scanLibrary(workerData));
