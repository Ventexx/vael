const { app, BrowserWindow, ipcMain, dialog, nativeImage, shell } = require('electron');
const path = require('path');
const fs = require('fs');

let win;

// Windows keys the taskbar icon/grouping off the app's AppUserModelID, not
// just the BrowserWindow `icon` option below -- without this, a dev run
// (or even some packaged installs) can silently fall back to the generic
// Electron icon in the taskbar even though icon.ico loads fine for the
// window itself. Matches the appId in package.json's build config. No-op
// on other platforms.
if (process.platform === 'win32') {
  app.setAppUserModelId('com.vael.reviewer');
}

function createWindow() {
  const iconPath = path.join(__dirname, process.platform === 'win32' ? 'icon.ico' : 'icon.png');
  const icon = fs.existsSync(iconPath) ? nativeImage.createFromPath(iconPath) : undefined;

  const saved = loadWindowState();

  win = new BrowserWindow({
    width: (saved && saved.width) || 1360,
    height: (saved && saved.height) || 860,
    x: saved && typeof saved.x === 'number' ? saved.x : undefined,
    y: saved && typeof saved.y === 'number' ? saved.y : undefined,
    minWidth: 860,
    minHeight: 560,
    backgroundColor: '#0a0a0a',
    frame: false,
    titleBarStyle: 'hidden',
    icon,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  if (saved && saved.isMaximized) win.maximize();

  win.loadFile(path.join(__dirname, 'reviewer.html'));
  win.webContents.on('did-finish-load', () => {
    if (saved && saved.zoomFactor) win.webContents.setZoomFactor(saved.zoomFactor);
  });

  win.on('resize', scheduleSaveWindowState);
  win.on('move', scheduleSaveWindowState);
  win.on('close', () => saveWindowStateNow());

  win.webContents.on('before-input-event', handleWindowShortcut);
}

function handleWindowShortcut(event, input) {
  if (input.type !== 'keyDown') return;
  if (input.key === 'F12') { event.preventDefault(); win.webContents.toggleDevTools(); return; }
  if (!(input.control || input.meta) || input.alt) return;
  if (input.key === '+' || input.key === '=') { event.preventDefault(); zoomBy(0.1); }
  else if (input.key === '-') { event.preventDefault(); zoomBy(-0.1); }
  else if (input.key === '0') { event.preventDefault(); win.webContents.setZoomFactor(1); scheduleSaveWindowState(); }
}

function zoomBy(delta) {
  const current = win.webContents.getZoomFactor();
  const next = Math.min(2.5, Math.max(0.5, +(current + delta).toFixed(2)));
  win.webContents.setZoomFactor(next);
  scheduleSaveWindowState();
}

// Window controls
ipcMain.on('win-minimize', () => win.minimize());
ipcMain.on('win-maximize', () => win.isMaximized() ? win.unmaximize() : win.maximize());
ipcMain.on('win-close', () => win.close());

// ---------------------------------------------------------------------------
// Config: a flat JSON file in userData holding the list of watched root
// folders and window state. Kept dead simple on purpose — this is a small
// utility app, not something that needs a real database.
// ---------------------------------------------------------------------------
const CONFIG_PATH = path.join(app.getPath('userData'), 'vael-reviewer-config.json');
// Old filename from when this app was branded "vanta." -- kept only so we can
// migrate a returning user's folders/window-state onto the new filename once.
const LEGACY_CONFIG_PATH = path.join(app.getPath('userData'), 'vanta-reviewer-config.json');

// One-time migration: if the new config doesn't exist yet but the old one
// does, carry it over so existing users don't lose their watched folders or
// window state just because of the rename. Safe to leave in indefinitely --
// it's a no-op once the new file exists (which it will after the first save).
function migrateLegacyConfig() {
  try {
    if (!fs.existsSync(CONFIG_PATH) && fs.existsSync(LEGACY_CONFIG_PATH)) {
      fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
      fs.copyFileSync(LEGACY_CONFIG_PATH, CONFIG_PATH);
    }
  } catch (e) {
    // Non-fatal -- worst case the user just starts with an empty config,
    // same as before this migration existed.
  }
}
migrateLegacyConfig();

function loadConfig() {
  try {
    const raw = fs.readFileSync(CONFIG_PATH, 'utf8');
    const cfg = JSON.parse(raw);
    if (!Array.isArray(cfg.folders)) cfg.folders = [];
    if (!Array.isArray(cfg.hiddenFolders)) cfg.hiddenFolders = [];
    return cfg;
  } catch (e) {
    return { folders: [], hiddenFolders: [] };
  }
}
function saveConfig(cfg) {
  fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2));
}

