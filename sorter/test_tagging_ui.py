"""Offscreen integration checks using temporary files, never user libraries."""
import copy
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMenu, QPlainTextEdit, QDialogButtonBox, QDialog
import app


class FakeProcess:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def close(self, **kwargs):
        pass

    def tag(self, path, general, character, phase, model_dir):
        self.calls.append(path.name)
        phase('Tagging image…')
        if path.name == self.fail:
            raise ValueError('Test decode failure')
        return ['blue_hair', 'solo']


class TaggingUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'workspace'
        self.root.mkdir()
        self.patches = [patch.object(app, 'APP_DIR', self.base), patch.object(app, 'DATA_DIR', self.base / '.local')]
        for item in self.patches:
            item.start()
        config = copy.deepcopy(app.DEFAULTS)
        config['sort_root'] = str(self.root)
        self.window = app.Window(config)
        self.tagging = self.window.tagging
        self.qt.processEvents()
        self.wait()

    def tearDown(self):
        self.wait()
        self.window.close()
        self.qt.processEvents()
        self.window.deleteLater()
        self.qt.processEvents()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def wait(self, timeout=10):
        end = time.monotonic() + timeout
        while self.tagging.busy or self.window.operation_task:
            self.qt.processEvents()
            if time.monotonic() > end:
                self.fail('Background task timed out')
            time.sleep(.01)
        self.qt.processEvents()

    def image(self, name):
        path = self.root / name
        path.write_bytes(name.encode())
        return path

    def actions(self, paths):
        menu = QMenu()
        self.tagging.add_image_actions(menu, paths)
        self.tagging.add_folder_action(menu, paths)
        return [a.text() for a in menu.actions() if not a.isSeparator()]

    def test_queue_stops_first_error_and_keeps_completed(self):
        paths = [self.image(name) for name in ('a.png', 'b.png', 'c.png')]
        fake = FakeProcess('b.png')
        self.tagging.process = fake
        self.tagging.start([self.root])
        self.wait()
        self.assertEqual(fake.calls, ['a.png', 'b.png'])
        self.assertEqual(self.tagging.state['done'], 1)
        self.assertEqual(self.tagging.state['total'], 3)
        self.assertTrue(self.tagging.store.has_tags(paths[0]))
        self.assertFalse(self.tagging.store.has_tags(paths[1]))
        self.assertEqual(self.tagging.tabs.currentIndex(), 1)
        self.assertTrue(self.tagging.dialog.isVisible())

    def test_context_menus_and_clipboard_inheritance(self):
        image = self.image('a.png')
        video = self.image('b.mp4')
        self.assertEqual(self.actions([image]), ['Auto-Tag Image/s'])
        self.assertEqual(self.actions([image, video]), [])
        self.assertEqual(self.actions([self.root]), ['Auto-Tag Image/s', 'Associate Tags'])
        self.tagging.process = FakeProcess()
        self.tagging.start([image, self.root])
        self.wait()
        self.assertEqual(self.tagging.state['total'], 1)
        self.assertEqual(self.actions([image]), ['Auto-Tag Image/s', 'Copy Tags'])
        self.tagging.store.associate(self.root, 'game_name solo')
        self.tagging.copy_tags(image)
        self.wait()
        self.assertEqual(self.qt.clipboard().text(), 'game_name solo blue_hair')
        image.write_bytes(b'replacement')
        self.tagging.copy_tags(image)
        self.wait()
        self.assertEqual(self.tagging.state['phase'], 'Copy failed')

    def test_folder_editor_prefills_and_cancel_preserves(self):
        self.tagging.store.associate(self.root, 'existing_tag')
        def edit(dialog):
            text = dialog.findChild(QPlainTextEdit)
            self.assertEqual(text.toPlainText(), 'existing_tag')
            text.setPlainText('updated_tag')
            buttons = dialog.findChild(QDialogButtonBox)
            buttons.accepted.emit()
            return QDialog.Accepted
        with patch.object(self.window, 'exec_dialog', side_effect=edit):
            self.tagging.associate(self.root)
        self.assertEqual(self.tagging.store.folder_text(self.root), 'updated_tag')
        with patch.object(self.window, 'exec_dialog', return_value=QDialog.Rejected):
            self.tagging.associate(self.root)
        self.assertEqual(self.tagging.store.folder_text(self.root), 'updated_tag')

    def test_empty_folder_uses_error_panel(self):
        self.tagging.start([self.root])
        self.wait()
        self.assertIn('No supported images', self.tagging.errors[-1])
        self.assertEqual(self.tagging.state['total'], 0)

    def test_app_rename_and_undo_preserve_tags(self):
        image = self.image('one.png')
        self.tagging.process = FakeProcess()
        self.tagging.start([image])
        self.wait()
        self.window.run_operations('Rename', [(str(image), lambda: self.window.service.rename(image, 'two.png'))])
        self.wait()
        self.assertTrue(self.tagging.store.has_tags(self.root / 'two.png'))
        self.window.undo()
        self.wait()
        self.assertTrue(self.tagging.store.has_tags(image))

    def test_cancel_finishes_current_image_only(self):
        paths = [self.image(name) for name in ('a.png', 'b.png')]
        entered, release = threading.Event(), threading.Event()
        fake = FakeProcess()
        original = fake.tag
        def slow(*args):
            entered.set()
            release.wait(5)
            return original(*args)
        fake.tag = slow
        self.tagging.process = fake
        self.tagging.start(paths)
        self.assertTrue(entered.wait(5))
        self.tagging.cancel()
        release.set()
        self.wait()
        self.assertEqual(fake.calls, ['a.png'])
        self.assertEqual(self.tagging.state['phase'], 'Stopped')
        self.assertTrue(self.tagging.store.has_tags(paths[0]))

    def test_mutations_blocked_during_queue(self):
        image = self.image('one.png')
        entered, release = threading.Event(), threading.Event()
        fake = FakeProcess()
        original = fake.tag
        def slow(*args):
            entered.set()
            release.wait(5)
            return original(*args)
        fake.tag = slow
        self.tagging.process = fake
        self.tagging.start([image])
        self.assertTrue(entered.wait(5))
        self.window.run_operations('Rename', [(str(image), lambda: self.window.service.rename(image, 'new.png'))])
        self.assertTrue(image.exists())
        self.assertIsNone(self.window.operation_task)
        release.set()
        self.wait()

    def test_save_failure_stops_remaining_images(self):
        paths = [self.image(name) for name in ('a.png', 'b.png')]
        self.tagging.process = FakeProcess()
        with patch.object(self.tagging.store, 'record', side_effect=OSError('disk full')):
            self.tagging.start(paths)
            self.wait()
        self.assertEqual(self.tagging.process.calls, ['a.png'])
        self.assertEqual(self.tagging.state['done'], 0)
        self.assertIn('disk full', self.tagging.errors[-1])


if __name__ == '__main__':
    unittest.main()
