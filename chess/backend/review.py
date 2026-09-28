"""Cancellable local review. Scores and grades are Vael estimates, not Elo."""
import math
import threading
from .engine_process import engine_process_options
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
        grade = "great" if gap >= 15 else "best"
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
    reply = next((e["text"] for e in row.get("evidence", []) if e["line"] == "played" and e["step"] == 2), None)
    if reply:
        detail = (reply + " Compare this response with the preferred line." if grade in ("inaccuracy", "mistake", "blunder")
                  else (detail + " " if detail else "") + reply)
    elif len(row["played_line"]) > 1:
        response = "The strongest analysed reply is " + row["played_line"][1]["san"] + "."
        detail = (detail + " " if detail else "") + response
    return lead + " " + (detail or "The engine favours this continuation, but no specific tactical reason was verified.")


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
        description = "No scored moves yet." if accuracy is None else "Very precise play." if accuracy >= 90 else "Strong overall play." if accuracy >= 80 else "Solid play with room to improve." if accuracy >= 65 else "An uneven game."
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


def critical_choice(board, move, best, gap, depth, stable):
    """Great is reserved for verified, nontrivial choices in undecided positions."""
    after = board.copy()
    after.push(move)
    return (stable and depth >= 18 and gap >= 15 and abs(best["value"]) < 500
            and not after.is_checkmate() and not board.is_capture(move)
            and board.legal_moves.count() > 2)


def analyse_move(engine, board, move, route, event, seconds=0.5, previous_best=None):
    initial_seconds = seconds
    def search(roots=None, budget=seconds, count=None):
        if event.is_set():
            raise Cancelled()
        kwargs = {}
        if roots is not None:
            kwargs["root_moves"] = roots
        if count:
            kwargs["multipv"] = count
        info = engine.analyse(board, chess.engine.Limit(time=budget), **kwargs)
        if event.is_set():
            raise Cancelled()
        return info

    def compare(budget):
        candidates = search(budget=budget, count=min(2, board.legal_moves.count()))
        if isinstance(candidates, dict):
            candidates = [candidates]
        roots = list(dict.fromkeys([item.get("pv", [move])[0] for item in candidates] + [move]))
        choices = [(candidate, search([candidate], budget)) for candidate in roots]
        choices.sort(key=lambda pair: score_data(board, pair[1])["value"] * (1 if board.turn else -1), reverse=True)
        best_move, best_info = choices[0]
        played_info = next(info for candidate, info in choices if candidate == move)
        before, after = score_data(board, best_info), score_data(board, played_info)
        second = choices[1][1] if len(choices) > 1 else None
        gap = max(0, chances(before, board.turn) - chances(score_data(board, second), board.turn)) if second else 0
        return best_move, best_info, played_info, before, after, gap

    if move not in board.legal_moves:
        raise ValueError("The game contains an illegal move.")
    best_move, best_info, played_info, before, after, gap = compare(seconds)
    original_best = best_move
    original_grade = grade_move(before, after, board.turn, move == best_move)[0]
    checked = seconds > 0.5
    if seconds <= 0.5 and (grade_move(before, after, board.turn, move == best_move)[1] >= 4
                          or before["mate"] is not None or after["mate"] is not None or gap >= 12):
        seconds = 1.0
        best_move, best_info, played_info, before, after, gap = compare(seconds)
        checked = True
    depth = min(best_info.get("depth", 0), played_info.get("depth", 0))
    stable = checked and original_best == best_move and (initial_seconds <= 0.5 or previous_best == best_move.uci())
    great = critical_choice(board, move, before, gap, depth, stable)
    grade, loss, accuracy = grade_move(before, after, board.turn, move == best_move,
                                      board.legal_moves.count() == 1, gap if great else 0)
    # Changed loss categories after verification deserve a visible uncertainty cue.
    changed = checked and original_grade in ("inaccuracy", "mistake", "blunder") and grade != original_grade
    row = {"id": " ".join(route + [move.uci()]), "before_id": " ".join(route), "fen": board.fen(),
           "san": board.san(move), "uci": move.uci(), "number": board.fullmove_number, "turn": "w" if board.turn else "b",
           "before": before, "after": after, "loss": max(0, (before["value"] - after["value"]) * (1 if board.turn else -1)),
           "chance_loss": loss, "accuracy": accuracy, "grade": grade, "candidate_gap": round(gap, 2),
           "best": board.san(best_move), "best_uci": best_move.uci(), "depth": depth,
           "provisional": depth < 14 or changed or any(best_info.get(k) or played_info.get(k) for k in ("lowerbound", "upperbound")),
           "best_line": line_data(board, best_info.get("pv") or [best_move]),
           "played_line": line_data(board, played_info.get("pv") or [move]),
           "phase": phase_of(board), "ply": len(route) + 1, "search_seconds": seconds, "grading_version": 3}
    row["evidence"] = continuation_evidence(board, row)
    row["explanation"] = explain(board, move, row)
    terminal = board.copy()
    terminal.push(move)
    if terminal.is_game_over(claim_draw=False):
        row["after"] = score_data(terminal, {})
    return row


