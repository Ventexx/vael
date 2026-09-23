// Decode away from the UI thread; retain only a small PNG thumbnail.
let pending = Promise.resolve();
self.onmessage = event => {
  const { id, bytes, mime } = event.data;
  pending = pending.then(async () => {
    let bitmap;
    try {
      bitmap = await createImageBitmap(new Blob([bytes], { type: mime }), { resizeWidth: 384 });
      const scale = Math.min(1, 384 / Math.max(bitmap.width, bitmap.height));
      const width = Math.max(1, Math.round(bitmap.width * scale));
      const height = Math.max(1, Math.round(bitmap.height * scale));
      const canvas = new OffscreenCanvas(width, height);
      canvas.getContext('2d').drawImage(bitmap, 0, 0, width, height);
      const blob = await canvas.convertToBlob({ type: 'image/png' });
      const dataUrl = new FileReaderSync().readAsDataURL(blob);
      self.postMessage({ id, result: { dataUrl, bytes: dataUrl.length * 2 + width * height * 4, width, height } });
    } catch (error) {
      self.postMessage({ id, error: error.message });
    } finally {
      bitmap?.close();
    }
  });
};
