"""
vael. cover (PySide6 edition) — single-file build
================================================================
Run:  python app.py
Deps: pip install -r requirements.txt   (PySide6, requests)

This file merges main.py, api.py, config.py, workflow_utils.py, and
style.qss (embedded as STYLE_QSS) into one script. No other files are
required to run the app; workflows_config.json and the outputs/ folder
are still created next to this script at runtime.
"""
import os
import sys
import copy
import time
import uuid
import json
import datetime
import requests
from pathlib import Path

from PySide6.QtCore import Qt, QObject, QThread, Signal, QMimeData, QUrl, QSize
from PySide6.QtGui import QPixmap, QImage, QDrag, QDesktopServices, QShortcut, QKeySequence, QIcon, QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QTabBar, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QPushButton, QLineEdit, QFileDialog, QMessageBox, QSplitter,
    QScrollArea, QFrame, QListWidget, QListWidgetItem, QProgressBar, QToolButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QDialog, QSpinBox, QDoubleSpinBox,
    QSizePolicy, QStackedWidget, QMenu
)

# ===========================================================================
# config.py — config persistence
# ===========================================================================
CONFIG_FILE = Path(__file__).resolve().with_name("workflows_config.json")
DEFAULT_SERVER = "http://127.0.0.1:8188"
DEFAULT_OUTPUT_DIR = str(Path(__file__).resolve().with_name("outputs"))

DEFAULTS = {
    "server": DEFAULT_SERVER,
    "output_dir": DEFAULT_OUTPUT_DIR,
    "tabs": [],
    "window_geometry": None,
    "sidebar_width": 340,
}


def load_config():
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            merged = dict(DEFAULTS)
            merged.update(data)
            return merged
        except Exception:
            pass
    return dict(DEFAULTS)


def save_config(config):
    try:
        CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
    except Exception:
        pass

# ===========================================================================
# api.py — ComfyUI HTTP API client
# ===========================================================================
class ComfyAPI:
    def __init__(self, server):
        self.server = server.rstrip("/")
        self.client_id = str(uuid.uuid4())

    def upload_image(self, filepath, dest_filename=None):
        with open(filepath, "rb") as f:
            files = {"image": (dest_filename or os.path.basename(filepath), f)}
            data = {"overwrite": "true"}
            r = requests.post(f"{self.server}/upload/image", files=files, data=data, timeout=60)
        r.raise_for_status()
        result = r.json()
        return result.get("name"), result.get("subfolder", ""), result.get("type", "input")

    def queue_prompt(self, workflow):
        payload = {"prompt": workflow, "client_id": self.client_id}
        r = requests.post(f"{self.server}/prompt", json=payload, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"ComfyUI rejected the prompt ({r.status_code}): {r.text}")
        return r.json()["prompt_id"]

    def get_history(self, prompt_id):
        r = requests.get(f"{self.server}/history/{prompt_id}", timeout=30)
        r.raise_for_status()
        return r.json()

    def get_image_bytes(self, filename, subfolder, folder_type):
        params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
        r = requests.get(f"{self.server}/view", params=params, timeout=120)
        r.raise_for_status()
        return r.content

# ===========================================================================
# workflow_utils.py — ComfyUI API-format workflow JSON helpers
# ===========================================================================
IMAGE_LOADER_CLASSES = {"LoadImage", "LoadImageMask"}
OUTPUT_CLASSES = {"SaveImage", "PreviewImage"}


