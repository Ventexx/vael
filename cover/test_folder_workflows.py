"""Folder workflow rules and settings regression checks (no server required)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

# Create Qt before importing app's shared QObject workers and thread pools.
from PySide6.QtWidgets import QApplication
_QT_APP = QApplication.instance() or QApplication([])
import app
# These tests do not create MainWindow, which normally stops this worker.
app.PIXMAP_WORKER.stop()
app.PIXMAP_WORKER.wait()


class FolderRulesTests(unittest.TestCase):
    def test_placeholders_literals_and_whole_names(self):
        matcher = app.folder_pattern_regex("[]-[]_[]")
        for name in ("Fantasy-Alice_Beach", "A-B_C", "Long genre-Character 12_Scene 300", "日本-人物_場所"):
            self.assertIsNotNone(matcher.fullmatch(name))
        for name in ("-Alice_Beach", "Fantasy-_Beach", "Fantasy-Alice_", "Fantasy_Alice-Beach", "unstructured"):
            self.assertIsNone(matcher.fullmatch(name))
        literal = app.folder_pattern_regex("set.(v1)+_[]")
        self.assertIsNotNone(literal.fullmatch("SET.(V1)+_anything"))
        self.assertIsNone(literal.fullmatch("setXv111_anything"))
        self.assertIsNone(app.folder_pattern_regex("Favorites").fullmatch("My Favorites"))

    def test_invalid_patterns(self):
        for pattern in ("", "  ", "[text]-[]", "[]-[", "[][]"):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                app.folder_pattern_regex(pattern)

    def test_priority_missing_workflows_and_promotion_names(self):
        rules = [{"pattern": "[]-OC_[]p", "workflow_id": "specific"},
                 {"pattern": "[]-[]_[]", "workflow_id": "general"}]
        self.assertEqual(app.matching_folder_workflow(".123-OC_20p", rules, {"specific", "general"}), "specific")
        self.assertEqual(app.matching_folder_workflow("123-OC_20p", rules, {"general"}), "general")
        self.assertEqual(app.matching_folder_workflow("123-OC_20p", rules[::-1], {"specific", "general"}), "general")
        self.assertIsNone(app.matching_folder_workflow("Other", rules, {"specific", "general"}))

    def test_legacy_migration_and_config_round_trip(self):
        config = {"folder_workflows": {"oc": "one", "character_scene": "two"}}
        rules = app.folder_workflow_rules(config)
        self.assertEqual(len(rules), 3)
        self.assertEqual(app.matching_folder_workflow("123-OC_20p-2", rules, {"one", "two"}), "one")
        config["folder_workflows"] = rules
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(app, "CONFIG_FILE", Path(directory) / "settings.json"):
                self.assertTrue(app.save_config(config))
                self.assertEqual(app.folder_workflow_rules(app.load_config()), rules)


class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = _QT_APP
        cls.qt.setStyleSheet(app.STYLE_QSS)

    def setUp(self):
        self.parent = app.QWidget()
        self.parent.server = app.DEFAULT_SERVER
        self.parent.output_dir = app.DEFAULT_OUTPUT_DIR
        self.parent.config_data = {"folder_workflows": [{"pattern": "[]-[]_[]", "workflow_id": "two"}]}
        self.parent.workflow_states = [SimpleNamespace(workflow_id=key, name="Workflow " + key, workflow_path=key + ".json") for key in ("one", "two")]
        self.parent.persist_all = Mock()
        self.parent.image_browser = SimpleNamespace(reload_folders=Mock())
        self.parent.workflow_sidebar = SimpleNamespace(list=Mock())
        self.dialog = app.SettingsDialog(self.parent)
        self.dialog.show()
        self.qt.processEvents()

    def tearDown(self):
        self.dialog.close()
        self.parent.close()
        self.parent.deleteLater()
        self.qt.sendPostedEvents(None, app.QEvent.DeferredDelete)

    def test_collapsed_sections_and_plain_help(self):
        toggles = [b for b in self.dialog.findChildren(app.QToolButton) if b.isCheckable()]
        self.assertEqual(len(toggles), 3)
        self.assertTrue(all(not b.isChecked() for b in toggles))
        self.assertFalse(self.dialog.rule_table.isVisible())
        toggles[-1].click()
        self.assertTrue(self.dialog.rule_table.isVisible())
        self.assertFalse(any(b.text() == "?" for b in self.dialog.findChildren(app.QToolButton)))
        self.assertTrue(all(label.toolTip() for label in self.dialog.findChildren(app.QLabel) if label.text() == "?"))

    def test_edit_preview_reorder_remove_save_and_cancel(self):
        self.dialog.rule_preview_edit.setText("Fantasy-Alice_Beach")
        self.assertIn("Workflow two", self.dialog.rule_preview_label.text())
        self.dialog._add_workflow_rule()
        self.dialog.rule_table.cellWidget(1, 0).setText("[]")
        self.dialog.rule_table.cellWidget(1, 1).setCurrentIndex(1)
        self.dialog._move_workflow_rule(-1)
        self.assertIn("Workflow one", self.dialog.rule_preview_label.text())
        self.dialog._remove_workflow_rule()
        self.assertEqual(len(self.dialog.workflow_rules), 1)
        self.dialog._save()
        self.parent.persist_all.assert_called_once()
        before = copy.deepcopy(self.parent.config_data)
        cancelled = app.SettingsDialog(self.parent)
        cancelled.rule_table.cellWidget(0, 0).setText("changed")
        cancelled.reject()
        self.assertEqual(self.parent.config_data, before)
        reopened = app.SettingsDialog(self.parent)
        self.assertEqual(reopened.rule_table.cellWidget(0, 0).text(), "[]-[]_[]")
        reopened.close()

    def test_invalid_rule_does_not_save(self):
        self.dialog._add_workflow_rule()
        with patch.object(app.QMessageBox, "warning") as warning:
            self.dialog._save()
        warning.assert_called_once()
        self.parent.persist_all.assert_not_called()

    def test_selection_survives_workflow_reordering(self):
        self.parent.workflow_states.reverse()
        app.MainWindow.select_folder_workflow(self.parent, "C:/images/Fantasy-Alice_Beach")
        self.parent.workflow_sidebar.list.setCurrentRow.assert_called_once_with(0)
        self.parent.workflow_sidebar.list.reset_mock()
        app.MainWindow.select_folder_workflow(self.parent, "C:/images/other")
        self.parent.workflow_sidebar.list.setCurrentRow.assert_not_called()
        state = app.WorkflowState(self.parent)
        self.assertEqual(app.WorkflowState(self.parent, state.to_dict()).workflow_id, state.workflow_id)


if __name__ == "__main__":
    unittest.main()
