"""
capture.py -- "Live" mode for vael. chess.

Piece-shape templates
----------------------------------------------------------------------
Shape templates used for tier-1 recognition come from
`piece_templates/<set_name>/`, checked first, with the bundled cburnett
SVGs (rendered via cairosvg) used only as a fallback for any (type,
color) pair missing from that set. A template set is twelve files named
after python-chess's own piece symbols, prefixed by color: bp/bn/bb/br/
bq/bk.png for black, wp/wn/wb/wr/wq/wk.png for white -- each a
transparent-background PNG with only the piece's silhouette opaque.
White and black are kept separate deliberately: a white piece often has
much lower contrast against its square than a black piece does, so its
extracted silhouette can genuinely look different (more outline, less
fill) rather than just being a recolor of the black one. Drop a set
matching whatever board skin you're actually scanning (chess.com,
lichess, etc.) into its own subfolder and point ACTIVE_TEMPLATE_SET at
it; recognition will use those shapes instead of cburnett's for every
(type, color) pair present.

Lets the user drag a box around a chess board anywhere on screen (a
streamed game, another app, a physical board on a webcam feed piped into a
window, whatever) and mirrors moves made there into this app's own board
state, while engine.py keeps analyzing normally off that same state.

--------------------------------------------------------------------------
Two-tier recognition
--------------------------------------------------------------------------
1. FULL-BOARD SCAN (primary). At the moment Live starts -- and again any
   time frame-to-frame tracking loses confidence -- we classify every one
   of the 64 squares from scratch: is it occupied, what piece type is
   sitting on it, and what color is that piece. Piece *type* is recognized
   by shape-matching the square's contents against this app's own bundled
   piece art (python-chess's cburnett set -- the exact same artwork
   frontend/pieces.js renders with), so it isn't guessing blind; piece
   *color* is recognized by splitting occupied squares into two brightness
   clusters (works regardless of board theme, since a piece set is by
   definition two-tone). This is what lets Live:
     - start on a game already in progress, with no FEN/PGN paste needed,
     - figure out the board's on-screen orientation itself, instead of
       requiring the user to pre-flip to match,
     - recover automatically if it ever loses sync, instead of silently
       drifting.
   This tier needs the optional `cairosvg` package (to rasterize the piece
   art for template matching). If it isn't installed, Live still works,
   just in the more limited tier-2-only mode described below -- a clear
   status message says so once, on start.

2. OCCUPANCY-DIFF TRACKING (fast path, always on). Once the position is
   known, most single moves are cheap and reliable to recognize just by
   diffing which squares became occupied/empty between polls and matching
   that pattern against every legal move's own occupancy diff -- no need
   to re-run full recognition on every frame. If a diff can't be matched
   confidently (a stray highlight, a clock animation, a dragged piece
   mid-slide), we don't just shrug -- we fall back to a full-board rescan
   (tier 1) to re-derive ground truth and either resolve the ambiguous
   move (including picking the right promotion piece) or, if the screen
   has moved on further than one move, hard-resync the whole position.

Caveats (communicated to the user in the UI, not hidden):
 - The selected region must tightly bound the 8x8 playing surface only --
   no rank/file labels, no captured-piece trays, no clock.
 - Piece-type recognition is shape-based best-effort, not a guarantee --
   exotic/animated piece sets can still confuse it. A confidence check
   gates every full-board scan; if confidence is too low we say so rather
   than silently trusting a bad reconstruction.
 - Side-to-move on a fresh scan is inferred (by testing which choice
   produces a legal-looking position), not read off a clock or move
   indicator, so an already-in-check-for-the-side-not-to-move position can
   occasionally be guessed wrong -- correctable by importing the FEN/PGN
   as before if it ever happens.
"""

import io
import os
import threading
import time

import chess
import chess.svg
import numpy as np
from mss import mss
from PIL import Image

try:
    import cairosvg
    _HAVE_CAIROSVG = True
except Exception:
    _HAVE_CAIROSVG = False

POLL_HZ = 5.0
STABLE_FRAMES_REQUIRED = 2       # frames a new grid must repeat before we trust it (debounces piece-slide animations)
MIN_MATCH_FRACTION = 0.85        # fraction of the changed-square pattern that must agree with a legal move's expected diff

TEMPLATE_SIZE = 64               # raster resolution used for both piece templates and captured cells
TEMPLATE_MIN_IOU = 0.35          # shape match below this is treated as "unknown", not trusted

# External template sets: a folder of bp/bn/bb/br/bq/bk.png (transparent
# background, opaque piece silhouette) named after python-chess's own
# lowercase piece symbols. Checked before falling back to the bundled
# cburnett SVG render for any type that isn't present. Switch board
# skins by changing ACTIVE_TEMPLATE_SET to another subfolder name.
_TEMPLATE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "piece_templates")
ACTIVE_TEMPLATE_SET = "chesscom"
_PIECE_SYMBOL = {
    chess.PAWN: "p", chess.KNIGHT: "n", chess.BISHOP: "b",
    chess.ROOK: "r", chess.QUEEN: "q", chess.KING: "k",
}