ipcMain.handle('get-config', () => loadConfig());

// ---------------------------------------------------------------------------
// Window state: remember size, position, maximized state, and content zoom
// across launches. Stored in the same config file as everything else.
// ---------------------------------------------------------------------------
function loadWindowState() {
  const cfg = loadConfig();
  return cfg.windowState || null;
}
function saveWindowStateNow() {
  if (!win || win.isDestroyed()) return;
  const bounds = win.getBounds();
  const isMaximized = win.isMaximized();
  const zoomFactor = win.webContents.getZoomFactor();
  const cfg = loadConfig();
  cfg.windowState = { ...bounds, isMaximized, zoomFactor };
  saveConfig(cfg);
}
let saveStateTimer = null;
function scheduleSaveWindowState() {
  clearTimeout(saveStateTimer);
  saveStateTimer = setTimeout(saveWindowStateNow, 400);
}

ipcMain.handle('add-folder', async () => {
  const { filePaths, canceled } = await dialog.showOpenDialog(win, {
    properties: ['openDirectory'],
    title: 'Add a folder to watch',
  });
  if (canceled || !filePaths || !filePaths[0]) return loadConfig();
  const cfg = loadConfig();
  const dir = filePaths[0];
  if (!cfg.folders.includes(dir)) cfg.folders.push(dir);
  saveConfig(cfg);
  return cfg;
});

ipcMain.handle('remove-folder', (_, folder) => {
  const cfg = loadConfig();
  cfg.folders = cfg.folders.filter(f => f !== folder);
  cfg.hiddenFolders = cfg.hiddenFolders.filter(f => f !== folder);
  saveConfig(cfg);
  return cfg;
});

// Hiding a folder excludes it from scanning (so it stops counting toward the
// watched-image total and drops out of the sidebar) without forgetting it --
// it stays in cfg.folders and can be un-hidden later without re-browsing to
// it. This is deliberately separate from remove-folder, which forgets the
// path entirely.
ipcMain.handle('toggle-folder-hidden', (_, folder) => {
  const cfg = loadConfig();
  if (cfg.hiddenFolders.includes(folder)) {
    cfg.hiddenFolders = cfg.hiddenFolders.filter(f => f !== folder);
  } else {
    cfg.hiddenFolders.push(folder);
  }
  saveConfig(cfg);
  return cfg;
});

// ---------------------------------------------------------------------------
// Scanning. Recurses every watched root, skipping any directory whose name
// starts with "." or "!". Inside every directory it groups image files by
// their "stable" name — everything before the trailing _<iteration>_ counter
// that ComfyUI appends — and keeps only groups with more than one file,
// since those are the only ones that need a human decision.
// ---------------------------------------------------------------------------
const { Worker } = require('node:worker_threads');
const scanJobs = new Set();
function scanSnapshot() {
  const config = loadConfig();
  return new Promise((resolve, reject) => {
    const worker = new Worker(path.join(__dirname, 'scanner.js'), { workerData: config });
    scanJobs.add(worker);
    let delivered = false;
    worker.once('message', result => { delivered = true; resolve(result); });
    worker.once('error', reject);
    worker.once('exit', code => {
      scanJobs.delete(worker);
      if (!delivered) reject(new Error('Folder scan stopped before returning results (exit ' + code + ').'));
    });
  });
}
ipcMain.handle('scan-snapshot', scanSnapshot);
ipcMain.handle('scan', async () => (await scanSnapshot()).repeated);
ipcMain.handle('scan-all', async () => (await scanSnapshot()).all);
function naturalCompare(a, b) {
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
}

ipcMain.handle('read-image-bytes', async (_, dir, name, expectedVersion) => {
  const filePath = path.join(dir, name);
  const handle = await fs.promises.open(filePath, 'r');
  const version = stat => JSON.stringify([stat.size, stat.mtimeMs, stat.ctimeMs]);
  try {
    const before = await handle.stat();
    if (!before.isFile() || before.size > 64 * 1024 * 1024) throw new Error('Image must be a file no larger than 64 MiB.');
    if (expectedVersion && version(before) !== expectedVersion) throw new Error('Image changed; rescan to load its new version.');
    const bytes = Buffer.alloc(before.size);
    let offset = 0;
    while (offset < bytes.length) {
      const { bytesRead } = await handle.read(bytes, offset, bytes.length - offset, offset);
      if (!bytesRead) throw new Error('Image changed while reading.');
      offset += bytesRead;
    }
    if (version(await handle.stat()) !== version(before)
        || version(await fs.promises.stat(filePath)) !== version(before)) throw new Error('Image changed while reading; rescan and retry.');
    const ext = path.extname(name).slice(1).toLowerCase();
    return { bytes: new Uint8Array(bytes), mime: 'image/' + (ext === 'jpg' ? 'jpeg' : ext) };
  } finally { await handle.close(); }
});

