const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  minimize: () => ipcRenderer.send('win-minimize'),
  maximize: () => ipcRenderer.send('win-maximize'),
  close:    () => ipcRenderer.send('win-close'),
  quit:     () => ipcRenderer.send('win-close'),
  save:     (filePath, src) => ipcRenderer.invoke('save', filePath, src),
  chooseSavePath: (name) => ipcRenderer.invoke('choose-save-path', name),
  openFolder: () => ipcRenderer.invoke('open-folder'),
  inspectDroppedPaths: (paths) => ipcRenderer.invoke('inspect-dropped-paths', paths),
  readImageFull: (filePath) => ipcRenderer.invoke('read-image-full', filePath),
});
