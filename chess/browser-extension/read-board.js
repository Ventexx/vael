/* Runs in the selected page's MAIN world: logical metadata, never artwork. */
function readVaelBoard() {
  const source = location.hostname.replace(/^www\./, '');
  const visible = node => node.getBoundingClientRect().width > 100;
  const uniqueBoard = selector => {
    const boards = [...document.querySelectorAll(selector)].filter(visible);
    if (boards.length !== 1) throw new Error('Open a single game board to connect Live.');
    return boards[0];
  };
  try {
    if (source === 'chess.com') {
      const board = uniqueBoard('wc-chess-board, chess-board');
      const fen = board.game?.getFEN?.();
      if (typeof fen === 'string' && fen.trim().split(/\s+/).length === 6) return {source, fen};
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
      const moves = [...document.querySelectorAll('.main-line-ply')];
      const selected = moves.findIndex(node => node.querySelector('.selected'));
      const displayed = selected >= 0 ? moves.slice(0, selected + 1) : moves;
      const sans = displayed.map(node => (node.querySelector('.node-highlight-content') || node).textContent.trim());
      return {source, pieces, sans};
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
      const displayedMoves = selected >= 0 ? moves.slice(0, selected + 1) : moves;
      const sans = displayedMoves.map(node => [...node.childNodes].filter(n => n.nodeType === 3)
        .map(n => n.textContent).join('').trim()).filter(s => s && s !== '…');
      const fen = document.querySelector('input.copyable.autoselect[readonly]')?.value;
      // Analysis pages may expose the current full FEN directly.
      if (typeof fen === 'string' && /^[prnbqkPRNBQK1-8/]+ [wb] /.test(fen)) return {source, fen};
      return {source, pieces, sans};
    }
    throw new Error('Open a game on lichess.org or chess.com.');
  } catch (error) { return {source, error: error.message}; }
}
