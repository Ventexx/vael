import threading
import unittest

import chess

from backend.engine import EngineManager


class QuietAnalysis:
    def __init__(self):
        self.iterating = threading.Event()
        self.stopped = threading.Event()
        self.release = threading.Event()

    def stop(self):
        self.stopped.set()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stop()

    def __iter__(self):
        self.iterating.set()
        self.release.wait(5)
        return iter([{"pv": [chess.Move.from_uci("e2e4")]}])


class FakeEngine:
    def __init__(self):
        self.searches = []
        self.starting = threading.Event()
        self.created = threading.Event()
        self.allow_start = threading.Event()
        self.allow_start.set()

    def analysis(self, *args, **kwargs):
        self.starting.set()
        self.allow_start.wait(5)
        result = QuietAnalysis()
        self.searches.append(result)
        self.created.set()
        return result


class AnalysisResponsivenessTests(unittest.TestCase):
    def setUp(self):
        self.updates = []
        self.manager = EngineManager(self.updates.append)
        self.engine = FakeEngine()
        self.manager.engine = self.engine
        self.workers = []

    def tearDown(self):
        self.manager.stop()
        self.engine.allow_start.set()
        for search in self.engine.searches:
            search.release.set()
        for worker in self.workers:
            worker.join(1)

    def start(self, board):
        self.manager.analyze(board)
        self.workers.append(self.manager.analysis_thread)

    def test_replacement_returns_without_waiting_for_quiet_worker(self):
        self.start(chess.Board())
        self.assertTrue(self.engine.starting.wait(1))
        # The worker can be quiet indefinitely, even after receiving stop.
        self.assertTrue(self.engine.created.wait(1))
        old = self.engine.searches[0]
        self.assertTrue(old.iterating.wait(1))
        board = chess.Board()
        board.push_uci("d2d4")
        returned = threading.Event()
        caller = threading.Thread(target=lambda: (self.start(board), returned.set()))
        caller.start()
        try:
            self.assertTrue(returned.wait(0.5), "Move path waited for engine output")
            self.assertTrue(old.stopped.is_set())
            self.assertTrue(self.workers[0].is_alive())
        finally:
            old.release.set()
            caller.join(4)
        self.workers[0].join(1)
        self.assertEqual(self.updates, [], "Old search emitted stale analysis")

    def test_replacement_does_not_wait_for_engine_startup_lock(self):
        self.engine.allow_start.clear()
        self.start(chess.Board())
        self.assertTrue(self.engine.starting.wait(1))
        returned = threading.Event()
        caller = threading.Thread(target=lambda: (self.start(chess.Board()), returned.set()))
        caller.start()
        try:
            self.assertTrue(returned.wait(0.5), "Move path waited for engine startup")
        finally:
            self.engine.allow_start.set()
            caller.join(4)
        self.workers[0].join(1)
        self.assertTrue(self.engine.searches[0].stopped.is_set())


if __name__ == "__main__":
    unittest.main()