# A full-board scan is ALWAYS trusted and adopted when it disagrees with the
# app's tracked position -- Live's job is to report what's actually on
# screen, not to second-guess it against how "legal" or "familiar" it
# looks. These two thresholds only control how the result is *labeled* to
# the user, never whether it gets used:
#   - below FULL_SCAN_MIN_USABLE: so few squares matched any piece template
#     that there's nothing coherent to build a board out of (misaligned
#     region, or a piece skin so different every single match fails) --
#     the only case a scan is discarded rather than adopted.
#   - between the two: adopted, but flagged "low confidence" so the UI can
#     say so honestly instead of pretending certainty it doesn't have.
FULL_SCAN_MIN_USABLE = 0.15
FULL_SCAN_MIN_CONFIDENCE = 0.42
BOARD_DIFF_TOLERANCE = 1         # squares allowed to disagree before two boards are considered "different"

_TEMPLATE_TYPES = [chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN, chess.KING]
_TEMPLATE_CACHE = {}
_TEMPLATES_READY = None  # tri-state: None = not checked yet, True/False after first check


# ------------------------------------------------------------------ piece shape templates
def _bbox_normalize(mask, size):
    """Crops a boolean mask to its own foreground bounding box and resizes
    that box to `size`x`size`. This is what lets a captured cell (whose
    piece may be inset by a very different margin than our reference
    template's canvas) compare fairly against the template: both get
    reduced to 'just the piece, filling the frame' before matching, so
    differing paddings/proportions between board skins wash out instead
    of corrupting the shape match."""
    ys, xs = np.where(mask)
    if ys.size == 0:
        return np.zeros((size, size), dtype=bool)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    cropped = mask[y0:y1, x0:x1]
    img = Image.fromarray((cropped.astype(np.uint8) * 255)).resize((size, size))
    return np.asarray(img, dtype=np.float32) > 127.0


def _load_external_template(piece_type, color):
    """Loads a shape mask for one (piece type, color) pair from
    piece_templates/<ACTIVE_TEMPLATE_SET>/<w|b><symbol>.png, if that file
    exists. A white piece typically has much lower contrast against a
    light square than a black piece does against either square, so its
    foreground mask can end up capturing mostly the outline stroke
    rather than a filled silhouette -- a genuinely different shape, not
    just a recolor. Loading white/black separately (falling back to
    whichever color IS present if only one was supplied) avoids
    blurring those two shapes into one template that fits neither.
    Foreground is taken from the PNG's own alpha channel. Returns None
    if no file for this type exists in either color."""
    symbol = _PIECE_SYMBOL.get(piece_type)
    if symbol is None or not ACTIVE_TEMPLATE_SET:
        return None
    prefix = "w" if color == chess.WHITE else "b"
    fallback_prefix = "b" if color == chess.WHITE else "w"
    for p in (prefix, fallback_prefix):
        path = os.path.join(_TEMPLATE_ROOT, ACTIVE_TEMPLATE_SET, f"{p}{symbol}.png")
        if not os.path.isfile(path):
            continue
        try:
            img = Image.open(path).convert("RGBA")
            alpha = np.asarray(img, dtype=np.float32)[:, :, 3]
            raw_mask = alpha > 127.0
            if not raw_mask.any():
                continue
            return _bbox_normalize(raw_mask, TEMPLATE_SIZE)
        except Exception as e:
            print(f"[live] external template load failed for {path}:", e)
    return None


def _piece_shape_template(piece_type, color):
    """A boolean silhouette mask for one (piece type, color) pair --
    from an external template file for that exact color if one is
    present (see _load_external_template), otherwise rendered from
    python-chess's own bundled cburnett art via cairosvg (cburnett's
    black/white silhouettes ARE identical, just recolored, so a single
    WHITE render covers both there) -- and cached for the life of the
    process. Bounding-box normalized so it compares fairly against
    captured cells that may inset their piece art by a different margin."""
    cache_key = (piece_type, color)
    if cache_key in _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE[cache_key]

    external = _load_external_template(piece_type, color)
    if external is not None:
        _TEMPLATE_CACHE[cache_key] = external
        return external

    if not _HAVE_CAIROSVG:
        _TEMPLATE_CACHE[cache_key] = None
        return None
    try:
        render_size = TEMPLATE_SIZE * 2  # render larger, then bbox-crop down, for less aliasing
        svg = chess.svg.piece(chess.Piece(piece_type, chess.WHITE), size=render_size)
        png_bytes = cairosvg.svg2png(
            bytestring=svg.encode("utf-8"),
            output_width=render_size, output_height=render_size,
            background_color="#7f7f7f",
        )
        img = Image.open(io.BytesIO(png_bytes)).convert("L")
        arr = np.asarray(img, dtype=np.float32)
        raw_mask = np.abs(arr - 127.0) > 40.0
        mask = _bbox_normalize(raw_mask, TEMPLATE_SIZE)
        _TEMPLATE_CACHE[cache_key] = mask
        return mask
    except Exception as e:
        print("[live] piece-template render failed, disabling shape recognition:", e)
        _TEMPLATE_CACHE[cache_key] = None
        return None


def _templates_available():
    global _TEMPLATES_READY
    if _TEMPLATES_READY is not None:
        return _TEMPLATES_READY
    _TEMPLATES_READY = all(
        _piece_shape_template(pt, color) is not None
        for pt in _TEMPLATE_TYPES for color in (chess.WHITE, chess.BLACK)
    )
    return _TEMPLATES_READY


