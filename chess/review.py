"""Cancellable local review. Scores and grades are Vael estimates, not Elo."""
import math
import threading
import chess
import chess.engine

VERSION = 2
GRADES = ("great", "best", "excellent", "good", "inaccuracy", "mistake", "blunder", "forced")
VALUES = {1: 1, 2: 3, 3: 3, 4: 5, 5: 9, 6: 0}


def score_data(board, info):
    if board.is_checkmate():
        return {"cp": None, "mate": 0, "value": -10000 if board.turn else 10000}
    if board.is_game_over(claim_draw=False):
        return {"cp": 0, "mate": None, "value": 0}
    score = info.get("score")
    if score is None:
        raise ValueError("The engine did not return an evaluation.")
    score = score.white()
    return {"cp": score.score(), "mate": score.mate(), "value": score.score(mate_score=10000)}


def chances(score, color):
    value = score["value"] * (1 if color else -1)
    return 100 / (1 + math.exp(-max(-10000, min(10000, value)) / 250))


def grade_move(best, played, color, is_best, forced=False, gap=0):
    loss = max(0, chances(best, color) - chances(played, color))
    cp_loss = max(0, (best["value"] - played["value"]) * (1 if color else -1))
    if forced:
        grade = "forced"
    elif is_best:
        grade = "great" if gap >= 12 else "best"
    elif loss >= 20:
        grade = "blunder"
    elif loss >= 10:
        grade = "mistake"
    elif loss >= 4:
        grade = "inaccuracy"
    elif loss >= 1.5 or cp_loss > 60:
        grade = "good"
    else:
        grade = "excellent"
    return grade, round(loss, 2), round(100 * math.exp(-loss / 20), 1)


def phase_of(board):
    strength = sum(VALUES[p.piece_type] for p in board.piece_map().values() if p.piece_type != chess.PAWN)
    return "endgame" if strength <= 26 else "opening" if board.fullmove_number <= 10 else "middlegame"


def line_data(board, moves):
    copy, output = board.copy(), []
    for move in moves[:8]:
        if move not in copy.legal_moves:
            break
        output.append({"uci": move.uci(), "san": copy.san(move)})
        copy.push(move)
    return output


def material(board, color):
    return sum(VALUES[p.piece_type] * (1 if p.color == color else -1) for p in board.piece_map().values())


def move_fact(board, move):
    captured = chess.PAWN if board.is_en_passant(move) else board.piece_type_at(move.to_square)
    after = board.copy()
    after.push(move)
    if after.is_checkmate():
        return "It delivers checkmate."
    if move.promotion:
        return "It promotes the pawn to a " + chess.piece_name(move.promotion) + "."
    if board.is_castling(move):
        return "It castles the king and brings the rook into play."
    if captured:
        return "It captures a " + chess.piece_name(captured) + " on " + chess.square_name(move.to_square) + "."
    if after.is_check():
        return "It gives check, requiring an immediate answer to the king threat."
    targets = [s for s in after.attacks(move.to_square) if after.piece_at(s) and after.color_at(s) != board.turn and after.piece_type_at(s) in (chess.ROOK, chess.QUEEN)]
    if targets:
        return "It attacks " + " and ".join("the " + chess.piece_name(after.piece_type_at(s)) + " on " + chess.square_name(s) for s in targets[:2]) + "."
    if board.piece_type_at(move.from_square) in (chess.KNIGHT, chess.BISHOP) and chess.square_rank(move.from_square) == (0 if board.turn else 7):
        return "It develops a minor piece from its starting rank."
    return ""


