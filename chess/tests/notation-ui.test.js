const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../frontend/app.js'),'utf8');
const render = source.slice(source.indexOf('function renderMovesList()'), source.indexOf('// ---------------------------------------------------------------- move input'));
const move = (number, turn, san, id, children=[]) => ({number,turn,san,id,children});
function markup(notation, selected='') {
  const box = {innerHTML:'',querySelectorAll:()=>[]};
  const context = {boardState:{notation,selected_node:selected,total_plies:4},el:id=>id==='moves-table'?box:{},boardLocked:()=>false};
  vm.runInNewContext(render+'\nrenderMovesList();',context);
  return box.innerHTML;
}
test('notation pairs White and Black under one move number',()=>{
  const html = markup([move(1,'w','e4','e2e4',[move(1,'b','e5','e2e4 e7e5',[move(2,'w','Nf3','e2e4 e7e5 g1f3')])])]);
  assert.equal((html.match(/class="notation-row"/g)||[]).length,2);
  assert.match(html,/notation-number">1<.*>e4<\/button><button.*>e5<\/button><\/div>/);
  assert.match(html,/notation-number">2<.*>Nf3<\/button><span><\/span>/);
});
test('Black-starting alternatives preserve alignment and selected branch visibility',()=>{
  const branch=move(23,'b','Kd7','e8d7');
  const html=markup([move(23,'b','Kf7','e8f7'),branch],'e8d7');
  assert.match(html,/notation-empty" aria-hidden="true">…<\/span><button/);
  assert.match(html,/data-branch="e8d7" open/);
  assert.match(html,/data-node="e8d7" aria-label="23… Kd7" aria-current="step"/);
});
