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
        self.window.activity.active.clear()
        self.window.activity.clear_notice()

    def test_no_collection_surface_or_actions(self):
        self.assertFalse(hasattr(self.window, 'shelf'))
        self.assertFalse(hasattr(self.window, 'workspace_button'))
        for browser in (self.window.library, self.window.sorter):
            menu = browser.build_options_menu()
            self.assertNotIn('Shelf', ' '.join(a.text() for a in menu.actions()))
            menu.deleteLater()

    def test_renames_reconcile_groups_without_collection_state(self):
        w = self.window
        source, target = self.workspace / 'before.txt', self.workspace / 'after.txt'
        saved = copy.deepcopy(w.config['groups'])
        source.write_text('temporary test file', encoding='utf-8')
        w.config['groups'] = [dict(id='test', members=[str(source)], notes=[], color='#568577')]
        try:
            source.rename(target)
            w.reconcile_references([(str(source), target)])
            self.assertEqual(w.config['groups'][0]['members'], [str(target)])
            target.rename(source)
            w.reconcile_references([(str(target), source)])
            self.assertEqual(w.config['groups'][0]['members'], [str(source)])
        finally:
            w.config['groups'] = saved
            source.unlink(missing_ok=True)
            target.unlink(missing_ok=True)

    def test_promoted_layout_restores_experimental_preferences(self):
        w = self.window
        w.config['layout'] = {'split': False}
        w.config['rework_layout'] = {'split': True}
        try:
            w.restore_layout()
            self.assertTrue(w.split_toggle.isChecked())
        finally:
            w.config.pop('rework_layout', None)

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

    def test_utility_focus_marker_only_follows_keyboard_navigation(self):
        from PySide6.QtGui import QFocusEvent
        button = self.window.tagging.button
        for reason, expected in ((sorter.Qt.ActiveWindowFocusReason, False),
                                 (sorter.Qt.TabFocusReason, True),
                                 (sorter.Qt.MouseFocusReason, False)):
            self.app.sendEvent(button, QFocusEvent(sorter.QEvent.FocusIn, reason))
            self.assertEqual(button.property('keyboardFocus'), expected)

    def test_center_separator_is_compact_and_has_no_shadow(self):
        self.assertEqual(self.window.vertical.handleWidth(), 4)
        self.assertIsNone(self.window.vertical.handle(1).graphicsEffect())

    def test_source_header_switch_refresh_hide_restore_and_overflow(self):
        w, browser = self.window, self.window.library
        folders = [self.root / f'Source {i} with a descriptive folder name' for i in range(8)]
        for folder in folders:
            folder.mkdir(exist_ok=True)
        w.config['sources'] = list(map(str, folders))
        try:
            browser.update_roots()
            settle(lambda: browser.source_tabs.count() == 8)
            self.assertFalse(browser.tabs.tabBar().isVisible())
            self.assertTrue(browser.pane_header.isAncestorOf(browser.source_tabs))
            browser.source_tabs.setCurrentIndex(7)
            self.assertEqual(browser.tabs.currentIndex(), 7)
            browser.reload_current_folder()
            settle(lambda: browser.source_tabs.tabToolTip(7) == str(folders[7]))
            browser.close_tab_for_session(str(folders[7]))
            self.assertEqual(browser.source_tabs.count(), 7)
            browser._restore_all_closed_folders()
            self.assertEqual(browser.source_tabs.count(), 8)
            w.resize(720, 520)
            settle()
            self.assertEqual(w.width(), 720)
            self.assertTrue(any(button.isVisible() for button in browser.source_tabs.findChildren(sorter.QToolButton)))
            self.assertGreater(browser.tools_btn.mapTo(w, sorter.QPoint()).x(),
                               browser.source_tabs.mapTo(w, sorter.QPoint()).x())
            # Config edits update both names and tooltips, without creating another row.
            w.config['source_names'] = {str(folders[0]): 'Renamed source'}
            browser.update_roots()
            self.assertEqual(browser.source_tabs.tabText(0), 'Renamed source')
            for folder in folders:
                browser.close_tab_for_session(str(folder))
            self.assertEqual(browser.source_tabs.count(), 0)
            self.assertTrue(browser.restore_hidden_btn.isVisible())
        finally:
            w.config['sources'] = []
            w.config['source_names'] = {}
            browser.update_roots()
        self.assertEqual(browser.source_tabs.count(), 0)
        self.assertTrue(browser.pane_header.isVisible())

    def test_activity_never_increases_footer_height_or_moves_toolbar(self):
        w = self.window
        w.resize(720, 520)
        w.split_toggle.setChecked(True)
        settle()
        buttons = [w.split_toggle, *w.history_controls[0], w.tagging.button]
        positions = [button.mapTo(w, sorter.QPoint()) for button in buttons]
        heights = [b.result_bar.height() for b in (w.sorter, w.second)]
        for b in (w.sorter, w.second):
            self.assertEqual(b.result_bar.height(), b.selection_summary.sizeHint().height())
            w.activity.publish(str(id(b)), b, 'Copying a very long filename ' * 10,
                               busy=True, total=10, done=2, stop=lambda: None)
        settle()
        self.assertEqual(heights, [b.result_bar.height() for b in (w.sorter, w.second)])
        self.assertEqual(positions, [button.mapTo(w, sorter.QPoint()) for button in buttons])
        self.assertEqual(w.width(), 720)
        self.assertFalse(hasattr(w, 'file_task_controls'))

    def test_latest_notification_replaces_previous_but_not_active_tasks(self):
        w = self.window
        w.split_toggle.setChecked(True)
        w.activity.publish('left', w.sorter, 'Left is working', busy=True)
        w.activity.publish('right', w.second, 'Right is working', busy=True)
        self.assertIn('Left is working', w.sorter.activity_text._message)
        self.assertIn('Right is working', w.second.activity_text._message)
        w.activity.publish('left', w.sorter, 'Left finished')
        self.assertIn('Left finished', w.sorter.activity_text._message)
        self.assertIn('Right is working', w.second.activity_text._message)
        w.activity.publish('right', w.second, 'Right finished')
        self.assertEqual(w.sorter.activity_text._message, '')
        self.assertIn('Right finished', w.second.activity_text._message)
        self.assertEqual(w.activity.timer.interval(), 10000)
        w.activity.timer.start(20)
        settle(lambda: w.activity.notice is None)
        self.assertEqual(w.second.activity_text._message, '')
        w.activity.timer.setInterval(10000)

    def test_file_job_keeps_its_owner_after_focus_changes(self):
        w = self.window
        w.split_toggle.setChecked(True)
        started, release = threading.Event(), threading.Event()
        def work():
            started.set()
            release.wait(2)
        try:
            w.run_operations('Sample work', [('sample', work)], browser=w.sorter)
            settle(started.is_set)
            w.second.grid.setFocus()
            settle()
            self.assertIs(w.activity.active['files']['browser'], w.sorter)
        finally:
            release.set()
            settle(lambda: w.operation_task is None)
        self.assertIs(w.activity.notice['browser'], w.sorter)

    def test_stop_button_only_cancels_file_operations(self):
        w = self.window
        files = SimpleNamespace(cancel=threading.Event())
        tagging = SimpleNamespace(cancel=threading.Event())
        w.operation_task, w.tagging.task = files, tagging
        w._operation_browser = w.sorter
        try:
            w.stop_file_operation()
            self.assertTrue(files.cancel.is_set())
            self.assertFalse(tagging.cancel.is_set())
            self.assertIsNone(w.activity.active['files']['stop'])
        finally:
            w.operation_task = w.tagging.task = None
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
                self.assertFalse(hasattr(tagger.button, '_animation'))
                self.assertEqual('tagging' in self.window.activity.active, busy)
                self.assertIn('Auto-tag images', tagger.button.toolTip())
                self.assertIn(phase, tagger.button.accessibleName())
                if not busy and expected != 'idle':
                    self.window.activity.clear_notice()
                    tagger.render()
                    self.assertIsNone(self.window.activity.notice)
        finally:
            tagger.task = None
            tagger.state, tagger.errors = saved_state, saved_errors
            tagger.render()

    def test_folder_focus_does_not_offer_hidden_layout_changes(self):
        w = self.window
        w.split_toggle.setChecked(True)
        w.focus_folder(self.workspace, w.sorter)
        try:
            self.assertFalse(w.split_toggle.isEnabled())
            self.assertIn('Exit folder focus', w.split_toggle.toolTip())
            self.assertTrue(w.focus_browser.isVisible())
        finally:
            w.exit_folder_focus()
        self.assertTrue(w.split_toggle.isEnabled())
        self.assertTrue(w.split_toggle.isChecked())
        self.assertTrue(w.sorter.isVisible())
        self.assertTrue(w.second.isVisible())

    @unittest.skipUnless(sys.platform == 'win32', 'Windows native frame regression')
    def test_native_resize_margin_does_not_steal_button_clicks(self):
        w = self.window
        settle()
        for button in (w.split_toggle, w.tagging.button, w._max_btn):
            local = button.mapTo(w, sorter.QPoint(button.width() // 2, 1))
            self.assertLess(local.y(), sorter._RESIZE_BORDER_PX)
            point = w.mapToGlobal(local)
            msg = SimpleNamespace(lParam=((point.y() & 0xffff) << 16) | (point.x() & 0xffff))
            self.assertEqual(w._win_hit_test(msg), (True, sorter._HTCLIENT))
        # The empty top edge still resizes the window.
        point = w.mapToGlobal(sorter.QPoint(170, 1))
        msg = SimpleNamespace(lParam=((point.y() & 0xffff) << 16) | (point.x() & 0xffff))
        self.assertEqual(w._win_hit_test(msg), (True, sorter._HTTOP))


if __name__ == '__main__':
    unittest.main()
