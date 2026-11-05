// Each hydration owns its worker, so foreground requests never wait behind
// an obsolete background decode. The read-ahead queue still runs one at a time.
function prepareImagePixels(source, memoryLimit) {
  return new Promise((resolve, reject) => {
    const worker = new Worker('image-prepare-worker.js');
    const finish = (error, result) => {
      clearTimeout(timer);
      worker.terminate();
      if (error) reject(error); else resolve(result);
    };
    const timer = setTimeout(() => finish(new Error('Image preparation timed out.')), 120000);
    worker.onmessage = ({ data }) => finish(data.error ? new Error(data.error) : null, data);
    worker.onerror = event => {
      event.preventDefault();
      finish(new Error(event.message || 'Image preparation failed'));
    };
    worker.onmessageerror = () => finish(new Error('Could not receive prepared pixels.'));
    try { worker.postMessage({ source, memoryLimit }); }
    catch (error) { finish(error); }
  });
}

function yieldImagePreparation() {
  return globalThis.scheduler?.postTask
    ? scheduler.postTask(() => {}, { priority: 'background' })
    : new Promise(resolve => setTimeout(resolve, 0));
}

async function installImagePixels(ctx, pixels, w, h, cancelled, paused = () => false) {
  // Keep each UI-thread upload small and let input/rendering run between them.
  const rows = Math.max(1, Math.floor(65536 / w));
  for (let y = 0; y < h; y += rows) {
    await yieldImagePreparation();
    while (paused() && !cancelled()) await new Promise(resolve => setTimeout(resolve, 25));
    if (cancelled()) return false;
    const height = Math.min(rows, h - y);
    const band = new ImageData(pixels.subarray(y * w * 4, (y + height) * w * 4), w, height);
    ctx.putImageData(band, 0, y);
  }
  return true;
}
