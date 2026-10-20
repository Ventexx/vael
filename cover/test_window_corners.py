"""Exercise the production window-shape method without starting app services."""
import ast
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication, QMainWindow

source = ast.parse(Path(__file__).with_name("app.py").read_text(encoding="utf-8-sig"))
window_class = next(node for node in source.body if isinstance(node, ast.ClassDef)
                    and node.name == 'MainWindow')
method = next(node for node in window_class.body if isinstance(node, ast.FunctionDef)
              and node.name == "_update_window_mask")
namespace = {}
exec(compile(ast.Module(body=[method], type_ignores=[]), "app.py", "exec"), namespace)


class ShapeWindow(QMainWindow):
    _update_window_mask = namespace["_update_window_mask"]


class WindowCorners(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_windows_corners_resize_and_restore_preserve_window_flags(self):
        window = ShapeWindow()
        window._is_windows = True
        flags = window.windowFlags()
        for width, height in ((800, 600), (1100, 750), (560, 460)):
            window.resize(width, height)
            window._update_window_mask()
            region = window.mask()
            for point in (QPoint(0, 0), QPoint(width - 1, 0),
                          QPoint(0, height - 1), QPoint(width - 1, height - 1)):
                self.assertFalse(region.contains(point))
            self.assertTrue(region.contains(QPoint(width // 2, height // 2)))
            self.assertTrue(region.contains(QPoint(width // 2, 0)))
        for state in (Qt.WindowMaximized, Qt.WindowFullScreen):
            window.setWindowState(state)
            window._update_window_mask()
            self.assertTrue(window.mask().isEmpty())
            window.setWindowState(Qt.WindowNoState)
            window._update_window_mask()
            self.assertFalse(window.mask().isEmpty())
        self.assertEqual(window.windowFlags(), flags)
        self.assertFalse(window.testAttribute(Qt.WA_TranslucentBackground))
        window.close()

    def test_other_platform_frameless_path_still_rounds(self):
        window = ShapeWindow()
        window._is_windows = False
        window.setObjectName("mainWindowFrameless")
        window.resize(800, 600)
        window._update_window_mask()
        self.assertFalse(window.mask().isEmpty())
        self.assertFalse(window.mask().contains(QPoint(0, 0)))
        window.close()


if __name__ == "__main__":
    unittest.main()
