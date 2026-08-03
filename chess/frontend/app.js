/* vael. chess -- frontend logic. Talks to the Python backend exclusively
 * through window.pywebview.api.* (calls) and window.onEngineInfo /
 * window.onBoardState (pushes from Python). No network calls -- everything
 * here is local.
 */

const FILES = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
const PROMO_ORDER = [['q', 'Queen'], ['r', 'Rook'], ['b', 'Bishop'], ['n', 'Knight']];

let flipped = false;
let liveActive = false;     // true while Live screen-reading is driving the board
let liveMode = 'continuous'; // 'continuous' (auto-polls) or 'manual' (only reads on Capture click)
let liveLowConfidence = false; // true when the last scan's piece-shape match confidence was low
let boardState = null;      // last state from the backend
let legalMoves = {};        // square -> [{to, uci, promotion}]
let selectedSquare = null;
let engineLines = {};       // multipv index -> latest info payload
let showArrows = true;
let pendingPromo = null;    // {from, to, matches}
let dragState = null;

const el = (id) => document.getElementById(id);

// ---------------------------------------------------------------- coordinate helpers
function squareAt(row, col) {
  // row/col are 0..7 in current *display* orientation (row 0 = top)
  if (!flipped) {
    const file = FILES[col];
    const rank = 8 - row;
    return file + rank;
  } else {
    const file = FILES[7 - col];
    const rank = row + 1;
    return file + rank;
  }
}
function squareToRowCol(square) {
  const file = FILES.indexOf(square[0]);
  const rank = parseInt(square[1], 10) - 1; // 0-indexed, 0 = rank1
  if (!flipped) return { row: 7 - rank, col: file };
  return { row: rank, col: 7 - file };
}
function isLightSquare(square) {
  const file = FILES.indexOf(square[0]);
  const rank = parseInt(square[1], 10) - 1;
  return (file + rank) % 2 === 1;
}
function fenToMap(fen) {
  const boardPart = fen.split(' ')[0];
  const rows = boardPart.split('/'); // index 0 = rank8 ... index7 = rank1
  const map = {};
  rows.forEach((rowStr, i) => {
    const rank = 8 - i;
    let fileIdx = 0;
    for (const ch of rowStr) {
      if (/\d/.test(ch)) {
        fileIdx += parseInt(ch, 10);
      } else {
        map[FILES[fileIdx] + rank] = ch;
        fileIdx += 1;
      }
    }
  });
  return map;
}

// ---------------------------------------------------------------- board build / render
function buildSquares() {
  const wrap = el('board-squares');
  wrap.innerHTML = '';
  for (let row = 0; row < 8; row++) {
    for (let col = 0; col < 8; col++) {
      const sq = squareAt(row, col);
      const div = document.createElement('div');
      div.className = 'sq ' + (isLightSquare(sq) ? 'light' : 'dark');
      div.dataset.square = sq;
      div.innerHTML = '<div class="overlay"></div>';
      wrap.appendChild(div);
    }
  }
  buildLabels();
}
function buildLabels() {
  const fl = el('file-labels');
  fl.innerHTML = '';
  for (let col = 0; col < 8; col++) {
    const f = flipped ? FILES[7 - col] : FILES[col];
    fl.innerHTML += `<span>${f}</span>`;
  }
  const rl = el('rank-labels');
  rl.innerHTML = '';
  for (let row = 0; row < 8; row++) {
    const r = flipped ? row + 1 : 8 - row;
    rl.innerHTML += `<span>${r}</span>`;
  }
}

function renderPieces() {
  const layer = el('pieces-layer');
  layer.innerHTML = '';
  if (!boardState) return;
  const map = fenToMap(boardState.fen);
  for (const sq in map) {
    const { row, col } = squareToRowCol(sq);
    const code = map[sq];
    const div = document.createElement('div');
    div.className = 'piece';
    div.dataset.square = sq;
    div.style.left = (col * 12.5) + '%';
    div.style.top = (row * 12.5) + '%';
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 45 45');
    svg.innerHTML = PIECE_SVG[code] || '';
    div.appendChild(svg);
    div.addEventListener('pointerdown', onPiecePointerDown);
    layer.appendChild(div);
  }
}