def continuation_evidence(board, row):
    """Facts attached to an exact, legal continuation step, never inferred intent."""
    evidence = []
    for branch in ("played", "best"):
        current = board.copy()
        line = row[branch + "_line"]
        for index, item in enumerate(line):
            move = chess.Move.from_uci(item["uci"])
            if move not in current.legal_moves:
                break
            side = current.turn
            captured = chess.PAWN if current.is_en_passant(move) else current.piece_type_at(move.to_square)
            was_check = current.is_check()
            is_castle = current.is_castling(move)
            current.push(move)
            fact = None
            if current.is_checkmate():
                fact = item["san"] + " delivers checkmate."
            elif captured and index == 1:
                recapture = any(m.to_square == move.to_square and current.is_capture(m) for m in current.legal_moves)
                fact = "The reply " + item["san"] + " captures your " + chess.piece_name(captured) + " on " + chess.square_name(move.to_square)
                fact += "." if recapture else "; there is no immediate legal recapture on that square."
            elif current.is_check():
                fact = item["san"] + " gives check" + (" while escaping the check." if was_check else ", forcing a response to the king threat.")
            elif index == 0 and is_castle:
                fact = item["san"] + " castles the king and brings the rook into play."
            if index <= 1 and current.piece_at(move.to_square) and not current.is_pinned(side, move.to_square):
                targets = [sq for sq in current.attacks(move.to_square)
                           if current.color_at(sq) == (not side) and current.piece_type_at(sq) in (chess.ROOK, chess.QUEEN)]
                if len(targets) >= 2:
                    fact = item["san"] + " attacks " + " and ".join("the " + chess.piece_name(current.piece_type_at(sq)) + " on " + chess.square_name(sq) for sq in targets[:2]) + " at once."
            if fact:
                evidence.append({"line": branch, "step": index + 1, "text": fact})
            if len([e for e in evidence if e["line"] == branch]) >= 2:
                break
    return evidence

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

    def deepen(self, path, board, move, route, job_id, seconds=3, previous_best=None):
        self.cancel()
        event = self.cancel_event = threading.Event()
        self.thread = threading.Thread(target=self._deep, args=(path, board.copy(), move, list(route), job_id, event, seconds, previous_best), daemon=True)
        self.thread.start()

    def _deep(self, path, board, move, route, job_id, event, seconds, previous_best=None):
        try:
            with self.run_lock:
                if event.is_set():
                    return
                with chess.engine.SimpleEngine.popen_uci(path, timeout=10, **engine_process_options()) as engine:
                    engine.configure({k: v for k, v in {"Threads": 1, "Hash": 128, "UCI_LimitStrength": False, "Skill Level": 20}.items() if k in engine.options})
                    row = analyse_move(engine, board, move, route, event, seconds, previous_best)
                    if not event.is_set():
                        self.publish({"kind": "deep", "status": "complete", "job_id": job_id, "row": row})
        except Cancelled:
            pass
        except Exception as exc:
            if not event.is_set():
                self.publish({"kind": "deep", "status": "error", "job_id": job_id, "error": "Deeper check stopped: " + str(exc)})


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
                with chess.engine.SimpleEngine.popen_uci(path, timeout=10, **engine_process_options()) as engine:
                    engine.configure({k: v for k, v in {"Threads": 1, "Hash": 128, "UCI_LimitStrength": False, "Skill Level": 20}.items() if k in engine.options})
                    result["engine"] = getattr(engine, "id", {}).get("name", "Local engine")
                    board, route = chess.Board(fen), []
                    emit()
                    for move in moves:
                        check()
                        row = analyse_move(engine, board, move, route, event)
                        board.push(move)
                        route.append(move.uci())
                        result["rows"].append(row)
                        if not result["points"]:
                            result["points"].append(row["before"])
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
