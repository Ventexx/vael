"""
engine.py -- thin wrapper around python-chess's UCI engine client that runs
continuous background analysis on a position and streams incremental
updates (one per depth / multipv line) back to a callback.

Only one analysis run is ever active at a time. Calling analyze() again
invalidates the previous search and sends UCI "stop" without waiting for
the worker. Engine startup and search transitions happen in background threads.
Every emitted result carries a "gen" (generation) counter so a caller can discard stale
messages that were in flight when the position changed.
"""

import threading
from .engine_process import engine_process_options
import chess
import chess.engine


class EngineManager:
    def __init__(self, push_callback):
        self.push_callback = push_callback  # push_callback(dict) -> None
        self.engine = None
        self.engine_path = None
        self.lock = threading.RLock()
        self.analysis_thread = None
        self.search_lock = threading.RLock()
        self.active_analysis = None
        self.generation = 0

        self.options = {
            "multipv": 3,
            "skill_level": 20,          # 0-20, only used when use_limit_strength is False
            "use_limit_strength": False,
            "elo": 1500,                # 1320-3190ish, only used when use_limit_strength is True
            "threads": 1,
            "hash_mb": 128,
            "depth_limit": None,        # None => no depth cap
            "movetime_ms": None,        # None => no per-move time cap (infinite analysis)
        }

    # ------------------------------------------------------------ lifecycle
    def connect(self, path):
        with self.lock:
            self._disconnect_locked()
            self.engine = chess.engine.SimpleEngine.popen_uci(path, **engine_process_options())
            self.engine_path = path
            self._apply_options_locked()
            return dict(self.engine.id)

    def disconnect(self):
        self.stop()
        with self.lock:
            self._disconnect_locked()

    def _disconnect_locked(self):
        if self.engine:
            try:
                self.engine.quit()
            except Exception:
                pass
            self.engine = None

    def is_connected(self):
        return self.engine is not None

    def identity(self):
        if not self.engine:
            return None
        return dict(self.engine.id)

    def available_options(self):
        if not self.engine:
            return []
        return list(self.engine.options.keys())

    # ------------------------------------------------------------ options
    def set_options(self, **kwargs):
        with self.lock:
            self.options.update({k: v for k, v in kwargs.items() if k in self.options})
            self._apply_options_locked()

    def _apply_options_locked(self):
        if not self.engine:
            return
        avail = self.engine.options
        cfg = {}
        if "Threads" in avail:
            cfg["Threads"] = int(self.options["threads"])
        if "Hash" in avail:
            cfg["Hash"] = int(self.options["hash_mb"])
        if self.options["use_limit_strength"] and "UCI_LimitStrength" in avail:
            cfg["UCI_LimitStrength"] = True
            if "UCI_Elo" in avail:
                lo = avail["UCI_Elo"].min or 1320
                hi = avail["UCI_Elo"].max or 3190
                cfg["UCI_Elo"] = max(lo, min(hi, int(self.options["elo"])))
        else:
            if "UCI_LimitStrength" in avail:
                cfg["UCI_LimitStrength"] = False
            if "Skill Level" in avail:
                cfg["Skill Level"] = max(0, min(20, int(self.options["skill_level"])))
        if cfg:
            try:
                self.engine.configure(cfg)
            except Exception as e:
                print("[engine] configure warning:", e)

    # ------------------------------------------------------------ analysis
    def stop(self):
        # Never join a worker on the board's request path. It may be waiting
        # for engine output or for the webview to consume its last callback.
        with self.search_lock:
            self.generation += 1
            analysis = self.active_analysis
            self.active_analysis = None
            self.analysis_thread = None
            if analysis is not None:
                try:
                    analysis.stop()
                except chess.engine.EngineTerminatedError:
                    pass

    def analyze(self, board: chess.Board):
        with self.search_lock:
            self.stop()
            if not self.engine:
                return
            gen = self.generation
            self.analysis_thread = threading.Thread(
                target=self._run_analysis, args=(board.copy(), gen), daemon=True
            )
            self.analysis_thread.start()

    def _run_analysis(self, board, gen):
        if board.is_game_over(claim_draw=True):
            self.push_callback({"type": "gameover", "gen": gen})
            return
        limit_kwargs = {}
        if self.options["movetime_ms"]:
            limit_kwargs["time"] = self.options["movetime_ms"] / 1000.0
        elif self.options["depth_limit"]:
            limit_kwargs["depth"] = int(self.options["depth_limit"])
        limit = chess.engine.Limit(**limit_kwargs) if limit_kwargs else None
        multipv = max(1, int(self.options["multipv"]))

        try:
            with self.lock:
                if not self.engine or gen != self.generation:
                    return
                analysis = self.engine.analysis(board, limit=limit, multipv=multipv)
            with analysis:
                with self.search_lock:
                    if gen != self.generation:
                        return
                    self.active_analysis = analysis
                for info in analysis:
                    if gen != self.generation:
                        break
                    self._emit(board, info, gen)
        except chess.engine.EngineTerminatedError:
            pass
        except Exception as e:
            print("[engine] analysis error:", e)
        finally:
            with self.search_lock:
                if gen == self.generation:
                    self.active_analysis = None

    def _emit(self, board, info, gen):
        pv = info.get("pv")
        if not pv:
            return
        temp = board.copy()
        san_moves = []
        for mv in pv[:14]:
            if mv not in temp.legal_moves:
                break
            san_moves.append(temp.san(mv))
            temp.push(mv)
        if not san_moves:
            return

        score = info.get("score")
        cp, mate = None, None
        if score is not None:
            pov = score.white()  # always report from White's perspective; UI flips as needed
            if pov.is_mate():
                mate = pov.mate()
            else:
                cp = pov.score()

        payload = {
            "type": "info",
            "fen": board.fen(),
            "gen": gen,
            "multipv": info.get("multipv", 1),
            "depth": info.get("depth"),
            "seldepth": info.get("seldepth"),
            "nodes": info.get("nodes"),
            "nps": info.get("nps"),
            "hashfull": info.get("hashfull"),
            "cp": cp,
            "mate": mate,
            "pv_uci": [m.uci() for m in pv[:14]],
            "pv_san": san_moves,
            "start_fullmove": board.fullmove_number,
            "start_turn": "w" if board.turn == chess.WHITE else "b",
        }
        self.push_callback(payload)