def _match_shape(mask, color):
    """Best-matching piece type for a boolean silhouette mask, against
    templates of the given piece color only, by intersection-over-union.
    Returns (piece_type_or_None, score)."""
    best_type, best_score = None, -1.0
    for pt in _TEMPLATE_TYPES:
        tmpl = _piece_shape_template(pt, color)
        if tmpl is None:
            continue
        inter = np.logical_and(mask, tmpl).sum()
        union = np.logical_or(mask, tmpl).sum()
        score = float(inter) / union if union else 0.0
        if score > best_score:
            best_score, best_type = score, pt
    if best_score < TEMPLATE_MIN_IOU:
        return None, best_score
    return best_type, best_score


# ------------------------------------------------------------------ small numeric helpers
def _kmeans_1d(values, iters=25):
    """Minimal 2-cluster 1-D k-means (no sklearn dependency needed for
    something this small). Returns (low_center, high_center)."""
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return None
    c0, c1 = float(values.min()), float(values.max())
    if c0 == c1:
        return (c0, c1)
    for _ in range(iters):
        d0 = np.abs(values - c0)
        d1 = np.abs(values - c1)
        g0 = values[d0 <= d1]
        g1 = values[d1 < d0]
        new_c0 = float(g0.mean()) if g0.size else c0
        new_c1 = float(g1.mean()) if g1.size else c1
        if abs(new_c0 - c0) < 1e-3 and abs(new_c1 - c1) < 1e-3:
            c0, c1 = new_c0, new_c1
            break
        c0, c1 = new_c0, new_c1
    return (min(c0, c1), max(c0, c1))


def _auto_occupied_threshold(scores):
    """Separates occupied/empty squares from the scores themselves (no
    reliance on a caller-supplied board), by treating the 64 scores as a
    bimodal distribution and splitting at the midpoint between clusters."""
    centers = _kmeans_1d(scores.flatten())
    if centers is None:
        return float(np.median(scores)) + 1.0
    lo, hi = centers
    return (lo + hi) / 2.0


def _status_bit_count(status):
    try:
        return bin(int(status)).count("1")
    except Exception:
        return 99


def _boards_differ(a, b, tolerance=0):
    diffs = 0
    for sq in chess.SQUARES:
        if a.piece_at(sq) != b.piece_at(sq):
            diffs += 1
            if diffs > tolerance:
                return True
    return False


# ------------------------------------------------------------------ occupancy grid from a raw frame
def _cell_bounds(h, w, r, c):
    cell_h, cell_w = h / 8.0, w / 8.0
    y0, y1 = int(r * cell_h), int((r + 1) * cell_h)
    x0, x1 = int(c * cell_w), int((c + 1) * cell_w)
    return y0, y1, x0, x1


def _board_base_colors(frame):
    """Estimate the two checkerboard background colors by averaging the
    outer border ring of every cell, split by light/dark parity. The
    border ring shows the bare square surface behind a piece on the large
    majority of chess UI skins (piece art is inset from the cell edges),
    so this stays reliable even on a fully-occupied board -- no reliance
    on any square actually being empty."""
    h, w = frame.shape[0], frame.shape[1]
    light_px, dark_px = [], []
    for r in range(8):
        for c in range(8):
            y0, y1, x0, x1 = _cell_bounds(h, w, r, c)
            cell = frame[y0:y1, x0:x1, :3]
            ch, cw = cell.shape[0], cell.shape[1]
            band = max(2, int(min(ch, cw) * 0.10))
            border = np.concatenate([
                cell[:band, :, :].reshape(-1, 3),
                cell[-band:, :, :].reshape(-1, 3),
                cell[:, :band, :].reshape(-1, 3),
                cell[:, -band:, :].reshape(-1, 3),
            ])
            (light_px if (r + c) % 2 == 0 else dark_px).append(border.mean(axis=0))
    light_ref = np.median(np.array(light_px), axis=0) if light_px else np.array([200.0, 200.0, 200.0])
    dark_ref = np.median(np.array(dark_px), axis=0) if dark_px else np.array([80.0, 80.0, 80.0])
    return light_ref, dark_ref


def _occupancy_scores(frame):
    """frame: HxWx4 (BGRA, from mss). Returns an 8x8 float array -- higher
    means 'more likely a piece is sitting on this square'.

    Combines two signals so it holds up across different piece-set styles:
      - texture: local pixel variance (catches detailed/outlined pieces)
      - color deviation: how far the cell's average color sits from that
        square's own background color (catches flat, low-detail piece art
        that has little internal texture but a clearly different color
        from the board surface -- the case texture-only missed)."""
    gray = frame[:, :, :3].astype(np.float32).mean(axis=2)
    color = frame[:, :, :3].astype(np.float32)
    h, w = gray.shape
    light_ref, dark_ref = _board_base_colors(frame)

    scores = np.zeros((8, 8), dtype=np.float32)
    for r in range(8):
        for c in range(8):
            y0, y1, x0, x1 = _cell_bounds(h, w, r, c)
            cell_gray = gray[y0:y1, x0:x1]
            cell_color = color[y0:y1, x0:x1]
            iy = int(cell_gray.shape[0] * 0.2)
            ix = int(cell_gray.shape[1] * 0.2)
            inner_gray = cell_gray[iy: cell_gray.shape[0] - iy, ix: cell_gray.shape[1] - ix]
            inner_color = cell_color[iy: cell_color.shape[0] - iy, ix: cell_color.shape[1] - ix]
            if inner_gray.size == 0:
                inner_gray, inner_color = cell_gray, cell_color

            texture = float(inner_gray.std())
            ref = light_ref if (r + c) % 2 == 0 else dark_ref
            color_dev = float(np.linalg.norm(inner_color.reshape(-1, 3).mean(axis=0) - ref))
            scores[r, c] = texture + color_dev * 0.6
    return scores


