"""Render production dialogs with no database or preferences side effects."""
import ast
import os
from pathlib import Path
from typing import Optional
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import *
from PySide6.QtGui import *
from PySide6.QtWidgets import *

_SESSION_POS = {}
names = {"_DraggableDialog", "ConfirmRemoveDialog", "AddTagDialog",
         "AddScriptDialog", "AddIdentifierDialog", "CreateNoteDialog", "apply_style"}
tree = ast.parse(Path(__file__).with_name("app.py").read_text(encoding="utf-8-sig"))
exec(compile(ast.Module(body=[
    node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    and node.name in names
], type_ignores=[]), "app.py", "exec"))


class DialogCorners(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_style(cls.app)

    def test_custom_dialogs_have_clear_corners_and_full_size_content(self):
        for factory in (lambda: ConfirmRemoveDialog("Test"), AddTagDialog,
                        AddScriptDialog, AddIdentifierDialog, CreateNoteDialog):
            dialog = factory()
            with self.subTest(dialog=type(dialog).__name__):
                image = dialog.grab().toImage()
                w, h = image.width(), image.height()
                for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
                    self.assertEqual(image.pixelColor(x, y).alpha(), 0)
                self.assertEqual(image.pixelColor(w // 2, h // 2).alpha(), 255)
                frame = dialog.findChild(QFrame, "editDialogFrame")
                self.assertEqual(frame.geometry(), dialog.rect())
                self.assertIsNone(dialog.findChild(QFrame, "dialogShadow"))
                # Closing must still reject without saving or accepting changes.
                dialog.findChild(QToolButton, "dbDialogClose").click()
                self.assertEqual(dialog.result(), QDialog.Rejected)
            dialog.close()


if __name__ == "__main__":
    unittest.main()
