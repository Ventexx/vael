class ThumbnailCache {
  constructor(budget = 32 * 1024 * 1024) { this.budget = budget; this.bytes = 0; this.entries = new Map(); }
  get(key) {
    const value = this.entries.get(key);
    if (value) { this.entries.delete(key); this.entries.set(key, value); }
    return value;
  }
  put(key, value) {
    if (this.entries.has(key)) { this.bytes -= this.entries.get(key).bytes; this.entries.delete(key); }
    if (value.bytes > this.budget) return;
    while (this.entries.size && (this.bytes + value.bytes > this.budget || this.entries.size >= 512)) {
      const oldest = this.entries.keys().next().value;
      this.bytes -= this.entries.get(oldest).bytes;
      this.entries.delete(oldest);
    }
    this.entries.set(key, value); this.bytes += value.bytes;
  }
  clear() { this.entries.clear(); this.bytes = 0; }
}
const thumbnailCache = new ThumbnailCache();
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
    .then(result => { if (epoch === thumbnailEpoch) thumbnailCache.put(key, result); return result; })
    .finally(() => thumbnailRequests.delete(requestKey));
  thumbnailRequests.set(requestKey, pending);
  return pending;
}
function clearThumbnails() { thumbnailEpoch++; thumbnailCache.clear(); }

const trackedPreviews = new Set(), previewQueue = new Map();
let activePreviews = 0;
const previewObserver = new IntersectionObserver(entries => {
  for (const {target, isIntersecting} of entries) {
    target._previewVisible = isIntersecting;
    target._previewToken = (target._previewToken || 0) + 1;
    if (isIntersecting) previewQueue.set(target, target._previewToken);
    else { previewQueue.delete(target); target.removeAttribute('src'); }
  }
  pumpPreviews();
}, {rootMargin: '250px'});
function trackPreview(element, dir, entry) {
  element._previewFile = {dir, entry};
  element.alt = 'Loading preview';
  trackedPreviews.add(element); previewObserver.observe(element);
}
function untrackPreviews(container) {
  for (const image of container.querySelectorAll('img')) {
    previewObserver.unobserve(image); trackedPreviews.delete(image); previewQueue.delete(image);
    image._previewVisible = false; image._previewToken++; image.removeAttribute('src');
  }
}
function resetPreviews() {
  previewObserver.disconnect(); previewQueue.clear();
  for (const image of trackedPreviews) { image._previewVisible = false; image._previewToken++; image.removeAttribute('src'); }
  trackedPreviews.clear();
}
function pumpPreviews() {
  while (activePreviews < 2 && previewQueue.size) {
    const [image, token] = previewQueue.entries().next().value;
    previewQueue.delete(image);
    if (!image.isConnected || !image._previewVisible) continue;
    const {dir, entry} = image._previewFile;
    activePreviews++;
    getThumbnail(dir, entry).then(result => {
      if (image.isConnected && image._previewVisible && image._previewToken === token) {
        image.src = result.dataUrl; image.alt = entry.name; image.title = '';
      }
    }).catch(error => {
      if (image.isConnected && image._previewToken === token) {
        image.removeAttribute('src'); image.alt = 'Preview unavailable'; image.title = dir + '/' + entry.name + ': ' + error.message;
      }
    }).finally(() => { activePreviews--; pumpPreviews(); });
  }
}
window.addEventListener('beforeunload', () => { resetPreviews(); clearThumbnails(); stopDecoder(); });
