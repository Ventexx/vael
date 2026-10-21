"""Original-file drag payloads and selection ordering, without starting Sorter."""
import ast
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QMimeData, QUrl, QByteArray, QPoint
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

source = ast.parse(Path(__file__).with_name("app.py").read_text(encoding="utf-8-sig"))
namespace = dict(globals())
helper = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "file_drag_mime")
exec(compile(ast.Module(body=[helper], type_ignores=[]), "app.py", "exec"), namespace)
file_drag_mime = namespace["file_drag_mime"]


def method(cls, name):
    node = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == cls)
    node = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), namespace)
    return namespace[name]


class FileDrags(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_original_paths_names_and_bytes_survive_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            originals = []
            original_bytes = []
            for name, data in (("02 café #1.png", b"original PNG bytes\x00\xff"),
                               ("01 space %.jpg", b"original JPEG metadata and pixels")):
                path = Path(folder) / name
                path.write_bytes(data)
                originals.append(path)
                original_bytes.append(data)
            mime = file_drag_mime(originals + originals, True)
            received = [Path(url.toLocalFile()) for url in mime.urls()]
            self.assertEqual(received, originals)
            self.assertEqual([p.read_bytes() for p in received], original_bytes)
            self.assertFalse(mime.hasImage())
            self.assertFalse(mime.hasHtml())
            self.assertEqual(bytes(mime.data("application/x-vael-sorter")), b"workspace")
            if sys.platform == "win32":
                self.assertEqual(bytes(mime.data('application/x-qt-windows-mime;value="Preferred DropEffect"')), b"\x01\x00\x00\x00")

    def test_top_card_uses_selected_originals_and_copy_only(self):
        paths = [Path("02.png").absolute(), Path("01.png").absolute()]
        for browser in (SimpleNamespace(editable=False, drag_paths=lambda card: paths), None):
            drag = Mock()
            namespace.update(QDrag=Mock(return_value=drag), THUMB_W=106, THUMB_H=159)
            card = SimpleNamespace(filepath=str(paths[0]), browser=browser,
                                   _img_lbl=SimpleNamespace(pixmap=lambda: QPixmap()))
            method("ThumbnailCard", "_start_drag")(card)
            mime = drag.setMimeData.call_args.args[0]
            self.assertEqual([Path(u.toLocalFile()) for u in mime.urls()], paths if browser else paths[:1])
            self.assertEqual(bytes(mime.data("application/x-vael-sorter")), b"source")
            drag.exec.assert_called_once_with(Qt.CopyAction, Qt.CopyAction)

    def test_workspace_drag_keeps_internal_origin_and_copy_only(self):
        paths = [Path("02.png").absolute(), Path("01.png").absolute()]
        drag = Mock()
        namespace["QDrag"] = Mock(return_value=drag)
        view = SimpleNamespace(browser=SimpleNamespace(editable=True, selected_paths=lambda: paths))
        method("DragViewMixin", "startDrag")(view, Qt.MoveAction | Qt.CopyAction)
        mime = drag.setMimeData.call_args.args[0]
        self.assertEqual([Path(u.toLocalFile()) for u in mime.urls()], paths)
        self.assertEqual(bytes(mime.data("application/x-vael-sorter")), b"workspace")
        drag.exec.assert_called_once_with(Qt.CopyAction, Qt.CopyAction)

    def test_top_selection_follows_display_order(self):
        def card(name, x, y):
            return SimpleNamespace(filepath=name, isVisible=lambda: True, mapToGlobal=lambda point: QPoint(x, y))
        cards = [card("a.png", 10, 20), card("z.png", 10, 10), card("b.png", 20, 10)]
        browser = SimpleNamespace(_selected_file_paths={c.filepath for c in cards},
            _sections_by_path={"test": SimpleNamespace(isVisible=lambda: True, _cards=cards)},
            selected_paths=lambda: [Path(c.filepath) for c in cards])
        result = method("CoverBrowser", "drag_paths")(browser, cards[0])
        self.assertEqual(result, [Path("z.png"), Path("b.png"), Path("a.png")])

    def test_workspace_selection_follows_rows_not_click_order(self):
        indexes = [SimpleNamespace(row=lambda n=n: n, column=lambda: 0,
                   data=lambda role, n=n: SimpleNamespace(path=Path(str(n)))) for n in (4, 1, 3)]
        browser = SimpleNamespace(grid=SimpleNamespace(selectionModel=lambda:
            SimpleNamespace(selectedIndexes=lambda: indexes)))
        self.assertEqual(method("WorkspaceBrowser", "selected_paths")(browser),
                         [Path("1"), Path("3"), Path("4")])


if __name__ == "__main__":
    unittest.main()