def explain(board, move, row):
    grade, best = row["grade"], row["best"]
    sign = 1 if board.turn else -1
    before, after = row["before"]["value"] * sign, row["after"]["value"] * sign
    leads = {
        "forced": "There was only one legal move; it is excluded from the accuracy score.",
        "great": "You found the strongest move when the next candidate was substantially worse.",
        "best": "You found the engine's first choice.",
        "excellent": "This is very close to the strongest continuation.",
        "good": "This keeps most of the position's value, although " + best + " was stronger.",
    }
    lead = leads.get(grade)
    if not lead:
        lead = ("This gives up a clear advantage." if before >= 150 and after <= 50 else
                "This leaves you at a clear disadvantage." if before > -100 and after <= -200 else
                "This concedes more than necessary.") + " " + best + " was stronger."
    end = board.copy()
    for item in row["played_line"]:
        end.push_uci(item["uci"])
    change = material(end, board.turn) - material(board, board.turn)
    if row["after"]["mate"] is not None:
        detail = "The engine finds a forced mate " + ("for your side." if after > 0 else "for the opponent.")
    elif grade in ("inaccuracy", "mistake", "blunder") and change <= -2:
        detail = "In the displayed engine continuation, your material balance falls by " + str(abs(change)) + " points."
    else:
        positive = grade in ("great", "best", "excellent", "forced")
        detail = move_fact(board, move if positive else chess.Move.from_uci(row["best_uci"]))
        if detail and not positive:
            detail = "With " + best + ": " + detail[0].lower() + detail[1:]
    return lead + " " + (detail or "Compare the continuations to see the difference; no specific tactical motif was verified.")


def summarize(rows):
    result = {}
    for color in ("w", "b"):
        own = [r for r in rows if r["turn"] == color]
        scored = [r for r in own if r["grade"] != "forced"]
        accuracy = round(sum(r["accuracy"] for r in scored) / len(scored), 1) if scored else None
        counts = {g: sum(r["grade"] == g for r in own) for g in GRADES}
        phases = {}
        for phase in ("opening", "middlegame", "endgame"):
            subset = [r for r in scored if r["phase"] == phase]
            phases[phase] = {"moves": len(subset), "accuracy": round(sum(r["accuracy"] for r in subset) / len(subset), 1) if subset else None}
        description = "No scored moves yet." if accuracy is None else "Very precise play." if accuracy >= 90 else "Strong overall play." if accuracy >= 80 else "Solid play with room to improve." if accuracy >= 65 else "Several costly decisions to revisit."
        costly = counts["mistake"] + counts["blunder"]
        if costly:
            description += " " + str(costly) + (" costly decision to revisit." if costly == 1 else " costly decisions to revisit.")
        result[color] = {"accuracy": accuracy, "moves": len(own), "counts": counts, "phases": phases, "description": description}
    return result


def highlights(rows):
    chosen = set()
    for color in ("w", "b"):
        own = [r for r in rows if r["turn"] == color and r["grade"] != "forced"]
        chosen.update(r["ply"] for r in sorted(own, key=lambda r: r["chance_loss"], reverse=True)[:3] if r["chance_loss"] >= 4)
        good = [r for r in own if r["grade"] in ("great", "best")]
        if good:
            chosen.add(max(good, key=lambda r: (r["grade"] == "great", r["candidate_gap"], r["ply"]))["ply"])
    return sorted(chosen) if chosen else sorted(set([rows[0]["ply"], rows[-1]["ply"]])) if rows else []


class Cancelled(Exception):
    pass


