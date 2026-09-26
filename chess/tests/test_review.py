import threading
import unittest
from unittest.mock import patch
import chess
import chess.engine
from review import grade_move, summarize, highlights, move_fact, line_data, GameReview, phase_of
import test_workspace


def score(cp):
    return {"cp": cp, "mate": None, "value": cp}


class GradingTests(unittest.TestCase):
    def test_colour_symmetry_and_loss_order(self):
        previous = 101
        for cp in (0, -20, -80, -150, -350, -1000):
            white = grade_move(score(0), score(cp), chess.WHITE, False)
            black = grade_move(score(0), score(-cp), chess.BLACK, False)
            self.assertEqual(white, black)
            self.assertLessEqual(white[2], previous)
            previous = white[2]
        self.assertEqual(white[0], "blunder")

    def test_forced_and_unique_best_are_distinct(self):
        self.assertEqual(grade_move(score(0), score(0), True, True, True, 40)[0], "forced")
        self.assertEqual(grade_move(score(0), score(0), True, True, False, 15)[0], "great")
        self.assertEqual(grade_move(score(0), score(0), True, True)[0], "best")

    def test_lost_position_is_not_repeatedly_penalized(self):
        self.assertNotEqual(grade_move(score(-1800), score(-2400), True, False)[0], "blunder")

    def test_facts_include_mate_and_underpromotion(self):
        board = chess.Board()
        for san in ("f3", "e5", "g4"):
            board.push_san(san)
        self.assertIn("checkmate", move_fact(board, chess.Move.from_uci("d8h4")))
        board = chess.Board("8/P7/8/8/8/8/7k/4K3 w - - 0 1")
        self.assertIn("knight", move_fact(board, chess.Move.from_uci("a7a8n")))
        self.assertEqual(phase_of(board), "endgame")

    def test_pv_is_legal_truncated_and_nonmutating(self):
        board = chess.Board()
        original = board.fen()
        line = line_data(board, [chess.Move.from_uci(m) for m in ("e2e4", "e7e5", "e2e3")])
        self.assertEqual([m["san"] for m in line], ["e4", "e5"])
        self.assertEqual(board.fen(), original)

    def test_summary_excludes_forced_and_handles_absent_phases(self):
        rows = [{"turn":"w", "grade":"best", "accuracy":100, "phase":"opening", "chance_loss":0, "ply":1,"candidate_gap":0},
                {"turn":"w", "grade":"forced", "accuracy":0, "phase":"opening", "chance_loss":40, "ply":3,"candidate_gap":0},
                {"turn":"b", "grade":"blunder", "accuracy":30, "phase":"opening", "chance_loss":25, "ply":2,"candidate_gap":0}]
        report = summarize(rows)
        self.assertEqual(report["w"]["accuracy"],100)
        self.assertEqual(report["w"]["counts"]["forced"],1)
        self.assertIsNone(report["w"]["phases"]["endgame"]["accuracy"])
        self.assertEqual(highlights(rows),[1,2])


class SearchTests(unittest.TestCase):
    def test_large_loss_rechecked_and_played_root_is_compared(self):
        calls = []
        class Engine:
            options = {}
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def configure(self,_): pass
            def analyse(self,board,limit,**kwargs):
                calls.append((limit.time,kwargs))
                roots = kwargs.get("root_moves") or [chess.Move.from_uci("e2e4"),chess.Move.from_uci("d2d4")]
                infos = [{"score":chess.engine.PovScore(chess.engine.Cp(-400 if m.uci()=="f2f3" else 0),True),
                          "pv":[m],"depth":18} for m in roots]
                return infos if "multipv" in kwargs else infos[0]
        updates = []
        with patch("review.chess.engine.SimpleEngine.popen_uci",return_value=Engine()):
            GameReview(updates.append)._run("engine",chess.STARTING_FEN,[chess.Move.from_uci("f2f3")],1,threading.Event())
        final = updates[-1]
        self.assertEqual(final["status"],"complete")
        row = final["rows"][0]
        self.assertEqual(row["grade"],"blunder")
        self.assertEqual(row["played_line"][0]["uci"],"f2f3")
        self.assertEqual(row["best_line"][0]["uci"],"e2e4")
        self.assertEqual([t for t,k in calls if k.get("root_moves")==[chess.Move.from_uci("f2f3")]],[0.5,1.0])
        self.assertIn("e4",row["explanation"])

    def test_cancellation_during_search_publishes_no_finished_row(self):
        stop = threading.Event()
        class Engine:
            options={}
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def configure(self,_): pass
            def analyse(self,*args,**kwargs):
                stop.set()
                return []
        updates=[]
        with patch("review.chess.engine.SimpleEngine.popen_uci",return_value=Engine()):
            GameReview(updates.append)._run("engine",chess.STARTING_FEN,[chess.Move.from_uci("e2e4")],1,stop)
        self.assertFalse(any(u["rows"] for u in updates))


class ReviewPreviewTests(unittest.TestCase):
    # Reuse the isolated settings/session fixture without duplicating its tests.
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    def prepare(self):
        self.api.import_pgn("1. f3 e5 2. g4 Qh4# *")
        board = chess.Board()
        for san in ("f3","e5"):
            board.push_san(san)
        self.api.review_result = {"signature": self.api._review_signature(), "rows":[
            {"ply":1,"fen":chess.STARTING_FEN,"before_id":"","best_line":[{"uci":"e2e4"},{"uci":"e7e5"}],"played_line":[{"uci":"f2f3"}]}]}
    def test_preview_is_ephemeral_and_can_step_both_lines(self):
        self.prepare()
        original = self.api.study.snapshot()
        saved = self.api.session_store.path.read_text()
        preview = self.api.review_position(1,"best",2)
        self.assertIn("4p3",preview["state"]["fen"])
        self.assertEqual(preview["state"]["last_move"],"e7e5")
        self.assertEqual(self.api.study.snapshot(),original)
        self.assertEqual(self.api.session_store.path.read_text(),saved)
        self.assertEqual(self.api.review_position(1,"played",1)["state"]["last_move"],"f2f3")
    def test_invalid_stale_and_live_previews_fail(self):
        self.prepare()
        self.assertIn("error",self.api.review_position(0))
        self.assertIn("error",self.api.review_position(1,"unknown"))
        self.api.live_active=True
        self.assertIn("error",self.api.review_position(1))
        self.api.live_active=False
        self.api.new_game()
        self.assertIn("error",self.api.review_position(1))
