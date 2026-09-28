"""Authenticated, loopback-only receiver for the opt-in browser companion.

No image classification: accept a complete FEN or reconstruct legal notation
and require an exact match with the site's logical piece squares.
"""
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chess

PORT = 18765


def resolve_position(payload, current):
    fen = payload.get("fen")
    if fen:
        if not isinstance(fen, str) or len(fen.split()) != 6:
            raise ValueError("The browser did not provide a complete position.")
        board = chess.Board(fen)
        if not board.is_valid():
            raise ValueError("Waiting for a valid board position.")
        return board
    pieces = payload.get("pieces")
    if not isinstance(pieces, dict) or not 2 <= len(pieces) <= 32:
        raise ValueError("No complete board found. Open a game or analysis board.")
    observed = chess.Board(None)
    for square, symbol in pieces.items():
        if not isinstance(symbol, str) or symbol not in "pnbrqkPNBRQK" or len(symbol) != 1:
            raise ValueError("Unrecognized piece data.")
        observed.set_piece_at(chess.parse_square(square), chess.Piece.from_symbol(symbol))
    placement = observed.board_fen()
    # Replay the complete notation. Match the displayed ply, including takebacks.
    sans = payload.get("sans", [])
    if not isinstance(sans, list) or len(sans) > 2000:
        raise ValueError("Invalid move history.")
    replay = chess.Board()
    matches = []
    if replay.board_fen() == placement:
        matches.append(replay.copy())
    for san in sans:
        try:
            replay.push_san(san)
        except (ValueError, TypeError):
            break
        if replay.board_fen() == placement:
            matches.append(replay.copy())
    if matches:
        return matches[-1]
    # Imported custom FENs retain their turn, castling and en-passant rights.
    if current.board_fen() == placement:
        return current.copy()
    candidates = []
    for move in current.legal_moves:
        candidate = current.copy()
        candidate.push(move)
        if candidate.board_fen() == placement:
            candidates.append(candidate)
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError("Open the game's move list, or import its FEN once to sync this position.")


class BrowserLive:
    def __init__(self, on_position, on_status):
        self.on_position = on_position
        self.on_status = on_status
        self.server = None
        self.token = None
        self.session = None
        self.last_seen = 0
        self.lock = threading.RLock()
        self.stop_event = threading.Event()

    @property
    def active(self):
        return self.server is not None

    def start(self, token=None, session=None):
        self.stop()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.connection.settimeout(3)
                status, result = 200, {"ok": True}
                try:
                    if self.path != "/position":
                        raise ValueError("Unknown endpoint.")
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 65536:
                        raise ValueError("Invalid message size.")
                    with owner.lock:
                        supplied = self.headers.get("Authorization", "")
                        if not owner.token or not secrets.compare_digest(supplied, "Bearer " + owner.token):
                            status = 403
                            raise ValueError("Pairing code expired. Start Live again and copy the new code.")
                        payload = json.loads(self.rfile.read(length))
                        if not isinstance(payload, dict):
                            raise ValueError("Invalid message.")
                        session = payload.get("session")
                        if not isinstance(session, str) or not 1 <= len(session) <= 100:
                            raise ValueError("Missing browser session.")
                        if owner.session and owner.session != session:
                            status = 409
                            raise ValueError("Another tab is connected. Stop Live before switching tabs.")
                        if payload.get("source") not in ("lichess.org", "chess.com"):
                            raise ValueError("Open Lichess or Chess.com.")
                        owner.session = session
                        owner.last_seen = time.monotonic()
                        if payload.get("error"):
                            raise ValueError(str(payload["error"])[:240])
                        owner.on_position(payload)
                except (ValueError, TypeError, KeyError) as exc:
                    status = status if status != 200 else 422
                    result = {"ok": False, "error": str(exc)}
                    if status == 422 and owner.session:
                        owner.on_status({"live": True, "mode": "browser", "warning": str(exc)})
                except Exception:
                    status, result = 500, {"ok": False, "error": "Could not update the board. Reconnect Live."}
                data = json.dumps(result).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        server.daemon_threads = True
        self.server = server
        self.token = token or secrets.token_hex(16)
        self.session = session
        self.last_seen = time.monotonic()
        self.stop_event = threading.Event()
        event = self.stop_event
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def monitor():
            while not event.wait(2):
                with self.lock:
                    if not event.is_set() and time.monotonic() - self.last_seen > 8:
                        self.on_status({"live": True, "mode": "browser", "warning":
                            "Browser disconnected or paused. Reopen the connected tab to resume." if self.session else
                            "Waiting for browser. Enter the pairing code in the Vael extension."})
        threading.Thread(target=monitor, daemon=True).start()
        return self.token

    def stop(self):
        with self.lock:
            self.stop_event.set()
            server, self.server = self.server, None
            self.token = None
            self.session = None
        if server:
            server.shutdown()
            server.server_close()
