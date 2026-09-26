import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import chess
import chess.engine
import app
from study import Study, SessionStore
from review import GameReview, score_data


class StudyTests(unittest.TestCase):
    def test_alternative_keeps_mainline_and_pgn_round_trips(self):
        study = Study.from_pgn('1. e4 e5 2. Nf3 Nc6 *')
        study.select('e2e4 e7e5')
        study.play(chess.Move.from_uci('f1c4'))
        self.assertEqual([m.uci() for m in study.game.mainline_moves()], ['e2e4','e7e5','g1f3','b8c6'])
        restored = Study.restore(study.snapshot())
        self.assertEqual(restored.node.board().fen(), study.node.board().fen())
        self.assertIn('Bc4', restored.export())
        self.assertIn('Nf3', restored.export())

    def test_black_to_move_custom_root_notation(self):
        study = Study('4k3/8/8/8/8/8/8/4K3 b - - 0 23')
        study.play(chess.Move.from_uci('e8d7'))
        self.assertEqual(study.notation()[0]['number'], 23)
        self.assertEqual(study.notation()[0]['turn'], 'b')

    def test_atomic_recovery_falls_back_to_previous_valid_session(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(Path(folder) / 'session.json')
            study = Study.from_pgn('1. d4 d5 *')
            store.save({'study':study.snapshot()})
            study.play(chess.Move.from_uci('c2c4'))
            store.save({'study':study.snapshot()})
            store.path.write_text('{broken', encoding='utf-8')
            restored = Study.restore(store.load()['study'])
            self.assertEqual(len(restored.path), 2)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = {}
        self.load = patch('app.load_settings', side_effect=lambda: dict(self.settings))
        self.save = patch('app.save_settings', side_effect=lambda data:self.settings.update(data))
        self.load.start()
        self.save.start()
        self.path = str(Path(self.temp.name) / 'session.json')
        self.api = app.Api(self.path)

    def tearDown(self):
        self.api.browser_live.stop()
        self.api.reviewer.cancel()
        self.load.stop()
        self.save.stop()
        self.temp.cleanup()

    def test_navigation_remembers_selected_branch(self):
        self.api.import_pgn('1. e4 e5 2. Nf3 Nc6 *')
        self.api.go_to_ply(2)
        self.api.make_move('f1c4')
        self.api.go_to_ply(1)
        self.api.go_to_ply(3)
        self.assertEqual(self.api._board().peek().uci(), 'f1c4')
        self.assertIn('Nf3', self.api.export_pgn())

    def test_pause_buffers_moves_and_keeps_exploration_on_resume(self):
        board = chess.Board()
        board.push_san('e4')
        self.api.live_active = True
        self.api._on_browser_position({'source':'lichess.org','session':'test','fen':board.fen()})
        with patch.object(type(self.api.browser_live), 'active', property(lambda _: True)):
            self.api.pause_live()
        self.api.make_move('c7c5')
        explored = self.api._board().fen()
        board.push_san('e5')
        self.api._on_browser_position({'source':'lichess.org','session':'test','fen':board.fen()})
        self.assertEqual(self.api._board().fen(), explored)
        self.api.resume_live()
        self.assertEqual(self.api._board().fen(), board.fen())
        self.assertIn('c5', self.api.export_pgn())
        self.assertEqual(list(self.api.study.game.mainline_moves())[-1].uci(), 'e7e5')

    def test_session_restores_branches_cursor_preferences_and_paused_live(self):
        self.api.import_pgn('1. e4 e5 (1... c5) 2. Nf3 *')
        self.api.go_to_node('e2e4 c7c5')
        self.api.live_board = chess.Board()
        self.api.live_board.push_san('d4')
        self.api.live_paused = True
        self.api.set_view_preferences({'flipped':True,'show_arrows':False})
        restored = app.Api(self.path)
        self.assertEqual(restored._board().fen(), self.api._board().fen())
        self.assertEqual(restored.export_pgn(), self.api.export_pgn())
        self.assertTrue(restored.view_preferences['flipped'])
        self.assertTrue(restored.live_paused)
        self.assertEqual(restored.live_board.fen(), self.api.live_board.fen())

    def test_pairing_survives_shutdown_but_stop_disables_automatic_start(self):
        with patch('browser_live.PORT', 0):
            token = self.api.start_live()['token']
        self.api.shutdown()
        self.assertTrue(self.settings['live_enabled'])
        restored = app.Api(self.path)
        with patch('browser_live.PORT', 0):
            self.assertEqual(restored.start_live()['token'], token)
        restored.stop_live()
        self.assertFalse(self.settings['live_enabled'])

    def test_stale_review_cannot_replace_new_game_results(self):
        old_job = self.api.review_job
        self.api.new_game()
        self.api._on_review({'job_id':old_job,'status':'complete','rows':[{}]})
        self.assertEqual(self.api.get_review()['status'], 'idle')

    def test_restarting_live_returns_to_cached_browser_position(self):
        self.api.import_pgn('1. e4 e5 *')
        self.api.live_board = self.api._board().copy()
        expected = self.api.live_board.fen()
        self.api.go_to_ply(0)
        with patch('browser_live.PORT', 0):
            self.api.start_live()
        self.assertEqual(self.api._board().fen(), expected)
        self.api.stop_live()


class ReviewTests(unittest.TestCase):
    def test_mate_and_draw_scores_have_correct_perspective(self):
        board = chess.Board()
        for san in ['f3','e5','g4','Qh4#']:
            board.push_san(san)
        self.assertEqual(score_data(board, {})['value'], -10000)
        self.assertEqual(score_data(chess.Board('4k3/8/8/8/8/8/8/4K3 w - - 0 1'), {})['value'], 0)

    def test_review_streams_progress_and_completes(self):
        class Engine:
            options = {}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def configure(self, _): pass
            def analyse(self, board, limit, **kwargs):
                moves = kwargs.get('root_moves') or list(board.legal_moves)
                infos = [{'score':chess.engine.PovScore(chess.engine.Cp(25),chess.WHITE), 'pv':[move], 'depth':16} for move in moves[:kwargs.get('multipv', 1)]]
                return infos if 'multipv' in kwargs else infos[0]
        updates = []
        review = GameReview(updates.append)
        with patch('review.chess.engine.SimpleEngine.popen_uci', return_value=Engine()):
            review._run('engine', chess.STARTING_FEN, [chess.Move.from_uci('e2e4')], 1, threading.Event())
        self.assertEqual(updates[-1]['status'],'complete')
        self.assertEqual(updates[-1]['completed'],1)
        self.assertEqual(len(updates[-1]['points']),2)

    def test_cancelled_review_does_not_publish(self):
        updates = []
        event = threading.Event()
        event.set()
        review = GameReview(updates.append)
        with patch('review.chess.engine.SimpleEngine.popen_uci', side_effect=RuntimeError('cancelled')):
            review._run('engine', chess.STARTING_FEN, [], 1, event)
        self.assertEqual(updates, [])
