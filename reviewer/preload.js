const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  minimize: () => ipcRenderer.send('win-minimize'),
  maximize: () => ipcRenderer.send('win-maximize'),
  close: () => ipcRenderer.send('win-close'),

  getConfig: () => ipcRenderer.invoke('get-config'),
  addFolder: () => ipcRenderer.invoke('add-folder'),
  removeFolder: (folder) => ipcRenderer.invoke('remove-folder', folder),
  toggleFolderHidden: (folder) => ipcRenderer.invoke('toggle-folder-hidden', folder),

  scan: (opts) => ipcRenderer.invoke('scan', opts),
  scanAll: (opts) => ipcRenderer.invoke('scan-all', opts),
  readImage: (dir, name) => ipcRenderer.invoke('read-image', dir, name),
  deleteFiles: (items) => ipcRenderer.invoke('delete-files', items),

  flagGroup: (dir, base) => ipcRenderer.invoke('flag-group', dir, base),
  unflagGroup: (dir, base) => ipcRenderer.invoke('unflag-group', dir, base),
  getFlags: () => ipcRenderer.invoke('get-flags'),

  // Kicks off a native OS file drag for the given image, instead of letting
  // the browser drag the in-memory data URL. See main.js for why.
  startDrag: (dir, name) => ipcRenderer.send('start-drag', dir, name),
});
