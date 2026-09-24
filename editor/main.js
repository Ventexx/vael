const { app, BrowserWindow, ipcMain, globalShortcut, dialog, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');
const { outputFormat, decodeExport, fileVersion, readSnapshot, atomicSave } = require('./image-files');
const { SessionCache } = require('./session-cache');
const { keepPngText } = require('./image-metadata');
const sessionCache = new SessionCache();
ipcMain.handle('cache-put', (_, value) => sessionCache.put(value));
ipcMain.handle('cache-get', (_, key) => sessionCache.get(key));
ipcMain.handle('cache-remove', (_, key) => sessionCache.remove(key));
app.on('will-quit', () => sessionCache.close());

let win;

// Windows keys the taskbar icon/grouping off the app's AppUserModelID, not
// just the BrowserWindow `icon` option below -- without this, a dev run (or
// even some packaged installs) can silently fall back to the generic
// Electron icon in the taskbar even though icon.ico loads fine for the
// window itself. Matches the appId in package.json's build config. No-op
// on other platforms.
if (process.platform === 'win32') {
  app.setAppUserModelId('com.vael.editor');
}

function createWindow() {
  // Load icon
  const iconPath = path.join(__dirname, process.platform === 'win32' ? 'icon.ico' : 'icon.png');
  const icon = fs.existsSync(iconPath) ? nativeImage.createFromPath(iconPath) : undefined;

  win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 800,
    minHeight: 500,
    backgroundColor: '#0a0a0a',
    frame: false,          // custom titlebar
    titleBarStyle: 'hidden',
    icon,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      backgroundThrottling: false, // Finish imports even while minimized.
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  win.loadFile('editor.html');

  globalShortcut.register('F12', () => win.webContents.toggleDevTools());

  // Let the renderer handle close confirmation
  win.on('close', e => {
    e.preventDefault();
    win.webContents.executeJavaScript('attemptClose()');
  });
}

// Window control IPC
ipcMain.on('win-minimize', () => win.minimize());
ipcMain.on('win-maximize', () => win.isMaximized() ? win.unmaximize() : win.maximize());
ipcMain.on('win-close',    () => { win.destroy(); app.quit(); });

// Open a whole folder of images at once (core workflow: batch-pixelate a shoot)
const IMG_EXT = new Set(['.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif']);

// Discover paths first; thumbnails are requested separately by renderer workers.
// No image decoding or synchronous filesystem walk on the window's main thread.
async function readImagesInSingleDir(dir) {
  let entries;
  try { entries = await fs.promises.readdir(dir, { withFileTypes: true }); }
  catch { return null; }
  return entries
    .filter(e => e.isFile() && IMG_EXT.has(path.extname(e.name).toLowerCase()))
    .map(e => e.name)
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }))
    .map(name => ({ name, path: path.join(dir, name) }));
}

ipcMain.handle('read-thumbnail-source', (_, filePath) => fs.promises.readFile(filePath));

// Shared by the dialog-based "Open Folder" button and by drag-and-drop of a
// folder from the OS file explorer. Reads images out of `dir` two layers
// deep: every image directly inside `dir` (first layer), followed by every
// image directly inside each of `dir`'s immediate subfolders (second layer,
// subfolders walked in name order) — but no deeper than that. The two
// layers are simply concatenated in that order into one flat list, so a
// dropped folder full of subfolders still becomes a single category, with
// its images ordered first-layer, then first subfolder, then second
// subfolder, etc. Each returned entry still carries the image's real,
// original absolute `path` on disk (from readImagesInSingleDir), so saving
// later writes back to wherever the file actually lives — the merging here
// is purely an in-app grouping and never moves anything on disk.
async function readImagesFromDir(dir) {
  const firstLayer = await readImagesInSingleDir(dir);
  if (firstLayer === null) return null; // dir itself unreadable/missing

  let subdirNames;
  try {
    subdirNames = (await fs.promises.readdir(dir, { withFileTypes: true }))
      .filter(e => e.isDirectory())
      .map(e => e.name)
      .sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }));
  } catch (e) {
    subdirNames = [];
  }

  let images = firstLayer;
  for (const subName of subdirNames) {
    const subImages = await readImagesInSingleDir(path.join(dir, subName));
    if (subImages && subImages.length) images = images.concat(subImages);
  }
  return images;
}

ipcMain.handle('open-folder', async () => {
  const { filePaths, canceled } = await dialog.showOpenDialog(win, {
    properties: ['openDirectory'],
    title: 'Open folder of images',
  });
  if (canceled || !filePaths || !filePaths[0]) return null;
  const dir = filePaths[0];
  const images = await readImagesFromDir(dir);
  if (images === null) return null;
  return { dir, images };
});

// Drag-and-drop of a folder from the OS: Electron gives every dropped File —
// folders included — a real absolute `.path`, but a plain browser File object
// can't be read as a directory. The renderer collects the dropped paths and
// hands them here; we stat each one and, for directories, read their images
// the same way "Open Folder" does, so a dropped folder becomes its own
// category with no further prompting. Multiple folders dropped at once
// return paths only, so categories appear before thumbnail decoding starts.
ipcMain.handle('inspect-dropped-paths', async (_, paths) => {
  const results = [];
  for (const p of paths || []) {
    let stat;
    try {
      stat = await fs.promises.stat(p);
    } catch (e) {
      results.push({ path: p, isDirectory: false });
      continue;
    }
    if (stat.isDirectory()) {
      const images = (await readImagesFromDir(p)) || [];
      results.push({ path: p, isDirectory: true, name: path.basename(p), images });
    } else {
      results.push({ path: p, isDirectory: false });
    }
  }
  return results;
});

// Fetches one image's real full-resolution bytes as a data URL, on demand.
// The renderer calls this the moment it actually needs the pixels — an image
// gets opened/selected, edited, exported, etc. — instead of every image in
// an imported folder being fully decoded and held in memory up front. Reads
// asynchronously (not readFileSync) so a slow/large file never blocks the
// main process or the UI.
ipcMain.handle('read-image-full', async (_, filePath) => {
  return readSnapshot(filePath);
});

// Choose the destination before the renderer encodes its canvas.
ipcMain.handle('choose-save-path', async (_, defaultName) => {
  const { filePath } = await dialog.showSaveDialog(win, {
    defaultPath: defaultName,
    filters: [
      { name: 'Supported images', extensions: ['png', 'jpg', 'jpeg', 'webp'] },
      { name: 'PNG', extensions: ['png'] }, { name: 'JPEG', extensions: ['jpg', 'jpeg'] },
      { name: 'WebP', extensions: ['webp'] },
    ],
  });
  if (!filePath) return null;
  const destination = path.extname(filePath) ? filePath : filePath + '.png';
  outputFormat(destination);
  return { filePath: destination, version: await fileVersion(destination) };
});

// Save to known path
ipcMain.handle('save', async (_, filePath, src, expectedVersion, metadata = {}) => {
  let bytes = decodeExport(filePath, src);
  const policy = metadata.policy || 'strip';
  if (policy === 'png-text') {
    const original = await sessionCache.get(metadata.sourceKey);
    bytes = keepPngText(bytes, Buffer.from(original.source.split(',')[1], 'base64'));
  } else if (policy !== 'strip') throw new Error('Unknown metadata policy.');
  return atomicSave(filePath, bytes, expectedVersion);
});

app.whenReady().then(createWindow);
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