def _cell_foreground_mask(inner_color, light_ref, dark_ref, parity):
    """Boolean mask (same resolution as `inner_color`) marking pixels that
    belong to a piece rather than the bare square beneath it, by distance
    from that square's own background color."""
    ref = light_ref if parity == 0 else dark_ref
    diff = np.linalg.norm(inner_color - ref.reshape(1, 1, 3), axis=2)
    return diff > 28.0


def _resize_mask(mask, size):
    return _bbox_normalize(mask, size)


# ------------------------------------------------------------------ whole-board recognition
def _full_scan(frame):
    """Single-shot recognition of the entire visible board from one frame.

    Returns (occupied[8,8] bool, type_grid[8,8] of (piece_type, score) or
    None, color_grid[8,8] of chess.WHITE/chess.BLACK or None, avg_confidence).
    """
    h, w = frame.shape[0], frame.shape[1]
    scores = _occupancy_scores(frame)
    threshold = _auto_occupied_threshold(scores)
    occupied = scores > threshold

    light_ref, dark_ref = _board_base_colors(frame)
    color_arr = frame[:, :, :3].astype(np.float32)
    templates_ok = _templates_available()

    # Pass 1: gather each occupied cell's own inner region + mean luminance.
    # Color must be known BEFORE shape matching now (white/black pieces are
    # matched against different template sets), so this has to finish
    # before any _match_shape call, not interleaved with it as before.
    inner_by_coord = {}
    lum_values, coords = [], []
    for r in range(8):
        for c in range(8):
            if not occupied[r, c]:
                continue
            y0, y1, x0, x1 = _cell_bounds(h, w, r, c)
            cell = color_arr[y0:y1, x0:x1, :]
            iy, ix = int(cell.shape[0] * 0.12), int(cell.shape[1] * 0.12)
            inner = cell[iy: cell.shape[0] - iy, ix: cell.shape[1] - ix, :]
            if inner.size == 0:
                inner = cell
            inner_by_coord[(r, c)] = inner
            lum_values.append(float(inner.mean()))
            coords.append((r, c))

    color_grid = np.full((8, 8), None, dtype=object)
    if lum_values:
        centers = _kmeans_1d(lum_values)
        if centers:
            mid = sum(centers) / 2.0
            for (r, c), lum in zip(coords, lum_values):
                color_grid[r, c] = chess.WHITE if lum >= mid else chess.BLACK

    # Pass 2: shape-match each occupied cell against its own color's templates.
    type_grid = np.full((8, 8), None, dtype=object)
    for (r, c), inner in inner_by_coord.items():
        if not templates_ok:
            continue
        color = color_grid[r, c]
        if color is None:
            continue
        mask = _cell_foreground_mask(inner, light_ref, dark_ref, (r + c) % 2)
        mask64 = _resize_mask(mask, TEMPLATE_SIZE)
        ptype, pscore = _match_shape(mask64, color)
        if ptype is not None:
            type_grid[r, c] = (ptype, pscore)

    confidences = [v[1] for v in type_grid.flatten() if v is not None]
    avg_conf = float(np.mean(confidences)) if confidences else 0.0
    return occupied, type_grid, color_grid, avg_conf


def _square_for_cell_static(r, c, flipped):
    if not flipped:
        file_idx, rank1 = c, 8 - r
    else:
        file_idx, rank1 = 7 - c, r + 1
    return chess.square(file_idx, rank1 - 1)


def _rc_for_square_static(sq, flipped):
    file_idx = chess.square_file(sq)
    rank1 = chess.square_rank(sq) + 1
    if not flipped:
        return 8 - rank1, file_idx
    return rank1 - 1, 7 - file_idx


def _build_board_candidate(occupied, type_grid, color_grid, flipped, turn):
    board = chess.Board(None)
    board.turn = turn
    for r in range(8):
        for c in range(8):
            if not occupied[r, c]:
                continue
            info = type_grid[r, c]
            color = color_grid[r, c]
            if info is None or color is None:
                continue
            sq = _square_for_cell_static(r, c, flipped)
            board.set_piece_at(sq, chess.Piece(info[0], color))
    return board


def _pawn_reversal_count(board):
    """Rotating a real position 180 degrees while keeping each detected
    piece's color attached to its (now relocated) square silently produces
    a structurally 'valid-enough' board -- chess.Status alone often can't
    tell the rotation happened. But it can't fake which way pawns face:
    it swaps white's and black's territory wholesale, so a position that
    started normally now has white's pawns sitting deep in black's half
    and vice versa. Counting pawns on the wrong side of the board is a
    cheap, reliable tiebreaker that bare legality misses -- it only gets
    weaker in very late endgames where few pawns remain, which is an
    acceptable blind spot since there's little left to get wrong by then."""
    reversed_count = 0
    for sq, piece in board.piece_map().items():
        if piece.piece_type != chess.PAWN:
            continue
        rank1 = chess.square_rank(sq) + 1  # 1..8
        if piece.color == chess.WHITE and rank1 >= 6:
            reversed_count += 1
        elif piece.color == chess.BLACK and rank1 <= 3:
            reversed_count += 1
    return reversed_count


