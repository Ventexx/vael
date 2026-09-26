/* Review is an on-demand, read-only preview of the saved game's main line. */
let reviewOpen = false;
let reviewPly = null;
let reviewMode = 'before';
let reviewStep = 0;
let reviewRequest = 0;
let reviewSide = 'all';
let reviewTourIndex = -1;
const reviewColors = {great:'#73c7dd',best:'#a4d4a9',excellent:'#93bf9b',good:'#b5c4b3',inaccuracy:'#e4c078',mistake:'#e8a066',blunder:'#ed8582',forced:'#a2a9aa'};
const reviewLabels = {great:'Great',best:'Best',excellent:'Excellent',good:'Good',inaccuracy:'Inaccuracy',mistake:'Mistake',blunder:'Blunder',forced:'Forced'};
function reviewScore(score) {
  if (!score) return '—';
  return score.mate === 0 ? 'Checkmate' : formatScore(score.cp, score.mate);
}
function reviewRow() {return reviewState.rows?.find(r => r.ply === reviewPly);}
function reviewTour() {
  return (reviewState.highlights || []).filter(p => reviewSide === 'all' || reviewState.rows.find(r => r.ply === p)?.turn === reviewSide);
}
function reviewNode(tag, text, cls) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function reviewButton(text, callback, cls='review-point') {
  const node = reviewNode('button',text,cls);
  node.type = 'button';
  node.addEventListener('click',callback);
  return node;
}
function gradeBadge(row) {
  const badge = reviewNode('span',reviewLabels[row.grade] || 'Unrated','review-grade');
  badge.style.color = reviewColors[row.grade] || 'var(--text-dim)';
  return badge;
}
function renderReview() {
  const data = reviewState, running = data.status === 'running';
  const modern = data.version === 2;
  el('review-status').textContent = data.error || (running ? `Analysing ${data.completed} / ${data.total} moves · checking alternatives…` :
    data.status === 'complete' ? `${data.total} moves analysed · ${data.engine || 'Local engine'}` :
    data.status === 'cancelled' ? 'Analysis cancelled · partial results' : 'Analyse your game, then explore its key moments.');
  if (data.rows?.length && !modern) el('review-status').textContent = 'Analyse again to upgrade this saved review with grades and playable alternatives.';
  el('review-start').disabled = running;
  el('review-start').textContent = data.rows?.length ? 'Analyse again' : 'Analyse game';
  el('review-cancel').hidden = !running;
  el('review-progress').hidden = !running;
  el('review-progress').max = data.total || 1;
  el('review-progress').value = data.completed || 0;
  el('review-tour').hidden = !modern || !data.rows?.length || reviewPly != null;
  el('review-tour').disabled = running;
  el('review-tour').textContent = data.status === 'complete' ? 'Start review' : 'Review analysed moves';
  el('review-journey').hidden = reviewPly == null;
  el('review-summary').hidden = reviewPly != null;
  el('review-position').max = Math.max(1,data.rows?.length || 1);
  const points = data.points || [];
  const x = i => points.length <= 1 ? 0 : i / (points.length - 1) * 600;
  const y = p => 80 - Math.tanh(p.value / 400) * 65;
  const path = points.map((p,i)=>`${x(i)},${y(p)}`).join(' ');
  el('review-graph').innerHTML = `<line x1="0" y1="80" x2="600" y2="80" stroke="var(--border2)"/><polyline points="${path}" fill="none" stroke="var(--accent)" stroke-width="2.5"/>`;
  (data.rows || []).forEach(row => {
    if (!points[row.ply]) return;
    const dot = document.createElementNS('http://www.w3.org/2000/svg','circle');
    dot.setAttribute('cx',x(row.ply)); dot.setAttribute('cy',y(points[row.ply]));
    dot.setAttribute('r',row.ply === reviewPly ? 6 : 3);
    dot.setAttribute('fill',reviewColors[row.grade] || 'var(--accent)');
    el('review-graph').appendChild(dot);
  });
  renderReviewSummary();
  const list = el('review-turning-points');
  list.replaceChildren();
  if (modern) (data.rows || []).forEach(row => {
    const button = reviewButton(`${row.number}${row.turn === 'w' ? '.' : '…'} ${row.san}`,()=>inspectReview(row.ply));
    button.appendChild(gradeBadge(row));
    list.appendChild(button);
  });
  el('review-move-list').hidden = !modern || !data.rows?.length;
  renderReviewDetail();
}
function renderReviewSummary() {
  const box = el('review-summary');
  box.replaceChildren();
  if (!reviewState.summary) {
    box.appendChild(reviewNode('p','Get a separate performance summary for each side, move grades, and a guided review of the important decisions.','review-intro'));
    return;
  }
  const players = reviewNode('div',null,'review-players');
  for (const color of ['w','b']) {
    const side = reviewState.summary[color];
    const card = reviewNode('div',null,'review-player');
    card.appendChild(reviewNode('span',color === 'w' ? 'WHITE' : 'BLACK','review-eyebrow'));
    card.appendChild(reviewNode('strong',reviewState.players?.[color] || (color === 'w' ? 'White' : 'Black'),'review-player-name'));
    card.appendChild(reviewNode('b',side.accuracy == null ? '—' : side.accuracy.toFixed(1),'review-accuracy'));
    card.appendChild(reviewNode('span','Vael accuracy','hint'));
    card.appendChild(reviewNode('p',side.description,'review-verdict'));
    players.appendChild(card);
  }
  box.appendChild(players);
  const table = reviewNode('table',null,'review-stats');
  const head = reviewNode('tr');
  for (const label of ['Move quality','White','Black']) head.appendChild(reviewNode('th',label));
  table.appendChild(head);
  for (const grade of Object.keys(reviewLabels)) {
    const row = reviewNode('tr');
    const name = reviewNode('td',reviewLabels[grade]); name.style.color = reviewColors[grade]; row.appendChild(name);
    for (const color of ['w','b']) row.appendChild(reviewNode('td',reviewState.summary[color].counts[grade]));
    table.appendChild(row);
  }
  box.appendChild(table);
  const phases = reviewNode('details',null,'review-phases');
  phases.appendChild(reviewNode('summary','Performance by phase'));
  const phaseTable = reviewNode('table',null,'review-stats');
  for (const phase of ['opening','middlegame','endgame']) {
    const row = reviewNode('tr');
    row.appendChild(reviewNode('th',phase[0].toUpperCase()+phase.slice(1)));
    for (const color of ['w','b']) {
      const data = reviewState.summary[color].phases[phase];
      const cell = reviewNode('td',data.accuracy == null ? '—' : data.accuracy.toFixed(1));
      cell.title = data.moves + ' scored moves';
      row.appendChild(cell);
    }
    phaseTable.appendChild(row);
  }
  phases.appendChild(phaseTable); box.appendChild(phases);
  const label = reviewNode('label','Review highlights for','review-side');
  const select = reviewNode('select'); select.id='review-side'; select.setAttribute('aria-label','Review highlights for');
  for (const [value,text] of [['all','Both sides'],['w','White'],['b','Black']]) {
    const option = reviewNode('option',text); option.value=value; select.appendChild(option);
  }
  select.value=reviewSide; select.addEventListener('change',()=>{reviewSide=select.value;});
  label.appendChild(select); box.appendChild(label);
  if (reviewState.rows.length < 20) box.appendChild(reviewNode('p','Short sample: a few moves can strongly affect the score.','hint'));
}
function renderReviewDetail() {
  const row = reviewRow();
  const detail = el('review-detail');
  detail.replaceChildren();
  if (!row) return;
  const title = reviewNode('h3',`${row.number}${row.turn === 'w' ? '.' : '…'} ${row.san} `);
  title.appendChild(gradeBadge(row)); detail.appendChild(title);
  detail.appendChild(reviewNode('p',row.explanation || 'Analyse again for a detailed explanation.'));
  detail.appendChild(reviewNode('p',`Played: ${reviewScore(row.after)} · Preferred: ${reviewScore(row.before)} · White’s perspective`,'hint'));
  if (row.provisional) detail.appendChild(reviewNode('p','Shallow search · treat this grade as provisional.','hint'));
  const tour = reviewTour();
  reviewTourIndex = tour.indexOf(reviewPly);
  el('review-counter').textContent = reviewTourIndex < 0 ? 'Move ' + reviewPly : `Highlight ${reviewTourIndex+1} of ${tour.length}`;
  el('review-prev').disabled = !tour.some(p=>p<reviewPly);
  el('review-next').textContent = tour.some(p=>p>reviewPly) ? 'Next highlight' : 'Finish review';
  el('review-position').value = reviewPly;
  el('review-position-label').textContent = `· ${reviewPly} / ${reviewState.rows.length}`;
  el('review-preferred').textContent = (row.best_uci === row.uci ? 'Show best: ' : 'Try ') + row.best;
  for (const mode of ['before','played','best']) {
    const id = mode === 'best' ? 'preferred' : mode;
    el('review-'+id).setAttribute('aria-pressed',String(reviewMode === mode));
  }
  const line = row[reviewMode + '_line'] || [];
  el('review-board-caption').textContent = reviewMode === 'before'
    ? (row.uci === row.best_uci ? 'Before the move · green arrow: your move matches the preferred move' : 'Before the move · green arrow: preferred · amber dashed arrow: played')
    : `${reviewMode === 'best' ? 'Preferred' : 'Played'} engine continuation · step ${reviewStep} of ${line.length} · preview only`;
  const moves = el('review-line'); moves.replaceChildren();
  line.forEach((item,i)=>{
    const button = reviewButton(item.san,()=>previewReviewLine(reviewMode,i+1),'small-btn');
    button.setAttribute('aria-label',`Preview step ${i+1}: ${item.san}`);
    button.setAttribute('aria-pressed',String(reviewStep === i+1));
    moves.appendChild(button);
  });
}
async function inspectReview(ply) {
  if (reviewState.version !== 2 || !reviewState.rows?.some(r=>r.ply===ply)) return;
  reviewPly=ply;
  await previewReviewLine('before',0);
  if (reviewOpen && reviewPly === ply) el('modal-review').querySelector('.modal-body').scrollTop = 0;
}
async function previewReviewLine(mode,step) {
  const request=++reviewRequest;
  const result=await window.pywebview.api.review_position(reviewPly,mode,step);
  if (request !== reviewRequest || !reviewOpen) return;
  if (result.error) {el('review-status').textContent=result.error; return;}
  reviewMode=mode; reviewStep=result.step;
  applyBundle(result);
  renderReview();
}
async function restoreReviewBoard() {
  const request=++reviewRequest;
  reviewPly=null; reviewMode='before'; reviewStep=0;
  const [state,moves]=await Promise.all([window.pywebview.api.get_state(),window.pywebview.api.legal_moves()]);
  if (request === reviewRequest) applyBundle({state,legal_moves:moves});
}
async function closeReview() {
  reviewOpen=false;
  el('modal-review').classList.remove('open');
  await restoreReviewBoard();
  el('btn-review').focus();
}
function reviewEvaluation() {
  const row=reviewRow();
  return !row ? null : reviewMode === 'before' ? row.before : reviewStep === 1 ? (reviewMode === 'best' ? row.before : row.after) : null;
}
function renderReviewArrows(layer) {
  const row=reviewRow(); if (!row) return;
  const arrows = reviewMode === 'before'
    ? [{uci:row.best_uci,color:'#a4d4a9',label:'Preferred move'}, ...(row.uci !== row.best_uci ? [{uci:row.uci,color:'#e4ad68',label:'Played move',dashed:true}] : [])]
    : (row[reviewMode+'_line']?.[reviewStep] ? [{uci:row[reviewMode+'_line'][reviewStep].uci,color:reviewMode === 'best' ? '#a4d4a9' : '#e4ad68',label:'Next move in continuation'}] : []);
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 100 100'); svg.setAttribute('width','100%'); svg.setAttribute('height','100%');
  svg.setAttribute('role','img'); svg.setAttribute('aria-label',arrows.map(a=>a.label+': '+a.uci).join('; '));
  arrows.forEach((arrow,i)=>{
    const a=squareToRowCol(arrow.uci.slice(0,2)), b=squareToRowCol(arrow.uci.slice(2,4));
    const x=(a.col+.5)*12.5,y=(a.row+.5)*12.5, xx=(b.col+.5)*12.5,yy=(b.row+.5)*12.5;
    const len=Math.hypot(xx-x,yy-y)||1, ex=xx-(xx-x)/len*3,ey=yy-(yy-y)/len*3;
    svg.innerHTML+=`<defs><marker id="review-head-${i}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="3.4" markerHeight="3.4" orient="auto"><path d="M0 0L10 5L0 10Z" fill="${arrow.color}"/></marker></defs><line x1="${x}" y1="${y}" x2="${ex}" y2="${ey}" stroke="${arrow.color}" stroke-width="1.8" opacity=".92" ${arrow.dashed ? 'stroke-dasharray="2 1.5"' : ''} marker-end="url(#review-head-${i})"/>`;
  });
  layer.appendChild(svg);
}
function initReview() {
  el('btn-review').addEventListener('click',async()=>{
    if (liveActive) {showLiveNotice('Stop Live before opening game review.',true); return;}
    reviewState=await window.pywebview.api.get_review(); reviewOpen=true; reviewPly=null;
    el('modal-review').classList.add('open'); renderAll(); renderReview();
    el(reviewState.version === 2 && reviewState.rows?.length ? 'review-tour' : 'review-start').focus();
  });
  el('review-close').addEventListener('click',closeReview);
  el('review-start').addEventListener('click',async()=>{
    el('review-start').disabled=true;
    await restoreReviewBoard();
    const result=await window.pywebview.api.start_review();
    if (!result.ok) {el('review-status').textContent=result.error; el('review-start').disabled=false; return;}
    reviewState=result.review; renderReview();
  });
  el('review-cancel').addEventListener('click',async()=>{reviewState=await window.pywebview.api.cancel_review();renderReview();});
  el('review-tour').addEventListener('click',async()=>{
    const tour=reviewTour();
    const fallback=reviewState.rows.find(r=>reviewSide==='all'||r.turn===reviewSide);
    if (tour.length || fallback) {await inspectReview(tour[0]||fallback.ply);el('review-next').focus();}
  });
  el('review-summary-back').addEventListener('click',async()=>{await restoreReviewBoard();renderReview();});
  el('review-prev').addEventListener('click',()=>{const previous=reviewTour().filter(p=>p<reviewPly).pop(); if(previous)inspectReview(previous);});
  el('review-next').addEventListener('click',async()=>{const next=reviewTour().find(p=>p>reviewPly);if(next)inspectReview(next);else {await restoreReviewBoard();renderReview();el('review-status').textContent='Review complete · revisit any move below.';}});
  el('review-before').addEventListener('click',()=>previewReviewLine('before',0));
  el('review-played').addEventListener('click',()=>previewReviewLine('played',1));
  el('review-preferred').addEventListener('click',()=>previewReviewLine('best',1));
  el('review-position').addEventListener('input',e=>inspectReview(Number(e.target.value)));
  el('review-graph').addEventListener('click',e=>{
    const count=reviewState.rows?.length||0;if(!count)return;
    const rect=e.currentTarget.getBoundingClientRect();
    inspectReview(Math.max(1,Math.min(count,Math.round((e.clientX-rect.left)/rect.width*count))));
  });
}
window.onReview=data=>{if ((data.job_id ?? 0) < (reviewState.job_id ?? 0)) return; reviewState=data;renderReview();};
