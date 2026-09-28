"""A PGN variation tree and atomic recovery for a single analysis workspace."""
import io
import json
import os
from pathlib import Path

import chess
import chess.pgn


class Study:
    def __init__(self, fen=chess.STARTING_FEN):
        self.game = chess.pgn.Game()
        self.game.setup(chess.Board(fen))
        self.node = self.game
        self.line = []

    @classmethod
    def from_pgn(cls, text):
        if len(text) > 2_000_000:
            raise ValueError("This game is too large to import.")
        game = chess.pgn.read_game(io.StringIO(text))
        if game is None or game.errors or not game.board().is_valid():
            raise ValueError("The PGN contains an invalid position or move.")
        study = cls()
        study.game = game
        study.select_mainline()
        return study

    @property
    def root_fen(self):
        return self.game.board().fen()

    @property
    def path(self):
        moves, node = [], self.node
        while node.parent is not None:
            moves.append(node.move)
            node = node.parent
        return list(reversed(moves))

    def select(self, identifier):
        node = self.game
        for uci in identifier.split():
            move = chess.Move.from_uci(uci)
            if not node.has_variation(move):
                raise ValueError("That variation is no longer available.")
            node = node.variation(move)
        self.node = node
        self.line = self.path
        while node.variations:
            node = node.variations[0]
            self.line.append(node.move)

    def select_mainline(self):
        self.select(" ".join(move.uci() for move in self.game.mainline_moves()))

    def play(self, move):
        if move not in self.node.board().legal_moves:
            raise ValueError("Illegal move.")
        node = self.node.variation(move) if self.node.has_variation(move) else self.node.add_variation(move)
        self.select(" ".join(m.uci() for m in self.path + [node.move]))

    def merge_board(self, board, select=True):
        """Keep exploration branches while advancing the external game's line."""
        if board.root().fen() != self.root_fen:
            raise ValueError("Different starting position.")
        node = self.game
        for move in board.move_stack:
            node = node.variation(move) if node.has_variation(move) else node.add_variation(move)
            node.parent.promote_to_main(node)
        if select:
            self.select(" ".join(m.uci() for m in board.move_stack))

    def export(self):
        return self.game.accept(chess.pgn.StringExporter(headers=True, variations=True, comments=True))

    def notation(self):
        # Iterative traversal avoids a Python recursion limit on long main lines.
        output = []
        stack = [(self.game, [], output)]
        while stack:
            parent, path, target = stack.pop()
            board = parent.board()
            for child in parent.variations:
                route = path + [child.move.uci()]
                item = {"id": " ".join(route), "san": board.san(child.move),
                        "number": board.fullmove_number, "turn": "w" if board.turn else "b", "children": []}
                target.append(item)
                stack.append((child, route, item["children"]))
        return output

    def snapshot(self):
        return {"pgn": self.export(), "selected": " ".join(m.uci() for m in self.path),
                "line": " ".join(m.uci() for m in self.line)}

    @classmethod
    def restore(cls, data):
        study = cls.from_pgn(data["pgn"])
        study.select(data.get("line", data.get("selected", "")))
        line = study.line[:]
        study.select(data.get("selected", ""))
        if line[:len(study.path)] == study.path:
            study.line = line
        return study


class SessionStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        for path in (self.path, self.path.with_suffix('.backup.json')):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version") != 1:
                    continue
                Study.restore(data["study"])
                return data
            except (OSError, ValueError, TypeError, KeyError):
                continue
        return None

    def save(self, data):
        data = {"version": 1, **data}
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        if self.path.exists():
            # Only retain a verified prior session as the recovery fallback.
            try:
                old = json.loads(self.path.read_text(encoding='utf-8'))
                Study.restore(old['study'])
                os.replace(self.path, self.path.with_suffix('.backup.json'))
            except (ValueError, KeyError, TypeError):
                pass
        os.replace(temporary, self.path)
