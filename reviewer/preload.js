const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  minimize: () => ipcRenderer.send('win-minimize'),
  maximize: () => ipcRenderer.send('win-maximize'),
  close: () => ipcRenderer.send('win-close'),

  getConfig: () => ipcRenderer.invoke('get-config'),
  addFolder: () => ipcRenderer.invoke('add-folder'),
  removeFolder: (folder) => ipcRenderer.invoke('remove-folder', folder),

  scan: () => ipcRenderer.invoke('scan'),
  scanAll: () => ipcRenderer.invoke('scan-all'),
  readImage: (dir, name) => ipcRenderer.invoke('read-image', dir, name),
  deleteFiles: (items) => ipcRenderer.invoke('delete-files', items),

  flagGroup: (dir, base) => ipcRenderer.invoke('flag-group', dir, base),
  unflagGroup: (dir, base) => ipcRenderer.invoke('unflag-group', dir, base),
  getFlags: () => ipcRenderer.invoke('get-flags'),
});
