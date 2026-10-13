// Encoding and JPEG compositing happen off the editor thread.
self.onmessage = async ({ data }) => {
  const { bitmap, mime, quality, background } = data;
  let canvas;
  try {
    canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    const ctx = canvas.getContext('2d');
    if (mime === 'image/jpeg') {
      ctx.fillStyle = background;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
    ctx.drawImage(bitmap, 0, 0);
    const blob = await canvas.convertToBlob({ type: mime, quality });
    if (blob.type !== mime) throw new Error('This image format could not be encoded.');
    const bytes = await blob.arrayBuffer();
    self.postMessage({ bytes }, [bytes]);
  } catch (error) {
    self.postMessage({ error: error.message || 'Image encoding failed' });
  } finally {
    bitmap.close();
    if (canvas) canvas.width = canvas.height = 0;
  }
};
