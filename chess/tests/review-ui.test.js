const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness() {
  const elements = new Map();
  const node = () => ({attrs:{},innerHTML:'',children:[],setAttribute(k,v){this.attrs[k]=v;},
    appendChild(x){this.children.push(x);},classList:{remove(){}},focus(){},querySelector(){return {scrollTop:0};}});
  const applied = [];
  const ctx = vm.createContext({window:{pywebview:{api:{}}},document:{createElementNS:node},
    el:id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id);},
    applyBundle:x=>applied.push(x),formatScore:()=>'',reviewState:{rows:[]},
    squareToRowCol:s=>({col:s.charCodeAt(0)-97,row:8-Number(s[1])})});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../frontend/review.js'),'utf8'),ctx);
  vm.runInContext('renderReview=()=>{}; reviewOpen=true;',ctx);
  return {ctx,applied,node,elements,run:s=>vm.runInContext(s,ctx)};
}

test('late preview responses cannot replace the newest selection',async()=>{
  const h=harness(), pending=[];
  h.ctx.window.pywebview.api.review_position=()=>new Promise(resolve=>pending.push(resolve));
  h.run('reviewPly=1');
  const first=h.run("previewReviewLine('best',1)");
  const second=h.run("previewReviewLine('played',1)");
  pending[1]({state:{fen:'newer'},step:1});await second;
  pending[0]({state:{fen:'older'},step:1});await first;
  assert.equal(h.applied.length,1);
  assert.equal(h.applied[0].state.fen,'newer');
});

test('closing the review discards pending previews and restores the original board',async()=>{
  const h=harness();let resolve;
  h.ctx.window.pywebview.api.review_position=()=>new Promise(r=>resolve=r);
  h.ctx.window.pywebview.api.get_state=async()=>({fen:'original'});
  h.ctx.window.pywebview.api.legal_moves=async()=>({});
  h.run('reviewPly=1');
  const pending=h.run("previewReviewLine('best',1)");
  await h.run('closeReview()');
  resolve({state:{fen:'preview'},step:1});await pending;
  assert.equal(h.applied.length,1);
  assert.equal(h.applied[0].state.fen,'original');
});

test('review arrows distinguish played and preferred and follow board coordinates',()=>{
  const h=harness();
  h.ctx.reviewState={rows:[{ply:1,uci:'d1h5',best_uci:'g1f3',best_line:[{uci:'g1f3'},{uci:'b8c6'}]}]};
  h.run('reviewPly=1');
  const layer=h.node();h.ctx.layer=layer;
  h.run('renderReviewArrows(layer)');
  assert.match(layer.children[0].attrs['aria-label'],/Preferred move: g1f3; Played move: d1h5/);
  assert.match(layer.children[0].innerHTML,/stroke-dasharray/);
  h.ctx.squareToRowCol=s=>({col:7-(s.charCodeAt(0)-97),row:Number(s[1])-1});
  const flipped=h.node();h.ctx.layer=flipped;
  h.run('renderReviewArrows(layer)');
  assert.match(flipped.children[0].innerHTML,/x1="18.75" y1="6.25"/);
  h.run("reviewMode='best';reviewStep=1");
  const next=h.node();h.ctx.layer=next;h.run('renderReviewArrows(layer)');
  assert.equal(next.children[0].attrs['aria-label'],'Next move in continuation: b8c6');
});

test('stale analysis events cannot resurrect a cancelled run',()=>{
  const h=harness();
  h.ctx.reviewState={job_id:4,status:'cancelled'};
  h.ctx.window.onReview({job_id:3,status:'running'});
  assert.equal(h.ctx.reviewState.status,'cancelled');
});