class GameReview:
    def __init__(self, publish):
        self.publish = publish
        self.cancel_event = threading.Event()
        self.thread = None
        self.run_lock = threading.Lock()

    def cancel(self):
        self.cancel_event.set()

    def start(self, engine_path, root_fen, moves, job_id, players=None):
        self.cancel()
        event = self.cancel_event = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(engine_path, root_fen, list(moves), job_id, event, players), daemon=True)
        self.thread.start()

    def _run(self, path, fen, moves, job_id, event, players=None):
        result = {"version": VERSION, "job_id": job_id, "status": "running", "rows": [], "points": [],
                  "completed": 0, "total": len(moves), "players": players or {"w": "White", "b": "Black"}}
        def check():
            if event.is_set():
                raise Cancelled()
        def emit():
            check()
            self.publish({**result, "rows": list(result["rows"]), "points": list(result["points"]),
                          "summary": summarize(result["rows"]), "highlights": highlights(result["rows"])})
        try:
            with self.run_lock:
                check()
                with chess.engine.SimpleEngine.popen_uci(path, timeout=10) as engine:
                    engine.configure({k: v for k, v in {"Threads": 1, "Hash": 128, "UCI_LimitStrength": False, "Skill Level": 20}.items() if k in engine.options})
                    result["engine"] = getattr(engine, "id", {}).get("name", "Local engine")
                    board, route = chess.Board(fen), []
                    def analyse(roots=None, seconds=0.5, count=None):
                        check()
                        kwargs = {}
                        if roots is not None:
                            kwargs["root_moves"] = roots
                        if count:
                            kwargs["multipv"] = count
                        info = engine.analyse(board, chess.engine.Limit(time=seconds), **kwargs)
                        check()
                        return info
                    emit()
                    for move in moves:
                        check()
                        if move not in board.legal_moves:
                            raise ValueError("The game contains an illegal move.")
                        legal_count = board.legal_moves.count()
                        def compare(seconds):
                            candidates = analyse(seconds=seconds, count=min(2, legal_count))
                            if isinstance(candidates, dict):
                                candidates = [candidates]
                            best_move = candidates[0].get("pv", [move])[0]
                            best_info = analyse([best_move], seconds)
                            played_info = best_info if move == best_move else analyse([move], seconds)
                            return candidates, best_move, best_info, played_info
                        candidates, best_move, best_info, played_info = compare(0.5)
                        before, after = score_data(board, best_info), score_data(board, played_info)
                        if grade_move(before, after, board.turn, move == best_move)[1] >= 4 or before["mate"] is not None or after["mate"] is not None:
                            candidates, best_move, best_info, played_info = compare(1.0)
                            before, after = score_data(board, best_info), score_data(board, played_info)
                        if chances(after, board.turn) > chances(before, board.turn):
                            best_info, best_move, before = played_info, move, after
                        second = next((c for c in candidates if c.get("pv") and c["pv"][0] != best_move), None)
                        gap = max(0, chances(before, board.turn) - chances(score_data(board, second), board.turn)) if second else 0
                        depth = min(best_info.get("depth", 0), played_info.get("depth", 0))
                        grade, loss, accuracy = grade_move(before, after, board.turn, move == best_move, legal_count == 1, gap if depth >= 14 else 0)
                        row = {"id": " ".join(route + [move.uci()]), "before_id": " ".join(route), "fen": board.fen(),
                               "san": board.san(move), "uci": move.uci(), "number": board.fullmove_number, "turn": "w" if board.turn else "b",
                               "before": before, "after": after, "loss": max(0, (before["value"] - after["value"]) * (1 if board.turn else -1)),
                               "chance_loss": loss, "accuracy": accuracy, "grade": grade, "candidate_gap": round(gap, 2),
                               "best": board.san(best_move), "best_uci": best_move.uci(), "depth": depth, "provisional": depth < 12,
                               "best_line": line_data(board, best_info.get("pv") or [best_move]),
                               "played_line": line_data(board, played_info.get("pv") or [move]),
                               "phase": phase_of(board), "ply": len(route) + 1}
                        row["explanation"] = explain(board, move, row)
                        board.push(move)
                        if board.is_game_over(claim_draw=False):
                            row["after"] = score_data(board, {})
                        route.append(move.uci())
                        result["rows"].append(row)
                        if not result["points"]:
                            result["points"].append(before)
                        result["points"].append(row["after"])
                        result["completed"] = len(route)
                        emit()
                    result["status"] = "complete"
                    emit()
        except Cancelled:
            pass
        except Exception as exc:
            result.update(status="error", error="Review stopped: " + str(exc))
            if not event.is_set():
                emit()