// ---------------------------------------------------------------------------
// Native drag-out. The renderer shows every image as an <img src="data:...">
// for speed/simplicity, but dragging *that* element out to an external app
// (e.g. ComfyUI) makes Chromium synthesize the drop from the in-memory data
// URL rather than handing over the real file -- which is unreliable for
// embedded PNG metadata (ComfyUI's workflow chunk) and for the filename
// (the target can fall back to using the raw data-URL/URI string as the
// "name", which is why it showed up long and garbled). Since every image
// here already exists as a real file on disk, startDrag() hands the OS the
// actual file path instead, so the drop is indistinguishable from dragging
// the file out of Explorer/Finder: original bytes, original metadata,
// original filename. The renderer calls this from an image's `dragstart`
// after calling preventDefault() to suppress the default HTML5 drag.
// ---------------------------------------------------------------------------
ipcMain.on('start-drag', async (event, dir, name) => {
  try {
    const filePath = path.join(dir, name);
    await fs.promises.access(filePath);
    const iconPath = path.join(__dirname, process.platform === 'win32' ? 'icon.ico' : 'icon.png');
    const icon = nativeImage.createFromPath(iconPath);
    if (!event.sender.isDestroyed()) event.sender.startDrag({ file: filePath, icon });
  } catch (error) {
    if (!event.sender.isDestroyed()) event.sender.send('operation-error', 'Could not drag ' + name + ': ' + error.message);
  }
});

// ---------------------------------------------------------------------------
// Execute: send marked files to the OS trash (never a hard delete — this is
// destructive enough that a safety net matters).
// ---------------------------------------------------------------------------
ipcMain.handle('delete-files', async (_, items) => {
  const results = [];
  for (const it of items || []) {
    const filePath = path.join(it.dir, it.name);
    try {
      await shell.trashItem(filePath);
      results.push({ dir: it.dir, name: it.name, ok: true });
    } catch (e) {
      results.push({ dir: it.dir, name: it.name, ok: false, error: e.message });
    }
  }
  return results;
});

// ---------------------------------------------------------------------------
// Flagging "needs to be requeued". Kept purely in memory -- these are
// temporary session markers, not something that needs to survive a restart,
// so there's no reason to persist them to disk at all. A flag disappears
// when the app closes, when the user explicitly unflags a set, or
// flags remain after trash execution until explicitly cleared or the app closes.
// ---------------------------------------------------------------------------
function flagKey(dir, base) {
  return JSON.stringify([dir, base]);
}

const flags = {}; // flagKey -> { dir, base }

ipcMain.handle('flag-group', (_, dir, base) => {
  flags[flagKey(dir, base)] = { dir, base };
  return true;
});

ipcMain.handle('unflag-group', (_, dir, base) => {
  delete flags[flagKey(dir, base)];
  return true;
});

ipcMain.handle('get-flags', async () => {
  const byDir = {};
  for (const k in flags) {
    const { dir, base } = flags[k];
    (byDir[dir] = byDir[dir] || []).push(base);
  }
  return Object.keys(byDir).sort().map(dir => ({ dir, bases: byDir[dir].sort(naturalCompare) }));
});

app.whenReady().then(createWindow);
app.on('window-all-closed', () => {
  // Explicit memory teardown on close. The process exiting would drop all of
  // this anyway on non-darwin, but clearing it here makes the "clean on
  // close" guarantee explicit rather than implicit, and it actually matters
  // on darwin, where the app process (and everything below) stays alive
  // after every window closes -- flags is already documented as a
  // session-only store that should disappear when the app closes, and the
  // directory-listing cache has no reason to hold onto anything once
  // there's no window around to ask for a rescan.
  for (const k in flags) delete flags[k];
  for (const worker of scanJobs) worker.terminate();
  scanJobs.clear();
  if (process.platform !== 'darwin') app.quit();
});
app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
