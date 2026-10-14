import copy
import os
import sys
import tempfile
import time
import struct
import unittest
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as sorter
from PySide6.QtCore import Qt, QItemSelectionModel
from PySide6.QtGui import QImage, QColor, QFontDatabase
from PySide6.QtWidgets import QApplication, QDialog

qt = QApplication.instance() or QApplication([])
font = Path('C:/Windows/Fonts/segoeui.ttf')
if font.exists():
    QFontDatabase.addApplicationFont(str(font))
qt.setStyle('Fusion')
qt.setStyleSheet(sorter.STYLE)


def wait_for(predicate, seconds=10):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        qt.processEvents()
        if predicate():
            qt.processEvents()
            return
        time.sleep(.02)
    raise AssertionError('Timed out waiting for background work')


class UITests(unittest.TestCase):
    def setUp(self):
        self.signal_errors = []
        self.old_hook = sys.excepthook
        sys.excepthook = lambda kind, value, tb: self.signal_errors.append(str(value))
        self.addCleanup(setattr, sys, 'excepthook', self.old_hook)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'Library' / 'Misty;Pokemon' / 'Set 01'
        self.source.mkdir(parents=True)
        self.target = self.base / 'Promotion'
        self.target.mkdir()
        for i, color in enumerate(('#749582', '#8b87a1', '#aa8a6a', '#5b809b')):
            image = QImage(480, 640, QImage.Format_RGB32)
            image.fill(QColor(color))
            self.assertTrue(image.save(str(self.source / f'image_{i + 1:02}.png')))
        # One real GIF fixture, plus a deliberately undecodable video to check
        # graceful playback errors without needing ffmpeg in production.
        import base64
        (self.source / 'animated.gif').write_bytes(base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'))
        (self.source / 'clip.mp4').write_bytes(b'invalid media')
        config = copy.deepcopy(sorter.DEFAULTS)
        config['sources'] = [str(self.base / 'Library')]
        config['sort_root'] = str(self.target)
        self.old_data = sorter.DATA_DIR
        sorter.DATA_DIR = self.base / 'data'
        self.window = sorter.Window(config)
        self.window.show()
        self.addCleanup(self.clean_window)
        wait_for(lambda: not self.window.jobs.tasks)

    def clean_window(self):
        self.window.close()
        qt.processEvents()
        self.window.deleteLater()
        qt.processEvents()
        sorter.DATA_DIR = self.old_data
        self.assertEqual(self.signal_errors, [])

    def test_full_workflow_and_render(self):
        window = self.window
        browser = window.library
        self.assertEqual(browser.proxy.rowCount(), 8)
        browser.search.setText('Pokemon g')
        browser.filter_rows()
        self.assertEqual(browser.proxy.rowCount(), 8)
        browser.kind.setCurrentText('Image')
        self.assertEqual(browser.proxy.rowCount(), 4)
        from PySide6.QtTest import QTest
        qt.processEvents()
        QTest.mouseClick(browser.grid.viewport(), Qt.LeftButton, Qt.NoModifier, browser.grid.visualRect(browser.proxy.index(0, 0)).center())
        selected = browser.selected_paths()
        self.assertEqual(len(selected), 1)
        window.shelf.add_paths(selected)
        window.shelf.add_paths(selected)
        self.assertEqual(window.shelf.list.count(), 1)
        window.transfer(selected, self.target)
        wait_for(lambda: window.operation_task is None and not window.jobs.tasks)
        self.assertTrue((self.target / selected[0].name).exists())
        self.assertTrue(selected[0].exists())
        window.sorter.grid.selectionModel().select(window.sorter.proxy.index(0, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        window.toggle_flags(window.sorter)
        self.assertEqual(len(window.config['flags']), 1)
        window.sorter.flagged.setChecked(True)
        self.assertEqual(window.sorter.proxy.rowCount(), 1)
        window.sorter.flagged.setChecked(False)
        window.undo()
        wait_for(lambda: window.operation_task is None and not window.jobs.tasks)
        self.assertFalse((self.target / selected[0].name).exists())
        window.split_toggle.setChecked(True)
        qt.processEvents()
        window.split_toggle.setChecked(False)
        qt.processEvents()
        destination = self.old_data / 'qa'
        destination.mkdir(parents=True, exist_ok=True)
        self.assertTrue(window.grab().save(str(destination / 'workspace.png')))
        window.split_toggle.setChecked(True)
        qt.processEvents()
        self.assertTrue(window.grab().save(str(destination / 'split.png')))

    def test_settings_rename_preview_and_media(self):
        settings = sorter.SettingsDialog(self.window.config, self.window)
        settings.test_pattern()
        self.assertIn('GENRE = Pokemon', settings.sample_result.text())
        self.assertEqual(settings.collect()['sources'], self.window.config['sources'])
        settings.deleteLater()
        rename = sorter.RenameDialog([self.source / 'image_01.png'], self.window)
        rename.prefix.setText('promo_')
        self.assertEqual(rename.mapping[0][1], 'promo_image_01.png')
        rename.deleteLater()
        preview = sorter.Preview(self.source / 'image_01.png', [self.source / 'image_01.png'], self.window)
        preview.show()
        qt.processEvents()
        self.assertFalse(preview.pixmap.isNull())
        preview.zoom(1.2)
        preview.close()
        preview.deleteLater()
        gif = sorter.Preview(self.source / 'animated.gif', [self.source / 'animated.gif'], self.window)
        gif.show()
        self.assertTrue(gif.movie.isValid())
        gif.close()
        gif.deleteLater()
        video = sorter.Preview(self.source / 'clip.mp4', [self.source / 'clip.mp4'], self.window)
        video.show()
        wait_for(lambda: bool(video.error.text()))
        self.assertIn('Playback unavailable', video.error.text())
        video.close()
        video.deleteLater()

    def test_real_video_playback(self):
        from PySide6.QtCore import QBuffer, QIODevice
        def chunk(name, content):
            return name + struct.pack('<I', len(content)) + content + (b'\0' if len(content) % 2 else b'')
        frames = []
        for color in ('red', 'green', 'blue', 'yellow'):
            image = QImage(160, 120, QImage.Format_RGB32)
            image.fill(QColor(color))
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            image.save(buffer, 'JPEG')
            frames.append(bytes(buffer.data()))
        maximum = max(map(len, frames))
        avih = struct.pack('<14I', 500000, maximum * 2, 0, 0, 4, 0, 1, maximum, 160, 120, 0, 0, 0, 0)
        strh = struct.pack('<4s4sIHHIIIIIIIIhhhh', b'vids', b'MJPG', 0, 0, 0, 0, 1, 2, 0, 4, maximum, 0xffffffff, 0, 0, 0, 160, 120)
        strf = struct.pack('<IiiHH4sIiiII', 40, 160, 120, 1, 24, b'MJPG', maximum, 0, 0, 0, 0)
        stream = chunk(b'LIST', b'strl' + chunk(b'strh', strh) + chunk(b'strf', strf))
        header = chunk(b'LIST', b'hdrl' + chunk(b'avih', avih) + stream)
        movie = chunk(b'LIST', b'movi' + b''.join(chunk(b'00dc', f) for f in frames))
        path = self.source / 'playable.avi'
        path.write_bytes(chunk(b'RIFF', b'AVI ' + header + movie))
        preview = sorter.Preview(path, [path], self.window)
        preview.show()
        wait_for(lambda: preview.player.position() > 0 or bool(preview.error.text()))
        self.assertFalse(preview.error.text(), preview.error.text())
        self.assertGreater(preview.player.duration(), 0)
        preview.close()
        qt.processEvents()
        preview.deleteLater()

    def test_drag_copy_move_and_reference_updates(self):
        from PySide6.QtCore import QMimeData, QUrl, QPointF, QByteArray
        from PySide6.QtGui import QDropEvent
        window = self.window
        source = self.source / 'image_01.png'
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(source))])
        mime.setData('application/x-vael-sorter', QByteArray(b'1'))
        event = QDropEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        window.sorter.grid.dropEvent(event)
        wait_for(lambda: window.operation_task is None and not window.jobs.tasks)
        copied = self.target / source.name
        self.assertTrue(copied.exists())
        self.assertTrue(source.exists())
        folder = window.service.mkdir(self.target, 'Site A')
        window.second.navigate(folder)
        wait_for(lambda: not window.jobs.tasks)
        window.config['flags'] = [str(copied)]
        window.shelf.add_paths([copied])
        mime.setUrls([QUrl.fromLocalFile(str(copied))])
        event = QDropEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        window.second.grid.dropEvent(event)
        wait_for(lambda: window.operation_task is None and not window.jobs.tasks)
        moved = folder / copied.name
        self.assertTrue(moved.exists())
        self.assertFalse(copied.exists())
        self.assertEqual(window.config['flags'], [str(moved)])
        self.assertEqual(window.shelf.list.item(0).data(Qt.UserRole), str(moved))
        window.undo()
        wait_for(lambda: window.operation_task is None and not window.jobs.tasks)
        self.assertTrue(copied.exists())
        self.assertEqual(window.config['flags'], [str(copied)])


if __name__ == '__main__':
    unittest.main()
