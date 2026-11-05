// Decode and read back full-resolution pixels outside the renderer thread.
self.onmessage = async ({ data }) => {
  let bitmap;
  try {
    bitmap = await createImageBitmap(await (await fetch(data.source)).blob());
    const w = bitmap.width, h = bitmap.height;
    if (w * h * 12 > data.memoryLimit) throw new Error('Image dimensions exceed the editing memory budget.');
    const canvas = new OffscreenCanvas(w, h);
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(bitmap, 0, 0);
    const pixels = ctx.getImageData(0, 0, w, h).data;
    self.postMessage({ w, h, pixels }, [pixels.buffer]);
  } catch (error) {
    self.postMessage({ error: error.message || 'Could not prepare image' });
  } finally {
    bitmap?.close();
  }
};
