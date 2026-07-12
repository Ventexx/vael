const { app, BrowserWindow, ipcMain, globalShortcut, dialog, nativeImage, shell } = require('electron');
const path = require('path');
const fs = require('fs');

let win;

// Windows keys the taskbar icon/grouping off the app's AppUserModelID, not
// just the BrowserWindow `icon` option below -- without this, a dev run
// (or even some packaged installs) can silently fall back to the generic
// Electron icon in the taskbar even though icon.png loads fine for the
// window itself. Matches the appId in package.json's build config. No-op
// on other platforms.
if (process.platform === 'win32') {
  app.setAppUserModelId('com.vael.reviewer');
}

function createWindow() {
  const iconPath = path.join(__dirname, 'icon.png');
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

  win.loadFile('reviewer.html');
  win.webContents.on('did-finish-load', () => {
    if (saved && saved.zoomFactor) win.webContents.setZoomFactor(saved.zoomFactor);
  });

  win.on('resize', scheduleSaveWindowState);
  win.on('move', scheduleSaveWindowState);
  win.on('close', () => saveWindowStateNow());

  globalShortcut.register('F12', () => win.webContents.toggleDevTools());

  // Content zoom -- there's no menu bar in this frameless window, so these
  // are the only way to change scale; the factor is remembered across launches.
  globalShortcut.register('CommandOrControl+=', () => zoomBy(0.1));
  globalShortcut.register('CommandOrControl+Plus', () => zoomBy(0.1));
  globalShortcut.register('CommandOrControl+-', () => zoomBy(-0.1));
  globalShortcut.register('CommandOrControl+0', () => { win.webContents.setZoomFactor(1); scheduleSaveWindowState(); });
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
ipcMain.on('win-close', () => { win.destroy(); app.quit(); });

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
    return cfg;
  } catch (e) {
    return { folders: [] };
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
const IMG_EXT = new Set(['.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif']);
// e.g. "base_B_7_00001_.png" -> base "base_B_7", iteration 1
const ITER_RE = /^(.+)_(\d{3,})_?\.(png|jpe?g|webp|bmp|gif)$/i;

function shouldIgnoreDir(name) {
  return name.startsWith('.') || name.startsWith('!');
}

function scanDir(dir, out) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (e) {
    return;
  }

  const files = [];
  for (const e of entries) {
    if (e.isDirectory()) {
      if (shouldIgnoreDir(e.name)) continue;
      scanDir(path.join(dir, e.name), out);
    } else if (e.isFile()) {
      if (IMG_EXT.has(path.extname(e.name).toLowerCase())) files.push(e.name);
    }
  }
  if (!files.length) return;

  const groups = {};
  for (const name of files) {
    const m = name.match(ITER_RE);
    if (!m) continue;
    const base = m[1];
    const iter = parseInt(m[2], 10);
    (groups[base] = groups[base] || []).push({ name, iter });
  }

  const multi = {};
  for (const base in groups) {
    if (groups[base].length > 1) {
      groups[base].sort((a, b) => a.iter - b.iter);
      multi[base] = groups[base];
    }
  }
  if (Object.keys(multi).length) out.push({ dir, groups: multi });
}

ipcMain.handle('scan', async () => {
  const cfg = loadConfig();
  const out = [];
  for (const root of cfg.folders) scanDir(root, out);
  out.sort((a, b) => a.dir.localeCompare(b.dir));
  return out;
});

// ---------------------------------------------------------------------------
// General-review scan. Same recursion/exclusion rules as scanDir above, but
// this one is for browsing *everything* rather than just what needs a
// requeue decision: every folder that contains at least one image is
// included, and every base name becomes a group -- singletons (no iteration
// suffix, or only one file) included -- instead of only groups with 2+
// files. Files that don't match the iteration-suffix pattern at all fall
// back to using their own filename (minus extension) as the base, so they
// still show up as their own one-image "set".
//
// Also returns `order`: the same files flattened into a single flat list,
// sorted by base then by iteration number, so the renderer can lay out a
// folder's whole image wall with every set's iterations kept contiguous
// (needed for the "scroll to this set" behavior in general review).
// ---------------------------------------------------------------------------
function scanAllDir(dir, out) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (e) {
    return;
  }

  const files = [];
  for (const e of entries) {
    if (e.isDirectory()) {
      if (shouldIgnoreDir(e.name)) continue;
      scanAllDir(path.join(dir, e.name), out);
    } else if (e.isFile()) {
      if (IMG_EXT.has(path.extname(e.name).toLowerCase())) files.push(e.name);
    }
  }
  if (!files.length) return;

  const groups = {};
  for (const name of files) {
    const m = name.match(ITER_RE);
    let base, iter;
    if (m) {
      base = m[1];
      iter = parseInt(m[2], 10);
    } else {
      base = name.replace(/\.[^.]+$/, '');
      iter = 1;
    }
    (groups[base] = groups[base] || []).push({ name, iter });
  }

  const order = [];
  for (const base of Object.keys(groups).sort()) {
    groups[base].sort((a, b) => a.iter - b.iter);
    for (const entry of groups[base]) order.push({ base, name: entry.name, iter: entry.iter });
  }

  out.push({ dir, groups, order });
}

ipcMain.handle('scan-all', async () => {
  const cfg = loadConfig();
  const out = [];
  for (const root of cfg.folders) scanAllDir(root, out);
  out.sort((a, b) => a.dir.localeCompare(b.dir));
  return out;
});

ipcMain.handle('read-image', async (_, dir, name) => {
  try {
    const filePath = path.join(dir, name);
    const buf = fs.readFileSync(filePath);
    const ext = path.extname(filePath).slice(1).toLowerCase();
    const mime = ext === 'jpg' ? 'jpeg' : ext;
    return `data:image/${mime};base64,${buf.toString('base64')}`;
  } catch (e) {
    return null;
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
// automatically once that set is fully executed/deleted (handled in the
// renderer's executeDelete, which calls unflag-group for any group whose
// images were entirely trashed).
// ---------------------------------------------------------------------------
function flagKey(dir, base) {
  return dir + '\u241F' + base; // unit-separator join char, won't collide with real path/base text
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
  return Object.keys(byDir).sort().map(dir => ({ dir, bases: byDir[dir].sort() }));
});

app.whenReady().then(createWindow);
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
