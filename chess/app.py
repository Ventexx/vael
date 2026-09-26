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
import secrets
import threading
import time

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
from browser_live import BrowserLive, resolve_position
from engine import EngineManager
from study import Study, SessionStore
from review import GameReview

APP_TITLE = "vael. chess"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(SCRIPT_DIR, "frontend")
SETTINGS_PATH = os.path.join(SCRIPT_DIR, ".vael_chess_settings.json")
SESSION_PATH = os.path.join(SCRIPT_DIR, ".vael_chess_session.json")
# .ico isn't reliably supported outside Windows, so use icon.png on Linux/macOS.
ICON_PATH = os.path.join(SCRIPT_DIR, "icon.ico" if sys.platform == "win32" else "icon.png")

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
    def __init__(self, session_path=None):
        self.state_lock = threading.RLock()
        self.study = Study()
        self.session_store = SessionStore(session_path or SESSION_PATH)
        self.view_preferences = {"flipped": False, "show_arrows": True}
        self.recovery_note = None
        self.review_result = {"status": "idle", "rows": [], "points": []}
        self.review_job = 0
        self.reviewer = GameReview(self._on_review)
        self.engine_mgr = EngineManager(self._push_info)
        self.settings = load_settings()
        self.live_active = False
        self.live_paused = False
        self.live_board = None
        self.live_source = None
        self.live_updated = None
        self.live_status = {"live": False}
        recovered = self.session_store.load()
        if recovered:
            self.study = Study.restore(recovered["study"])
            self.view_preferences.update(recovered.get("view", {}))
            self.review_result = recovered.get("review", self.review_result)
            if self.review_result.get("status") == "running":
                self.review_result["status"] = "cancelled"
            self.recovery_note = "Previous session restored."
            if recovered.get("live_study"):
                try:
                    self.live_board = Study.restore(recovered["live_study"]).node.board()
                    self.live_paused = bool(recovered.get("live_paused"))
                except (ValueError, KeyError, TypeError):
                    pass
        self.browser_live = BrowserLive(self._on_browser_position, self._push_live_status)
        self.live_watcher = capture.LiveWatcher(
            self._board, self._on_live_move, self._on_live_status, self._on_live_resync
        )

    # ------------------------------------------------------------ board helpers
    @property
    def root_fen(self):
        return self.study.root_fen

    @property
    def moves(self):
        return self.study.line

    @property
    def ply(self):
        return len(self.study.path)

    def _board(self):
        return self.study.node.board()

    def _save_session(self):
        with self.state_lock:
            self._write_session()

    def _write_session(self):
        live_study = None
        if self.live_board is not None:
            live_study = Study(self.live_board.root().fen())
            live_study.merge_board(self.live_board)
        try:
            self.session_store.save({"study": self.study.snapshot(), "view": self.view_preferences,
                "review": self.review_result, "live_paused": self.live_paused,
                "live_study": live_study.snapshot() if live_study else None})
        except OSError as exc:
            self.recovery_note = "Session could not be saved: " + str(exc)

    def set_view_preferences(self, preferences):
        with self.state_lock:
            for key in ("flipped", "show_arrows"):
                if key in preferences:
                    self.view_preferences[key] = bool(preferences[key])
            self._save_session()
        return {"ok": True}

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
            "selected_node": " ".join(m.uci() for m in self.study.path),
            "notation": self.study.notation(),
            "view": self.view_preferences,
            "recovery_note": self.recovery_note,
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
        with self.state_lock:
            out = {"state": self.get_state(), "legal_moves": self.legal_moves()}
            if extra:
                out.update(extra)
            return out

    # ------------------------------------------------------------ moves
    def make_move(self, uci):
        if self.live_active and not self.live_paused:
            return {"ok": False, "error": "live_active"}
        try:
            mv = chess.Move.from_uci(uci)
        except Exception:
            return {"ok": False, "error": "bad_uci"}
        b = self._board()
        if mv not in b.legal_moves:
            return {"ok": False, "error": "illegal"}
        with self.state_lock:
            self.study.play(mv)
            self._invalidate_review_if_changed()
            self._save_session()
            self._restart_analysis()
            return {"ok": True, **self._bundle()}

    def go_to_ply(self, ply):
        if self.live_active and not self.live_paused:
            return self._bundle()
        ply = max(0, min(len(self.moves), int(ply)))
        line = self.moves[:]
        self.study.select(" ".join(m.uci() for m in line[:ply]))
        self.study.line = line
        self._save_session()
        self._restart_analysis()
        return self._bundle()

    def step(self, delta):
        return self.go_to_ply(self.ply + int(delta))

    def go_to_node(self, identifier):
        with self.state_lock:
            if self.live_active and not self.live_paused:
                return self._bundle()
            try:
                self.study.select(identifier)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            self._save_session()
            self._restart_analysis()
            return self._bundle()

    def new_game(self):
        self._stop_live_if_active()
        self.study = Study()
        self.live_board = None
        self._clear_review()
        self._save_session()
        self._restart_analysis()
        return self._bundle()

    def set_fen(self, fen):
        try:
            b = chess.Board(fen)
            if not b.is_valid():
                raise ValueError("This is not a valid chess position.")
        except Exception as e:
            return {"ok": False, "error": str(e)}
        self._stop_live_if_active()
        self.study = Study(b.fen())
        self.live_board = None
        self._clear_review()
        self._save_session()
        self._restart_analysis()
        return {"ok": True, **self._bundle()}

    def import_pgn(self, pgn_text):
        try:
            study = Study.from_pgn(pgn_text)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        self._stop_live_if_active()
        self.study = study
        self.live_board = None
        self._clear_review()
        self._save_session()
        self._restart_analysis()
        return {"ok": True, **self._bundle()}

    def export_pgn(self):
        return self.study.export()

    # ------------------------------------------------------------ game review
    def _review_signature(self):
        return self.root_fen + "|" + " ".join(m.uci() for m in self.study.game.mainline_moves())

    def _clear_review(self):
        self.reviewer.cancel()
        self.review_job += 1
        self.review_result = {"status": "idle", "rows": [], "points": []}

    def _invalidate_review_if_changed(self):
        if self.review_result.get("signature") not in (None, self._review_signature()):
            self._clear_review()

    def get_review(self):
        self._invalidate_review_if_changed()
        return self.review_result

    def start_review(self):
        with self.state_lock:
            if self.live_active:
                return {"ok": False, "error": "Stop Live before reviewing the game."}
            path = self.engine_mgr.engine_path or self.settings.get("engine_path")
            moves = list(self.study.game.mainline_moves())
            if not path or not os.path.isfile(path):
                return {"ok": False, "error": "Connect a local engine in Engine settings first."}
            if not moves:
                return {"ok": False, "error": "Play or import a game to review."}
            self._clear_review()
            self.review_result = {"version": 2, "job_id": self.review_job, "status": "running", "rows": [], "points": [], "completed": 0,
                "total": len(moves), "signature": self._review_signature()}
            players = {color: self.study.game.headers.get(key, fallback) for color, key, fallback in
                       (("w", "White", "White"), ("b", "Black", "Black"))}
            players = {key: (value if value and value != "?" else ("White" if key == "w" else "Black")) for key, value in players.items()}
            self.reviewer.start(path, self.root_fen, moves, self.review_job, players)
            return {"ok": True, "review": self.review_result}

    def review_position(self, ply, line="before", step=0):
        """Return an ephemeral board; never mutate the study or its saved cursor."""
        with self.state_lock:
            if self.live_active:
                return {"error": "Stop Live before inspecting a review."}
            if self.review_result.get("signature") != self._review_signature():
                return {"error": "The game changed. Analyse it again."}
            try:
                row = self.review_result["rows"][int(ply) - 1]
                if int(ply) < 1 or row["ply"] != int(ply) or line not in ("before", "played", "best"):
                    raise ValueError()
                board = chess.Board(row["fen"])
                continuation = row.get(line + "_line", []) if line != "before" else []
                step = max(0, min(int(step), len(continuation)))
                for item in continuation[:step]:
                    board.push_uci(item["uci"])
                state = self.get_state()
                state.update(fen=board.fen(), turn="w" if board.turn else "b", in_check=board.is_check(),
                    game_over=board.is_game_over(), status="checkmate" if board.is_checkmate() else "draw" if board.is_game_over() else None,
                    last_move=board.peek().uci() if board.move_stack else None,
                    ply=int(ply) - 1 + step, fullmove_number=board.fullmove_number,
                    total_plies=len(list(self.study.game.mainline_moves())), selected_node=row["before_id"],
                    recovery_note="Review preview · your game and variations are unchanged")
                return {"state": state, "legal_moves": {}, "step": step}
            except (KeyError, IndexError, ValueError, TypeError):
                return {"error": "This review needs to be analysed again before its lines can be previewed."}

    def cancel_review(self):
        with self.state_lock:
            self.reviewer.cancel()
            self.review_job += 1
            self.review_result = {**self.review_result, "job_id": self.review_job, "status": "cancelled"}
            self._save_session()
        return self.review_result

    def _on_review(self, result):
        with self.state_lock:
            if result["job_id"] != self.review_job:
                return
            self.review_result = {**result, "signature": self._review_signature()}
            if result["status"] != "running":
                self._save_session()
            if window is not None:
                window.evaluate_js("window.onReview && window.onReview(%s)" % json.dumps(self.review_result))

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

    # ------------------------------------------------------------ live mode
    def start_live(self, flipped=False, mode="browser"):
        """Pair a browser tab by default; screen modes are experimental.

        Browser metadata supplies full FEN or notation plus logical squares.
        Custom positions without either need an imported FEN as their seed.
        Screen modes open a region picker and use piece template matching.
        """
        if self.live_active:
            return {"ok": False, "error": "already_active"}
        if mode == "browser":
            try:
                token = self.settings.get("browser_pair_token") or secrets.token_hex(16)
                token = self.browser_live.start(token, self.settings.get("browser_session"))
            except OSError:
                return {"ok": False, "error": "The browser connection port is in use. Close any other Vael Chess window and retry."}
            self.live_active = True
            if self.live_board is not None and not self.live_paused:
                with self.state_lock:
                    self._adopt_live_board()
            self.settings.update(browser_pair_token=token, live_enabled=True)
            save_settings(self.settings)
            self._push_live_status({"live": True, "mode": "browser", "info": "Waiting for your browser tab. Use the pairing code to connect."})
            return {"ok": True, "token": token, "paired": bool(self.settings.get("browser_session")), "extension_path": os.path.join(SCRIPT_DIR, "browser-extension")}
        if mode not in ("continuous", "manual"):
            return {"ok": False, "error": "Unknown Live mode."}
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
        self.browser_live.stop()
        self.live_watcher.stop()
        self.live_active = False
        self.live_paused = False
        self.settings["live_enabled"] = False
        save_settings(self.settings)
        self._save_session()
        self._push_live_status({"live": False})
        return {"ok": True}

    def _stop_live_if_active(self):
        if self.live_active:
            self.stop_live()

    def _on_browser_position(self, payload):
        with self.state_lock:
            current = self.live_board if self.live_board is not None else self._board()
            board = resolve_position(payload, current)
            if board.fen() == current.fen():
                board = current.copy()
            elif not board.move_stack:
                for move in current.legal_moves:
                    candidate = current.copy()
                    candidate.push(move)
                    if candidate.fen() == board.fen():
                        board = candidate
                        break
            changed = self.live_board is None or board.fen() != self.live_board.fen()
            self.live_board = board.copy()
            self.live_source = payload["source"]
            self.live_updated = time.time()
            if self.settings.get("browser_session") != payload.get("session"):
                self.settings["browser_session"] = payload.get("session")
                save_settings(self.settings)
            if changed:
                self._adopt_live_board()
                self._save_session()
            self._push_live_status({"live": True, "mode": "browser", "info":
                "Exploring locally · incoming moves are saved" if self.live_paused else "Board synced", "low_confidence": False})

    def _adopt_live_board(self):
        if self.live_board is None:
            return
        if self.live_board.root().fen() == self.root_fen:
            self.study.merge_board(self.live_board, select=not self.live_paused)
        elif not self.live_paused:
            self.study = Study(self.live_board.root().fen())
            self.study.merge_board(self.live_board)
        self._invalidate_review_if_changed()
        if not self.live_paused:
            self._restart_analysis()
            if window is not None:
                window.evaluate_js("window.onLiveMove && window.onLiveMove(%s)" % json.dumps(self._bundle()))

    def get_live_status(self):
        return {**self.live_status, "live": self.live_active, "paused": self.live_paused,
            "source": self.live_source, "updated": self.live_updated,
            "token": self.settings.get("browser_pair_token", ""),
            "extension_path": os.path.join(SCRIPT_DIR, "browser-extension")}

    def pause_live(self):
        if not self.live_active or not self.browser_live.active:
            return {"ok": False, "error": "Pause & explore is available for a connected browser board."}
        with self.state_lock:
            self.live_paused = True
            self._save_session()
            self._push_live_status({"live": True, "mode": "browser", "info": "Exploring locally · incoming moves are saved"})
            return self._bundle()

    def resume_live(self):
        with self.state_lock:
            if self.live_board is None:
                return {"ok": False, "error": "Waiting for the browser's first position."}
            self.live_paused = False
            self._adopt_live_board()
            self._save_session()
            self._push_live_status({"live": self.live_active, "mode": "browser", "info": "Returned to the latest browser position"})
            return self._bundle()

    def switch_live_tab(self):
        # Release ownership explicitly; the extension's Connect action claims
        # the chosen new tab. Existing tabs cannot race to reclaim ownership.
        self.stop_live()
        self.settings.pop("browser_session", None)
        self.settings["browser_pair_token"] = secrets.token_hex(16)
        self.live_board = None
        save_settings(self.settings)
        return self.start_live(mode="browser")

    def _on_live_move(self, move):
        """Runs on the watcher's background thread -- applies a detected
        move the same way make_move() would, then pushes the new state."""
        b = self._board()
        if move not in b.legal_moves:
            return
        self.study.play(move)
        self._invalidate_review_if_changed()
        self._save_session()
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
            if not b.is_valid():
                raise ValueError("Invalid recognized position")
        except Exception as e:
            print("[live] resync FEN was invalid, ignoring:", e)
            return
        self.study = Study(b.fen())
        self._clear_review()
        self._save_session()
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
        payload = {**payload, "paused": self.live_paused, "source": self.live_source, "updated": self.live_updated}
        self.live_status = payload
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
        self.shutdown()
        if window is not None:
            window.destroy()

    def shutdown(self):
        self.reviewer.cancel()
        self.browser_live.stop()
        self.live_watcher.stop()
        with self.state_lock:
            self._save_session()
        self.engine_mgr.disconnect()

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
        if api.settings.get("live_enabled"):
            api.start_live(mode="browser")
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
    window.events.closed += api.shutdown

    start_kwargs = {"debug": "--debug" in sys.argv}
    if os.path.exists(ICON_PATH):
        start_kwargs["icon"] = ICON_PATH
    else:
        print(f"[startup] icon not found at {ICON_PATH}; using default icon.")
    webview.start(**start_kwargs)


if __name__ == "__main__":
    main()