function renderHighlights() {
  document.querySelectorAll('.sq').forEach((d) => {
    d.classList.remove('sel', 'last', 'check');
    const overlay = d.querySelector('.overlay');
    const existingMarks = d.querySelectorAll('.dot, .ring');
    existingMarks.forEach((m) => m.remove());
  });
  if (!boardState) return;

  if (boardState.last_move) {
    const from = boardState.last_move.slice(0, 2);
    const to = boardState.last_move.slice(2, 4);
    [from, to].forEach((sq) => {
      const d = document.querySelector(`.sq[data-square="${sq}"]`);
      if (d) d.classList.add('last');
    });
  }
  if (boardState.in_check) {
    const map = fenToMap(boardState.fen);
    const kingCode = boardState.turn === 'w' ? 'K' : 'k';
    for (const sq in map) {
      if (map[sq] === kingCode) {
        const d = document.querySelector(`.sq[data-square="${sq}"]`);
        if (d) d.classList.add('check');
      }
    }
  }
  if (selectedSquare) {
    const d = document.querySelector(`.sq[data-square="${selectedSquare}"]`);
    if (d) d.classList.add('sel');
    const targets = legalMoves[selectedSquare] || [];
    const occupied = fenToMap(boardState.fen);
    const seen = new Set();
    targets.forEach((m) => {
      if (seen.has(m.to)) return;
      seen.add(m.to);
      const d2 = document.querySelector(`.sq[data-square="${m.to}"]`);
      if (!d2) return;
      const mark = document.createElement('div');
      mark.className = occupied[m.to] ? 'ring' : 'dot';
      d2.appendChild(mark);
    });
  }
}

function renderHeader() {
  if (!boardState) return;
  el('btn-step-back').disabled = liveActive || boardState.ply <= 0;
  el('btn-step-fwd').disabled = liveActive || boardState.ply >= boardState.total_plies;
}

function renderAll() {
  renderPieces();
  renderHighlights();
  renderHeader();
  renderMovesList();
  renderArrows();
}

// ---------------------------------------------------------------- moves list (notation)
function renderMovesList() {
  const box = el('moves-table');
  el('moves-count-tag').textContent = boardState ? boardState.total_plies : 0;
  if (!boardState || boardState.moves_san.length === 0) {
    box.innerHTML = '<div class="moves-empty">No moves played yet.</div>';
    return;
  }
  const sans = boardState.moves_san;
  let html = '';
  for (let i = 0; i < sans.length; i += 2) {
    const moveNum = i / 2 + 1;
    const whitePly = i + 1;
    const blackPly = i + 2;
    const whiteActive = boardState.ply === whitePly ? ' active' : '';
    html += `<div class="move-row">
      <div class="move-num">${moveNum}.</div>
      <div class="move-cell${whiteActive}" data-ply="${whitePly}">${sans[i]}</div>`;
    if (sans[i + 1] !== undefined) {
      const blackActive = boardState.ply === blackPly ? ' active' : '';
      html += `<div class="move-cell${blackActive}" data-ply="${blackPly}">${sans[i + 1]}</div>`;
    } else {
      html += `<div class="move-cell empty"></div>`;
    }
    html += `</div>`;
  }
  box.innerHTML = html;
  box.querySelectorAll('.move-cell[data-ply]').forEach((cell) => {
    cell.addEventListener('click', () => goToPly(parseInt(cell.dataset.ply, 10)));
  });
}

// ---------------------------------------------------------------- move input (click + drag)
function clearSelection() {
  selectedSquare = null;
  renderHighlights();
}

function trySelect(square) {
  if (legalMoves[square] && legalMoves[square].length) {
    selectedSquare = square;
    renderHighlights();
    return true;
  }
  return false;
}

function attemptMove(fromSquare, toSquare) {
  const matches = (legalMoves[fromSquare] || []).filter((m) => m.to === toSquare);
  if (matches.length === 0) {
    clearSelection();
    return;
  }
  if (matches.length === 1) {
    doMakeMove(matches[0].uci);
  } else {
    showPromoPicker(fromSquare, toSquare, matches);
  }
}

function doMakeMove(uci) {
  clearSelection();
  hidePromoPicker();
  window.pywebview.api.make_move(uci).then((res) => {
    if (res && res.ok) applyBundle(res);
  });
}

function showPromoPicker(fromSquare, toSquare, matches) {
  pendingPromo = { fromSquare, toSquare, matches };
  const picker = el('promo-picker');
  const color = boardState.turn; // side making this move
  picker.innerHTML = '';
  PROMO_ORDER.forEach(([letter, _name]) => {
    const match = matches.find((m) => m.promotion === letter);
    if (!match) return;
    const code = color === 'w' ? letter.toUpperCase() : letter;
    const opt = document.createElement('div');
    opt.className = 'promo-opt';
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 45 45');
    svg.innerHTML = PIECE_SVG[code] || '';
    opt.appendChild(svg);
    opt.addEventListener('click', () => doMakeMove(match.uci));
    picker.appendChild(opt);
  });
  const { row, col } = squareToRowCol(toSquare);
  const leftPct = Math.min(Math.max(col * 12.5 - 12.5, 0), 56);
  picker.style.left = leftPct + '%';
  picker.style.top = (row < 4 ? row * 12.5 : row * 12.5 - 12.5) + '%';
  picker.classList.add('open');
}
function hidePromoPicker() {
  pendingPromo = null;
  el('promo-picker').classList.remove('open');
}