def load_workflow_file(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("This does not look like an API-format ComfyUI workflow.")
    for node in data.values():
        if not isinstance(node, dict) or "class_type" not in node:
            raise ValueError(
                "This JSON doesn't look like an API-format workflow.\n"
                "In ComfyUI use 'Save (API Format)', not the regular 'Save'."
            )
    return data


def find_load_image_nodes(workflow):
    nodes = [nid for nid, n in workflow.items() if n.get("class_type") in IMAGE_LOADER_CLASSES]
    nodes.sort(key=lambda x: int(x) if str(x).isdigit() else str(x))
    return nodes


def find_node(workflow, identifier):
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    if identifier in workflow:
        return identifier
    low = identifier.lower()
    for nid, node in workflow.items():
        title = node.get("_meta", {}).get("title", "")
        if title.strip().lower() == low:
            return nid
    return None


def get_editable_inputs(node):
    editable = {}
    for key, value in node.get("inputs", {}).items():
        if isinstance(value, list):
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            editable[key] = (value, "int")
        elif isinstance(value, float):
            editable[key] = (value, "float")
        elif isinstance(value, str):
            editable[key] = (value, "string")
    return editable


def find_output_node_ids(workflow):
    ids = [nid for nid, n in workflow.items() if n.get("class_type") in OUTPUT_CLASSES]
    ids.sort(key=lambda x: int(x) if str(x).isdigit() else str(x))
    return ids


def node_label(workflow, node_id):
    node = workflow.get(node_id, {})
    title = node.get("_meta", {}).get("title")
    cls = node.get("class_type", "?")
    return f"{title} (#{node_id})" if title else f"{cls} (#{node_id})"

# ===========================================================================
# style.qss — embedded stylesheet (was a separate file)
# ===========================================================================
STYLE_QSS = """
/* ============================================================
   vael. cover — shares vael.'s design language (near-black
   surfaces, teal accent, frameless window chrome) with the
   rest of the vael. product line (see vael. indexer).
   ============================================================ */

/* ---- Global ---- */
QWidget {
    background-color: #0a0a0a;
    color: #e8e8e8;
    font-family: "Segoe UI", sans-serif;
    font-size: 12px;
}

QMainWindow {
    background-color: #0a0a0a;
}

/* Frameless top-level window must stay fully transparent so only the
   rounded #appShell surface underneath it is ever visible. */
#mainWindowFrameless {
    background: transparent;
}

/* ---- App shell / custom title bar ---- */
#appShell {
    background: #0a0a0a;
    border: 1px solid #5c5c5c;
    border-radius: 10px;
}
#appShell[maximized="true"] {
    border-radius: 0px;
    border: 1px solid rgba(255,255,255,0.07);
}
#appTitleBar {
    background: #0a0a0a;
    border: none;
    border-bottom: 1px solid rgba(255,255,255,0.12);
    border-top-left-radius: 9px;
    border-top-right-radius: 9px;
}
#appShell[maximized="true"] #appTitleBar {
    border-top-left-radius: 0px;
    border-top-right-radius: 0px;
}
#brandLbl {
    background: transparent;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.2px;
}
#winMinBtn, #winMaxBtn, #winCloseBtn {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.11);
    border-radius: 4px;
    color: rgba(200,200,200,0.65);
    font-size: 13px;
    font-weight: 500;
}
#winMinBtn:hover, #winMaxBtn:hover {
    background: rgba(255,255,255,0.09);
    border-color: rgba(255,255,255,0.22);
    color: rgba(230,230,230,0.95);
}
#winMinBtn:pressed, #winMaxBtn:pressed {
    background: rgba(255,255,255,0.04);
}
#winCloseBtn:hover {
    background: rgba(197,79,79,0.75);
    border-color: rgba(220,100,100,0.85);
    color: white;
}
#winCloseBtn:pressed {
    background: rgba(160,60,60,0.85);
}

#appRoot {
    background: #0a0a0a;
    border-bottom-left-radius: 9px;
    border-bottom-right-radius: 9px;
}
#appShell[maximized="true"] #appRoot {
    border-bottom-left-radius: 0px;
    border-bottom-right-radius: 0px;
}

/* ---- Tab bar (workflows) ---- */
QTabWidget::pane {
    border: none;
    border-top: 1px solid rgba(255,255,255,0.07);
    top: -1px;
    background: transparent;
}

QTabBar {
    background: transparent;
}

QTabBar::tab {
    background: transparent;
    color: rgba(200,200,200,0.55);
    padding: 8px 14px;
    margin-right: 2px;
    border-bottom: 2px solid transparent;
    min-width: 60px;
}

QTabBar::tab:selected {
    color: #e8e8e8;
    border-bottom: 2px solid #00d4a0;
}

QTabBar::tab:hover:!selected {
    color: rgba(220,220,220,0.85);
}

/* ---- Buttons ---- */
QPushButton {
    background-color: rgba(255,255,255,0.03);
    color: #e8e8e8;
    border: 1px solid rgba(255,255,255,0.11);
    border-radius: 5px;
    padding: 5px 12px;
}
QPushButton:hover {
    background-color: rgba(0,212,160,0.18);
    border-color: rgba(0,212,160,0.38);
    color: #ffffff;
}
QPushButton:pressed {
    background-color: rgba(255,255,255,0.03);
}
QPushButton:disabled {
    color: rgba(200,200,200,0.25);
    border-color: rgba(255,255,255,0.06);
    background-color: transparent;
}

QPushButton#accentButton {
    background-color: rgba(0,212,160,0.18);
    border: 1px solid rgba(0,212,160,0.38);
    color: rgb(60,235,190);
    font-weight: 700;
}
QPushButton#accentButton:hover {
    background-color: rgba(0,212,160,0.30);
    border-color: rgba(0,212,160,0.65);
    color: #ffffff;
}
QPushButton#accentButton:pressed {
    background-color: rgba(0,212,160,0.12);
}
QPushButton#accentButton:disabled {
    background-color: transparent;
    border-color: rgba(255,255,255,0.06);
    color: rgba(200,200,200,0.25);
}

QPushButton#dangerButton {
    background-color: transparent;
    border-color: rgba(197,79,79,0.35);
    color: rgba(220,140,140,0.85);
}
QPushButton#dangerButton:hover {
    background-color: rgba(197,79,79,0.20);
    border-color: rgba(220,100,100,0.65);
    color: #ffffff;
}

QPushButton#modeToggle {
    background-color: transparent;
    border: 1px solid rgba(255,255,255,0.11);
    border-radius: 5px;
    padding: 4px 12px;
    color: rgba(200,200,200,0.55);
}
QPushButton#modeToggle:hover {
    color: #e8e8e8;
}
QPushButton#modeToggle:checked {
    background-color: rgba(0,212,160,0.14);
    color: #00d4a0;
    border-color: rgba(0,212,160,0.45);
    font-weight: 600;
}

/* Small flat icon-only buttons (settings, sidebar toggle) */
QToolButton#iconButton {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.11);
    border-radius: 5px;
    color: rgba(200,200,200,0.65);
    font-size: 14px;
    padding: 3px 9px;
}
QToolButton#iconButton:hover {
    background: rgba(0,212,160,0.18);
    border-color: rgba(0,212,160,0.38);
    color: #00d4a0;
}
QToolButton#iconButton:checked {
    background: rgba(0,212,160,0.22);
    border-color: rgba(0,212,160,0.55);
    color: #00d4a0;
}

/* ---- Inputs ---- */
QLineEdit, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {
    background-color: #181818;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 6px;
    padding: 5px 9px;
    color: #e8e8e8;
    selection-background-color: #00d4a0;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: rgba(0,212,160,0.45);
    background-color: #1e1e1e;
}

/* ---- Image slot frame ---- */
QFrame#imageSlot {
    background-color: #141414;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 8px;
}
QFrame#imageSlot[dragOver="true"] {
    border: 1px dashed #00d4a0;
}
QLabel#slotGrip {
    color: rgba(200,200,200,0.35);
    font-weight: bold;
    padding: 0 2px;
}
QLabel#slotGrip:hover {
    color: #00d4a0;
}
QLabel#slotCaption {
    color: rgba(220,220,220,0.75);
    font-weight: 600;
    padding: 2px 0;
}
QLabel#slotCanvas {
    background-color: #101010;
    border-radius: 6px;
    color: rgba(200,200,200,0.35);
}

/* ---- Splitter handles (manual resizing) ---- */
QSplitter::handle {
    background-color: rgba(255,255,255,0.07);
    width: 4px;
}
QSplitter::handle:hover {
    background-color: #00d4a0;
}

/* ---- Sidebar (Outputs / Queue) ---- */
#outputsSidebar {
    background-color: #121212;
    border-left: 1px solid rgba(255,255,255,0.10);
}
#sidebarHandle {
    background-color: transparent;
}
#sidebarHandle:hover {
    background-color: rgba(0,212,160,0.45);
}

/* ---- Lists (queue / outputs) ---- */
QListWidget {
    background-color: transparent;
    border: none;
    padding: 0;
}
QListWidget::item {
    padding: 6px;
    border-radius: 5px;
}
QListWidget::item:selected {
    background-color: rgba(0,212,160,0.16);
    color: #e8e8e8;
}
QListWidget::item:hover:!selected {
    background-color: rgba(255,255,255,0.045);
}

QScrollBar:vertical, QScrollBar:horizontal {
    background: transparent;
    width: 8px;
    height: 8px;
    margin: 0;
}
QScrollBar::handle {
    background: rgba(255,255,255,0.14);
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:hover {
    background: #00d4a0;
}
QScrollBar::add-line, QScrollBar::sub-line {
    height: 0;
    width: 0;
}

QLabel#sectionTitle {
    font-size: 13px;
    font-weight: 700;
    color: #e8e8e8;
    padding: 2px 0 6px 0;
}

QLabel#hint {
    color: rgba(200,200,200,0.45);
    font-size: 11px;
}

QToolTip {
    background-color: #1e1e1e;
    color: #e8e8e8;
    border: 1px solid rgba(255,255,255,0.11);
    padding: 4px;
}

QTableWidget {
    background-color: transparent;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 6px;
    gridline-color: rgba(255,255,255,0.07);
}
QHeaderView::section {
    background-color: transparent;
    color: rgba(200,200,200,0.55);
    padding: 6px;
    border: none;
    border-bottom: 1px solid rgba(255,255,255,0.07);
    font-weight: 600;
}

QProgressBar {
    background-color: #181818;
    border: none;
    border-radius: 3px;
    text-align: center;
    color: #e8e8e8;
    max-height: 3px;
}
QProgressBar::chunk {
    background-color: #00d4a0;
    border-radius: 3px;
}
"""

# ===========================================================================
# main.py — application UI and logic
# ===========================================================================
import os
import sys
import copy
import time
import uuid
import datetime
from pathlib import Path

from PySide6.QtCore import (
    Qt, QObject, QThread, Signal, QMimeData, QUrl, QSize, QEvent, QPoint, QRect,
    QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import (
    QPixmap, QImage, QDrag, QDesktopServices, QShortcut, QKeySequence, QIcon, QAction,
    QPainter, QPen, QColor, QPalette, QGuiApplication,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QTabBar, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QPushButton, QLineEdit, QFileDialog, QMessageBox, QSplitter,
    QScrollArea, QFrame, QListWidget, QListWidgetItem, QProgressBar, QToolButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QDialog, QSpinBox, QDoubleSpinBox,
    QSizePolicy, QStackedWidget, QMenu, QSizeGrip
)

APP_TITLE = "cover"
APP_BRAND_PREFIX = "vael. "
APP_BRAND_SUFFIX = "cover"
POLL_INTERVAL = 1.0
POLL_TIMEOUT = 600

HOTKEYS = [
    ("Ctrl+R", "Run the current workflow tab"),
    ("Ctrl+Shift+A", "Add current workflow (with current inputs) to the run queue"),
    ("Ctrl+Shift+R", "Run every queued workflow, one after another"),
    ("Ctrl+Shift+X", "Clear the run queue"),
    ("Ctrl+N", "Create a new workflow tab"),
    ("Ctrl+Tab", "Next workflow tab"),
    ("Ctrl+Shift+Tab", "Previous workflow tab"),
    ("Ctrl+O", "Toggle the Outputs / Queue sidebar"),
    ("Ctrl+,", "Open Settings"),
    ("Ctrl+Shift+O", "Open the outputs folder on disk"),
    ("F5", "Refresh the outputs list"),
]


# ---------------------------------------------------------------------------
# Workflow execution (runs on a background thread)
# ---------------------------------------------------------------------------
def execute_workflow_sync(server, raw_workflow, image_map, optional_node_id, param_values):
    api = ComfyAPI(server)
    wf = copy.deepcopy(raw_workflow)

    for node_id, path in image_map.items():
        if not path:
            raise RuntimeError(f"Missing image for node #{node_id}")
        name, subfolder, ftype = api.upload_image(path)
        wf[node_id]["inputs"]["image"] = name

    if optional_node_id and optional_node_id in wf:
        for key, val in (param_values or {}).items():
            if key in wf[optional_node_id].get("inputs", {}):
                wf[optional_node_id]["inputs"][key] = val

    prompt_id = api.queue_prompt(wf)
    start = time.time()
    while True:
        time.sleep(POLL_INTERVAL)
        if time.time() - start > POLL_TIMEOUT:
            raise TimeoutError("Timed out waiting for the workflow to finish.")
        hist = api.get_history(prompt_id)
        entry = hist.get(prompt_id)
        if not entry:
            continue
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            raise RuntimeError(f"ComfyUI reported an error: {status}")
        if status.get("completed"):
            outputs = entry.get("outputs", {})
            image_info = None
            for node_out in outputs.values():
                if node_out.get("images"):
                    image_info = node_out["images"][0]
                    break
            if image_info is None:
                raise RuntimeError("Workflow finished but produced no image output.")
            return api.get_image_bytes(
                image_info["filename"], image_info.get("subfolder", ""), image_info.get("type", "output")
            )


class RunWorker(QObject):
    finished = Signal(bytes)
    error = Signal(str)

    def __init__(self, server, raw_workflow, image_map, optional_node_id, param_values):
        super().__init__()
        self.server = server
        self.raw_workflow = raw_workflow
        self.image_map = image_map
        self.optional_node_id = optional_node_id
        self.param_values = param_values

    def run(self):
        try:
            data = execute_workflow_sync(
                self.server, self.raw_workflow, self.image_map,
                self.optional_node_id, self.param_values,
            )
            self.finished.emit(data)
        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# Draggable / resizable image slot
# ---------------------------------------------------------------------------
class ImageSlot(QFrame):
    reorderRequested = Signal(int, int)     # source_index, target_index (moves the whole slot)
    imageSwapRequested = Signal(int, int)   # source_index, target_index (swaps only image content)
    changed = Signal()

    def __init__(self, index, node_id, caption, parent=None):
        super().__init__(parent)
        self.setObjectName("imageSlot")
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.NoFrame)
        self.index = index
        self.node_id = node_id
        self.caption = caption
        self.filepath = None
        self._pixmap = None
        self._press_kind = None
        self._press_pos = None
        self._dragging = False
        self.setMinimumWidth(150)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)
        self.grip = QLabel("\u22ee\u22ee")
        self.grip.setObjectName("slotGrip")
        self.grip.setCursor(Qt.OpenHandCursor)
        self.grip.setToolTip("Drag to reorder")
        header.addWidget(self.grip)

        self.caption_label = QLabel(caption)
        self.caption_label.setObjectName("slotCaption")
        self.caption_label.setWordWrap(True)
        header.addWidget(self.caption_label, 1)
        layout.addLayout(header)

        self.canvas = QLabel("Click to browse\nor drag && drop an image here\n\n(drag onto another slot to swap)")
        self.canvas.setObjectName("slotCanvas")
        self.canvas.setAlignment(Qt.AlignCenter)
        self.canvas.setWordWrap(True)
        self.canvas.setCursor(Qt.PointingHandCursor)
        self.canvas.setMinimumHeight(140)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.canvas, 1)

    # -- drag & drop -----------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            if self.grip.geometry().contains(self._press_pos):
                self._press_kind = "reorder"
            elif self.canvas.geometry().contains(self._press_pos):
                self._press_kind = "image"
            else:
                self._press_kind = None
            self._dragging = False
        elif event.button() == Qt.RightButton:
            if self.canvas.geometry().contains(event.position().toPoint()):
                self.clear_image()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_kind and (event.buttons() & Qt.LeftButton) and not self._dragging:
            if (event.position().toPoint() - self._press_pos).manhattanLength() > QApplication.startDragDistance():
                self._dragging = True
                self._start_drag(self._press_kind)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and not self._dragging and self._press_kind == "image":
            self.browse_image()
        self._press_kind = None
        self._dragging = False
        super().mouseReleaseEvent(event)

    def _start_drag(self, kind):
        mime = QMimeData()
        mime.setData(f"application/x-slot-{kind}", str(self.index).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        if self._pixmap is not None:
            drag.setPixmap(self._pixmap.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md.hasFormat("application/x-slot-reorder") or md.hasFormat("application/x-slot-image") or md.hasUrls():
            event.acceptProposedAction()
            self.setProperty("dragOver", True)
            self.style().unpolish(self)
            self.style().polish(self)

    def dragLeaveEvent(self, event):
        self.setProperty("dragOver", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event):
        self.setProperty("dragOver", False)
        self.style().unpolish(self)
        self.style().polish(self)
        md = event.mimeData()
        if md.hasFormat("application/x-slot-reorder"):
            src = int(bytes(md.data("application/x-slot-reorder")).decode())
            if src != self.index:
                self.reorderRequested.emit(src, self.index)
            event.acceptProposedAction()
        elif md.hasFormat("application/x-slot-image"):
            src = int(bytes(md.data("application/x-slot-image")).decode())
            if src != self.index:
                self.imageSwapRequested.emit(src, self.index)
            event.acceptProposedAction()
        elif md.hasUrls():
            path = md.urls()[0].toLocalFile()
            if path:
                self.set_image_path(path)
            event.acceptProposedAction()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render()

    # -- image handling ----------------------------------------------------
    def browse_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select input image", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
        )
        if path:
            self.set_image_path(path)

    def clear_image(self):
        self.filepath = None
        self._pixmap = None
        self._render()
        self.changed.emit()

    def set_image_path(self, path):
        pix = QPixmap(path)
        if pix.isNull():
            QMessageBox.warning(self, "Image error", f"Couldn't load image:\n{path}")
            return
        self.filepath = path
        self._pixmap = pix
        self._render()
        self.changed.emit()

    def _render(self):
        if self._pixmap is not None:
            target = self.canvas.size()
            scaled = self._pixmap.scaled(
                max(target.width() - 8, 10), max(target.height() - 8, 10),
                Qt.KeepAspectRatio, Qt.SmoothTransformation,
            )
            self.canvas.setPixmap(scaled)
        else:
            self.canvas.setPixmap(QPixmap())
            self.canvas.setText("Click to browse\nor drag && drop an image here\n\n(drag onto another slot to swap)")

    def set_index(self, new_index):
        self.index = new_index


# ---------------------------------------------------------------------------
# One workflow tab
# ---------------------------------------------------------------------------
class WorkflowTab(QWidget):
    def __init__(self, main_window, data=None):
        super().__init__()
        self.main_window = main_window
        data = data or {}
        self.workflow_path = data.get("workflow_path")
        self.optional_identifier = data.get("optional_identifier", "")
        self.saved_slot_node_order = data.get("slot_node_order") or []
        self.saved_splitter_sizes = data.get("splitter_sizes") or []
        self.name = Path(self.workflow_path).stem if self.workflow_path else "Workflow"

        self.raw_workflow = None
        self.slots = []            # list[ImageSlot], in display order
        self.optional_node_id = None
        self.param_widgets = {}    # key -> (widget, type_name)

        self._build_ui()
        self._thread = None
        self._worker = None

        if self.workflow_path and os.path.exists(self.workflow_path):
            try:
                self.load_workflow(self.workflow_path)
            except Exception as e:
                self._set_status(f"Couldn't load workflow: {e}", error=True)
        elif self.workflow_path:
            self._set_status("Workflow file not found - reconfigure this tab.", error=True)

    # -- UI ------------------------------------------------------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        toolbar = QHBoxLayout()
        self.status_label = QLabel("Ready.")
        self.status_label.setObjectName("hint")
        toolbar.addWidget(self.status_label)

        toolbar.addStretch(1)

        self.queue_btn = QPushButton("+ Add to Queue")
        self.queue_btn.clicked.connect(self.add_to_queue)
        toolbar.addWidget(self.queue_btn)

        self.run_btn = QPushButton("\u25b6  Run")
        self.run_btn.setObjectName("accentButton")
        self.run_btn.clicked.connect(self.run_now)
        toolbar.addWidget(self.run_btn)
        outer.addLayout(toolbar)

        self.progress = QProgressBar()
        self.progress.setMaximumHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.hide()
        outer.addWidget(self.progress)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.splitter = QSplitter(Qt.Horizontal)
        scroll.setWidget(self.splitter)
        outer.addWidget(scroll, 3)

        self.param_form_widget = QWidget()
        self.param_form = QFormLayout(self.param_form_widget)
        outer.addWidget(self.param_form_widget)
        self.param_form_widget.hide()

    # -- Workflow loading -------------------------------------------------
    def load_workflow(self, path):
        wf = load_workflow_file(path)
        self.raw_workflow = wf
        self.workflow_path = path
        self.name = Path(path).stem
        self.main_window.rename_tab(self, self.name)

        image_nodes = find_load_image_nodes(wf)
        if self.saved_slot_node_order:
            ordered = [n for n in self.saved_slot_node_order if n in image_nodes]
            ordered += [n for n in image_nodes if n not in ordered]
            image_nodes = ordered

        for s in self.slots:
            s.setParent(None)
        self.slots = []
        for i, nid in enumerate(image_nodes):
            slot = ImageSlot(i, nid, node_label(wf, nid))
            slot.reorderRequested.connect(self._on_reorder)
            slot.imageSwapRequested.connect(self._on_image_swap)
            self.splitter.addWidget(slot)
            self.slots.append(slot)

        if self.saved_splitter_sizes and len(self.saved_splitter_sizes) == len(self.slots):
            self.splitter.setSizes(self.saved_splitter_sizes)

        self.optional_node_id = find_node(wf, self.optional_identifier) if self.optional_identifier else None
        self._refresh_param_panel()
        self._set_status(f"Loaded. {len(self.slots)} image input(s) found.")

    def _refresh_param_panel(self):
        while self.param_form.rowCount():
            self.param_form.removeRow(0)
        self.param_widgets = {}
        if not self.optional_node_id:
            self.param_form_widget.hide()
            return
        node = self.raw_workflow.get(self.optional_node_id, {})
        editable = get_editable_inputs(node)
        if not editable:
            self.param_form_widget.hide()
            return
        for key, (value, type_name) in editable.items():
            if type_name == "int":
                w = QSpinBox()
                w.setRange(-2_147_483_648, 2_147_483_647)
                w.setValue(value)
            elif type_name == "float":
                w = QDoubleSpinBox()
                w.setRange(-1e12, 1e12)
                w.setDecimals(4)
                w.setValue(value)
            else:
                w = QLineEdit(str(value))
            self.param_form.addRow(key, w)
            self.param_widgets[key] = (w, type_name)
        self.param_form_widget.show()

    # -- Reordering / swapping --------------------------------------------
    def _on_reorder(self, src, tgt):
        if src == tgt or src >= len(self.slots) or tgt >= len(self.slots):
            return
        widget = self.slots.pop(src)
        self.slots.insert(tgt, widget)
        for i, s in enumerate(self.slots):
            self.splitter.insertWidget(i, s)
            s.set_index(i)
        self._persist_layout()

    def _on_image_swap(self, src, tgt):
        if src == tgt or src >= len(self.slots) or tgt >= len(self.slots):
            return
        a, b = self.slots[src], self.slots[tgt]
        a.filepath, b.filepath = b.filepath, a.filepath
        a._pixmap, b._pixmap = b._pixmap, a._pixmap
        a._render()
        b._render()

    def current_layout(self):
        return {
            "slot_node_order": [s.node_id for s in self.slots],
            "splitter_sizes": self.splitter.sizes(),
        }

    def _persist_layout(self):
        self.main_window.persist_all()

    # -- Running -------------------------------------------------------
    def _gather_image_map(self):
        return {s.node_id: s.filepath for s in self.slots}

    def _gather_param_values(self):
        values = {}
        for key, (w, type_name) in self.param_widgets.items():
            if type_name == "int":
                values[key] = w.value()
            elif type_name == "float":
                values[key] = w.value()
            else:
                values[key] = w.text()
        return values

    def _validate(self):
        if not self.raw_workflow:
            QMessageBox.warning(self, "No workflow", "This tab has no valid workflow loaded.")
            return False
        missing = [s.caption for s in self.slots if not s.filepath]
        if missing:
            QMessageBox.warning(self, "Missing images", "Please fill in:\n- " + "\n- ".join(missing))
            return False
        return True

    def run_now(self):
        if not self._validate():
            return
        self.run_btn.setEnabled(False)
        self.progress.show()
        self._set_status("Running...")
        self._thread = QThread()
        self._worker = RunWorker(
            self.main_window.server, copy.deepcopy(self.raw_workflow),
            self._gather_image_map(), self.optional_node_id, self._gather_param_values(),
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_run_finished)
        self._worker.error.connect(self._on_run_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.start()

    def _on_run_finished(self, data):
        self.run_btn.setEnabled(True)
        self.progress.hide()
        self._set_status("Done.")
        self.main_window.save_output(self.name, data)

    def _on_run_error(self, message):
        self.run_btn.setEnabled(True)
        self.progress.hide()
        self._set_status(f"Error: {message}", error=True)
        QMessageBox.critical(self, "Run failed", message)

    def add_to_queue(self):
        if not self._validate():
            return
        self.main_window.queue_manager.add_item({
            "id": str(uuid.uuid4()),
            "tab_name": self.name,
            "server": self.main_window.server,
            "raw_workflow": copy.deepcopy(self.raw_workflow),
            "image_map": self._gather_image_map(),
            "optional_node_id": self.optional_node_id,
            "param_values": self._gather_param_values(),
        })
        self._set_status("Added current inputs to the run queue.")

    def _set_status(self, text, error=False):
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color: rgba(220,140,140,0.9);" if error else "")

    def to_dict(self):
        layout = self.current_layout()
        return {
            "workflow_path": self.workflow_path,
            "optional_identifier": self.optional_identifier,
            "slot_node_order": layout["slot_node_order"],
            "splitter_sizes": layout["splitter_sizes"],
        }


# ---------------------------------------------------------------------------
# New / edit workflow tab dialog
# ---------------------------------------------------------------------------
class WorkflowConfigDialog(QDialog):
    def __init__(self, parent, mode="create", tab=None):
        super().__init__(parent)
        self.setWindowTitle("New Workflow" if mode == "create" else "Edit Workflow")
        self.mode = mode
        self.tab = tab
        self.result = None
        self.workflow_path = tab.workflow_path if tab else None
        self.optional_identifier = tab.optional_identifier if tab else ""
        self._preview_workflow = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("ComfyUI workflow (.json, API format):"))
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(self.workflow_path or "")
        self.path_edit.setReadOnly(True)
        path_row.addWidget(self.path_edit, 1)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        self.detected_lbl = QLabel("")
        self.detected_lbl.setWordWrap(True)
        layout.addWidget(self.detected_lbl)

        layout.addWidget(QLabel("Optional node (ID or title) for extra fields:"))
        self.identifier_edit = QLineEdit(self.optional_identifier)
        layout.addWidget(self.identifier_edit)
        hint = QLabel("Leave empty if you don't need extra parameters.")
        hint.setObjectName("hint")
        layout.addWidget(hint)

        btns = QHBoxLayout()
        btns.addStretch(1)
        if mode == "edit":
            del_btn = QPushButton("Delete Tab")
            del_btn.setObjectName("dangerButton")
            del_btn.clicked.connect(self._delete)
            btns.addWidget(del_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._cancel)
        btns.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("accentButton")
        save_btn.clicked.connect(self._save)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

        if self.workflow_path:
            self._validate_path(self.workflow_path)
        self.setMinimumWidth(420)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select ComfyUI workflow (API format)", "", "JSON (*.json)")
        if path:
            self.path_edit.setText(path)
            self._validate_path(path)

    def _validate_path(self, path):
        try:
            wf = load_workflow_file(path)
            self._preview_workflow = wf
            n_images = len(find_load_image_nodes(wf))
            self.detected_lbl.setText(f"\u2713 Valid workflow \u2014 {n_images} image input node(s) found.")
            self.detected_lbl.setStyleSheet("color:#5fd07c;")
        except Exception as e:
            self._preview_workflow = None
            self.detected_lbl.setText(f"\u26a0 {e}")
            self.detected_lbl.setStyleSheet("color:rgba(220,140,140,0.9);")

    def _delete(self):
        if QMessageBox.question(self, "Delete tab", f"Delete tab '{self.tab.name}'? This cannot be undone.") == QMessageBox.Yes:
            self.result = "delete"
            self.accept()

    def _cancel(self):
        self.result = "cancel"
        self.reject()

    def _save(self):
        path = self.path_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "Missing workflow", "Please select a workflow .json file.")
            return
        if self._preview_workflow is None:
            self._validate_path(path)
            if self._preview_workflow is None:
                return
        self.workflow_path = path
        self.optional_identifier = self.identifier_edit.text().strip()
        self.result = "save"
        self.accept()


# ---------------------------------------------------------------------------
# Queue manager (global; not tied to any single tab)
# ---------------------------------------------------------------------------
class QueueManager(QObject):
    queueChanged = Signal()
    itemStarted = Signal(str)
    itemFinished = Signal(str, bool, str)   # id, success, message

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.items = []
        self.running = False
        self._thread = None
        self._worker = None

    def add_item(self, item):
        item["status"] = "Waiting"
        self.items.append(item)
        self.queueChanged.emit()

    def remove_item(self, item_id):
        self.items = [i for i in self.items if i["id"] != item_id]
        self.queueChanged.emit()

    def clear(self):
        if self.running:
            QMessageBox.information(self.main_window, "Queue running", "Wait for the current run to finish first.")
            return
        self.items = []
        self.queueChanged.emit()

    def run_queue(self):
        if self.running or not self.items:
            return
        self.running = True
        self._run_next()

    def _run_next(self):
        pending = [i for i in self.items if i["status"] in ("Waiting", "Error")]
        if not pending:
            self.running = False
            self.queueChanged.emit()
            return
        item = pending[0]
        item["status"] = "Running"
        self.queueChanged.emit()
        self.itemStarted.emit(item["id"])

        self._thread = QThread()
        self._worker = RunWorker(
            item["server"], item["raw_workflow"], item["image_map"],
            item["optional_node_id"], item["param_values"],
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)

        def on_finished(data, item=item):
            item["status"] = "Done"
            self.main_window.save_output(item["tab_name"], data)
            self.itemFinished.emit(item["id"], True, "Done")
            self.items.remove(item)
            self.queueChanged.emit()
            self._thread.quit()
            self._run_next()

        def on_error(message, item=item):
            item["status"] = "Error"
            self.itemFinished.emit(item["id"], False, message)
            self.queueChanged.emit()
            self._thread.quit()
            self._run_next()

        self._worker.finished.connect(on_finished)
        self._worker.error.connect(on_error)
        self._thread.start()


# ---------------------------------------------------------------------------
# Outputs / Queue tab — one panel, two switchable modes (no separate window)
# ---------------------------------------------------------------------------
class OutputsTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        layout = QVBoxLayout(self)

        # -- header: mode toggle + contextual actions -----------------
        header = QHBoxLayout()
        self.outputs_mode_btn = QPushButton("Outputs")
        self.outputs_mode_btn.setObjectName("modeToggle")
        self.outputs_mode_btn.setCheckable(True)
        self.outputs_mode_btn.setChecked(True)
        self.outputs_mode_btn.clicked.connect(lambda: self._set_mode(0))
        header.addWidget(self.outputs_mode_btn)

        self.queue_mode_btn = QPushButton("Queue")
        self.queue_mode_btn.setObjectName("modeToggle")
        self.queue_mode_btn.setCheckable(True)
        self.queue_mode_btn.clicked.connect(lambda: self._set_mode(1))
        header.addWidget(self.queue_mode_btn)

        header.addStretch(1)

        # Outputs-mode actions
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        self.open_folder_btn = QPushButton("Open Folder")
        self.open_folder_btn.clicked.connect(self._open_folder)
        header.addWidget(self.open_folder_btn)
        self.clear_outputs_btn = QPushButton("Clear All")
        self.clear_outputs_btn.setObjectName("dangerButton")
        self.clear_outputs_btn.clicked.connect(self._clear_all)
        header.addWidget(self.clear_outputs_btn)

        # Queue-mode actions
        self.run_queue_btn = QPushButton("\u25b6  Run Queue")
        self.run_queue_btn.setObjectName("accentButton")
        self.run_queue_btn.clicked.connect(main_window.queue_manager.run_queue)
        header.addWidget(self.run_queue_btn)
        self.clear_queue_btn = QPushButton("Clear")
        self.clear_queue_btn.setObjectName("dangerButton")
        self.clear_queue_btn.clicked.connect(main_window.queue_manager.clear)
        header.addWidget(self.clear_queue_btn)

        layout.addLayout(header)

        # -- stacked content --------------------------------------------
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.outputs_list = QListWidget()
        self.outputs_list.setViewMode(QListWidget.IconMode)
        self.outputs_list.setIconSize(QSize(140, 140))
        self.outputs_list.setResizeMode(QListWidget.Adjust)
        self.outputs_list.setMovement(QListWidget.Static)
        self.outputs_list.setSpacing(10)
        self.outputs_list.itemDoubleClicked.connect(self._open_item)
        self.stack.addWidget(self.outputs_list)

        self.queue_list = QListWidget()
        self.stack.addWidget(self.queue_list)

        main_window.queue_manager.queueChanged.connect(self._refresh_queue)
        self._set_mode(0)
        self.refresh()
        self._refresh_queue()

    # -- mode switching ---------------------------------------------------
    def _set_mode(self, mode):
        """mode 0 = Outputs, 1 = Queue."""
        self.outputs_mode_btn.setChecked(mode == 0)
        self.queue_mode_btn.setChecked(mode == 1)
        self.stack.setCurrentIndex(mode)
        for w in (self.refresh_btn, self.open_folder_btn, self.clear_outputs_btn):
            w.setVisible(mode == 0)
        for w in (self.run_queue_btn, self.clear_queue_btn):
            w.setVisible(mode == 1)

    # -- outputs -----------------------------------------------------------
    def _output_dir(self):
        d = Path(self.main_window.output_dir)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def refresh(self):
        self.outputs_list.clear()
        d = self._output_dir()
        files = sorted(d.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files:
            pix = QPixmap(str(f))
            item = QListWidgetItem(QIcon(pix), f.name)
            item.setData(Qt.UserRole, str(f))
            self.outputs_list.addItem(item)

    def _open_item(self, item):
        path = item.data(Qt.UserRole)
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_dir())))

    def _clear_all(self):
        d = self._output_dir()
        files = list(d.glob("*.png"))
        if not files:
            return
        if QMessageBox.question(
            self, "Clear all outputs", f"Delete all {len(files)} saved output image(s)? This cannot be undone."
        ) != QMessageBox.Yes:
            return
        for f in files:
            try:
                f.unlink()
            except Exception:
                pass
        self.refresh()

    # -- queue ---------------------------------------------------------
    def _refresh_queue(self):
        self.queue_list.clear()
        for item in self.main_window.queue_manager.items:
            text = f"[{item['status']}]  {item['tab_name']}"
            self.queue_list.addItem(QListWidgetItem(text))


