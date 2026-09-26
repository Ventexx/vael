"""Reproducible engine calibration checks; fixtures are synthetic controls, not human ratings."""
import argparse
import json
import io
from pathlib import Path
import sys
import threading
import chess
import chess.engine
import chess.pgn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from review import analyse_move, GameReview

def run(engine_path, output):
    corpus = json.loads((ROOT / "tests/review_corpus.json").read_text())
    results = []
    with chess.engine.SimpleEngine.popen_uci(engine_path) as engine:
        engine.configure({"Threads": 1, "Hash": 128})
        identity = engine.id.get("name", "Local engine")
        for case in corpus:
            original = chess.Board() if case["fen"] == "start" else chess.Board(case["fen"])
            for uci in case["moves"]:
                original.push_uci(uci)
            for mirrored in (False, True):
                board = original.mirror() if mirrored else original.copy()
                move = chess.Move.from_uci(case["move"])
                if mirrored:
                    move = chess.Move(chess.square_mirror(move.from_square), chess.square_mirror(move.to_square), promotion=move.promotion)
                assert board.is_valid() and move in board.legal_moves, case["name"]
                checks = []
                for budget in (0.5, 1.5):
                    engine.configure({"Clear Hash": None})
                    row = analyse_move(engine, board, move, [], threading.Event(), budget)
                    checks.append({"budget": budget, "grade": row["grade"], "accuracy": row["accuracy"],
                                   "best": row["best"], "loss": row["chance_loss"], "depth": row["depth"],
                                   "pass": row["grade"] in case["allowed"]})
                results.append({"case": case["name"], "mirrored": mirrored, "checks": checks})
                print(case["name"], "black-mirror" if mirrored else "original", [c["grade"] for c in checks], flush=True)
    report = {"engine": identity, "cases": len(results), "checks": len(results)*2,
              "passed": sum(c["pass"] for r in results for c in r["checks"]),
              "grade_changes": sum(r["checks"][0]["grade"] != r["checks"][1]["grade"] for r in results),
              "scope": "Synthetic tactical and positional controls with both colours; not a human-calibrated rating benchmark.",
              "results": results}
    Path(output).write_text(json.dumps(report, indent=2)+"\n",encoding="utf-8")
    return report

def run_games(engine_path, report, output):
    reports=[]
    for case in json.loads((ROOT / "tests/review_games.json").read_text()):
        game=chess.pgn.read_game(io.StringIO(case["pgn"]))
        updates=[]
        GameReview(updates.append)._run(engine_path, game.board().fen(), list(game.mainline_moves()), 1, threading.Event())
        result=updates[-1]
        passed=result["status"]=="complete"
        if passed and "better" in case:
            other="b" if case["better"]=="w" else "w"
            passed=result["summary"][case["better"]]["accuracy"] > result["summary"][other]["accuracy"]
            passed=passed and any(r["san"]==case["blunder"] and r["grade"]=="blunder" for r in result["rows"])
        if passed and "minimum" in case:
            passed=all(s["accuracy"] >= case["minimum"] for s in result["summary"].values())
            passed=passed and not any(r["grade"] in ("great", "blunder") for r in result["rows"])
        reports.append({"case":case["name"],"pass":passed,"accuracy":{c:s["accuracy"] for c,s in result.get("summary",{}).items()},
                        "moves":[{"san":r["san"],"grade":r["grade"]} for r in result["rows"]]})
        print("Game",case["name"],"passed" if passed else "FAILED",flush=True)
    report["games"]=reports
    Path(output).write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    return all(r["pass"] for r in reports)

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--engine")
    parser.add_argument("--output",default=str(ROOT/"tests/review-calibration-report.json"))
    parser.add_argument("--games-only",action="store_true",help="Append game checks to an existing position report")
    args=parser.parse_args()
    if not args.engine:
        from app import load_settings
        args.engine=load_settings().get("engine_path")
    if not args.engine:
        parser.error("Provide --engine or configure the app's engine first.")
    report=json.loads(Path(args.output).read_text()) if args.games_only else run(args.engine,args.output)
    games_passed=run_games(args.engine,report,args.output)
    print(f"{report['passed']}/{report['checks']} controls passed; {report['grade_changes']} budget-dependent grade changes.")
    sys.exit(0 if report["passed"]==report["checks"] and games_passed else 1)