function onPiecePointerDown(e) {
  if (liveActive) return; // board is driven by screen capture -- no manual input
  e.preventDefault();
  const pieceEl = e.currentTarget;
  const square = pieceEl.dataset.square;

  // Clicking a legal capture target while something else is selected:
  // complete the move immediately rather than starting a drag on this piece.
  if (selectedSquare && selectedSquare !== square) {
    const matches = (legalMoves[selectedSquare] || []).filter((m) => m.to === square);
    if (matches.length) {
      attemptMove(selectedSquare, square);
      return;
    }
  }

  if (!(legalMoves[square] && legalMoves[square].length)) {
    clearSelection();
    return;
  }

  selectedSquare = square;
  renderHighlights();

  const boardWrap = el('board-wrap');
  dragState = { square, pieceEl, moved: false };
  pieceEl.classList.add('dragging');
  pieceEl.setPointerCapture(e.pointerId);

  const rect = boardWrap.getBoundingClientRect();
  const onMove = (ev) => {
    dragState.moved = true;
    let x = ((ev.clientX - rect.left) / rect.width) * 100 - 6.25;
    let y = ((ev.clientY - rect.top) / rect.height) * 100 - 6.25;
    x = Math.min(Math.max(x, -6.25), 93.75);
    y = Math.min(Math.max(y, -6.25), 93.75);
    pieceEl.style.left = x + '%';
    pieceEl.style.top = y + '%';
  };
  const onUp = (ev) => {
    pieceEl.releasePointerCapture(ev.pointerId);
    pieceEl.removeEventListener('pointermove', onMove);
    pieceEl.removeEventListener('pointerup', onUp);
    pieceEl.classList.remove('dragging');

    if (dragState.moved) {
      let colF = ((ev.clientX - rect.left) / rect.width) * 8;
      let rowF = ((ev.clientY - rect.top) / rect.height) * 8;
      const col = Math.min(Math.max(Math.floor(colF), 0), 7);
      const row = Math.min(Math.max(Math.floor(rowF), 0), 7);
      const targetSquare = squareAt(row, col);
      if (targetSquare === square) {
        // dropped back on itself -- treat as a click/select, keep selection
      } else {
        attemptMove(square, targetSquare);
      }
    }
    dragState = null;
    renderPieces();
    renderHighlights();
  };
  pieceEl.addEventListener('pointermove', onMove);
  pieceEl.addEventListener('pointerup', onUp);
}

// clicking an empty (or non-piece-owning) square to complete a move
el('board-squares').addEventListener('click', (e) => {
  if (liveActive) return;
  const sqDiv = e.target.closest('.sq');
  if (!sqDiv) return;
  const square = sqDiv.dataset.square;
  if (selectedSquare && selectedSquare !== square) {
    attemptMove(selectedSquare, square);
  } else if (selectedSquare === square) {
    clearSelection();
  }
});

function goToPly(ply) {
  if (liveActive) return;
  window.pywebview.api.go_to_ply(ply).then(applyBundle);
}

// ---------------------------------------------------------------- state application
function applyBundle(res) {
  boardState = res.state;
  legalMoves = res.legal_moves;
  selectedSquare = null;
  engineLines = {};
  renderAll();
  renderLines();
  updateStatusbar();
}

function updateStatusbar() {
  if (!boardState) return;
  let liveTag = '';
  if (liveActive) {
    const modeLabel = liveMode === 'manual' ? 'LIVE (manual)' : 'LIVE';
    liveTag = `<span class="status-live-tag">&#9679; ${modeLabel} &mdash; reading screen</span> &middot; `;
    if (liveLowConfidence) {
      liveTag += `<span class="status-low-confidence" title="Piece-shape match confidence was low on the last scan -- worth double-checking the position">&#9888; low confidence</span> &middot; `;
    }
  }
  el('status-left').innerHTML =
    `${liveTag}<b>${boardState.fullmove_number}</b> move${boardState.fullmove_number === 1 ? '' : 's'} &middot; ply <b>${boardState.ply}</b> / ${boardState.total_plies}`;
  el('status-right').textContent = boardState.fen;
}

