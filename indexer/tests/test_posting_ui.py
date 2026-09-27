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
        self.path.write_text(json.dumps({"version": 1, "modes": {"Mira": {"Download": {
            "Good": {"name": "Good.zip", "status": "complete", "texts": [{"label": "Title", "value": "ready"}]},
            "Bad": {"name": "Bad.zip", "status": "incomplete", "texts": [{"label": "Do not copy", "value": "partial"}],
                    "errors": [{"message": "Missing character tags"}]}
        }, "Promote": {}}}, "errors": []}), encoding="utf-8")
        self.addCleanup(patch.stopall)
        patch.object(indexer, "POSTING_INFO_FILE", self.path).start()
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
        entries = section._child_sections[0]._child_sections[0]._child_sections
        good, bad = entries
        copies = [b for b in good.findChildren(QToolButton) if b.text() == "Copy"]
        self.assertEqual(len(copies), 1)
        copies[0].click()
        self.assertEqual(self.qt.clipboard().text(), "ready")
        self.assertFalse(any(b.text() == "Copy" for b in bad.findChildren(QToolButton)))
        self.assertTrue(any(l.text() == "Missing character tags" for l in bad.findChildren(QLabel)))
        self.assertIn("#ed9292", bad._header.styleSheet())

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
        entries = section._child_sections[0]._child_sections[0]._child_sections
        self.assertEqual([e._header.text() for e in entries], ["Bad.zip"])
        self.assertEqual(notes.read_bytes(), before)

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