def _best_orientation(occupied, type_grid, color_grid, hint_flipped):
    """Picks which way up the board is being viewed (White-at-bottom vs
    Black-at-bottom). This deliberately does NOT use chess.Status legality
    at all -- a puzzle, a scrambled test position, or anything mid-analysis
    is exactly the kind of screen Live needs to read correctly, and scoring
    orientation by 'which reading looks like a more normal game' would
    actively distort those. Only two things decide it:
      - pawn-reversal count: a purely geometric tell (which way each
        colored pawn is physically pointing), not a legality judgement --
        it stays meaningful even on an otherwise illegal/unusual position.
      - the previous known orientation (hint_flipped), as a tiebreaker so
        a stable but pawn-light position (e.g. a late endgame, or a
        position with no pawns at all) doesn't flip-flop on noise.
    Turn (whose move it is) is resolved separately in _resolve_turn, since
    that genuinely can't be read from square contents at all and has no
    bearing on where any piece gets placed."""
    best = None
    for flipped_try in (False, True):
        cand = _build_board_candidate(occupied, type_grid, color_grid, flipped_try, chess.WHITE)
        pawn_score = _pawn_reversal_count(cand)
        hint_penalty = 0 if flipped_try == hint_flipped else 1
        key = (pawn_score, hint_penalty)
        if best is None or key < best[0]:
            best = (key, flipped_try)
    _, best_flipped = best
    return best_flipped


def _resolve_turn(board_no_turn):
    """Side-to-move can't be read from the picture at all (no clock, no
    move-indicator dot assumed) -- chess.Status legality is the only signal
    available, so it's used here and only here, purely to pick a turn for
    an already-fixed piece layout. It never influences which orientation or
    which pieces were read off the screen."""
    board_no_turn.turn = chess.WHITE
    white_score = _status_bit_count(board_no_turn.status())
    board_no_turn.turn = chess.BLACK
    black_score = _status_bit_count(board_no_turn.status())
    return chess.WHITE if white_score <= black_score else chess.BLACK


def _recognize_board(occupied, type_grid, color_grid, hint_flipped):
    """Full pipeline from one frame's raw per-square reads to a finished
    board: pick orientation (pawn-direction only, never legality), build
    the piece layout for that orientation, then separately resolve whose
    move it is. Returns (flipped, board)."""
    flipped = _best_orientation(occupied, type_grid, color_grid, hint_flipped)
    board = _build_board_candidate(occupied, type_grid, color_grid, flipped, chess.WHITE)
    board.turn = _resolve_turn(board)
    return flipped, board