// ---------------------------------------------------------------- eval bar + arrows
function cpToWhiteFraction(cp, mate) {
  if (mate !== null && mate !== undefined) {
    return mate > 0 ? 0.98 : 0.02;
  }
  if (cp === null || cp === undefined) return 0.5;
  return 1 / (1 + Math.pow(10, -cp / 400));
}
function formatScore(cp, mate) {
  if (mate !== null && mate !== undefined) return (mate > 0 ? 'M' : '-M') + Math.abs(mate);
  if (cp === null || cp === undefined) return '\u2014';
  const val = (cp / 100).toFixed(1);
  return (cp > 0 ? '+' : '') + val;
}

function updateEvalBar() {
  const line1 = engineLines[1];
  const cp = line1 ? line1.cp : null;
  const mate = line1 ? line1.mate : null;
  const frac = cpToWhiteFraction(cp, mate);
  el('eval-fill-white').style.flexBasis = (frac * 100) + '%';
  el('eval-fill-black').style.flexBasis = ((1 - frac) * 100) + '%';
  el('eval-num').textContent = line1 ? formatScore(cp, mate) : '\u2014';
}

// A single arrow hue (the accent color) that desaturates toward gray the
// further a line's evaluation trails behind the top line.
const ARROW_BASE_RGB = [0, 212, 160];   // --accent
const ARROW_FADE_RGB = [130, 130, 130]; // neutral gray
const ARROW_FADE_RANGE_CP = 150;        // cp loss at which an arrow is fully gray
const ARROW_WIDTHS = [3.2, 2.1, 1.5, 1.1, 0.9];

function comparableScore(line) {
  // Returns a single sortable/subtractable number for cp or mate scores,
  // from the perspective already encoded on the line (higher = better).
  if (line.mate !== null && line.mate !== undefined) {
    return line.mate > 0 ? (100000 - line.mate) : (-100000 - line.mate);
  }
  return line.cp ?? 0;
}
function lerpColor(c1, c2, t) {
  t = Math.max(0, Math.min(1, t));
  const r = Math.round(c1[0] + (c2[0] - c1[0]) * t);
  const g = Math.round(c1[1] + (c2[1] - c1[1]) * t);
  const b = Math.round(c1[2] + (c2[2] - c1[2]) * t);
  return `rgb(${r}, ${g}, ${b})`;
}

function renderArrows() {
  const layer = el('arrows-layer');
  layer.innerHTML = '';
  if (!showArrows) return;
  const lines = Object.values(engineLines).sort((a, b) => a.multipv - b.multipv);
  if (!lines.length) return;

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 100 100');
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', '100%');
  const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  svg.appendChild(defs);

  const topScore = comparableScore(lines[0]);

  lines.slice(0, 5).forEach((line, idx) => {
    const uci = line.pv_uci && line.pv_uci[0];
    if (!uci) return;
    const from = uci.slice(0, 2);
    const to = uci.slice(2, 4);
    const a = squareToRowCol(from);
    const b = squareToRowCol(to);
    const x1 = (a.col + 0.5) * 12.5, y1 = (a.row + 0.5) * 12.5;
    const x2 = (b.col + 0.5) * 12.5, y2 = (b.row + 0.5) * 12.5;

    const cpLoss = idx === 0 ? 0 : Math.max(0, topScore - comparableScore(line));
    const fadeT = cpLoss / ARROW_FADE_RANGE_CP;

    const opacity = 0.85;
    const widthPx = ARROW_WIDTHS[Math.min(idx, ARROW_WIDTHS.length - 1)];
    const color = lerpColor(ARROW_BASE_RGB, ARROW_FADE_RGB, fadeT);
    const markerId = 'arrowhead-' + idx;

    const marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
    marker.setAttribute('id', markerId);
    marker.setAttribute('viewBox', '0 0 10 10');
    marker.setAttribute('refX', '6');
    marker.setAttribute('refY', '5');
    marker.setAttribute('markerWidth', '4.2');
    marker.setAttribute('markerHeight', '4.2');
    marker.setAttribute('orient', 'auto-start-reverse');
    marker.innerHTML = `<path d="M0,0 L10,5 L0,10 z" fill="${color}" opacity="${opacity}"></path>`;
    defs.appendChild(marker);

    // shorten the line so the arrowhead doesn't sit under the destination piece
    const dx = x2 - x1, dy = y2 - y1;
    const len = Math.sqrt(dx * dx + dy * dy) || 1;
    const shrink = 4.2;
    const ex = x2 - (dx / len) * shrink;
    const ey = y2 - (dy / len) * shrink;

    const lineEl = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    lineEl.setAttribute('x1', x1); lineEl.setAttribute('y1', y1);
    lineEl.setAttribute('x2', ex); lineEl.setAttribute('y2', ey);
    lineEl.setAttribute('stroke', color);
    lineEl.setAttribute('stroke-width', widthPx);
    lineEl.setAttribute('stroke-linecap', 'round');
    lineEl.setAttribute('opacity', opacity);
    lineEl.setAttribute('marker-end', `url(#${markerId})`);
    svg.appendChild(lineEl);
  });

  layer.appendChild(svg);
}

