const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
const handler = source.slice(source.indexOf('window.onEngineInfo ='), source.indexOf('window.onEngineStatus ='));

test('engine updates must belong to the displayed position and a current search', () => {
  let renders = 0;
  const context = {
    window: {}, reviewOpen: false,
    boardState: {fen: 'current position', analysis_gen: 4},
    engineLines: {}, scheduleEngineRender: () => renders++,
  };
  vm.runInNewContext(handler, context);
  const send = (gen, fen) => context.window.onEngineInfo({type: 'info', gen, fen, multipv: 1});
  send(3, 'current position'); // Same position revisited after an undo.
  send(4, 'old position');
  send(5, 'next position'); // Arrived before the board's move response.
  assert.equal(renders, 0);
  send(4, 'current position');
  assert.equal(renders, 1);
  send(5, 'current position'); // Settings can restart analysis without moving.
  assert.equal(renders, 2);
  context.reviewOpen = true;
  send(5, 'current position');
  assert.equal(renders, 2);
});
