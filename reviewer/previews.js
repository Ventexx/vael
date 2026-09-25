// Keep rendered previews for the entire folder visit, regardless of size.
// Canvas pixels stay resident when Chromium drops off-screen image decodes.
class ThumbnailCache {
  constructor() { this.entries = new Map(); }
  get(key) { return this.entries.get(key); }
  put(key, value) { this.entries.set(key, value); }
  clear() { this.entries.clear(); }
}
const thumbnailCache = new ThumbnailCache();
let previewFolder = null;
function beginPreviewFolder(dir) {
  if (dir === previewFolder) return;
  clearThumbnails();
  previewFolder = dir;
}
const thumbnailRequests = new Map();
let thumbnailEpoch = 0, decoder = null, decodeId = 0;
const decodeJobs = new Map();
function stopDecoder(error = new Error('Preview decoder stopped.')) {
  decoder?.terminate(); decoder = null;
  for (const job of decodeJobs.values()) { clearTimeout(job.timer); job.reject(error); }
  decodeJobs.clear();
}
function decodeThumbnail(bytes, mime) {
  if (!decoder) {
    decoder = new Worker('preview-worker.js');
    decoder.onmessage = ({data}) => {
      const job = decodeJobs.get(data.id);
      if (!job) return;
      decodeJobs.delete(data.id); clearTimeout(job.timer);
      if (data.error) job.reject(new Error(data.error)); else job.resolve(data.result);
    };
    decoder.onerror = event => { event.preventDefault(); stopDecoder(new Error(event.message || 'Preview decoding failed.')); };
  }
  return new Promise((resolve, reject) => {
    const id = ++decodeId;
    const timer = setTimeout(() => stopDecoder(new Error('Preview decoding timed out.')), 30000);
    decodeJobs.set(id, {resolve, reject, timer});
    decoder.postMessage({id, bytes, mime}, [bytes.buffer]);
  });
}
function getThumbnail(dir, entry) {
  const key = JSON.stringify([dir, entry.name, entry.version]);
  const cached = thumbnailCache.get(key);
  if (cached) return Promise.resolve(cached);
  const epoch = thumbnailEpoch, requestKey = epoch + ':' + key;
  if (thumbnailRequests.has(requestKey)) return thumbnailRequests.get(requestKey);
  const pending = window.electronAPI.readImageBytes(dir, entry.name, entry.version)
    .then(({bytes, mime}) => decodeThumbnail(bytes, mime))
    .then(async result => {
      const image = new Image();
      image.src = result.dataUrl;
      await image.decode();
      const canvas = document.createElement('canvas');
      canvas.width = result.width; canvas.height = result.height;
      canvas.getContext('2d').drawImage(image, 0, 0);
      const preview = { canvas, width: result.width, height: result.height };
      if (epoch === thumbnailEpoch) thumbnailCache.put(key, preview);
      return preview;
    })
    .finally(() => thumbnailRequests.delete(requestKey));
  thumbnailRequests.set(requestKey, pending);
  return pending;
}
function clearThumbnails() { thumbnailEpoch++; thumbnailCache.clear(); }

const trackedPreviews = new Set(), previewQueue = new Map();
let activePreviews = 0;
function trackPreview(element, dir, entry) {
  element._previewFile = {dir, entry};
  element._previewVisible = true;
  element._previewToken = 1;
  element._previewReady = new Promise(resolve => { element._previewDone = resolve; });
  element.alt = 'Loading preview';
  trackedPreviews.add(element);
  previewQueue.set(element, element._previewToken);
  // Callers append the tile synchronously after registering it.
  queueMicrotask(pumpPreviews);
}
function untrackPreviews(container) {
  for (const image of container.querySelectorAll('canvas')) {
    trackedPreviews.delete(image); previewQueue.delete(image);
    image._previewVisible = false; image._previewToken++; image.width = image.height = 0;
    image._previewDone?.();
  }
}
function resetPreviews() {
  previewQueue.clear();
  for (const image of trackedPreviews) {
    image._previewVisible = false; image._previewToken++; image.width = image.height = 0; image._previewDone?.();
  }
  trackedPreviews.clear();
}
function pumpPreviews() {
  while (activePreviews < 2 && previewQueue.size) {
    const [image, token] = previewQueue.entries().next().value;
    previewQueue.delete(image);
    if (!image.isConnected || !image._previewVisible) { image._previewDone?.(); continue; }
    const {dir, entry} = image._previewFile;
    activePreviews++;
    getThumbnail(dir, entry).then(async result => {
      if (image.isConnected && image._previewVisible && image._previewToken === token) {
        image.title = entry.name;
        image.width = result.width; image.height = result.height;
        image.getContext('2d').drawImage(result.canvas, 0, 0);
      }
    }).catch(error => {
      if (image.isConnected && image._previewToken === token) {
        image.width = image.height = 0; image.alt = 'Preview unavailable'; image.title = dir + '/' + entry.name + ': ' + error.message;
        image.width = 160; image.height = 160;
      }
    }).finally(() => { image._previewDone?.(); activePreviews--; pumpPreviews(); });
  }
}
window.addEventListener('beforeunload', () => { resetPreviews(); clearThumbnails(); stopDecoder(); });
