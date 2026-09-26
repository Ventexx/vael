"""Cancellable full-strength, bounded engine review; no speculative move labels."""
import threading

import chess
import chess.engine


def score_data(board, info):
    if board.is_checkmate():
        return {"cp": None, "mate": 0, "value": -10000 if board.turn else 10000}
    if board.is_game_over(claim_draw=True):
        return {"cp": 0, "mate": None, "value": 0}
    score = info.get("score")
    if score is None:
        raise ValueError("The engine did not return an evaluation.")
    score = score.white()
    return {"cp": score.score(), "mate": score.mate(), "value": score.score(mate_score=10000)}


class GameReview:
    def __init__(self, publish):
        self.publish = publish
        self.cancel_event = threading.Event()
        self.thread = None

    def cancel(self):
        self.cancel_event.set()

    def start(self, engine_path, root_fen, moves, job_id):
        self.cancel()
        event = self.cancel_event = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(engine_path, root_fen, list(moves), job_id, event), daemon=True)
        self.thread.start()

    def _run(self, path, fen, moves, job_id, event):
        result = {"job_id": job_id, "status": "running", "rows": [], "points": [],
                  "completed": 0, "total": len(moves), "seconds_per_position": 0.35}
        def emit():
            if not event.is_set():
                self.publish({**result, "rows": list(result["rows"]), "points": list(result["points"])})
        try:
            with chess.engine.SimpleEngine.popen_uci(path, timeout=10) as engine:
                options = {k: v for k, v in {"Threads": 1, "Hash": 64, "UCI_LimitStrength": False, "Skill Level": 20}.items() if k in engine.options}
                engine.configure(options)
                board = chess.Board(fen)
                def analyse():
                    if board.is_game_over(claim_draw=True):
                        return {}, score_data(board, {})
                    info = engine.analyse(board, chess.engine.Limit(time=0.35))
                    return info, score_data(board, info)
                before_info, before = analyse()
                result["points"].append(before)
                emit()
                route = []
                for move in moves:
                    if event.is_set():
                        return
                    san, number, color = board.san(move), board.fullmove_number, board.turn
                    pv = before_info.get("pv", [])
                    best = board.san(pv[0]) if pv and pv[0] in board.legal_moves else None
                    board.push(move)
                    after_info, after = analyse()
                    route.append(move.uci())
                    loss = max(0, (before["value"] - after["value"]) * (1 if color else -1))
                    result["rows"].append({"id": " ".join(route), "san": san, "number": number,
                        "turn": "w" if color else "b", "before": before, "after": after,
                        "loss": loss, "best": best, "depth": after_info.get("depth"), "ply": len(route)})
                    result["points"].append(after)
                    result["completed"] = len(route)
                    before, before_info = after, after_info
                    emit()
                result["status"] = "complete"
                emit()
        except Exception as exc:
            result.update(status="error", error="Review stopped: " + str(exc))
            emit()
