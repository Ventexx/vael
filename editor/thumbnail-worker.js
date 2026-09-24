// Decode away from the UI thread. Only the small thumbnail crosses back.
self.onmessage = async ({ data }) => {
  let full;
  try {
    full = await createImageBitmap(new Blob([data.bytes]));
    const w = full.width, h = full.height;
    const scale = Math.min(1, 320 / w, 120 / h);
    const canvas = new OffscreenCanvas(Math.max(1, Math.round(w * scale)), Math.max(1, Math.round(h * scale)));
    canvas.getContext('2d').drawImage(full, 0, 0, canvas.width, canvas.height);
    const bitmap = canvas.transferToImageBitmap();
    self.postMessage({ w, h, bitmap }, [bitmap]);
  } catch (error) {
    self.postMessage({ error: error.message || 'Could not decode image' });
  } finally {
    full?.close();
  }
};