// ---------------------------------------------------------------- analysis lines panel
function renderLines() {
  const list = el('lines-list');
  const lines = Object.values(engineLines).sort((a, b) => a.multipv - b.multipv);
  if (!lines.length) {
    const status = window.pywebview && el('engine-tag').textContent === 'on'
      ? 'Analyzing&hellip;'
      : 'Connect an engine to see live analysis lines here.';
    list.innerHTML = `<div class="lines-empty">${status}</div>`;
    el('lines-depth-tag').textContent = '\u2014';
    el('lines-meta').textContent = '\u2014';
    return;
  }
  const top = lines[0];
  el('lines-depth-tag').textContent = 'depth ' + (top.depth ?? '\u2014');
  const nps = top.nps ? Math.round(top.nps / 1000) + 'k nps' : '';
  el('lines-meta').textContent = [nps, top.nodes ? (top.nodes + ' nodes') : ''].filter(Boolean).join(' \u00b7 ');

  list.innerHTML = '';
  lines.forEach((line, idx) => {
    const row = document.createElement('div');
    row.className = 'line-row' + (idx === 0 ? ' top' : '');
    const scoreText = formatScore(line.cp, line.mate);
    const scoreClass = (line.mate !== null && line.mate !== undefined)
      ? (line.mate > 0 ? 'pos' : 'neg')
      : (line.cp > 0 ? 'pos' : (line.cp < 0 ? 'neg' : ''));
    const moveNum = line.start_fullmove;
    const startTurn = line.start_turn;
    let pvText = '';
    line.pv_san.forEach((san, i) => {
      const isWhiteMove = (startTurn === 'w' && i % 2 === 0) || (startTurn === 'b' && i % 2 === 1);
      const num = moveNum + Math.floor((startTurn === 'w' ? i : i + 1) / 2);
      if (i === 0) {
        pvText += startTurn === 'w' ? `${num}. ` : `${num}...`;
      } else if (isWhiteMove) {
        pvText += ` ${num}.`;
      }
      pvText += ` ${san}`;
    });
    row.innerHTML = `<div class="line-score ${scoreClass}">${scoreText}</div><div class="line-moves">${pvText.trim()}</div>`;
    list.appendChild(row);
  });
}

// ---------------------------------------------------------------- engine settings panel
function collectEngineOptions() {
  const limitMode = el('limit-mode').value;
  return {
    multipv: parseInt(el('multipv-val').textContent, 10),
    use_limit_strength: el('toggle-limit-strength').checked,
    skill_level: parseInt(el('skill-slider').value, 10),
    elo: parseInt(el('elo-input').value, 10),
    threads: parseInt(el('threads-input').value, 10),
    hash_mb: parseInt(el('hash-input').value, 10),
    depth_limit: limitMode === 'depth' ? parseInt(el('depth-input').value, 10) : null,
    movetime_ms: limitMode === 'movetime' ? parseInt(el('movetime-input').value, 10) : null,
  };
}
function applyEngineOptions() {
  window.pywebview.api.set_engine_options(collectEngineOptions());
}

function setEngineConnectedUI(connected, identity) {
  el('engine-dot').className = 'status-dot ' + (connected ? 'on' : 'off');
  el('engine-status-text').textContent = connected
    ? ((identity && identity.name) || 'Engine connected')
    : 'Engine not connected';
  el('engine-tag').textContent = connected ? 'on' : 'off';
  el('engine-id-box').innerHTML = connected
    ? `<span class="ok">Connected</span> &middot; ${(identity && identity.name) || 'Unknown engine'}${identity && identity.author ? ' \u2014 ' + identity.author : ''}`
    : 'No engine connected. Point this at your local Stockfish binary.';
}

