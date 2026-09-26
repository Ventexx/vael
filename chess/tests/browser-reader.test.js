const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const sourceCode = fs.readFileSync(require('node:path').join(__dirname, '../browser-extension/read-board.js'), 'utf8');
function read(host, board, moves = []) {
  const context = {location: {hostname: host}, document: {
    querySelectorAll: selector => selector.includes('cg-board') || selector.includes('chess-board') ? [board] : moves,
    querySelector: () => null
  }};
  vm.createContext(context);
  vm.runInContext(sourceCode, context);
  return JSON.parse(JSON.stringify(vm.runInContext('readVaelBoard()', context)));
}
function lichessPiece(key, color, role, extra = []) {
  return {cgKey:key, cgPiece:`${color} ${role}`, classList:{contains: c => extra.includes(c)}};
}
test('Lichess logical squares ignore orientation, piece artwork and animation offsets', () => {
  const board = {getBoundingClientRect: () => ({width: 600}), querySelectorAll: () => [
    lichessPiece('e1','white','king'), lichessPiece('e8','black','king'), lichessPiece('c6','black','knight'),
    lichessPiece('d5','white','pawn',['ghost'])]};
  assert.deepEqual(read('lichess.org', board).pieces, {e1:'K', e8:'k', c6:'n'});
});
test('Chess.com semantic piece classes ignore theme and flip', () => {
  const board = {getBoundingClientRect: () => ({width: 500}), querySelectorAll: () => [
    {classList:['piece','wk','square-51','custom-style']}, {classList:['piece','bk','square-58']}]};
  assert.deepEqual(read('www.chess.com', board).pieces, {e1:'K', e8:'k'});
});
test('Chess.com prefers full game FEN', () => {
  const fen = '4k3/8/8/8/8/8/8/4K3 b - - 7 12';
  const board = {getBoundingClientRect: () => ({width: 500}), game:{getFEN:()=>fen}};
  assert.equal(read('www.chess.com', board).fen, fen);
});
test('Dragging is withheld rather than publishing an intermediate board', () => {
  const board = {getBoundingClientRect: () => ({width:600}), querySelectorAll: () => [lichessPiece('e4','white','pawn',['dragging'])]};
  assert.match(read('lichess.org',board).error, /finish/);
});
test('Selected Lichess notation limits replay during takebacks', () => {
  const board = {getBoundingClientRect: () => ({width:600}), querySelectorAll: () => []};
  const moves = ['e4','e5','Nf3'].map((san, index) => ({classList: {contains: c => c === 'a1t' && index === 1},childNodes:[{nodeType:3,textContent:san}]}));
  assert.deepEqual(read('lichess.org',board,moves).sans,['e4','e5']);
});
test('Chess.com analysis notation provides a complete fallback without game API', () => {
  const board = {getBoundingClientRect: () => ({width:500}), querySelectorAll: () => [
    {classList:['piece','wk','square-51']}, {classList:['piece','bk','square-58']}]};
  const moves = ['e4','e5','Nf3'].map((san,i) => ({textContent:san, querySelector: selector => selector === '.selected' ? (i === 1 ? {} : null) : {textContent:san}}));
  assert.deepEqual(read('www.chess.com',board,moves).sans,['e4','e5']);
});
