const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const TEXT_CHUNKS = new Set(['tEXt', 'zTXt', 'iTXt']);
const CRC_TABLE = Array.from({ length: 256 }, (_, index) => {
  let crc = index;
  for (let bit = 0; bit < 8; bit++) crc = crc & 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
  return crc >>> 0;
});
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = CRC_TABLE[(crc ^ byte) & 255] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}
function pngChunks(bytes) {
  if (!bytes.subarray(0, 8).equals(PNG_SIGNATURE)) {
    throw new Error('Keeping PNG text requires a PNG source and destination. Choose PNG or remove source metadata in Save options.');
  }
  const chunks = [];
  for (let offset = 8; offset + 12 <= bytes.length;) {
    const size = bytes.readUInt32BE(offset);
    const end = offset + size + 12;
    if (end > bytes.length) throw new Error('The PNG metadata is truncated.');
    const type = bytes.toString('ascii', offset + 4, offset + 8);
    if (crc32(bytes.subarray(offset + 4, end - 4)) !== bytes.readUInt32BE(end - 4)) {
      throw new Error('The PNG contains a damaged chunk; source metadata was not copied.');
    }
    chunks.push({ type, bytes: bytes.subarray(offset, end) });
    offset = end;
    if (type === 'IEND') return chunks;
  }
  throw new Error('The PNG has no complete end chunk.');
}
function keepPngText(encoded, original) {
  const sourceText = pngChunks(original).filter(chunk => TEXT_CHUNKS.has(chunk.type));
  const output = pngChunks(encoded).filter(chunk => !TEXT_CHUNKS.has(chunk.type));
  const parts = [PNG_SIGNATURE];
  for (const chunk of output) {
    if (chunk.type === 'IEND') parts.push(...sourceText.map(item => item.bytes));
    parts.push(chunk.bytes);
  }
  return Buffer.concat(parts);
}

module.exports = { keepPngText, pngChunks, crc32 };