function initEnginePanelEvents() {
  el('btn-browse-engine').addEventListener('click', async () => {
    const path = await window.pywebview.api.pick_engine_file();
    if (path) el('engine-path').value = path;
  });
  el('btn-connect-engine').addEventListener('click', async () => {
    const path = el('engine-path').value.trim();
    if (!path) return;
    const res = await window.pywebview.api.connect_engine(path);
    if (res.ok) {
      setEngineConnectedUI(true, res.identity);
      applyEngineOptions();
    } else {
      setEngineConnectedUI(false, null);
      el('engine-id-box').innerHTML = `<span class="bad">Failed:</span> ${res.error}`;
    }
  });
  el('btn-disconnect-engine').addEventListener('click', async () => {
    await window.pywebview.api.disconnect_engine();
    setEngineConnectedUI(false, null);
    engineLines = {};
    renderLines();
    renderArrows();
    updateEvalBar();
  });

  el('multipv-dec').addEventListener('click', () => {
    const v = Math.max(1, parseInt(el('multipv-val').textContent, 10) - 1);
    el('multipv-val').textContent = v;
    engineLines = {}; renderLines();
    applyEngineOptions();
  });
  el('multipv-inc').addEventListener('click', () => {
    const v = Math.min(8, parseInt(el('multipv-val').textContent, 10) + 1);
    el('multipv-val').textContent = v;
    engineLines = {}; renderLines();
    applyEngineOptions();
  });

  el('toggle-limit-strength').addEventListener('change', (e) => {
    el('skill-field').style.display = e.target.checked ? 'none' : '';
    el('elo-field').style.display = e.target.checked ? '' : 'none';
    applyEngineOptions();
  });
  el('skill-slider').addEventListener('input', () => {
    el('skill-val').textContent = el('skill-slider').value;
  });
  el('skill-slider').addEventListener('change', applyEngineOptions);

  el('limit-mode').addEventListener('change', () => {
    const mode = el('limit-mode').value;
    el('depth-field').style.display = mode === 'depth' ? '' : 'none';
    el('movetime-field').style.display = mode === 'movetime' ? '' : 'none';
  });

  el('toggle-arrows').addEventListener('change', (e) => {
    showArrows = e.target.checked;
    renderArrows();
  });

  el('btn-apply-engine').addEventListener('click', applyEngineOptions);
}

// ---------------------------------------------------------------- collapsible panel sections
function initPanelSections() {
  document.querySelectorAll('.panel-section-head').forEach((head) => {
    head.addEventListener('click', (e) => {
      if (e.target.closest('.no-collapse')) return;
      head.closest('.panel-section').classList.toggle('collapsed');
    });
  });
}

// ---------------------------------------------------------------- titlebar window controls
function initTitlebar() {
  el('win-min').addEventListener('click', () => window.pywebview.api.minimize_window());
  el('win-max').addEventListener('click', () => window.pywebview.api.toggle_maximize_window());
  el('win-close').addEventListener('click', () => window.pywebview.api.close_window());
}

// ---------------------------------------------------------------- manual edge/corner resize
// Frameless windows have no native resize grips, so drag the invisible
// .resize-handle strips and ask the Python side to resize the real window.
function fixPointFor(edge) {
  const vertical = edge.includes('n') ? 'SOUTH' : 'NORTH';
  const horizontal = edge.includes('w') ? 'EAST' : 'WEST';
  return vertical + '|' + horizontal;
}

function initResizeHandles() {
  document.querySelectorAll('.resize-handle').forEach((handle) => {
    handle.addEventListener('pointerdown', async (e) => {
      e.preventDefault();
      const edge = handle.dataset.edge;
      const fixPoint = fixPointFor(edge);
      const start = await window.pywebview.api.get_window_geometry();
      const startX = e.screenX, startY = e.screenY;
      handle.setPointerCapture(e.pointerId);

      let pending = null;
      let nextW = start.width, nextH = start.height;
      const flush = () => {
        pending = null;
        window.pywebview.api.resize_window(nextW, nextH, fixPoint);
      };

      const onMove = (ev) => {
        const dx = ev.screenX - startX;
        const dy = ev.screenY - startY;
        nextW = edge.includes('w') ? start.width - dx : (edge.includes('e') ? start.width + dx : start.width);
        nextH = edge.includes('n') ? start.height - dy : (edge.includes('s') ? start.height + dy : start.height);
        if (!pending) pending = requestAnimationFrame(flush);
      };
      const onUp = (ev) => {
        handle.releasePointerCapture(ev.pointerId);
        handle.removeEventListener('pointermove', onMove);
        handle.removeEventListener('pointerup', onUp);
        if (pending) cancelAnimationFrame(pending);
      };
      handle.addEventListener('pointermove', onMove);
      handle.addEventListener('pointerup', onUp);
    });
  });
}

