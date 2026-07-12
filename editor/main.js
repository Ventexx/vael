const { app, BrowserWindow, ipcMain, globalShortcut, dialog, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');

let win;

// Windows keys the taskbar icon/grouping off the app's AppUserModelID, not
// just the BrowserWindow `icon` option below -- without this, a dev run (or
// even some packaged installs) can silently fall back to the generic
// Electron icon in the taskbar even though icon.png loads fine for the
// window itself. Matches the appId in package.json's build config. No-op
// on other platforms.
if (process.platform === 'win32') {
  app.setAppUserModelId('com.vael.editor');
}

function createWindow() {
  // Load icon
  const iconPath = path.join(__dirname, 'icon.png');
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

// Shared by the dialog-based "Open Folder" button and by drag-and-drop of a
// folder from the OS file explorer: reads every image directly inside `dir`
// (non-recursive, matching the old behavior) and returns {name, path, dataUrl}
// for each so the renderer never needs raw filesystem access.
function readImagesFromDir(dir) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (e) {
    return null;
  }
  const names = entries
    .filter(e => e.isFile() && IMG_EXT.has(path.extname(e.name).toLowerCase()))
    .map(e => e.name)
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }));
  const images = [];
  for (const name of names) {
    const filePath = path.join(dir, name);
    try {
      const buf = fs.readFileSync(filePath);
      const ext = path.extname(name).slice(1).toLowerCase();
      const mime = ext === 'jpg' ? 'jpeg' : ext;
      images.push({ name, path: filePath, dataUrl: `data:image/${mime};base64,${buf.toString('base64')}` });
    } catch (e) { /* skip unreadable file */ }
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
  const images = readImagesFromDir(dir);
  if (images === null) return null;
  return { dir, images };
});

// Drag-and-drop of a folder from the OS: Electron gives every dropped File —
// folders included — a real absolute `.path`, but a plain browser File object
// can't be read as a directory. The renderer collects the dropped paths and
// hands them here; we stat each one and, for directories, read their images
// the same way "Open Folder" does, so a dropped folder becomes its own
// category with no further prompting.
ipcMain.handle('inspect-dropped-paths', async (_, paths) => {
  const results = [];
  for (const p of paths || []) {
    let stat;
    try {
      stat = fs.statSync(p);
    } catch (e) {
      results.push({ path: p, isDirectory: false });
      continue;
    }
    if (stat.isDirectory()) {
      const images = readImagesFromDir(p) || [];
      results.push({ path: p, isDirectory: true, name: path.basename(p), images });
    } else {
      results.push({ path: p, isDirectory: false });
    }
  }
  return results;
});

// Save-as via native dialog
ipcMain.handle('save-as', async (_, src, defaultName) => {
  const { filePath } = await dialog.showSaveDialog(win, {
    defaultPath: defaultName,
    filters: [{ name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'webp'] }],
  });
  if (!filePath) return null;
  const base64 = src.replace(/^data:image\/\w+;base64,/, '');
  fs.writeFileSync(filePath, Buffer.from(base64, 'base64'));
  return filePath;
});

// Save to known path
ipcMain.handle('save', async (_, filePath, src) => {
  const base64 = src.replace(/^data:image\/\w+;base64,/, '');
  fs.writeFileSync(filePath, Buffer.from(base64, 'base64'));
  return true;
});

app.whenReady().then(createWindow);
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });