"""Render the real workspace classes without starting scans or loading settings."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QSize, QRect, QRectF, QPoint, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QIcon, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QApplication, QListView, QStyledItemDelegate, QStyle

DragViewMixin = type("DragViewMixin", (), {})
GROUP = {"id": "test", "color": "#a06030"}


def group_for(config, path):
    return GROUP if config["grouped"] else None


tree = ast.parse(Path(__file__).with_name("app.py").read_text(encoding="utf-8-sig"))
exec(compile(ast.Module(body=[
    node for node in tree.body if isinstance(node, ast.ClassDef)
    and node.name in {"WorkspaceGrid", "WorkspaceEntryDelegate"}
], type_ignores=[]), "app.py", "exec"))


class WorkspaceCorners(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet("QListView { background: #0a0a0a; border: none; }")

    def test_group_reaches_corners_without_tinting_image(self):
        grid = WorkspaceGrid()
        grid.resize(150, 180)
        config = {"grouped": False}
        grid.browser = SimpleNamespace(entry_zoom=100, window=SimpleNamespace(config=config))
        model = QStandardItemModel()
        item = QStandardItem()
        item.setData(SimpleNamespace(path="test", kind="Image", name="test.png", is_dir=False), Qt.UserRole)
        image = QPixmap(96, 116)
        image.fill(QColor("#abcdef"))
        item.setData(image, Qt.UserRole + 2)
        model.appendRow(item)
        grid.setModel(model)
        grid.setViewMode(QListView.IconMode)
        grid.setGridSize(QSize(102, 134))
        grid.setSpacing(0)
        grid.setItemDelegate(WorkspaceEntryDelegate(grid))
        grid.show()
        self.app.processEvents()
        plain = grid.grab().toImage()
        config["grouped"] = True
        grouped = grid.grab().toImage()
        rect = grid.visualRect(model.index(0, 0))
        origin = grid.viewport().mapTo(grid, QPoint(0, 0))
        corner = origin + rect.topLeft() + QPoint(0, 7)
        centre = origin + rect.center()
        self.assertNotEqual(plain.pixelColor(corner), grouped.pixelColor(corner))
        self.assertEqual(plain.pixelColor(centre), grouped.pixelColor(centre))
        self.assertEqual(grid.group_at(rect.topLeft() + QPoint(0, 7)), GROUP)
        self.assertIsNone(grid.group_at(rect.center()))
        grid.close()


if __name__ == "__main__":
    unittest.main()