// ---------------------------------------------------------------- topbar actions
function initTopbar() {
  el('btn-new-game').addEventListener('click', async () => {
    const res = await window.pywebview.api.new_game();
    applyBundle(res);
  });
  el('btn-flip').addEventListener('click', () => {
    flipped = !flipped;
    buildSquares();
    renderAll();
  });
  el('btn-step-back').addEventListener('click', () => goToPly((boardState?.ply ?? 1) - 1));
  el('btn-step-fwd').addEventListener('click', () => goToPly((boardState?.ply ?? 0) + 1));

  el('btn-live').addEventListener('click', async () => {
    const btn = el('btn-live');
    if (liveActive) {
      btn.disabled = true;
      await window.pywebview.api.stop_live();
      btn.disabled = false;
      return;
    }
    clearSelection();
    hidePromoPicker();
    btn.disabled = true;
    btn.title = 'Drag a box around the board in the overlay window\u2026';
    const chosenMode = el('live-mode-select').value;
    const res = await window.pywebview.api.start_live(flipped, chosenMode);
    btn.disabled = false;
    if (!res.ok && res.error !== 'cancelled') {
      alert('Could not start Live mode: ' + res.error);
    }
    // on success, window.onLiveStatus({live:true,...}) drives the UI state
  });

  el('btn-capture-now').addEventListener('click', async () => {
    const btn = el('btn-capture-now');
    btn.disabled = true;
    btn.classList.add('pulse');
    await window.pywebview.api.capture_live_now();
    btn.disabled = false;
    btn.classList.remove('pulse');
  });

  el('btn-import').addEventListener('click', () => el('modal-import').classList.add('open'));
  el('import-close').addEventListener('click', () => el('modal-import').classList.remove('open'));
  el('import-cancel').addEventListener('click', () => el('modal-import').classList.remove('open'));
  document.querySelectorAll('.modal-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.modal-tab').forEach((t) => t.classList.remove('active'));
      document.querySelectorAll('.modal-pane').forEach((p) => p.classList.remove('active'));
      tab.classList.add('active');
      el('pane-' + tab.dataset.tab).classList.add('active');
    });
  });
  el('import-go').addEventListener('click', async () => {
    const activeTab = document.querySelector('.modal-tab.active').dataset.tab;
    let res;
    if (activeTab === 'fen') {
      res = await window.pywebview.api.set_fen(el('fen-input').value.trim());
    } else {
      res = await window.pywebview.api.import_pgn(el('pgn-input').value);
    }
    if (res.ok) {
      applyBundle(res);
      el('modal-import').classList.remove('open');
    } else {
      alert('Could not load position: ' + res.error);
    }
  });

  el('btn-export').addEventListener('click', async () => {
    const pgn = await window.pywebview.api.export_pgn();
    el('export-text').value = pgn;
    el('modal-export').classList.add('open');
  });
  el('export-close').addEventListener('click', () => el('modal-export').classList.remove('open'));
  el('export-copy').addEventListener('click', () => {
    el('export-text').select();
    navigator.clipboard.writeText(el('export-text').value).catch(() => document.execCommand('copy'));
  });

  document.querySelectorAll('.modal-backdrop').forEach((m) => {
    m.addEventListener('click', (e) => { if (e.target === m) m.classList.remove('open'); });
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') goToPly((boardState?.ply ?? 1) - 1);
    else if (e.key === 'ArrowRight') goToPly((boardState?.ply ?? 0) + 1);
    else if (e.key === 'Escape') { clearSelection(); hidePromoPicker(); }
  });
}

// ---------------------------------------------------------------- backend push hooks
//
// Stockfish streams "infinite" analysis continuously (not just while the
// engine is "thinking" about a move) -- with MultiPV > 1 that can easily be
// dozens of info lines per second. Previously every single one triggered an
// immediate, synchronous full DOM rebuild (renderLines rewrites innerHTML,
// renderArrows tears down/rebuilds an SVG, updateEvalBar touches layout).
// That's what made the board feel sluggish: the UI thread was constantly
// busy redrawing engine output, so clicks/drags to make a move ended up
// queued behind it. Coalescing to at most one render per animation frame
// fixes this without losing any information -- only the latest line per
// multipv slot is ever shown anyway.
let engineRenderScheduled = false;
function scheduleEngineRender() {
  if (engineRenderScheduled) return;
  engineRenderScheduled = true;
  requestAnimationFrame(() => {
    engineRenderScheduled = false;
    renderLines();
    renderArrows();
    updateEvalBar();
  });
}

window.onEngineInfo = function (payload) {
  if (payload.type === 'gameover') return;
  if (payload.type !== 'info') return;
  engineLines[payload.multipv] = payload;
  scheduleEngineRender();
};

window.onEngineStatus = function (payload) {
  setEngineConnectedUI(payload.connected, payload.identity);
};

// A move was detected on the watched screen region -- apply it exactly
// like a locally-made move (board/notation/eval bar all update the same
// way), just without ever calling make_move() ourselves.
window.onLiveMove = function (res) {
  applyBundle(res);
};

