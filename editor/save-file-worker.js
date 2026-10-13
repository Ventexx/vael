const { parentPort } = require('worker_threads');
const fs = require('fs');
const v8 = require('v8');
const { validateExportBytes, atomicSave } = require('./image-files');
const { keepPngText } = require('./image-metadata');

parentPort.on('message', async ({ id, filePath, bytes, expectedVersion, policy, sourceFile }) => {
  try {
    let encoded = validateExportBytes(filePath, Buffer.from(bytes));
    if (policy === 'png-text') {
      const original = v8.deserialize(await fs.promises.readFile(sourceFile));
      encoded = keepPngText(encoded, Buffer.from(original.source.split(',')[1], 'base64'));
    } else if (policy !== 'strip') throw new Error('Unknown metadata policy.');
    const version = await atomicSave(filePath, encoded, expectedVersion);
    parentPort.postMessage({ id, version });
  } catch (error) { parentPort.postMessage({ id, error: error.message }); }
});