class LiveWatcher:
    def __init__(self, get_board, on_move, on_status, on_resync=None):
        """
        get_board: () -> chess.Board   current live position
        on_move:   (chess.Move) -> None   called (background thread) when a move is detected
        on_status: (dict) -> None         status/error pushes to the UI
        on_resync: (fen, confidence) -> None   called (background thread) when
                   a full-board rescan finds the screen no longer matches the
                   tracked position closely enough to explain by a move or
                   two -- the caller should adopt `fen` as the new position.
        """
        self.get_board = get_board
        self.on_move = on_move
        self.on_status = on_status
        self.on_resync = on_resync

        self._thread = None
        self._stop = threading.Event()
        self.active = False
        self.region = None
        self.flipped = False
        self._threshold = None
        self.mode = "continuous"     # "continuous" (auto-poll at POLL_HZ) or "manual" (only on trigger_capture())
        self._manual_trigger = threading.Event()

    # ------------------------------------------------------------ orientation mapping
    def _square_for_cell(self, r, c):
        return _square_for_cell_static(r, c, self.flipped)

    def _rc_for_square(self, sq):
        return _rc_for_square_static(sq, self.flipped)

    # ------------------------------------------------------------ calibration (legacy / diff-tracking thresholds)
    def _calibrate(self, scores, board):
        occ_vals, empty_vals = [], []
        for r in range(8):
            for c in range(8):
                sq = self._square_for_cell(r, c)
                (occ_vals if board.piece_at(sq) else empty_vals).append(scores[r, c])
        self._occ_ref = float(np.median(occ_vals)) if occ_vals else 1.0
        self._empty_ref = float(np.median(empty_vals)) if empty_vals else 0.0
        self._threshold = max(self._empty_ref + (self._occ_ref - self._empty_ref) * 0.5, self._empty_ref + 1.0)

    def _grid_to_bool(self, scores):
        return scores > self._threshold

    # ------------------------------------------------------------ lifecycle
    def start(self, region, flipped, mode="continuous"):
        self.stop()
        self.region = region
        self.flipped = bool(flipped)
        self.mode = mode if mode in ("continuous", "manual") else "continuous"
        self._stop.clear()
        self._manual_trigger.clear()
        self.active = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._manual_trigger.set()  # wake a manual-mode thread blocked on the trigger wait
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=3)
        self._thread = None
        self.active = False

    def trigger_capture(self):
        """Manual mode only: wake the watcher thread to take one screenshot
        of the region right now and update the game from it. No-op if Live
        isn't running in manual mode."""
        if self.active and self.mode == "manual":
            self._manual_trigger.set()

    # ------------------------------------------------------------ main loop
    def _run(self):
        interval = 1.0 / POLL_HZ
        if not _HAVE_CAIROSVG:
            print("[live] cairosvg not installed -- Live will run in legacy occupancy-only "
                  "mode: no auto-orientation, no auto board-scan, no self-resync. Run "
                  "'pip install cairosvg' and restart to enable full piece recognition.")
        try:
            with mss() as sct:
                mon = {"left": self.region["left"], "top": self.region["top"],
                        "width": self.region["width"], "height": self.region["height"]}

                frame = np.array(sct.grab(mon))
                app_board = self.get_board()
                used_full_scan = False
                scan_note = None

                low_confidence = False
                if _templates_available():
                    occupied, type_grid, color_grid, avg_conf = _full_scan(frame)
                    if avg_conf >= FULL_SCAN_MIN_USABLE:
                        low_confidence = avg_conf < FULL_SCAN_MIN_CONFIDENCE
                        best_flipped, recognized_board = _recognize_board(
                            occupied, type_grid, color_grid, self.flipped
                        )
                        self.flipped = best_flipped
                        used_full_scan = True
                        if _boards_differ(recognized_board, app_board, tolerance=BOARD_DIFF_TOLERANCE):
                            if self.on_resync:
                                self.on_resync(recognized_board.fen(), avg_conf)
                                app_board = self.get_board()
                                scan_note = (
                                    "Live scanned the board on screen and it didn't match the position "
                                    "loaded in the app, so the app's position was updated to match what's "
                                    "actually there (including detecting the board's on-screen orientation "
                                    "automatically)."
                                )
                            else:
                                app_board = recognized_board
                        else:
                            scan_note = "Live scanned the board and confirmed it matches -- orientation detected automatically."
                        if low_confidence:
                            scan_note = (
                                (scan_note + " " if scan_note else "") +
                                f"Confidence was low ({avg_conf:.0%}, piece art may not closely match the "
                                f"bundled set) -- worth double-checking the position against the source."
                            )
                    else:
                        scan_note = (
                            f"Live's board-scan couldn't match enough squares to read anything "
                            f"({avg_conf:.0%}) -- falling back to assuming the app's current position and "
                            f"Flip setting already match the screen. Check that the region tightly bounds "
                            f"just the 8\u00d78 board, or paste the source's FEN/PGN into Import first."
                        )

                scores = _occupancy_scores(frame)
                self._calibrate(scores, app_board)
                last_grid = self._grid_to_bool(scores)
                pending_grid, pending_count = None, 0

                expected_occupied = len(app_board.piece_map())
                detected_occupied = int(last_grid.sum())
                print(f"[live] watching region {mon} | empty~{self._empty_ref:.1f} "
                      f"occupied~{self._occ_ref:.1f} threshold={self._threshold:.1f} "
                      f"| detected {detected_occupied}/64 occupied squares "
                      f"(tracked position has {expected_occupied}) | orientation="
                      f"{'flipped' if self.flipped else 'normal'}"
                      f"{' (auto-detected)' if used_full_scan else ' (from Flip toggle)'}")

                status_payload = {"live": True, "calibrated": True,
                                   "mode": self.mode,
                                   "orientation": "flipped" if self.flipped else "normal",
                                   "auto_detected": used_full_scan,
                                   "low_confidence": low_confidence}
                if scan_note:
                    status_payload["info"] = scan_note
                if abs(detected_occupied - expected_occupied) > 2:
                    status_payload["warning"] = (
                        f"Detected {detected_occupied} occupied squares on screen but the tracked "
                        f"position has {expected_occupied}. Check that the region tightly bounds just "
                        f"the 8\u00d78 board (no labels/trays/clock)."
                    )
                self.on_status(status_payload)

                if self.mode == "manual":
                    self._run_manual(sct, mon)
                    return

                while not self._stop.is_set():
                    time.sleep(interval)
                    frame = np.array(sct.grab(mon))
                    scores = _occupancy_scores(frame)
                    grid = self._grid_to_bool(scores)

                    if np.array_equal(grid, last_grid):
                        pending_grid, pending_count = None, 0
                        continue

                    if pending_grid is not None and np.array_equal(grid, pending_grid):
                        pending_count += 1
                    else:
                        pending_grid, pending_count = grid, 1

                    if pending_count < STABLE_FRAMES_REQUIRED:
                        continue

                    move = self._match_move(last_grid, grid, frame)
                    last_grid = grid
                    pending_grid, pending_count = None, 0

                    if move is not None:
                        self.on_move(move)
                        continue

                    # Couldn't confidently map this change to a legal move --
                    # rather than silently drifting out of sync, re-derive
                    # ground truth from the frame we already have and either
                    # resolve it (including catching up on a missed move or
                    # two) or hard-resync.
                    recovered = self._attempt_resync(frame)
                    if not recovered:
                        self.on_status({"live": True, "unrecognized_change": True})
        except Exception as e:
            self.on_status({"live": False, "error": str(e)})
        finally:
            self.active = False

    # ------------------------------------------------------------ manual-capture loop
    def _run_manual(self, sct, mon):
        """User-driven alternative to continuous polling: the region is
        assigned once (already done by the time we get here) and nothing
        happens again until trigger_capture() is called -- then exactly one
        screenshot is taken and read, and whatever's on it is adopted as
        the new position, the same way a big resync would be. No implicit
        polling, no guessing about whether enough time has passed."""
        while not self._stop.is_set():
            self._manual_trigger.wait()
            self._manual_trigger.clear()
            if self._stop.is_set():
                return

            frame = np.array(sct.grab(mon))
            if not _templates_available():
                self.on_status({
                    "live": True, "mode": "manual",
                    "warning": "cairosvg isn't installed, so Live can't identify piece types -- "
                               "run 'pip install cairosvg' and restart to enable manual capture.",
                })
                continue

            resolved = self._attempt_resync(frame)
            if resolved:
                self.on_status({"live": True, "mode": "manual", "captured": True})
            else:
                self.on_status({
                    "live": True, "mode": "manual", "captured": False,
                    "info": "No change detected, or the region couldn't be read confidently enough "
                            "to update the position.",
                })

    # ------------------------------------------------------------ resync recovery
    def _attempt_resync(self, frame):
        """Called when frame-to-frame diffing couldn't explain a change.
        Re-derives the full board from this frame and either finds the
        (possibly two-move) legal sequence that explains it, or -- if it
        can't -- hard-resyncs to whatever is actually on screen. Returns
        True if it resolved something (a move was played or a resync
        happened), False if there was nothing usable to go on."""
        if not _templates_available():
            return False
        occupied, type_grid, color_grid, avg_conf = _full_scan(frame)
        if avg_conf < FULL_SCAN_MIN_USABLE:
            return False

        board_now = self.get_board()
        candidate = _build_board_candidate(occupied, type_grid, color_grid, self.flipped, board_now.turn)
        if not _boards_differ(candidate, board_now, tolerance=BOARD_DIFF_TOLERANCE):
            return False  # nothing actually changed -- this frame's recognition was just noisy

        for mv in board_now.legal_moves:
            b1 = board_now.copy()
            b1.push(mv)
            if not _boards_differ(b1, candidate, tolerance=BOARD_DIFF_TOLERANCE):
                self.on_move(mv)
                return True

        for mv1 in board_now.legal_moves:
            b1 = board_now.copy()
            b1.push(mv1)
            for mv2 in b1.legal_moves:
                b2 = b1.copy()
                b2.push(mv2)
                if not _boards_differ(b2, candidate, tolerance=BOARD_DIFF_TOLERANCE):
                    self.on_move(mv1)
                    self.on_move(mv2)
                    return True

        # Couldn't explain the change as a short legal sequence from here --
        # the screen may have moved on to an unrelated position (or the
        # board got flipped), so re-derive orientation fresh rather than
        # assuming self.flipped still holds, then hard resync to whatever
        # is actually on screen instead of guessing wrong.
        if self.on_resync:
            self.flipped, recognized_board = _recognize_board(occupied, type_grid, color_grid, self.flipped)
            self.on_resync(recognized_board.fen(), avg_conf)
            return True
        return False

    # ------------------------------------------------------------ move matching
    def _expected_diff(self, board, move):
        before = {sq for sq in chess.SQUARES if board.piece_at(sq)}
        b2 = board.copy()
        b2.push(move)
        after = {sq for sq in chess.SQUARES if b2.piece_at(sq)}
        changed = before.symmetric_difference(after)
        diff = np.zeros((8, 8), dtype=np.int8)
        for sq in changed:
            r, c = self._rc_for_square(sq)
            diff[r, c] = 1 if sq in after else -1
        return diff

    def _resolve_promotion(self, frame, to_square, color):
        """Shape-matches whatever is actually sitting on the promotion
        square to pick the real promotion piece, instead of defaulting to
        whichever promotion choice python-chess's move generator happens
        to list first (usually queen -- wrong for an underpromotion)."""
        if not _templates_available():
            return None
        h, w = frame.shape[0], frame.shape[1]
        r, c = self._rc_for_square(to_square)
        y0, y1, x0, x1 = _cell_bounds(h, w, r, c)
        cell = frame[y0:y1, x0:x1, :3].astype(np.float32)
        iy, ix = int(cell.shape[0] * 0.12), int(cell.shape[1] * 0.12)
        inner = cell[iy: cell.shape[0] - iy, ix: cell.shape[1] - ix, :]
        if inner.size == 0:
            inner = cell
        light_ref, dark_ref = _board_base_colors(frame)
        mask = _cell_foreground_mask(inner, light_ref, dark_ref, (r + c) % 2)
        mask64 = _resize_mask(mask, TEMPLATE_SIZE)
        ptype, score = _match_shape(mask64, color)
        if ptype is None or ptype in (chess.PAWN, chess.KING):
            return None
        return ptype

    def _match_move(self, before_grid, after_grid, frame):
        observed = np.zeros((8, 8), dtype=np.int8)
        observed[after_grid & ~before_grid] = 1
        observed[before_grid & ~after_grid] = -1
        if not observed.any():
            return None

        board = self.get_board()
        best_move, best_score = None, -1.0
        tied_moves = []
        observed_changed = max(int(np.abs(observed).sum()), 1)

        for mv in board.legal_moves:
            expected = self._expected_diff(board, mv)
            expected_changed = max(int(np.abs(expected).sum()), 1)
            agree = int(np.sum((expected != 0) & (expected == observed)))
            score = agree / max(expected_changed, observed_changed)
            if score > best_score:
                best_score, best_move = score, mv
                tied_moves = [mv]
            elif score == best_score and best_move is not None and \
                    mv.from_square == best_move.from_square and mv.to_square == best_move.to_square:
                tied_moves.append(mv)

        if best_move is None or best_score < MIN_MATCH_FRACTION:
            return None

        if best_move.promotion is not None and len(tied_moves) > 1:
            resolved_type = self._resolve_promotion(frame, best_move.to_square, board.turn)
            if resolved_type is not None:
                for mv in tied_moves:
                    if mv.promotion == resolved_type:
                        return mv

        return best_move


