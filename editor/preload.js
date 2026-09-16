const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  minimize: () => ipcRenderer.send('win-minimize'),
  maximize: () => ipcRenderer.send('win-maximize'),
  close:    () => ipcRenderer.send('win-close'),
  quit:     () => ipcRenderer.send('win-close'),
  save:     (filePath, src, expectedVersion) => ipcRenderer.invoke('save', filePath, src, expectedVersion),
  chooseSavePath: (name) => ipcRenderer.invoke('choose-save-path', name),
  openFolder: () => ipcRenderer.invoke('open-folder'),
  inspectDroppedPaths: (paths) => ipcRenderer.invoke('inspect-dropped-paths', paths),
  readImageFull: (filePath) => ipcRenderer.invoke('read-image-full', filePath),
  cachePut: value => ipcRenderer.invoke('cache-put', value),
  cacheGet: key => ipcRenderer.invoke('cache-get', key),
  cacheRemove: key => ipcRenderer.invoke('cache-remove', key),
});
