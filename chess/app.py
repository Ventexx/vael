"""
vael. chess -- local desktop chess analysis board.

A pywebview window hosts frontend/index.html. All chess logic (legality,
notation, PGN/FEN) is handled here via python-chess; all engine analysis
runs through engine.py, which talks to a locally installed Stockfish binary
over UCI. Results stream back into the page via window.evaluate_js(), so
the UI updates live as the engine thinks.

Run:
    pip install -r requirements.txt
    python app.py
"""

import io
import json
import os
import shutil
import sys

if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception as e:
            print("[startup] could not set DPI awareness (Live mode region selection may be misaligned on scaled displays):", e)

import chess
import chess.pgn
import webview

import capture
from engine import EngineManager

APP_TITLE = "vael. chess"
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".vael_chess_settings.json")

window = None  # set once webview.create_window() runs


def load_settings():
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_settings(data):
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print("[settings] could not save:", e)


def find_stockfish():
    """Best-effort auto-detect of a locally installed Stockfish binary, used
    so the engine can connect on startup even if the user never pointed the
    app at one explicitly."""
    found = shutil.which("stockfish") or shutil.which("stockfish.exe")
    if found:
        return found
    candidates = [
        "/usr/local/bin/stockfish",
        "/usr/bin/stockfish",
        "/opt/homebrew/bin/stockfish",
        "C:\\Program Files\\Stockfish\\stockfish.exe",
        "C:\\Program Files (x86)\\Stockfish\\stockfish.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


class Api:
    def __init__(self):
        self.root_fen = chess.STARTING_FEN  # position the master line starts from
        self.moves = []                     # master line: list[chess.Move], from root_fen
        self.ply = 0                        # current viewing position = moves[:ply] applied to root
        self.engine_mgr = EngineManager(self._push_info)
        self.settings = load_settings()
        self.live_active = False
        self.live_watcher = capture.LiveWatcher(
            self._board, self._on_live_move, self._on_live_status, self._on_live_resync
        )

    # ------------------------------------------------------------ board helpers
    def _board(self):
        b = chess.Board(self.root_fen)
        for mv in self.moves[: self.ply]:
            b.push(mv)
        return b

    def _san_history(self):
        b = chess.Board(self.root_fen)
        out = []
        for mv in self.moves:
            out.append(b.san(mv))
            b.push(mv)
        return out

    def _restart_analysis(self):
        if self.engine_mgr.is_connected():
            self.engine_mgr.analyze(self._board())

    # ------------------------------------------------------------ push helpers (Python -> JS)
    def _push_info(self, payload):
        if window is None:
            return
        try:
            window.evaluate_js("window.onEngineInfo && window.onEngineInfo(%s)" % json.dumps(payload))
        except Exception as e:
            print("[push] engine info failed:", e)

    def _push_engine_status(self):
        if window is None:
            return
        payload = {
            "connected": self.engine_mgr.is_connected(),
            "identity": self.engine_mgr.identity(),
        }
        try:
            window.evaluate_js("window.onEngineStatus && window.onEngineStatus(%s)" % json.dumps(payload))
        except Exception as e:
            print("[push] engine status failed:", e)

    # ------------------------------------------------------------ state (JS -> Python calls)
    def get_state(self):
        b = self._board()
        status = None
        if b.is_checkmate():
            status = "checkmate"
        elif b.is_stalemate():
            status = "stalemate"
        elif b.is_insufficient_material():
            status = "insufficient_material"
        elif b.can_claim_threefold_repetition():
            status = "threefold_repetition"
        elif b.can_claim_fifty_moves():
            status = "fifty_move_rule"

        return {
            "fen": b.fen(),
            "turn": "w" if b.turn == chess.WHITE else "b",
            "in_check": b.is_check(),
            "game_over": b.is_game_over(claim_draw=True),
            "status": status,
            "moves_san": self._san_history(),
            "moves_uci": [m.uci() for m in self.moves],
            "ply": self.ply,
            "total_plies": len(self.moves),
            "last_move": self.moves[self.ply - 1].uci() if self.ply > 0 else None,
            "fullmove_number": b.fullmove_number,
        }

    def legal_moves(self):
        b = self._board()
        out = {}
        for mv in b.legal_moves:
            out.setdefault(chess.square_name(mv.from_square), []).append(
                {
                    "to": chess.square_name(mv.to_square),
                    "uci": mv.uci(),
                    "promotion": chess.piece_symbol(mv.promotion) if mv.promotion else None,
                }
            )
        return out

    def _bundle(self, extra=None):
        out = {"state": self.get_state(), "legal_moves": self.legal_moves()}
        if extra:
            out.update(extra)
        return out

    # ------------------------------------------------------------ moves
    def make_move(self, uci):
        if self.live_active:
            return {"ok": False, "error": "live_active"}
        try:
            mv = chess.Move.from_uci(uci)
        except Exception:
            return {"ok": False, "error": "bad_uci"}
        b = self._board()
        if mv not in b.legal_moves:
            return {"ok": False, "error": "illegal"}
        self.moves = self.moves[: self.ply] + [mv]
        self.ply += 1
        self._restart_analysis()
        return {"ok": True, **self._bundle()}

    def go_to_ply(self, ply):
        if self.live_active:
            return self._bundle()
        ply = max(0, min(len(self.moves), int(ply)))
        self.ply = ply
        self._restart_analysis()
        return self._bundle()

    def step(self, delta):
        return self.go_to_ply(self.ply + int(delta))

    def new_game(self):
        self._stop_live_if_active()
        self.root_fen = chess.STARTING_FEN
        self.moves = []
        self.ply = 0
        self._restart_analysis()
        return self._bundle()

    def set_fen(self, fen):
        try:
            b = chess.Board(fen)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        self._stop_live_if_active()
        self.root_fen = b.fen()
        self.moves = []
        self.ply = 0
        self._restart_analysis()
        return {"ok": True, **self._bundle()}

    def import_pgn(self, pgn_text):
        try:
            game = chess.pgn.read_game(io.StringIO(pgn_text))
            if game is None:
                return {"ok": False, "error": "No game found in PGN text."}
            root = game.board()  # honors a [FEN] header if present, else standard start
            mvs = list(game.mainline_moves())
        except Exception as e:
            return {"ok": False, "error": str(e)}
        self._stop_live_if_active()
        self.root_fen = root.fen()
        self.moves = mvs
        self.ply = len(mvs)
        self._restart_analysis()
        return {"ok": True, **self._bundle()}

    def export_pgn(self):
        game = chess.pgn.Game()
        game.headers["Event"] = "vael. chess"
        game.headers["Site"] = "local"
        if self.root_fen != chess.STARTING_FEN:
            game.headers["FEN"] = self.root_fen
            game.headers["SetUp"] = "1"
            game.setup(chess.Board(self.root_fen))
        node = game
        for mv in self.moves:
            node = node.add_variation(mv)
        exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
        return game.accept(exporter)

    # ------------------------------------------------------------ engine
    def connect_engine(self, path):
        try:
            ident = self.engine_mgr.connect(path)
        except Exception as e:
            self._push_engine_status()
            return {"ok": False, "error": str(e)}
        self.settings["engine_path"] = path
        save_settings(self.settings)
        self._restart_analysis()
        self._push_engine_status()
        return {"ok": True, "identity": ident, "options": self.engine_mgr.available_options()}

    def disconnect_engine(self):
        self.engine_mgr.disconnect()
        self._push_engine_status()
        return {"ok": True}

    def engine_status(self):
        return {
            "connected": self.engine_mgr.is_connected(),
            "identity": self.engine_mgr.identity(),
            "path": self.engine_mgr.engine_path,
            "options": self.engine_mgr.options,
        }

    def set_engine_options(self, options):
        self.engine_mgr.set_options(**options)
        self.settings["engine_options"] = self.engine_mgr.options
        save_settings(self.settings)
        self._restart_analysis()
        return {"ok": True}

    def get_saved_settings(self):
        return self.settings

    # ------------------------------------------------------------ live mode (screen-region board reader)
    def start_live(self, flipped, mode="continuous"):
        """Opens the region-selection overlay (blocks until the user drags a
        box or cancels), then starts watching that region for board changes
        and mirroring them into the game.

        mode="continuous" (default) polls the region on a timer, same as
        before. mode="manual" assigns the region but does nothing further
        until capture_live_now() is called -- useful when a piece skin or
        overlay makes continuous auto-recognition unreliable, or when you'd
        rather control exactly when a read happens.

        Live calibrates against whatever position is currently loaded here
        -- it works starting from any position, not just a fresh game --
        but it has no way to independently know what's actually on screen.
        To join a game already in progress: paste the FEN/PGN from the
        source into Import first, *then* start Live, so the seed position
        actually matches what's on screen."""
        if self.live_watcher.active:
            return {"ok": False, "error": "already_active"}
        region = capture.select_region()
        if not region:
            return {"ok": False, "error": "cancelled"}
        self.live_active = True
        self.live_watcher.start(region, bool(flipped), mode=mode)
        return {"ok": True}

    def capture_live_now(self):
        """Manual mode only: take one screenshot of the assigned region
        right now and update the game from it."""
        if not self.live_watcher.active:
            return {"ok": False, "error": "not_active"}
        if self.live_watcher.mode != "manual":
            return {"ok": False, "error": "not_manual_mode"}
        self.live_watcher.trigger_capture()
        return {"ok": True}

    def stop_live(self):
        self.live_watcher.stop()
        self.live_active = False
        self._push_live_status({"live": False})
        return {"ok": True}

    def _stop_live_if_active(self):
        if self.live_watcher.active:
            self.live_watcher.stop()
            self.live_active = False
            self._push_live_status({"live": False})

    def _on_live_move(self, move):
        """Runs on the watcher's background thread -- applies a detected
        move the same way make_move() would, then pushes the new state."""
        b = self._board()
        if move not in b.legal_moves:
            return
        self.moves = self.moves[: self.ply] + [move]
        self.ply += 1
        self._restart_analysis()
        if window is None:
            return
        try:
            window.evaluate_js("window.onLiveMove && window.onLiveMove(%s)" % json.dumps(self._bundle()))
        except Exception as e:
            print("[live] push move failed:", e)

    def _on_live_resync(self, fen, confidence):
        """Runs on the watcher's background thread -- called when a
        full-board rescan finds the screen no longer matches the tracked
        position closely enough to explain with a move or two (including
        right when Live starts, if it was pointed at a game already in
        progress). Adopts the rescanned position as the new starting point
        for the master line, the same way Import/set_fen would."""
        try:
            b = chess.Board(fen)
        except Exception as e:
            print("[live] resync FEN was invalid, ignoring:", e)
            return
        self.root_fen = b.fen()
        self.moves = []
        self.ply = 0
        self._restart_analysis()
        if window is None:
            return
        try:
            window.evaluate_js(
                "window.onLiveResync && window.onLiveResync(%s)"
                % json.dumps({**self._bundle(), "confidence": confidence})
            )
        except Exception as e:
            print("[live] push resync failed:", e)

    def _on_live_status(self, payload):
        if not payload.get("live", True):
            self.live_active = False
        self._push_live_status(payload)

    def _push_live_status(self, payload):
        if window is None:
            return
        try:
            window.evaluate_js("window.onLiveStatus && window.onLiveStatus(%s)" % json.dumps(payload))
        except Exception as e:
            print("[push] live status failed:", e)

    def pick_engine_file(self):
        """Open a native file picker for the Stockfish binary."""
        if window is None:
            return None
        result = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False)
        if result:
            return result[0]
        return None

    # ------------------------------------------------------------ window controls (custom titlebar)
    def minimize_window(self):
        if window is not None:
            window.minimize()

    def toggle_maximize_window(self):
        if window is not None:
            # pywebview has no cross-platform maximize/restore toggle; a
            # borderless fullscreen toggle is the closest equivalent for a
            # frameless window and works consistently across backends.
            window.toggle_fullscreen()

    def close_window(self):
        self.live_watcher.stop()
        if window is not None:
            window.destroy()

    def get_window_geometry(self):
        """Current size, used by the JS-side edge/corner resize handles as
        the starting point for a drag (frameless windows have no native
        resize grips, so we implement it ourselves)."""
        if window is None:
            return {"width": 0, "height": 0}
        return {"width": window.width, "height": window.height}

    def resize_window(self, width, height, fix_point):
        """fix_point is a string like 'NORTH|WEST' naming the corner that
        should stay put while the opposite edge/corner moves."""
        if window is None:
            return
        width = max(1040, int(width))
        height = max(680, int(height))
        fp = None
        for name in fix_point.split("|"):
            name = name.strip().upper()
            part = getattr(webview.window.FixPoint, name, None)
            if part is not None:
                fp = part if fp is None else (fp | part)
        try:
            if fp is not None:
                window.resize(width, height, fix_point=fp)
            else:
                window.resize(width, height)
        except Exception as e:
            print("[resize] failed:", e)


def main():
    global window
    api = Api()

    # easy_drag=True (pywebview's default) makes the ENTIRE frameless window
    # draggable from any point, completely ignoring page CSS. We only want
    # #titlebar to be draggable, so we turn it off and instead rely on
    # pywebview's own "pywebview-drag-region" class mechanism (see
    # #titlebar in index.html). DRAG_REGION_DIRECT_TARGET_ONLY makes sure
    # only the titlebar element itself starts a drag, not its buttons/logo.
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True

    index_path = os.path.join(FRONTEND_DIR, "index.html")
    window = webview.create_window(
        APP_TITLE,
        url=index_path,
        js_api=api,
        width=1360,
        height=860,
        min_size=(1040, 680),
        background_color="#0a0a0a",
        frameless=True,
        easy_drag=False,
        resizable=True,
    )

    def on_shown():
        saved = api.settings.get("engine_path")
        path = saved if saved and os.path.exists(saved) else find_stockfish()
        try:
            if path:
                api.connect_engine(path)
            else:
                print("[startup] no Stockfish binary found; engine left disconnected.")
                api._push_engine_status()
        except Exception as e:
            print("[startup] engine auto-connect failed:", e)

    window.events.shown += on_shown
    webview.start(debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