# ------------------------------------------------------------------ region picker (overlay window)
_OVERLAY_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  html,body{margin:0;padding:0;overflow:hidden;cursor:crosshair;background:#000;}
  #bg{position:absolute;top:0;left:0;width:100%;height:100%;user-select:none;-webkit-user-drag:none;}
  #dim{position:absolute;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.35);pointer-events:none;}
  #sel{position:absolute;border:2px solid #00d4a0;background:rgba(0,212,160,0.12);
       box-shadow:0 0 0 2000px rgba(0,0,0,0.35);display:none;}
  #hint{position:fixed;top:22px;left:50%;transform:translateX(-50%);
       font:600 13px/1.4 -apple-system,Segoe UI,sans-serif;color:#fff;
       background:rgba(0,0,0,0.7);padding:9px 18px;border-radius:9px;letter-spacing:.02em;
       pointer-events:none;white-space:nowrap;}
</style></head>
<body>
  <img id="bg" src="data:image/png;base64,__IMG_B64__" draggable="false">
  <div id="dim"></div>
  <div id="sel"></div>
  <div id="hint">Drag a box tightly around the 8&times;8 board (no labels/borders) &middot; Esc to cancel</div>
  <script>
    let startX = 0, startY = 0, dragging = false;
    const sel = document.getElementById('sel');
    document.addEventListener('mousedown', (e) => {
      dragging = true; startX = e.clientX; startY = e.clientY;
      Object.assign(sel.style, {left: startX+'px', top: startY+'px', width: '0px', height: '0px', display: 'block'});
    });
    document.addEventListener('mousemove', (e) => {
      if (!dragging) return;
      const x = Math.min(e.clientX, startX), y = Math.min(e.clientY, startY);
      const w = Math.abs(e.clientX - startX), h = Math.abs(e.clientY - startY);
      Object.assign(sel.style, {left: x+'px', top: y+'px', width: w+'px', height: h+'px'});
    });
    document.addEventListener('mouseup', (e) => {
      if (!dragging) return;
      dragging = false;
      const x = Math.min(e.clientX, startX), y = Math.min(e.clientY, startY);
      const w = Math.abs(e.clientX - startX), h = Math.abs(e.clientY - startY);
      if (w < 24 || h < 24) { window.pywebview.api.cancel(); return; }
      // The screenshot is a fixed-resolution PNG (real screen pixels) but
      // this window/its mouse events may be reported in OS-scaled logical
      // pixels (Windows display scaling). Correct for that here so the
      // rect we send back always lines up with mss's own pixel space.
      const bg = document.getElementById('bg');
      const scaleX = bg.naturalWidth / window.innerWidth;
      const scaleY = bg.naturalHeight / window.innerHeight;
      window.pywebview.api.submit(x * scaleX, y * scaleY, w * scaleX, h * scaleY);
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') window.pywebview.api.cancel(); });
  </script>
</body></html>"""


class _OverlayBridge:
    def __init__(self, done, result):
        self._done = done
        self._result = result

    def submit(self, x, y, w, h):
        self._result["rect"] = (x, y, w, h)
        self._done.set()

    def cancel(self):
        self._result["rect"] = None
        self._done.set()


def select_region():
    """Blocks the calling thread (safe -- js_api calls already run off the
    main UI thread) until the user drags a region or cancels. Returns
    {"left","top","width","height"} in absolute screen coordinates, or None.
    """
    import webview
    import base64

    with mss() as sct:
        mon = sct.monitors[0]  # 0 = full virtual desktop, spans all monitors
        raw = sct.grab(mon)
    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    done = threading.Event()
    result = {}
    bridge = _OverlayBridge(done, result)

    overlay = webview.create_window(
        "select-region",
        html=_OVERLAY_HTML.replace("__IMG_B64__", img_b64),
        x=mon["left"], y=mon["top"], width=mon["width"], height=mon["height"],
        frameless=True, on_top=True, resizable=False, js_api=bridge,
    )
    overlay.events.closed += lambda: done.set()

    done.wait(timeout=180)
    try:
        overlay.destroy()
    except Exception:
        pass

    rect = result.get("rect")
    if not rect:
        print("[live] region selection cancelled")
        return None
    x, y, w, h = rect
    region = {
        "left": int(mon["left"] + x),
        "top": int(mon["top"] + y),
        "width": int(w),
        "height": int(h),
    }
    print(f"[live] region selected: {region}")
    return region
