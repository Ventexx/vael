"""Offscreen UI regressions using disposable files, never configured user folders.

Run: sorter/venv/Scripts/python.exe -m unittest discover -s sorter/tests -v
"""
import copy
import os
from pathlib import Path
import sys
import tempfile
import time
import threading
from types import SimpleNamespace
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as sorter


def settle(until=None, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sorter.QApplication.processEvents()
        sorter.QApplication.sendPostedEvents(None, sorter.QEvent.DeferredDelete)
        if until is not None and until():
            return
        time.sleep(.01)
        if until is None:
            return
    raise AssertionError('UI did not settle before timeout')


class ToolbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = sorter.QApplication.instance() or sorter.QApplication([])
        sorter._IS_WINDOWS = False  # Do not call DWM for an offscreen window.
        sorter.apply_style(cls.app)
        cls.temp = tempfile.TemporaryDirectory(prefix='toolbar-test-', dir=Path(__file__).parent)
        cls.root = Path(cls.temp.name).resolve()
        assert cls.root.parent == Path(__file__).resolve().parent
        cls.workspace = cls.root / 'Workspace'
        cls.workspace.mkdir()
        (cls.workspace / 'Child').mkdir()
        (cls.workspace / 'sample.txt').write_text('test', encoding='utf-8')
        sorter.DATA_DIR = cls.root / 'settings'
        config = copy.deepcopy(sorter.DEFAULTS)
        config['sort_root'] = str(cls.workspace)
        cls.window = sorter.Window(config)
        # Prevent reference reconciliation tests from accessing the real tag store.
        cls.window.tagging.store = None
        cls.window.show()
        settle(lambda: cls.window.sorter.proxy.rowCount() == 2)

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        settle()
        cls.temp.cleanup()

    def tearDown(self):
        self.window.history.clear()
        self.window.update_history_controls()

    def test_no_collection_surface_or_actions(self):
        self.assertFalse(hasattr(self.window, 'shelf'))
        self.assertFalse(hasattr(self.window, 'workspace_button'))
        for browser in (self.window.library, self.window.sorter):
            menu = browser.build_options_menu()
            self.assertNotIn('Shelf', ' '.join(a.text() for a in menu.actions()))
            menu.deleteLater()

    def test_split_tooltip_describes_next_action(self):
        toggle = self.window.split_toggle
        toggle.setChecked(False)
        self.assertIn('off', toggle.toolTip())
        toggle.setChecked(True)
        self.assertIn('Use one', toggle.toolTip())
        self.assertTrue(self.window.second.isVisible())
        self.assertEqual(toggle.accessibleName(), 'Split view')

    def test_history_describes_the_action_it_will_reverse(self):
        w = self.window
        w.history[:] = [dict(title='Move 3 images', applied=1, units=1)]
        w.update_history_controls()
        undo, redo, history = w.history_controls[0]
        self.assertTrue(undo.isEnabled())
        self.assertFalse(redo.isEnabled())
        self.assertIn('Move 3 images', undo.toolTip())
        self.assertEqual(history.accessibleName(), 'Action history')
        w.history[0]['applied'] = 0
        w.update_history_controls()
        self.assertFalse(undo.isEnabled())
        self.assertTrue(redo.isEnabled())
        self.assertIn('Move 3 images', redo.toolTip())

    def test_window_controls_have_accessible_names(self):
        for name in ('winMinBtn', 'winMaxBtn', 'winCloseBtn'):
            button = self.window.findChild(sorter.QToolButton, name)
            self.assertTrue(button.accessibleName())
            self.assertTrue(button.toolTip())

    def test_task_controls_do_not_move_toolbar_buttons(self):
        w = self.window
        w.resize(720, 520)
        w.file_task_controls.hide()
        settle()
        buttons = [w.split_toggle, *w.history_controls[0], w.tagging.button]
        positions = [button.mapTo(w, sorter.QPoint()) for button in buttons]
        try:
            w.file_task_controls.show()
            w.progress.show()
            w.cancel_button.show()
            w.operation_status.setText('Copying a long filename into the workspace')
            settle()
            self.assertEqual(positions, [button.mapTo(w, sorter.QPoint()) for button in buttons])
            self.assertEqual(w.width(), 720)
        finally:
            w.file_task_controls.hide()
            w.progress.hide()
            w.cancel_button.hide()
            w.operation_status.setText('Ready')

    def test_stop_button_only_cancels_file_operations(self):
        w = self.window
        files = SimpleNamespace(cancel=threading.Event())
        tagging = SimpleNamespace(cancel=threading.Event())
        w.operation_task, w.tagging.task = files, tagging
        try:
            w.stop_file_operation()
            self.assertTrue(files.cancel.is_set())
            self.assertFalse(tagging.cancel.is_set())
            self.assertFalse(w.cancel_button.isEnabled())
        finally:
            w.operation_task = w.tagging.task = None
            w.cancel_button.setEnabled(True)
            w.operation_status.setText('Ready')

    def test_tagger_reports_task_states_without_hover(self):
        tagger = self.window.tagging
        saved_state, saved_errors = tagger.state, tagger.errors
        try:
            cases = [('Ready', None, False, 'idle'),
                     ('Tagging', None, True, 'running'),
                     ('Complete', 1, False, 'complete'),
                     ('Stopped', 1, False, 'stopped'),
                     ('Stopped on error', 1, False, 'error')]
            for phase, ended, busy, expected in cases:
                tagger.state = dict(phase=phase, ended=ended, started=None, done=1, total=3, current='')
                tagger.task = SimpleNamespace(cancel=threading.Event()) if busy else None
                tagger.errors = []
                tagger.render()
                self.assertEqual(tagger.button.task_state, expected)
                self.assertEqual(tagger.button._animation.isActive(), expected == 'running')
                self.assertIn('Auto-tag images', tagger.button.toolTip())
                self.assertIn(phase, tagger.button.accessibleName())
        finally:
            tagger.task = None
            tagger.state, tagger.errors = saved_state, saved_errors
            tagger.render()


if __name__ == '__main__':
    unittest.main()
