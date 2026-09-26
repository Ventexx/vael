/* Runs in the selected page's MAIN world: logical metadata, never artwork. */
function readVaelBoard() {
  const source = location.hostname.replace(/^www\./, '');
  const visible = node => node.getBoundingClientRect().width > 100;
  const normalizedResult = text => {
    const value = String(text || '').trim().replace(/½/g, '1/2').replace(/[–−]/g, '-').replace(/\s/g, '');
    return ['1-0','0-1','1/2-1/2'].includes(value) ? value : null;
  };
  const completion = (board, atEnd) => {
    if (!atEnd) return {};
    if (source === 'lichess.org') {
      // Lichess renderResult: ui/round/src/view/replay.ts (result-wrap > p.result).
      const node = document.querySelector('.round__app .result-wrap .result');
      const result = node && node.getBoundingClientRect().width > 0 ? normalizedResult(node.textContent) : null;
      return result ? {finished: true, result} : {};
    }
    let result;
    try {
      result = normalizedResult(board.game?.getResult?.());
      const pgn = !result && board.game?.getPGN?.();
      if (typeof pgn === 'string') result = normalizedResult(pgn.match(/\[Result "([^"]+)"\]/)?.[1]);
    } catch { /* Optional site metadata can be absent during a page update. */ }
    if (result) return {finished: true, result};
    const node = document.querySelector('.game-over-header-component, [data-cy="game-over-modal"], .game-over-modal-content');
    const text = node?.textContent || '';
    if (node && node.getBoundingClientRect().width > 0 && !/abort/i.test(text) && /\b(won|wins|draw|checkmate|resignation|timeout)\b/i.test(text))
      return {finished: true, result: 'finished'};
    return {};
  };
  const uniqueBoard = selector => {
    const boards = [...document.querySelectorAll(selector)].filter(visible);
    if (boards.length !== 1) throw new Error('Open a single game board to connect Live.');
    return boards[0];
  };
  try {
    if (source === 'chess.com') {
      const board = uniqueBoard('wc-chess-board, chess-board');
      const moves = [...document.querySelectorAll('.main-line-ply')];
      const selected = moves.findIndex(node => node.querySelector('.selected'));
      const ended = completion(board, selected < 0 || selected === moves.length - 1);
      const fen = board.game?.getFEN?.();
      if (typeof fen === 'string' && fen.trim().split(/\s+/).length === 6) return {source, fen, ...ended};
      const pieces = {};
      for (const piece of board.querySelectorAll('.piece')) {
        const classes = [...piece.classList];
        if (classes.includes('dragging')) throw new Error('Waiting for the move to finish.');
        const role = classes.find(c => /^[wb][pnbrqk]$/.test(c));
        const square = classes.find(c => /^square-[1-8][1-8]$/.test(c));
        if (!role || !square) throw new Error('Waiting for complete piece data.');
        const key = 'abcdefgh'[Number(square[7]) - 1] + square[8];
        if (pieces[key]) throw new Error('Waiting for the board animation.');
        pieces[key] = role[0] === 'w' ? role[1].toUpperCase() : role[1];
      }
      const displayed = selected >= 0 ? moves.slice(0, selected + 1) : moves;
      const sans = displayed.map(node => (node.querySelector('.node-highlight-content') || node).textContent.trim());
      return {source, pieces, sans, ...ended};
    }
    if (source === 'lichess.org') {
      const board = uniqueBoard('.round__app cg-board, .analyse__board cg-board, .puzzle__board cg-board');
      const pieces = {};
      const roles = {pawn:'p', knight:'n', bishop:'b', rook:'r', queen:'q', king:'k'};
      for (const piece of board.querySelectorAll('piece')) {
        if (piece.classList.contains('ghost') || piece.classList.contains('fading')) continue;
        if (piece.classList.contains('dragging')) throw new Error('Waiting for the move to finish.');
        const [color, role] = (piece.cgPiece || piece.className).split(/\s+/);
        const key = piece.cgKey;
        if (!/^[a-h][1-8]$/.test(key) || !roles[role] || !['white','black'].includes(color))
          throw new Error('This board does not expose logical piece squares.');
        if (pieces[key]) throw new Error('Waiting for the board animation.');
        pieces[key] = color === 'white' ? roles[role].toUpperCase() : roles[role];
      }
      // Current and legacy round notation tags; no dependency on piece CSS.
      const moves = [...document.querySelectorAll('.round__app z7yx, .round__app kwdb, .round__app move')];
      const selected = moves.findIndex(node => node.classList.contains('a1t') || node.classList.contains('active'));
      const ended = completion(board, selected < 0 || selected === moves.length - 1);
      const displayedMoves = selected >= 0 ? moves.slice(0, selected + 1) : moves;
      const sans = displayedMoves.map(node => [...node.childNodes].filter(n => n.nodeType === 3)
        .map(n => n.textContent).join('').trim()).filter(s => s && s !== '…');
      const fen = document.querySelector('input.copyable.autoselect[readonly]')?.value;
      // Analysis pages may expose the current full FEN directly.
      if (typeof fen === 'string' && /^[prnbqkPRNBQK1-8/]+ [wb] /.test(fen)) return {source, fen, ...ended};
      return {source, pieces, sans, ...ended};
    }
    throw new Error('Open a game on lichess.org or chess.com.');
  } catch (error) { return {source, error: error.message}; }
}
