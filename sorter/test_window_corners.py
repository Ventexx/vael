"""Exercise the production window-shape method without starting app services."""
import ast
import os
from pathlib import Path
import unittest
import ctypes
from ctypes import wintypes
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication, QMainWindow

source = ast.parse(Path(__file__).with_name("app.py").read_text(encoding="utf-8-sig"))
window_class = next(node for node in source.body if isinstance(node, ast.ClassDef)
                    and node.name == 'Window')
method = next(node for node in window_class.body if isinstance(node, ast.FunctionDef)
              and node.name == "_update_window_mask")
namespace = {"ctypes": ctypes, "wintypes": wintypes, "_WM_NCCALCSIZE": 0x83, "_WM_NCHITTEST": 0x84}
exec(compile(ast.Module(body=[method], type_ignores=[]), "app.py", "exec"), namespace)


for name in ("nativeEvent", "_configure_native_frame"):
    node = next(node for node in window_class.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), namespace)


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

    def test_native_rounding_clears_legacy_mask(self):
        window = ShapeWindow()
        window._is_windows = True
        window.resize(800, 600)
        window._update_window_mask()
        self.assertFalse(window.mask().isEmpty())
        window._native_corners = True
        for state in (Qt.WindowNoState, Qt.WindowMaximized, Qt.WindowNoState,
                      Qt.WindowFullScreen, Qt.WindowNoState):
            window.setWindowState(state)
            window._update_window_mask()
            self.assertTrue(window.mask().isEmpty())
        window.close()

    def test_legacy_native_caption_paint_is_consumed(self):
        window = ShapeWindow()
        window._is_windows = True
        for message in (0xAE, 0xAF):
            msg = wintypes.MSG()
            msg.message = message
            result = namespace["nativeEvent"](window, "windows_generic_MSG", ctypes.addressof(msg))
            self.assertEqual(result, (True, 0))
        window.close()

    def test_activation_updates_without_native_caption_repaint(self):
        window = ShapeWindow()
        window._is_windows = True
        proc = Mock(return_value=1)
        fake = SimpleNamespace(windll=SimpleNamespace(user32=SimpleNamespace(DefWindowProcW=proc)),
                               c_ssize_t=ctypes.c_ssize_t)
        namespace["ctypes"] = fake
        try:
            for active in (0, 1):
                msg = wintypes.MSG()
                msg.hWnd = 123
                msg.message = 0x86
                msg.wParam = active
                result = namespace["nativeEvent"](window, "windows_generic_MSG", ctypes.addressof(msg))
                self.assertEqual(result, (True, 1))
                proc.assert_called_with(123, 0x86, active, -1)
        finally:
            namespace["ctypes"] = ctypes
            window.close()

    def test_dwm_failure_keeps_fallback_available(self):
        setter = Mock()
        fake = SimpleNamespace(windll=SimpleNamespace(dwmapi=SimpleNamespace(DwmSetWindowAttribute=setter)),
                               c_void_p=ctypes.c_void_p, c_long=ctypes.c_long,
                               c_int=ctypes.c_int, byref=ctypes.byref, sizeof=ctypes.sizeof)
        namespace["ctypes"] = fake
        try:
            for result, supported in ((0, True), (-2147024809, False)):
                setter.return_value = result
                window = SimpleNamespace(_is_windows=True, winId=lambda: 123, _sync_shell_rounding=Mock())
                namespace["_configure_native_frame"](window)
                self.assertEqual(window._native_corners, supported)
                window._sync_shell_rounding.assert_called_once()
        finally:
            namespace["ctypes"] = ctypes

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
