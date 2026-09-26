import threading
import unittest
from unittest.mock import patch
import chess
import chess.engine
from review import grade_move, summarize, highlights, move_fact, line_data, GameReview, phase_of
import test_workspace


class DeeperReviewTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown

    def prepare(self):
        self.api.import_pgn("1. f3 e5 2. g4 Qh4# *")
        row = {"ply":1,"id":"f2f3","before_id":"","fen":chess.STARTING_FEN,"uci":"f2f3",
               "grade":"mistake","turn":"w","phase":"opening","accuracy":50,"chance_loss":15,
               "candidate_gap":0,"before":score(30),"after":score(-120),"search_seconds":1}
        self.api.review_result = {"version":2,"status":"complete","signature":self.api._review_signature(),
                                  "rows":[row],"points":[score(30),score(-120)],"completed":1,"total":1}
        self.api.settings["engine_path"]="test-engine"
        return row.copy()

    def test_deep_check_preserves_history_and_only_replaces_target(self):
        original = self.prepare()
        saved = self.api.study.snapshot()
        with patch("app.os.path.isfile",return_value=True), patch.object(self.api.reviewer,"deepen") as worker:
            result=self.api.deepen_review(1)
            self.assertTrue(result["ok"])
            self.assertEqual(worker.call_args.args[-1],3)
        replacement={**original,"grade":"inaccuracy","accuracy":80,"chance_loss":5,"search_seconds":3}
        self.api._on_review({"kind":"deep","status":"complete","job_id":self.api.review_job,"row":replacement})
        self.assertEqual(self.api.study.snapshot(),saved)
        self.assertEqual(self.api.review_result["summary"]["w"]["accuracy"],80)
        self.assertEqual(self.api.review_result["deep"]["previous_grade"],"mistake")
        self.assertEqual(self.api.review_result["status"],"complete")

    def test_cancel_keeps_report_and_discards_late_deep_response(self):
        original=self.prepare()
        with patch("app.os.path.isfile",return_value=True), patch.object(self.api.reviewer,"deepen"):
            self.api.deepen_review(1)
        job=self.api.review_job
        self.api.cancel_review()
        self.api._on_review({"kind":"deep","status":"complete","job_id":job,"row":{**original,"grade":"blunder"}})
        self.assertEqual(self.api.review_result["rows"][0],original)
        self.assertEqual(self.api.review_result["status"],"complete")
        self.assertEqual(self.api.review_result["deep"]["status"],"cancelled")

    def test_deep_failure_and_limit_keep_previous_row(self):
        original=self.prepare()
        with patch("app.os.path.isfile",return_value=True), patch.object(self.api.reviewer,"deepen"):
            self.api.deepen_review(1)
        self.api._on_review({"kind":"deep","status":"error","job_id":self.api.review_job,"error":"Engine stopped"})
        self.assertEqual(self.api.review_result["rows"][0],original)
        self.api.review_result["rows"][0]["search_seconds"]=6
        with patch("app.os.path.isfile",return_value=True):
            self.assertFalse(self.api.deepen_review(1)["ok"])


class LiveCompletionTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown

    def send(self,board,**extra):
        self.api._on_browser_position({"source":"lichess.org","session":"fixture","fen":board.fen(),**extra})

    def test_mate_offered_once_and_dismissal_survives_polling(self):
        self.api.import_pgn("1. f3 e5 2. g4 Qh4# *")
        self.api.live_active=True
        board=self.api._board().copy()
        self.send(board)
        offer=self.api.get_live_status()["review_offer"]
        self.assertEqual(offer["result"],"0-1")
        self.send(board)
        self.assertEqual(self.api.get_live_status()["review_offer"]["id"],offer["id"])
        self.api.dismiss_live_review(offer["id"])
        self.send(board)
        self.assertIsNone(self.api.get_live_status()["review_offer"])

    def test_resignation_metadata_and_new_game_clear_offer(self):
        self.api.import_pgn("1. e4 e5 *")
        self.api.live_active=True
        self.send(self.api._board(),finished=True,result="1-0")
        self.assertIsNone(self.api.live_review_offer)
        self.send(self.api._board(),finished=True,result="1-0")
        self.assertIsNotNone(self.api.live_review_offer)
        self.send(self.api._board())
        self.assertIsNotNone(self.api.live_review_offer)
        self.send(chess.Board())
        self.assertIsNone(self.api.live_review_offer)

    def test_resignation_review_excludes_later_exploration_and_recovers(self):
        self.api.import_pgn("1. e4 e5 2. Nf3 Nc6 *")
        finished = chess.Board()
        finished.push_san("e4")
        finished.push_san("e5")
        self.api.live_board = finished.copy()
        self.api.live_active = True
        self.send(finished, finished=True, result="1-0")
        self.send(finished, finished=True, result="1-0")
        self.assertTrue(self.api.review_finished_game(self.api.live_review_offer["id"])["ok"])
        self.assertEqual([m.uci() for m in self.api._review_moves()], ["e2e4", "e7e5"])
        self.assertIn("Nc6", self.api.export_pgn())
        recovered = self.api.session_store.load()
        self.assertEqual(recovered["review_route"]["moves"], ["e2e4", "e7e5"])

    def test_false_or_unrecognized_result_does_not_end_game(self):
        self.api.import_pgn("1. e4 e5 *")
        self.send(self.api._board(),finished=True,result="aborted")
        self.assertIsNone(self.api.live_review_offer)
        self.send(self.api._board(),finished=False,result="1-0")
        self.assertIsNone(self.api.live_review_offer)

    def test_handoff_uses_live_game_while_preserving_exploration(self):
        self.api.import_pgn("1. f3 e5 2. g4 *")
        self.api.live_board=self.api._board().copy()
        self.api.live_active=True
        self.api.live_paused=True
        self.api.make_move("b8c6")
        finished=self.api.live_board.copy()
        finished.push_san("Qh4#")
        self.send(finished)
        offer=self.api.live_review_offer["id"]
        self.assertFalse(self.api.review_finished_game("stale")["ok"])
        result=self.api.review_finished_game(offer)
        self.assertTrue(result["ok"])
        self.assertEqual(self.api._board().fen(),finished.fen())
        self.assertIn("Nc6",self.api.export_pgn())
        self.assertFalse(self.api.live_active)
        self.assertIsNone(self.api.live_review_offer)



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
