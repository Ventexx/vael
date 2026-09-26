import json
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import chess
from browser_live import BrowserLive, resolve_position


def snapshot(board, sans=None):
    return {"pieces": {chess.square_name(sq): p.symbol() for sq, p in board.piece_map().items()}, "sans": sans or []}


class PositionTests(unittest.TestCase):
    def test_join_midgame_preserves_history_and_rights(self):
        sans = "e4 a6 e5 d5 exd6 cxd6 Nf3 Nf6 Be2 e6 O-O Be7".split()
        board = chess.Board()
        for san in sans:
            board.push_san(san)
        result = resolve_position(snapshot(board, sans), chess.Board())
        self.assertEqual(result.fen(), board.fen())
        self.assertEqual(result.move_stack, board.move_stack)

    def test_fen_retains_turn_castling_and_en_passant(self):
        board = chess.Board()
        for san in ["e4", "a6", "e5", "d5"]:
            board.push_san(san)
        self.assertEqual(resolve_position({"fen": board.fen()}, chess.Board()).fen(), board.fen())

    def test_underpromotion_from_imported_position(self):
        seed = chess.Board("8/P7/7k/8/8/8/8/4K3 w - - 0 1")
        target = seed.copy()
        target.push_uci("a7a8n")
        self.assertEqual(resolve_position(snapshot(target), seed).fen(), target.fen())

    def test_takeback(self):
        seed = chess.Board()
        for san in ["d4", "d5", "c4", "e6"]:
            seed.push_san(san)
        earlier = seed.copy()
        earlier.pop()
        self.assertEqual(resolve_position(snapshot(earlier, ["d4", "d5", "c4"]), seed).fen(), earlier.fen())

    def test_invalid_and_incomplete_reads_are_rejected(self):
        for payload in [{"fen": "8/8/8/8/8/8/8/8 w - - 0 1"}, {"pieces": {}}, {"fen": "8/8/8/8/8/8/8/8"}]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                resolve_position(payload, chess.Board())

    def test_unknown_midgame_does_not_guess_turn_or_rights(self):
        board = chess.Board()
        board.push_san("e4")
        board.push_san("e5")
        with self.assertRaises(ValueError):
            resolve_position(snapshot(board), chess.Board())

    def test_malformed_notation_cannot_override_visible_board(self):
        board = chess.Board()
        board.push_san("d4")
        board.push_san("d5")
        with self.assertRaises(ValueError):
            resolve_position(snapshot(board, ["e4", "oops"]), chess.Board())


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        self.receiver = BrowserLive(self.received.append, lambda _: None)
        with patch("browser_live.PORT", 0):
            self.token = self.receiver.start()
        self.url = f"http://127.0.0.1:{self.receiver.server.server_port}/position"

    def tearDown(self):
        self.receiver.stop()

    def send(self, token=None, session="tab-one", **extra):
        data = {"source": "lichess.org", "fen": chess.STARTING_FEN, "session": session, **extra}
        request = urllib.request.Request(self.url, json.dumps(data).encode(), headers={"Authorization": "Bearer " + (token or self.token), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_authenticated_feed_and_single_tab_ownership(self):
        self.assertEqual(self.send(token="bad")[0], 403)
        self.assertEqual(self.received, [])
        self.assertEqual(self.send()[0], 200)
        self.assertEqual(self.send(session="another-tab")[0], 409)
        self.assertEqual(self.send()[0], 200)
        self.assertEqual(len(self.received), 2)

    def test_site_error_keeps_last_board(self):
        self.assertEqual(self.send(error="Waiting for board")[0], 422)
        self.assertEqual(self.received, [])

    def test_stop_invalidates_pairing(self):
        self.receiver.stop()
        self.assertIsNone(self.receiver.token)
        self.assertFalse(self.receiver.active)


if __name__ == "__main__":
    unittest.main()
