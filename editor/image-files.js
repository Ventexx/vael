const path = require('path');

function outputFormat(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  const format = { '.png': 'png', '.jpg': 'jpeg', '.jpeg': 'jpeg', '.webp': 'webp' }[ext];
  if (!format) throw new Error('Choose a .png, .jpg, .jpeg, or .webp filename.');
  return format;
}

function decodeExport(filePath, src) {
  const expected = outputFormat(filePath);
  const match = /^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)$/.exec(src);
  if (!match || match[1] !== expected) throw new Error('The image encoding does not match the filename.');
  const bytes = Buffer.from(match[2], 'base64');
  const valid = expected === 'png'
    ? bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
    : expected === 'jpeg'
      ? bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255
      : bytes.toString('ascii', 0, 4) === 'RIFF' && bytes.toString('ascii', 8, 12) === 'WEBP';
  if (!valid) throw new Error('The encoded image is not valid for the chosen format.');
  return bytes;
}

module.exports = { outputFormat, decodeExport };