// A full-board rescan found the screen didn't match the tracked position
// closely enough to explain with a move or two -- Live already adopted the
// rescanned position (including whatever orientation it detected) as the
// new starting point. This is routine, expected behavior (e.g. starting
// Live on a game already in progress), so it's surfaced quietly rather
// than with a modal alert.
const BIG_RESYNC_SQUARES = 6; // more squares differing than a couple of ordinary moves would produce
function occupancyDiffCount(fenA, fenB) {
  const a = fenToMap(fenA), b = fenToMap(fenB);
  const squares = new Set([...Object.keys(a), ...Object.keys(b)]);
  let diff = 0;
  squares.forEach((sq) => { if (a[sq] !== b[sq]) diff++; });
  return diff;
}

window.onLiveResync = function (res) {
  const prevFen = boardState ? boardState.fen : null;
  const bigChange = prevFen ? occupancyDiffCount(prevFen, res.state.fen) > BIG_RESYNC_SQUARES : false;

  if (bigChange) {
    // A jump this size means the screen moved on to a different game/
    // position entirely, not a continuation -- briefly clear the board
    // before showing the new one instead of pieces just teleporting,
    // so it visually reads as "new position" rather than "your last move
    // got weird".
    const layer = el('pieces-layer');
    layer.classList.add('flash-clear');
    setTimeout(() => {
      applyBundle(res);
      layer.classList.remove('flash-clear');
    }, 160);
  } else {
    applyBundle(res);
  }
  console.log(`[live] board resynced from screen scan (confidence ${(res.confidence * 100).toFixed(0)}%)`);
};

window.onLiveStatus = function (payload) {
  liveActive = !!payload.live;
  if (payload.mode) liveMode = payload.mode;
  if ('low_confidence' in payload) liveLowConfidence = !!payload.low_confidence;
  if (!liveActive) liveLowConfidence = false;

  const btn = el('btn-live');
  const modeSelect = el('live-mode-select');
  const captureBtn = el('btn-capture-now');
  document.getElementById('app').classList.toggle('live-mode', liveActive);
  btn.classList.toggle('active', liveActive);
  btn.title = liveActive
    ? 'Stop Live (screen reading active)'
    : 'Watch a screen region and mirror moves made there live';
  modeSelect.disabled = liveActive; // mode is fixed for the duration of a Live session
  captureBtn.style.display = (liveActive && liveMode === 'manual') ? '' : 'none';

  clearSelection();
  hidePromoPicker();
  updateStatusbar();

  // Routine, expected notices (low confidence, "no change on manual
  // capture", orientation confirmed, etc.) are surfaced quietly in the
  // status bar / console rather than as a blocking alert() -- with the
  // confidence gate removed, these can fire on essentially every scan of
  // an unfamiliar piece skin, and a modal per-scan would make Live
  // unusable. A real error (the watcher thread died) still interrupts,
  // since Live has actually stopped and the user needs to know.
  if (payload.info) {
    console.log('[live] ' + payload.info);
  }
  if (payload.warning) {
    console.warn('[live] ' + payload.warning);
  }
  if (payload.error) {
    alert('Live mode stopped: ' + payload.error);
  }
};

// ---------------------------------------------------------------- boot
async function boot() {
  buildSquares();
  initTitlebar();
  initResizeHandles();
  initTopbar();
  initEnginePanelEvents();
  initPanelSections();

  const [state, legal, status, saved] = await Promise.all([
    window.pywebview.api.get_state(),
    window.pywebview.api.legal_moves(),
    window.pywebview.api.engine_status(),
    window.pywebview.api.get_saved_settings(),
  ]);
  boardState = state;
  legalMoves = legal;
  renderAll();
  updateStatusbar();

  if (saved && saved.engine_options) {
    const o = saved.engine_options;
    if (o.multipv) el('multipv-val').textContent = o.multipv;
    if (o.skill_level !== undefined) { el('skill-slider').value = o.skill_level; el('skill-val').textContent = o.skill_level; }
    if (o.elo) el('elo-input').value = o.elo;
    if (o.threads) el('threads-input').value = o.threads;
    if (o.hash_mb) el('hash-input').value = o.hash_mb;
    if (o.use_limit_strength) {
      el('toggle-limit-strength').checked = true;
      el('skill-field').style.display = 'none';
      el('elo-field').style.display = '';
    }
    if (o.depth_limit != null) {
      el('limit-mode').value = 'depth';
      el('depth-input').value = o.depth_limit;
      el('depth-field').style.display = '';
      el('movetime-field').style.display = 'none';
    } else if (o.movetime_ms != null) {
      el('limit-mode').value = 'movetime';
      el('movetime-input').value = o.movetime_ms;
      el('movetime-field').style.display = '';
      el('depth-field').style.display = 'none';
    }
  }
  if (saved && saved.engine_path) el('engine-path').value = saved.engine_path;

  setEngineConnectedUI(status.connected, status.identity);
}

if (window.pywebview) {
  boot();
} else {
  window.addEventListener('pywebviewready', boot);
}
