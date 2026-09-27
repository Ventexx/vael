import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import app as indexer
    from PySide6.QtWidgets import QApplication, QLabel, QToolButton
except ImportError:
    indexer = None


@unittest.skipIf(indexer is None, "PySide6 is required for UI tests")
class PostingUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        indexer.PIXMAP_WORKER.requestInterruption()
        indexer.PIXMAP_WORKER.wait(3000)
        cls.qt.shutdown()

    def flush_widgets(self):
        # Flush deferred widget deletion before QApplication is destroyed.
        from PySide6.QtCore import QCoreApplication, QEvent
        for widget in self.qt.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def setUp(self):
        self.addCleanup(self.flush_widgets)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "posting_info.json"
        self.script = self.root / "posting_info.py"
        self.script.write_text("# personal generator")
        self.path.write_text(json.dumps({"version": 1, "modes": {"Mira": {"Download": {
            "Good": {"name": "Good.zip", "status": "complete", "texts": [{"label": "Title", "value": "ready"}]},
            "Bad": {"name": "Bad.zip", "status": "incomplete", "texts": [{"label": "Do not copy", "value": "partial"}],
                    "errors": [{"message": "Missing character tags"}]}
        }, "Promote": {}}}, "errors": []}), encoding="utf-8")
        self.addCleanup(patch.stopall)
        patch.object(indexer, "POSTING_INFO_FILE", self.path).start()
        patch.object(indexer, "POSTING_INFO_SCRIPT", self.script).start()
        patch.object(indexer, "DEV_MODE", False).start()

    def test_readonly_section_errors_copy_and_reload(self):
        requests = []
        section = indexer._posting_section(lambda: requests.append(True))
        self.addCleanup(section.deleteLater)
        self.assertFalse(section._expanded)
        self.assertTrue(section._reload_button.isHidden())
        section._set_expanded(True, focus=False)
        self.assertFalse(section._reload_button.isHidden())
        section._reload_button.click()
        self.assertEqual(requests, [True])
        entries = section._child_sections[0]._child_sections[0]._cards
        good, bad = entries
        self.assertIsInstance(good, indexer.NoteEntryCard)
        self.assertEqual(good.size(), indexer.NoteEntryCard("", "", self.path, None).size())
        self.assertFalse(good.findChildren(QToolButton))
        from PySide6.QtTest import QTest
        QTest.mouseClick(good, indexer.Qt.MouseButton.LeftButton)
        self.assertEqual(self.qt.clipboard().text(), "ready")
        QTest.mouseClick(bad, indexer.Qt.MouseButton.LeftButton)
        self.assertEqual(self.qt.clipboard().text(), "partial")
        self.assertEqual(bad._errors[0]["message"], "Missing character tags")
        self.assertEqual(section._reload_button.objectName(), "folderCopyBtn")
        self.assertEqual(section._reload_button.text(), "")
        self.assertFalse(section._reload_button.icon().isNull())
        menu = bad._error_menu()
        self.assertEqual([a.text() for a in menu.actions()], ["Missing character tags"])
        self.assertIsNone(menu.actions()[0].menu())

    def test_bottom_placement_reload_search_and_normal_notes_preserved(self):
        notes = self.root / "notes.json"
        notes.write_text(json.dumps({"A-Z Sort in Folder": True, "Normal": {"Example": "original"}}))
        before = notes.read_bytes()
        panel = indexer.NotePanel(notes)
        self.addCleanup(panel.deleteLater)
        panel.reload()
        section = panel._layout.itemAt(panel._layout.count() - 2).widget()
        self.assertIsInstance(section, indexer.GeneratedNoteSection)
        section._set_expanded(True, focus=False)
        panel.reload()
        section = panel._layout.itemAt(panel._layout.count() - 2).widget()
        self.assertTrue(section._expanded)
        panel.reload("Bad")
        section = panel._layout.itemAt(panel._layout.count() - 2).widget()
        entries = section._child_sections[0]._child_sections[0]._cards
        self.assertEqual([e._name for e in entries], ["Bad.zip"])
        self.assertEqual(notes.read_bytes(), before)

    def test_section_requires_script_and_generated_data(self):
        self.script.unlink()
        self.assertIsNone(indexer._posting_section(lambda: None))
        self.script.write_text("# personal generator")
        self.path.unlink()
        self.assertIsNone(indexer._posting_section(lambda: None))

    def test_only_folder_metadata_errors_have_ignore_submenus(self):
        ignored = []
        folder = str(self.root / "Honkai")
        card = indexer.GeneratedNoteCard("Example", "partial", [
            {"code": "folder_metadata", "message": "Missing Honkai metadata", "folder_path": folder},
            {"code": "character_tags", "message": "Missing character tags"},
        ], ignored.append)
        self.addCleanup(card.deleteLater)
        menu = card._error_menu()
        actions = menu.actions()
        self.assertEqual(len(actions), 2)
        self.assertIsNone(actions[1].menu())
        action = actions[0].menu().actions()[0]
        self.assertEqual(action.text(), "Ignore Error")
        action.trigger()
        self.assertEqual(ignored, [folder])

    def test_two_texts_are_two_cards_without_entry_folders(self):
        data = json.loads(self.path.read_text())
        entry = data["modes"]["Mira"]["Download"]["Good"]
        entry["texts"].append({"label": "Characters", "value": "A B C D"})
        self.path.write_text(json.dumps(data))
        section = indexer._posting_section(lambda: None)
        self.addCleanup(section.deleteLater)
        destination = section._child_sections[0]._child_sections[0]
        self.assertFalse(destination._child_sections)
        self.assertEqual(len(destination._cards), 3)
        self.assertIn("Characters", destination._cards[1]._name)

    def test_runner_collects_reports_from_all_scripts_and_hides_regular_output(self):
        scripts = []
        for i in range(2):
            path = self.root / f"script{i}.py"
            path.write_text('print("normal progress")\nprint(\'VAEL_NOTIFY {"level":"error","message":"Fix tags"}\')\n')
            scripts.append({"name": f"Script {i}", "path": str(path), "args": ""})
        runner = indexer.ScriptRunner(scripts)
        runner.run()
        self.assertEqual(runner.error, "")
        self.assertEqual(len(runner.notifications), 2)
        self.assertEqual([m["source"] for m in runner.notifications], ["Script 0", "Script 1"])


if __name__ == "__main__":
    unittest.main()