# ---------------------------------------------------------------------------
# Settings dialog (server address, output path, hotkey overview)
# opened from the settings icon in the top-right corner of the window
# ---------------------------------------------------------------------------
class SettingsDialog(QDialog):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.server_edit = QLineEdit(main_window.server)
        form.addRow("ComfyUI server address:", self.server_edit)

        out_row = QHBoxLayout()
        self.output_edit = QLineEdit(main_window.output_dir)
        out_row.addWidget(self.output_edit, 1)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_output)
        out_row.addWidget(browse_btn)
        out_wrap = QWidget()
        out_wrap.setLayout(out_row)
        form.addRow("Output folder:", out_wrap)
        layout.addLayout(form)

        hk_title = QLabel("Hotkeys")
        hk_title.setObjectName("sectionTitle")
        layout.addWidget(hk_title)

        table = QTableWidget(len(HOTKEYS), 2)
        table.setHorizontalHeaderLabels(["Shortcut", "Action"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.verticalHeader().hide()
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        for row, (seq, desc) in enumerate(HOTKEYS):
            table.setItem(row, 0, QTableWidgetItem(seq))
            table.setItem(row, 1, QTableWidgetItem(desc))
        layout.addWidget(table, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btns.addWidget(close_btn)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("accentButton")
        save_btn.clicked.connect(self._save)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "Select output folder", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def _save(self):
        self.main_window.server = self.server_edit.text().strip() or DEFAULT_SERVER
        self.main_window.output_dir = self.output_edit.text().strip() or DEFAULT_OUTPUT_DIR
        self.main_window.persist_all()
        self.accept()


# ---------------------------------------------------------------------------
# Window chrome — vael.'s shared frameless title bar (copied from vael.
# indexer so every app in the vael. product line looks/behaves the same).
# ---------------------------------------------------------------------------
def _make_win_icon(kind: str, color: str = "#b4b4b4", size: int = 10) -> QIcon:
    """Hand-draw a small crisp icon for the maximize/restore window button."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    pen = QPen(QColor(color))
    pen.setWidth(1)
    p.setPen(pen)
    if kind == "max":
        p.drawRect(0, 0, size - 1, size - 1)
    elif kind == "restore":
        back = size - 3
        p.drawRect(2, 0, back, back)
        p.fillRect(0, 3, back, back, QColor("#0a0a0a"))
        p.drawRect(0, 3, back, back)
    elif kind == "min":
        mid = size // 2
        p.drawLine(0, mid, size - 1, mid)
    elif kind == "close":
        p.drawLine(0, 0, size - 1, size - 1)
        p.drawLine(0, size - 1, size - 1, 0)
    p.end()
    return QIcon(pm)


# ── Windows: real Aero-Snap for the frameless main window ────────────────────
_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class _NCCALCSIZE_PARAMS(ctypes.Structure):
        _fields_ = [("rgrc", _RECT * 3), ("lppos", ctypes.c_void_p)]

    _WM_NCCALCSIZE = 0x0083
    _WM_NCHITTEST = 0x0084
    _HTCLIENT = 1
    _HTCAPTION = 2
    _HTLEFT, _HTRIGHT, _HTTOP, _HTBOTTOM = 10, 11, 12, 15
    _HTTOPLEFT, _HTTOPRIGHT, _HTBOTTOMLEFT, _HTBOTTOMRIGHT = 13, 14, 16, 17
    _SM_CXSIZEFRAME = 32
    _SM_CYSIZEFRAME = 33
    _SM_CXPADDEDBORDER = 92
    _RESIZE_BORDER_PX = 8  # invisible grab margin for edge/corner resize


class _TitleBar(QWidget):
    """Thin custom title bar for the frameless main window.

    Provides drag-to-move and double-click-to-maximize, matching the
    frameless chrome used by vael.'s other apps (e.g. vael. indexer).
    Dragging is delegated to the OS via QWindow.startSystemMove() so
    Snap zones / Snap Assist / Win+Arrow keep working.
    """

    def __init__(self, window: "MainWindow", parent=None):
        super().__init__(parent)
        self._window = window
        self._drag_offset = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            wh = self._window.windowHandle()
            started = bool(wh is not None and wh.startSystemMove())
            if not started:
                self._drag_offset = (
                    event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
                )
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            if self._window.isMaximized():
                self._window._toggle_maximize(force_normal=True)
                self._drag_offset = QPoint(self._window.width() // 2, 14)
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._window._toggle_maximize()
        super().mouseDoubleClickEvent(event)


# ---------------------------------------------------------------------------
# Tab widget that notifies MainWindow when it's resized, so the Outputs /
# Queue sidebar (an overlay child of this widget) can be repositioned.
# ---------------------------------------------------------------------------
class MainTabs(QTabWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.main_window._position_sidebar()


# ---------------------------------------------------------------------------
# Draggable left-edge handle used to resize the Outputs / Queue sidebar
# ---------------------------------------------------------------------------
class _SidebarHandle(QWidget):
    def __init__(self, sidebar, parent=None):
        super().__init__(parent)
        self.sidebar = sidebar
        self.setObjectName("sidebarHandle")
        self.setFixedWidth(5)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self._dragging = False
        self._start_x = 0
        self._start_width = 0

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._start_x = event.globalPosition().toPoint().x()
            self._start_width = self.sidebar.width()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging:
            dx = self._start_x - event.globalPosition().toPoint().x()
            self.sidebar.set_width(self._start_width + dx, persist=False)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self.sidebar.persist_width()
            event.accept()


# ---------------------------------------------------------------------------
# Outputs / Queue sidebar — slides in/out over the tab *content* area only
# (the tab bar itself is untouched). Draggable in width; the width is
# remembered across launches. Closed by default.
# ---------------------------------------------------------------------------
class OutputsSidebar(QWidget):
    MIN_WIDTH = 260
    MAX_WIDTH = 640

    def __init__(self, main_window):
        super().__init__(main_window.tabs)
        self.main_window = main_window
        self.setObjectName("outputsSidebar")
        self._width = max(self.MIN_WIDTH, min(self.MAX_WIDTH, int(
            main_window.config_data.get("sidebar_width", 340)
        )))
        self._open = False
        self._anim = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.handle = _SidebarHandle(self)
        layout.addWidget(self.handle)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(12, 12, 12, 12)
        self.outputs_panel = OutputsTab(main_window)
        content_layout.addWidget(self.outputs_panel)
        layout.addWidget(content, 1)

        self.resize(self._width, self.height())
        self.hide()

    # -- width ------------------------------------------------------------
    def set_width(self, width, persist=True):
        width = max(self.MIN_WIDTH, min(self.MAX_WIDTH, int(width)))
        if width == self._width:
            return
        self._width = width
        self.main_window._position_sidebar()
        if persist:
            self.persist_width()

    def persist_width(self):
        self.main_window.config_data["sidebar_width"] = self._width
        self.main_window.persist_all()

    # -- open / close -------------------------------------------------------
    def is_open(self):
        return self._open

    def toggle(self):
        self.set_open(not self._open)

    def set_open(self, open_):
        if open_ == self._open:
            return
        self._open = open_
        tabs = self.main_window.tabs
        bar_h = tabs.tabBar().height()
        start_rect = self.geometry()
        if open_:
            self.show()
            self.raise_()
            end_rect = QRect(max(0, tabs.width() - self._width), bar_h, self._width, max(0, tabs.height() - bar_h))
        else:
            end_rect = QRect(tabs.width(), bar_h, self._width, max(0, tabs.height() - bar_h))

        anim = QPropertyAnimation(self, b"geometry", self)
        anim.setDuration(180)
        anim.setStartValue(start_rect)
        anim.setEndValue(end_rect)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if not open_:
            anim.finished.connect(self.hide)
        anim.start()
        self._anim = anim  # keep a reference alive for the duration


# ---------------------------------------------------------------------------
# Tab bar that only allows drag-reordering among the workflow tabs
# ---------------------------------------------------------------------------
class LockedTabBar(QTabBar):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setMovable(True)

    def mousePressEvent(self, event):
        idx = self.tabAt(event.position().toPoint() if hasattr(event, "position") else event.pos())
        fixed_start = self.main_window.fixed_tabs_start_index()
        self.setMovable(idx < fixed_start if idx >= 0 else True)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self._is_windows = _IS_WINDOWS
        self._RESIZE_MARGIN = 6
        self._size_grip = None

        if not self._is_windows:
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setObjectName("mainWindowFrameless")
            self.setMouseTracking(True)
            QApplication.instance().installEventFilter(self)

        self.setMinimumSize(720, 520)
        self.resize(1280, 840)

        self.config_data = load_config()
        self.server = self.config_data.get("server", DEFAULT_SERVER)
        self.output_dir = self.config_data.get("output_dir", DEFAULT_OUTPUT_DIR)

        # ── Restore last window geometry ──────────────────────────────
        _geo = self.config_data.get("window_geometry")
        if isinstance(_geo, dict):
            x, y, w, h = _geo.get("x"), _geo.get("y"), _geo.get("width"), _geo.get("height")
            if all(isinstance(v, int) for v in (x, y, w, h)):
                self.resize(max(720, w), max(520, h))
                self.move(x, y)
            available = QGuiApplication.primaryScreen().virtualGeometry()
            title_bar_rect = self.frameGeometry()
            title_bar_rect.setHeight(min(100, title_bar_rect.height()))
            if not available.intersects(title_bar_rect):
                primary = QGuiApplication.primaryScreen().availableGeometry()
                self.move(
                    primary.x() + (primary.width() - self.width()) // 2,
                    primary.y() + (primary.height() - self.height()) // 2,
                )

        self.queue_manager = QueueManager(self)
        self.workflow_tabs = []

        # ── Outer shell: custom title bar on top, app content below ───────
        shell = QWidget()
        shell.setObjectName("appShell")
        shell.setProperty("maximized", "false")
        self._shell = shell
        self.setCentralWidget(shell)
        shell_lay = QVBoxLayout(shell)
        shell_lay.setContentsMargins(1, 1, 1, 1)
        shell_lay.setSpacing(0)

        title_bar = _TitleBar(self)
        title_bar.setObjectName("appTitleBar")
        title_bar.setFixedHeight(34)
        self._title_bar = title_bar
        tb_lay = QHBoxLayout(title_bar)
        tb_lay.setContentsMargins(14, 0, 8, 0)
        tb_lay.setSpacing(2)

        brand_lbl = QLabel()
        brand_lbl.setObjectName("brandLbl")
        brand_lbl.setText(
            f'<span style="color:#e8e8e8;">{APP_BRAND_PREFIX}</span>'
            f'<span style="color:#00d4a0;">{APP_BRAND_SUFFIX}</span>'
        )
        tb_lay.addWidget(brand_lbl)
        tb_lay.addStretch()

        self._min_btn = QToolButton()
        self._min_btn.setObjectName("winMinBtn")
        self._min_btn.setIcon(_make_win_icon("min"))
        self._min_btn.setIconSize(QSize(10, 10))
        self._min_btn.setFixedSize(30, 24)
        self._min_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._min_btn.clicked.connect(self.showMinimized)

        self._max_btn = QToolButton()
        self._max_btn.setObjectName("winMaxBtn")
        self._max_btn.setIcon(_make_win_icon("max"))
        self._max_btn.setIconSize(QSize(10, 10))
        self._max_btn.setFixedSize(30, 24)
        self._max_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._max_btn.clicked.connect(self._toggle_maximize)

        self._close_win_btn = QToolButton()
        self._close_win_btn.setObjectName("winCloseBtn")
        self._close_win_btn.setIcon(_make_win_icon("close"))
        self._close_win_btn.setIconSize(QSize(10, 10))
        self._close_win_btn.setFixedSize(30, 24)
        self._close_win_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._close_win_btn.clicked.connect(self.close)

        tb_lay.addWidget(self._min_btn)
        tb_lay.addWidget(self._max_btn)
        tb_lay.addWidget(self._close_win_btn)

        shell_lay.addWidget(title_bar)

        root = QWidget()
        root.setObjectName("appRoot")
        shell_lay.addWidget(root, 1)
        self._content_root = root

        self._size_grip = QSizeGrip(shell)
        self._size_grip.setFixedSize(14, 14)
        if self._is_windows:
            self._size_grip.setVisible(False)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(4)

        # ── Tabs ─────────────────────────────────────────────────────────
        self.tabs = MainTabs(self)
        self.tabs.setTabBar(LockedTabBar(self))
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        outer.addWidget(self.tabs, 1)

        self.plus_placeholder = QWidget()
        pl = QVBoxLayout(self.plus_placeholder)
        hint = QLabel('Click the "+" tab above to add a ComfyUI workflow.')
        hint.setAlignment(Qt.AlignCenter)
        hint.setObjectName("hint")
        pl.addWidget(hint)
        self.tabs.addTab(self.plus_placeholder, "  +  ")

        # ── Corner buttons: sidebar toggle (left) + settings (far right) ──
        corner = QWidget()
        corner_lay = QHBoxLayout(corner)
        corner_lay.setContentsMargins(0, 0, 0, 0)
        corner_lay.setSpacing(4)

        self.sidebar_btn = QToolButton()
        self.sidebar_btn.setObjectName("iconButton")
        self.sidebar_btn.setText("\u25a4")
        self.sidebar_btn.setToolTip("Outputs / Queue")
        self.sidebar_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sidebar_btn.setCheckable(True)
        self.sidebar_btn.clicked.connect(self._toggle_sidebar)
        corner_lay.addWidget(self.sidebar_btn)

        self.settings_btn = QToolButton()
        self.settings_btn.setObjectName("iconButton")
        self.settings_btn.setText("\u2699")
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.clicked.connect(self.open_settings)
        corner_lay.addWidget(self.settings_btn)

        self.tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)

        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.tabBar().tabMoved.connect(self._on_tab_moved)
        self.tabs.tabBarDoubleClicked.connect(self._on_tab_double_clicked)
        self.tabs.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._on_tab_context_menu)

        # ── Outputs / Queue sidebar (overlay, closed by default) ──────────
        self.outputs_sidebar = OutputsSidebar(self)
        self.outputs_tab = self.outputs_sidebar.outputs_panel
        self.queue_manager.itemFinished.connect(lambda *_: self.outputs_tab.refresh())

        for tab_data in self.config_data.get("tabs", []):
            self._add_tab(tab_data, select=False)
        if self.workflow_tabs:
            self.tabs.setCurrentWidget(self.workflow_tabs[0])

        self._setup_hotkeys()
        self._position_sidebar()

    # -- sidebar ---------------------------------------------------------
    def _toggle_sidebar(self):
        self.outputs_sidebar.toggle()
        self.sidebar_btn.setChecked(self.outputs_sidebar.is_open())

    def _position_sidebar(self):
        """Keep the sidebar filling the tab *content* area (never the tab
        bar itself) whenever the window/tabs are resized."""
        sidebar = getattr(self, "outputs_sidebar", None)
        if sidebar is None:
            return
        tabs = self.tabs
        bar_h = tabs.tabBar().height()
        w = sidebar._width
        if sidebar.is_open():
            x = max(0, tabs.width() - w)
        else:
            x = tabs.width()
        sidebar.setGeometry(x, bar_h, w, max(0, tabs.height() - bar_h))

    # -- tab bookkeeping -----------------------------------------------
    def fixed_tabs_start_index(self):
        """Index of the first non-workflow tab ('+'), i.e. workflow tab count."""
        return len(self.workflow_tabs)

    def rename_tab(self, tab, name):
        idx = self.tabs.indexOf(tab)
        if idx >= 0:
            self.tabs.setTabText(idx, name)

    def _on_tab_changed(self, index):
        widget = self.tabs.widget(index)
        if widget is self.plus_placeholder:
            self._new_tab_flow()

    def _on_tab_double_clicked(self, index):
        widget = self.tabs.widget(index)
        if isinstance(widget, WorkflowTab):
            self._edit_tab(widget)

    def _on_tab_context_menu(self, pos):
        """Right-clicking a workflow tab opens that tab's settings."""
        index = self.tabs.tabBar().tabAt(pos)
        widget = self.tabs.widget(index)
        if isinstance(widget, WorkflowTab):
            self._edit_tab(widget)

    def open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()

    def _on_tab_moved(self, from_idx, to_idx):
        # Keep '+' / Outputs pinned after all workflow tabs.
        fixed_start = self.fixed_tabs_start_index()
        if to_idx >= fixed_start or from_idx >= fixed_start:
            self.tabs.tabBar().blockSignals(True)
            self.tabs.tabBar().moveTab(to_idx, from_idx)
            self.tabs.tabBar().blockSignals(False)
            return
        # keep self.workflow_tabs python list in sync with the visual order
        self.workflow_tabs.sort(key=lambda t: self.tabs.indexOf(t))
        self.persist_all()

    def _new_tab_flow(self):
        dlg = WorkflowConfigDialog(self, mode="create")
        dlg.exec()
        if dlg.result == "save":
            self._add_tab({"workflow_path": dlg.workflow_path, "optional_identifier": dlg.optional_identifier}, select=True)
        else:
            if self.workflow_tabs:
                self.tabs.setCurrentWidget(self.workflow_tabs[-1])

    def _edit_tab(self, tab):
        dlg = WorkflowConfigDialog(self, mode="edit", tab=tab)
        dlg.exec()
        if dlg.result == "delete":
            idx = self.tabs.indexOf(tab)
            self.tabs.removeTab(idx)
            self.workflow_tabs.remove(tab)
        elif dlg.result == "save":
            if dlg.workflow_path != tab.workflow_path:
                try:
                    tab.load_workflow(dlg.workflow_path)
                except Exception as e:
                    QMessageBox.critical(self, "Workflow error", str(e))
                    return
            tab.optional_identifier = dlg.optional_identifier
            tab.optional_node_id = find_node(tab.raw_workflow, dlg.optional_identifier) if dlg.optional_identifier else None
            tab._refresh_param_panel()
        self.persist_all()

    def _add_tab(self, data, select=True):
        tab = WorkflowTab(self, data)
        plus_index = self.tabs.indexOf(self.plus_placeholder)
        self.tabs.insertTab(plus_index, tab, tab.name)
        self.workflow_tabs.append(tab)
        if select:
            self.tabs.setCurrentWidget(tab)
        self.persist_all()
        return tab

    # -- outputs ---------------------------------------------------------
    def save_output(self, workflow_name, data):
        out_dir = Path(self.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in workflow_name)
        path = out_dir / f"{safe_name}_{stamp}.png"
        with open(path, "wb") as f:
            f.write(data)
        self.outputs_tab.refresh()

    # -- persistence -------------------------------------------------------
    def persist_all(self):
        self.workflow_tabs.sort(key=lambda t: self.tabs.indexOf(t))
        self.config_data["tabs"] = [t.to_dict() for t in self.workflow_tabs]
        self.config_data["server"] = self.server
        self.config_data["output_dir"] = self.output_dir
        save_config(self.config_data)

    # -- frameless window chrome (copied from vael. indexer) ───────────────
    def nativeEvent(self, eventType, message):
        if self._is_windows and eventType in ("windows_generic_MSG", b"windows_generic_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == _WM_NCCALCSIZE:
                if msg.wParam:
                    if self.isMaximized():
                        params = _NCCALCSIZE_PARAMS.from_address(msg.lParam)
                        cx = ctypes.windll.user32.GetSystemMetrics(
                            _SM_CXSIZEFRAME
                        ) + ctypes.windll.user32.GetSystemMetrics(_SM_CXPADDEDBORDER)
                        cy = ctypes.windll.user32.GetSystemMetrics(
                            _SM_CYSIZEFRAME
                        ) + ctypes.windll.user32.GetSystemMetrics(_SM_CXPADDEDBORDER)
                        params.rgrc[0].left += cx
                        params.rgrc[0].top += cy
                        params.rgrc[0].right -= cx
                        params.rgrc[0].bottom -= cy
                    return True, 0
            elif msg.message == _WM_NCHITTEST:
                return self._win_hit_test(msg)
        return super().nativeEvent(eventType, message)

    def _win_hit_test(self, msg):
        x = ctypes.c_short(msg.lParam & 0xFFFF).value
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        local = self.mapFromGlobal(QPoint(x, y))
        w, h, b = self.width(), self.height(), _RESIZE_BORDER_PX

        if not self.isMaximized() and not self.isFullScreen():
            if local.x() < b and local.y() < b:
                return True, _HTTOPLEFT
            if local.x() >= w - b and local.y() < b:
                return True, _HTTOPRIGHT
            if local.x() < b and local.y() >= h - b:
                return True, _HTBOTTOMLEFT
            if local.x() >= w - b and local.y() >= h - b:
                return True, _HTBOTTOMRIGHT
            if local.x() < b:
                return True, _HTLEFT
            if local.x() >= w - b:
                return True, _HTRIGHT
            if local.y() < b:
                return True, _HTTOP
            if local.y() >= h - b:
                return True, _HTBOTTOM

        title_bar = self._title_bar
        if title_bar.geometry().contains(local):
            child = title_bar.childAt(title_bar.mapFrom(self, local))
            if not isinstance(child, QToolButton):
                return True, _HTCAPTION

        return True, _HTCLIENT

    def _edges_at(self, global_pos: QPoint) -> Qt.Edges:
        if self.isMaximized() or self.isFullScreen():
            return Qt.Edges()
        r = self.frameGeometry()
        m = self._RESIZE_MARGIN
        edges = Qt.Edges()
        if abs(global_pos.x() - r.left()) <= m:
            edges |= Qt.Edge.LeftEdge
        elif abs(global_pos.x() - r.right()) <= m:
            edges |= Qt.Edge.RightEdge
        if abs(global_pos.y() - r.top()) <= m:
            edges |= Qt.Edge.TopEdge
        elif abs(global_pos.y() - r.bottom()) <= m:
            edges |= Qt.Edge.BottomEdge
        return edges

    _EDGE_CURSORS = {
        frozenset({Qt.Edge.LeftEdge}): Qt.CursorShape.SizeHorCursor,
        frozenset({Qt.Edge.RightEdge}): Qt.CursorShape.SizeHorCursor,
        frozenset({Qt.Edge.TopEdge}): Qt.CursorShape.SizeVerCursor,
        frozenset({Qt.Edge.BottomEdge}): Qt.CursorShape.SizeVerCursor,
        frozenset({Qt.Edge.TopEdge, Qt.Edge.LeftEdge}): Qt.CursorShape.SizeFDiagCursor,
        frozenset({Qt.Edge.BottomEdge, Qt.Edge.RightEdge}): Qt.CursorShape.SizeFDiagCursor,
        frozenset({Qt.Edge.TopEdge, Qt.Edge.RightEdge}): Qt.CursorShape.SizeBDiagCursor,
        frozenset({Qt.Edge.BottomEdge, Qt.Edge.LeftEdge}): Qt.CursorShape.SizeBDiagCursor,
    }

    def eventFilter(self, obj, event) -> bool:
        et = event.type()
        if et in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress) and self.isVisible():
            gpos = event.globalPosition().toPoint()
            under = QApplication.widgetAt(gpos)
            if under is None or not (under is self or self.isAncestorOf(under)):
                return super().eventFilter(obj, event)

            edges = self._edges_at(gpos)
            if et == QEvent.Type.MouseMove:
                if edges:
                    cursor = self._EDGE_CURSORS.get(frozenset(self._edge_set(edges)))
                    if cursor is not None:
                        self.setCursor(cursor)
                else:
                    self.unsetCursor()
            elif et == QEvent.Type.MouseButtonPress:
                if edges and event.button() == Qt.MouseButton.LeftButton:
                    wh = self.windowHandle()
                    if wh is not None:
                        wh.startSystemResize(edges)
                        return True
        return super().eventFilter(obj, event)

    @staticmethod
    def _edge_set(edges: "Qt.Edges") -> set:
        result = set()
        for e in (Qt.Edge.LeftEdge, Qt.Edge.RightEdge, Qt.Edge.TopEdge, Qt.Edge.BottomEdge):
            if edges & e:
                result.add(e)
        return result

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._size_grip is not None:
            self._size_grip.move(
                self.width() - self._size_grip.width() - 2,
                self.height() - self._size_grip.height() - 2,
            )
            self._size_grip.raise_()
        self._update_window_mask()

    def _toggle_maximize(self, force_normal: bool = False) -> None:
        if force_normal or self.isMaximized():
            self.showNormal()
            self._max_btn.setIcon(_make_win_icon("max"))
        else:
            self.showMaximized()
            self._max_btn.setIcon(_make_win_icon("restore"))
        self._sync_shell_rounding()

    def _sync_shell_rounding(self) -> None:
        is_max = self.isMaximized() or self.isFullScreen()
        prop = "true" if is_max else "false"
        if self._shell.property("maximized") != prop:
            self._shell.setProperty("maximized", prop)
            self._shell.style().unpolish(self._shell)
            self._shell.style().polish(self._shell)
        self._update_window_mask()

    def _update_window_mask(self) -> None:
        if self.objectName() != "mainWindowFrameless":
            return
        if self.isMaximized() or self.isFullScreen() or self.width() <= 0 or self.height() <= 0:
            self.clearMask()
            return
        from PySide6.QtGui import QPainterPath, QRegion

        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(self.width()), float(self.height()), 10, 10)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_shell_rounding()
            self._max_btn.setIcon(_make_win_icon("restore" if self.isMaximized() else "max"))
        super().changeEvent(event)

    def closeEvent(self, event):
        if not self._is_windows:
            QApplication.instance().removeEventFilter(self)
        self.config_data["window_geometry"] = {
            "x": self.x(), "y": self.y(), "width": self.width(), "height": self.height(),
        }
        self.persist_all()
        super().closeEvent(event)

    # -- hotkeys -------------------------------------------------------
    def _setup_hotkeys(self):
        self._shortcuts = []

        def bind(seq, func):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(func)
            self._shortcuts.append(sc)

        bind("Ctrl+R", self._hk_run_current)
        bind("Ctrl+Shift+A", self._hk_queue_current)
        bind("Ctrl+Shift+R", self.queue_manager.run_queue)
        bind("Ctrl+Shift+X", self.queue_manager.clear)
        bind("Ctrl+N", self._new_tab_flow)
        bind("Ctrl+Tab", self._hk_next_tab)
        bind("Ctrl+Shift+Tab", self._hk_prev_tab)
        bind("Ctrl+O", self._toggle_sidebar)
        bind("Ctrl+,", self.open_settings)
        bind("Ctrl+Shift+O", self.outputs_tab._open_folder)
        bind("F5", self.outputs_tab.refresh)

    def _current_workflow_tab(self):
        w = self.tabs.currentWidget()
        return w if isinstance(w, WorkflowTab) else None

    def _hk_run_current(self):
        t = self._current_workflow_tab()
        if t:
            t.run_now()

    def _hk_queue_current(self):
        t = self._current_workflow_tab()
        if t:
            t.add_to_queue()

    def _hk_next_tab(self):
        if self.workflow_tabs:
            i = self.tabs.currentIndex()
            n = self.fixed_tabs_start_index()
            self.tabs.setCurrentIndex((i + 1) % n if i < n else 0)

    def _hk_prev_tab(self):
        if self.workflow_tabs:
            i = self.tabs.currentIndex()
            n = self.fixed_tabs_start_index()
            self.tabs.setCurrentIndex((i - 1) % n if i < n else n - 1)


def apply_style(app: QApplication) -> None:
    app.setStyle("Fusion")

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(10, 10, 10))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(232, 232, 232))
    pal.setColor(QPalette.ColorRole.Base, QColor(7, 7, 7))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(17, 17, 17))
    pal.setColor(QPalette.ColorRole.Text, QColor(232, 232, 232))
    pal.setColor(QPalette.ColorRole.Button, QColor(24, 24, 24))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(232, 232, 232))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(0, 212, 160))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(24, 24, 24))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(220, 220, 220))
    app.setPalette(pal)

    app.setStyleSheet(STYLE_QSS)


def main():
    app = QApplication(sys.argv)
    apply_style(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
