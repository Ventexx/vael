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
import hashlib
import datetime
import requests
from pathlib import Path

from PySide6.QtCore import (
    Qt, QObject, QThread, Signal, QMimeData, QUrl, QSize, QRunnable, QThreadPool,
    QAbstractListModel, QModelIndex, QRect,
)
from PySide6.QtGui import (
    QPixmap, QImage, QDrag, QDesktopServices, QShortcut, QKeySequence, QIcon, QAction,
    QColor, QPen, QPainter, QPainterPath,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QTabBar, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QPushButton, QLineEdit, QFileDialog, QMessageBox, QSplitter,
    QScrollArea, QFrame, QListWidget, QListWidgetItem, QListView, QStyledItemDelegate,
    QStyle, QProgressBar, QToolButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QDialog, QSpinBox, QDoubleSpinBox,
    QSizePolicy, QStackedWidget, QMenu, QCheckBox
)

# ===========================================================================
# config.py — config persistence
# ===========================================================================
CONFIG_FILE = Path(__file__).resolve().with_name("workflows_config.json")
ICON_FILE = Path(__file__).resolve().with_name(
    "icon.ico" if sys.platform == "win32" else "icon.png"
)
THUMB_CACHE_DIR = Path(__file__).resolve().with_name("thumb_cache")
DEFAULT_SERVER = "http://127.0.0.1:8188"
DEFAULT_OUTPUT_DIR = str(Path(__file__).resolve().with_name("outputs"))

# -- Image Selection soft limits (spec section 9, resolved) -----------------
# Purely advisory: crossing these never blocks anything, it just surfaces a
# warning so the user knows before things get unwieldy. See SettingsDialog
# (folder count) and FolderSection (recursion depth).
FOLDER_COUNT_WARN = 20
RECURSION_DEPTH_WARN = 5

DEFAULTS = {
    "server": DEFAULT_SERVER,
    "output_dir": DEFAULT_OUTPUT_DIR,
    "tabs": [],
    "window_geometry": None,
    "sidebar_width": 340,
    "workflow_sidebar_width": 260,
    # -- Image Selection (spec sections 4 & 5) --------------------------
    "image_selection_folders": [],       # [{"path": str, "recursive": bool}, ...]
    "hide_dotted_bang_folders": True,    # global toggle, default on
    "image_browser_state": {
        "expanded_headers": [],          # folder paths (any depth) expanded last session
        "active_tab": None,              # top-level folder path of the last active tab
    },
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

/* ---- Left sidebar (Workflows) ---- */
#workflowSidebar {
    background-color: #121212;
    border-right: 1px solid rgba(255,255,255,0.10);
}
#edgeTab {
    background-color: #121212;
    border-right: 1px solid rgba(255,255,255,0.10);
}
#edgeTab:hover {
    background-color: rgba(0,212,160,0.14);
}
#edgeTabChevron {
    color: rgba(200,200,200,0.45);
    font-weight: 700;
}
QListWidget#workflowList::item {
    padding: 8px 8px;
    border-radius: 6px;
    margin-bottom: 2px;
}
QListWidget#workflowList::item:selected {
    background-color: rgba(0,212,160,0.16);
    color: #e8e8e8;
    border-left: 2px solid #00d4a0;
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

/* ---- Roster icon (bottom Input Roster) ---- */
QFrame#rosterIcon {
    background-color: #141414;
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 7px;
}
QFrame#rosterIcon:hover {
    border-color: rgba(0,212,160,0.35);
}
QFrame#rosterIcon[dragOver="true"] {
    border: 1px dashed #00d4a0;
}
QFrame#rosterIcon[armed="true"] {
    border: 2px solid #00d4a0;
    background-color: rgba(0,212,160,0.08);
}
QLabel#rosterIconCanvas {
    background: transparent;
    color: rgba(200,200,200,0.35);
    font-size: 16px;
    font-weight: 300;
}

/* ---- Center — Image Browser ---- */
#imageBrowserPanel {
    background-color: #0d0d0d;
}
QTabWidget#browserTabs::pane {
    border: none;
    border-top: 1px solid rgba(255,255,255,0.07);
    top: -1px;
}
QTabWidget#browserTabs QTabBar::tab {
    background: transparent;
    color: rgba(200,200,200,0.55);
    padding: 7px 14px;
    margin-right: 2px;
    border-bottom: 2px solid transparent;
}
QTabWidget#browserTabs QTabBar::tab:selected {
    color: #e8e8e8;
    border-bottom: 2px solid #00d4a0;
}
QTabWidget#browserTabs QTabBar::tab:hover:!selected {
    color: rgba(220,220,220,0.85);
}
QFrame#folderHeader {
    background-color: #141414;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 6px;
}
QFrame#folderHeader:hover {
    border-color: rgba(0,212,160,0.30);
}
QFrame#subHeader {
    background-color: transparent;
    border-left: 2px solid rgba(0,212,160,0.55);
    border-radius: 0px;
}
QFrame#subHeader:hover {
    background-color: rgba(255,255,255,0.03);
}
/* Nesting depth reads as a progressively fainter accent, so deeper
   sub-headers feel "further in" without needing extra chrome (spec 3.4). */
QFrame#subHeader[depth="2"] { border-left-color: rgba(0,212,160,0.44); }
QFrame#subHeader[depth="3"] { border-left-color: rgba(0,212,160,0.34); }
QFrame#subHeader[depth="4"] { border-left-color: rgba(0,212,160,0.26); }
QFrame#subHeader[depth="5"] { border-left-color: rgba(0,212,160,0.20); }
QFrame#subHeader[depth="6"] { border-left-color: rgba(0,212,160,0.16); }
/* Past the recommended recursion-depth guideline (soft warning only, spec
   section 9) — amber instead of teal, still fully functional. */
QFrame#subHeader[deep="true"] {
    border-left: 2px solid rgba(226,163,55,0.55);
}
QFrame#subHeader[deep="true"]:hover {
    background-color: rgba(226,163,55,0.05);
}
QLabel#folderHeaderName {
    color: rgba(230,230,230,0.92);
    font-weight: 600;
}
QLabel#subHeaderName {
    color: rgba(200,200,200,0.75);
    font-weight: 500;
    font-size: 12px;
}
QLabel#headerChevron {
    color: rgba(0,212,160,0.75);
    font-weight: 700;
}
QListView#thumbnailGrid {
    background: transparent;
    border: none;
}

/* ---- Bottom bar — Input Roster ---- */
#rosterBar {
    background-color: #101010;
    border-top: 1px solid rgba(255,255,255,0.08);
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
QLabel#hint[state="warning"] {
    color: rgba(226,163,55,0.90);
}
QLabel#hint[state="error"] {
    color: rgba(224,110,100,0.85);
}
QLabel#hint[state="muted"] {
    color: rgba(200,200,200,0.30);
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
import hashlib
import datetime
from pathlib import Path

from PySide6.QtCore import (
    Qt, QObject, QThread, Signal, QMimeData, QUrl, QSize, QEvent, QPoint, QRect,
    QPropertyAnimation, QEasingCurve, QRunnable, QThreadPool,
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
    QSizePolicy, QStackedWidget, QMenu, QSizeGrip, QCheckBox
)


APP_TITLE = "cover"
APP_BRAND_PREFIX = "vael. "
APP_BRAND_SUFFIX = "cover"
POLL_INTERVAL = 1.0
POLL_TIMEOUT = 600

HOTKEYS = [
    ("Ctrl+R", "Run the active workflow"),
    ("Ctrl+Shift+A", "Add current workflow (with current inputs) to the run queue"),
    ("Ctrl+Shift+R", "Run every queued workflow, one after another"),
    ("Ctrl+Shift+X", "Clear the run queue"),
    ("Ctrl+N", "Create a new workflow"),
    ("Ctrl+Tab", "Next workflow"),
    ("Ctrl+Shift+Tab", "Previous workflow"),
    ("Ctrl+O", "Toggle the Outputs / Queue sidebar"),
    ("Ctrl+Shift+W", "Toggle the Workflows sidebar"),
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
# Roster icon — one small square per image input on the *active* workflow,
# rendered left-to-right in the bottom "input roster" bar. This replaces the
# old big per-slot panel (ImageSlot): the panel content now belongs to the
# Image Browser (center), and this icon is just the compact roster target.
#
# Primary flow (spec 3.6): click an icon to "arm" it, then click a thumbnail
# in the Image Browser to assign it. The Image Browser isn't built yet
# (that's the bulk of section 3), so for now the icon also keeps the old
# manual fallback alive: double-click to browse via file dialog, or
# drag-and-drop a file from Explorer straight onto it. Dragging one icon
# onto another still reorders (grip) or swaps image content (canvas).
# ---------------------------------------------------------------------------
class RosterIcon(QFrame):
    reorderRequested = Signal(int, int)     # source_index, target_index (moves the whole slot)
    imageSwapRequested = Signal(int, int)   # source_index, target_index (swaps only image content)
    armToggled = Signal(int)                # this icon's index was clicked to arm/disarm
    changed = Signal()

    SIZE = 60

    def __init__(self, index, node_id, caption, parent=None):
        super().__init__(parent)
        self.setObjectName("rosterIcon")
        self.setProperty("armed", False)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.NoFrame)
        self.index = index
        self.node_id = node_id
        self.caption = caption
        self.filepath = None
        self._pixmap = None
        self._press_pos = None
        self._dragging = False
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self._update_tooltip()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = QLabel("+")
        self.canvas.setObjectName("rosterIconCanvas")
        self.canvas.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.canvas)

    def _update_tooltip(self):
        base = f"Input #{self.index + 1}: {self.caption}"
        hint = "\n\nClick to arm for the Image Browser.\nDouble-click or drag a file here to assign manually.\nDrag onto another icon to reorder or swap.\nRight-click to clear."
        self.setToolTip(base + hint)

    # -- drag & drop -----------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            self._dragging = False
        elif event.button() == Qt.RightButton:
            self.clear_image()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_pos is not None and (event.buttons() & Qt.LeftButton) and not self._dragging:
            if (event.position().toPoint() - self._press_pos).manhattanLength() > QApplication.startDragDistance():
                self._dragging = True
                self._start_drag()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and not self._dragging:
            self.armToggled.emit(self.index)
        self._press_pos = None
        self._dragging = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.browse_image()
        super().mouseDoubleClickEvent(event)

    def _start_drag(self):
        mime = QMimeData()
        mime.setData("application/x-slot-reorder", str(self.index).encode())
        # Also register as an image-swap source when this icon already holds
        # an image, so dropping it onto another filled icon swaps content.
        if self.filepath:
            mime.setData("application/x-slot-image", str(self.index).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        if self._pixmap is not None:
            drag.setPixmap(self._pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
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
        if md.hasFormat("application/x-slot-image"):
            src = int(bytes(md.data("application/x-slot-image")).decode())
            if src != self.index:
                self.imageSwapRequested.emit(src, self.index)
            event.acceptProposedAction()
        elif md.hasFormat("application/x-slot-reorder"):
            src = int(bytes(md.data("application/x-slot-reorder")).decode())
            if src != self.index:
                self.reorderRequested.emit(src, self.index)
            event.acceptProposedAction()
        elif md.hasUrls():
            path = md.urls()[0].toLocalFile()
            if path:
                self.set_image_path(path)
            event.acceptProposedAction()

    # -- image handling ----------------------------------------------------
    def browse_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select input image (manual override)", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
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

    def set_armed(self, armed: bool):
        if self.property("armed") == armed:
            return
        self.setProperty("armed", armed)
        self.style().unpolish(self)
        self.style().polish(self)

    def _render(self):
        if self._pixmap is not None:
            inner = self.SIZE - 6
            scaled = self._pixmap.scaled(inner, inner, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.canvas.setPixmap(scaled)
            self.canvas.setText("")
        else:
            self.canvas.setPixmap(QPixmap())
            self.canvas.setText("+")

    def set_index(self, new_index):
        self.index = new_index
        self._update_tooltip()


# ---------------------------------------------------------------------------
# One workflow's data + run logic (no widget of its own any more). Under the
# revamped shell there's exactly one center Image Browser and one bottom
# roster shared by whichever workflow is active, so the old per-tab QWidget
# is replaced by a plain QObject the shared RosterBar renders against.
# ---------------------------------------------------------------------------
class WorkflowState(QObject):
    statusChanged = Signal(str, bool)     # text, is_error
    runStateChanged = Signal(bool)        # running
    slotsRebuilt = Signal()               # slot list replaced wholesale (e.g. on load)

    def __init__(self, main_window, data=None):
        super().__init__()
        self.main_window = main_window
        data = data or {}
        self.workflow_path = data.get("workflow_path")
        self.optional_identifier = data.get("optional_identifier", "")
        self.saved_slot_node_order = data.get("slot_node_order") or []
        self.name = Path(self.workflow_path).stem if self.workflow_path else "Workflow"

        self.raw_workflow = None
        self.slots = []            # list of {"node_id","caption","filepath"}, display order
        self.optional_node_id = None
        self.param_values = {}     # key -> current value for the optional node's editable inputs
        self.status_text = "Ready."
        self.status_error = False
        self.running = False
        self._thread = None
        self._worker = None

        if self.workflow_path and os.path.exists(self.workflow_path):
            try:
                self.load_workflow(self.workflow_path)
            except Exception as e:
                self._set_status(f"Couldn't load workflow: {e}", error=True)
        elif self.workflow_path:
            self._set_status("Workflow file not found - reconfigure this workflow.", error=True)

    # -- Workflow loading -------------------------------------------------
    def load_workflow(self, path):
        wf = load_workflow_file(path)
        self.raw_workflow = wf
        self.workflow_path = path
        self.name = Path(path).stem
        self.main_window.rename_workflow(self, self.name)

        image_nodes = find_load_image_nodes(wf)
        if self.saved_slot_node_order:
            ordered = [n for n in self.saved_slot_node_order if n in image_nodes]
            ordered += [n for n in image_nodes if n not in ordered]
            image_nodes = ordered

        self.slots = [
            {"node_id": nid, "caption": node_label(wf, nid), "filepath": None}
            for nid in image_nodes
        ]

        self.optional_node_id = find_node(wf, self.optional_identifier) if self.optional_identifier else None
        self.param_values = {}
        for key, (value, _type_name) in self.editable_params().items():
            self.param_values[key] = value

        self.slotsRebuilt.emit()
        self._set_status(f"Loaded. {len(self.slots)} image input(s) found.")

    def editable_params(self):
        if not self.optional_node_id or not self.raw_workflow:
            return {}
        return get_editable_inputs(self.raw_workflow.get(self.optional_node_id, {}))

    # -- Reordering / swapping (driven by the roster bar's icons) ---------
    def reorder_slot(self, src, tgt):
        if src == tgt or src >= len(self.slots) or tgt >= len(self.slots):
            return
        item = self.slots.pop(src)
        self.slots.insert(tgt, item)
        self.slotsRebuilt.emit()
        self.main_window.persist_all()

    def swap_slot_images(self, src, tgt):
        if src == tgt or src >= len(self.slots) or tgt >= len(self.slots):
            return
        self.slots[src]["filepath"], self.slots[tgt]["filepath"] = (
            self.slots[tgt]["filepath"], self.slots[src]["filepath"],
        )
        self.main_window.persist_all()

    def to_dict(self):
        return {
            "workflow_path": self.workflow_path,
            "optional_identifier": self.optional_identifier,
            "slot_node_order": [s["node_id"] for s in self.slots],
        }

    # -- Running -------------------------------------------------------
    def _gather_image_map(self):
        return {s["node_id"]: s["filepath"] for s in self.slots}

    def _validate(self):
        if not self.raw_workflow:
            QMessageBox.warning(self.main_window, "No workflow", f"'{self.name}' has no valid workflow loaded.")
            return False
        missing = [s["caption"] for s in self.slots if not s["filepath"]]
        if missing:
            QMessageBox.warning(self.main_window, "Missing images", "Please fill in:\n- " + "\n- ".join(missing))
            return False
        return True

    def run_now(self):
        if not self._validate():
            return
        self.running = True
        self.runStateChanged.emit(True)
        self._set_status("Running...")
        self._thread = QThread()
        self._worker = RunWorker(
            self.main_window.server, copy.deepcopy(self.raw_workflow),
            self._gather_image_map(), self.optional_node_id, dict(self.param_values),
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_run_finished)
        self._worker.error.connect(self._on_run_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.start()

    def _on_run_finished(self, data):
        self.running = False
        self.runStateChanged.emit(False)
        self._set_status("Done.")
        self.main_window.save_output(self.name, data)

    def _on_run_error(self, message):
        self.running = False
        self.runStateChanged.emit(False)
        self._set_status(f"Error: {message}", error=True)
        QMessageBox.critical(self.main_window, "Run failed", message)

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
            "param_values": dict(self.param_values),
        })
        self._set_status("Added current inputs to the run queue.")

    def _set_status(self, text, error=False):
        self.status_text = text
        self.status_error = error
        self.statusChanged.emit(text, error)


# ---------------------------------------------------------------------------
# Thumbnail worker plumbing (spec 3.5, 7). QRunnable has no signals of its
# own, so each worker owns a small QObject to talk back to the GUI thread.
# All file I/O and image decoding happens off the UI thread; only cheap
# QPixmap-from-QImage conversion happens back on it.
# ---------------------------------------------------------------------------
class _ScanSignals(QObject):
    done = Signal(str, list, list)   # section_path, files, subdirs
    failed = Signal(str, str)        # section_path, message


IMAGE_FILE_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff",
)


class _RoughScanWorker(QRunnable):
    """Rough pass: list filenames (and, if recursive, subfolder names) for
    one folder section. Fast — no image decoding at all."""

    def __init__(self, section_path, recursive, hide_dotted_bang):
        super().__init__()
        self.section_path = section_path
        self.recursive = recursive
        self.hide_dotted_bang = hide_dotted_bang
        self.signals = _ScanSignals()

    def run(self):
        files, subdirs = [], []
        try:
            with os.scandir(self.section_path) as it:
                for entry in it:
                    name = entry.name
                    if self.hide_dotted_bang and (name.startswith(".") or name.startswith("!")):
                        continue
                    try:
                        if entry.is_file() and name.lower().endswith(IMAGE_FILE_EXTENSIONS):
                            files.append(entry.path)
                        elif self.recursive and entry.is_dir():
                            subdirs.append(entry.path)
                    except OSError:
                        continue
        except FileNotFoundError:
            self.signals.failed.emit(self.section_path, "not_found")
            return
        except PermissionError:
            self.signals.failed.emit(self.section_path, "permission_denied")
            return
        except OSError as e:
            self.signals.failed.emit(self.section_path, f"error:{e}")
            return
        files.sort(key=str.lower)
        subdirs.sort(key=str.lower)
        self.signals.done.emit(self.section_path, files, subdirs)


class _ThumbSignals(QObject):
    ready = Signal(str, QImage)   # filepath, decoded (already scaled) image


class _ThumbnailWorker(QRunnable):
    """Full pass: decode + scale one folder section's images, on the shared
    thread pool, using a disk cache keyed by (path, mtime) so re-expanding
    a header later is instant."""

    THUMB_PX = 160

    def __init__(self, filepaths, cache_dir):
        super().__init__()
        self.filepaths = filepaths
        self.cache_dir = cache_dir
        self.signals = _ThumbSignals()

    def _cache_path(self, filepath, mtime_ns):
        key = hashlib.sha1(filepath.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}_{mtime_ns}.png"

    def run(self):
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        for path in self.filepaths:
            try:
                mtime_ns = os.stat(path).st_mtime_ns
            except OSError:
                continue
            cache_path = self._cache_path(path, mtime_ns)
            img = None
            if cache_path.exists():
                cached = QImage(str(cache_path))
                if not cached.isNull():
                    img = cached
            if img is None:
                src = QImage(path)
                if src.isNull():
                    continue
                img = src.scaled(
                    self.THUMB_PX, self.THUMB_PX,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation,
                )
                try:
                    img.save(str(cache_path), "PNG")
                except OSError:
                    pass
            self.signals.ready.emit(path, img)


# ---------------------------------------------------------------------------
# Thumbnail grid backing store + painter (spec 3.5 / Section 7 technical
# notes). ThumbnailModel holds nothing but filepaths + decoded pixmaps —
# no per-item widgets — and ThumbnailDelegate paints each cell directly,
# so a section's memory/layout cost no longer scales with per-item Qt
# object overhead the way QListWidgetItem + QIcon did.
# ---------------------------------------------------------------------------
class ThumbnailModel(QAbstractListModel):
    FilepathRole = Qt.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filepaths = []
        self._pixmaps = {}   # filepath -> QPixmap, populated as thumbnails decode

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._filepaths)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        path = self._filepaths[index.row()]
        if role == Qt.DisplayRole:
            return Path(path).name
        if role == Qt.ToolTipRole:
            return path
        if role == self.FilepathRole:
            return path
        if role == Qt.DecorationRole:
            return self._pixmaps.get(path)
        return None

    def set_files(self, filepaths):
        self.beginResetModel()
        self._filepaths = list(filepaths)
        self._pixmaps = {}
        self.endResetModel()

    def set_thumbnail(self, filepath, pixmap):
        try:
            row = self._filepaths.index(filepath)
        except ValueError:
            return
        self._pixmaps[filepath] = pixmap
        idx = self.index(row)
        self.dataChanged.emit(idx, idx, [Qt.DecorationRole])

    def filepath_at(self, index):
        if index.isValid() and 0 <= index.row() < len(self._filepaths):
            return self._filepaths[index.row()]
        return None


class ThumbnailDelegate(QStyledItemDelegate):
    """Paints one thumbnail cell: image (or a soft placeholder while it's
    still decoding), plus an elided filename caption underneath."""

    ICON_PX = 96
    CELL_W = 108
    CELL_H = 108 + 18

    def sizeHint(self, option, index):
        return QSize(self.CELL_W, self.CELL_H)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect
        hovered = bool(option.state & QStyle.State_MouseOver)

        if hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 212, 160, 26))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 8, 8)

        icon_x = rect.x() + (rect.width() - self.ICON_PX) // 2
        icon_rect = QRect(icon_x, rect.y() + 5, self.ICON_PX, self.ICON_PX)

        pixmap = index.data(Qt.DecorationRole)
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(
                self.ICON_PX, self.ICON_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            x = icon_rect.x() + (self.ICON_PX - scaled.width()) // 2
            y = icon_rect.y() + (self.ICON_PX - scaled.height()) // 2
            path = _rounded_pixmap_path(scaled.rect(), 6)
            painter.save()
            painter.translate(x, y)
            painter.setClipPath(path)
            painter.drawPixmap(0, 0, scaled)
            painter.restore()
        else:
            # Placeholder while the background thumbnail worker hasn't
            # reached this file yet (rough pass reserved the slot already).
            painter.setPen(QPen(QColor(255, 255, 255, 30)))
            painter.setBrush(QColor(255, 255, 255, 8))
            painter.drawRoundedRect(icon_rect, 6, 6)

        name = index.data(Qt.DisplayRole) or ""
        text_rect = QRect(rect.x() + 3, icon_rect.bottom() + 4, rect.width() - 6, 14)
        painter.setPen(QColor(200, 200, 200, 145 if not hovered else 210))
        fm = painter.fontMetrics()
        elided = fm.elidedText(name, Qt.ElideMiddle, text_rect.width())
        painter.drawText(text_rect, Qt.AlignHCenter | Qt.AlignVCenter, elided)
        painter.restore()


def _rounded_pixmap_path(rect, radius):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


class ThumbnailGrid(QListView):
    thumbnailClicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("thumbnailGrid")
        self._model = ThumbnailModel(self)
        self.setModel(self._model)
        self._delegate = ThumbnailDelegate(self)
        self.setItemDelegate(self._delegate)
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(4)
        self.setFrameShape(QFrame.NoFrame)
        self.setSelectionMode(QListView.NoSelection)
        self.setMouseTracking(True)   # required for hover-state repaint in the delegate
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.clicked.connect(self._on_item_clicked)

    def set_files(self, filepaths):
        self._model.set_files(filepaths)
        self._sync_height()

    def set_thumbnail(self, filepath, qimage):
        self._model.set_thumbnail(filepath, QPixmap.fromImage(qimage))

    def _on_item_clicked(self, index):
        path = self._model.filepath_at(index)
        if path:
            self.thumbnailClicked.emit(path)

    def _sync_height(self):
        # Fixed-position (non-scrolling) grid: grow to fit its own rows so
        # the *page* scrolls, not each individual grid (spec 3.5 design
        # caveat — collapsed sections still never scan/decode anything;
        # this only governs layout of an already-expanded section).
        n = self._model.rowCount()
        if n == 0:
            self.setFixedHeight(0)
            return
        cell_w = self._delegate.CELL_W + self.spacing()
        cell_h = self._delegate.CELL_H + self.spacing()
        cols = max(1, self.viewport().width() // cell_w)
        rows = (n + cols - 1) // cols
        self.setFixedHeight(rows * cell_h + 8)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_height()


# ---------------------------------------------------------------------------
# One folder header (spec 3.3) or, nested under it, one recursive
# sub-header (spec 3.4). Closed by default; only scans/generates thumbnails
# once the user expands it.
# ---------------------------------------------------------------------------
class FolderSection(QWidget):
    def __init__(self, browser, path, depth=0, recursive=False, closable=False):
        super().__init__()
        self.browser = browser
        self.path = path
        self.depth = depth
        self.recursive = recursive
        self.closable = closable
        self._expanded = False
        self._rough_done = False
        self._rough_started = False
        self._thumb_started = False
        self._files = []
        self._child_sections = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        header = QFrame()
        header.setObjectName("subHeader" if depth > 0 else "folderHeader")
        if depth > 0:
            # Drives the per-depth accent-color QSS below, so deeper nesting
            # reads as progressively "further in" at a glance (spec 3.4).
            header.setProperty("depth", min(depth, 6))
        is_deep = depth > RECURSION_DEPTH_WARN
        if is_deep:
            header.setProperty("deep", True)
        header.setCursor(Qt.PointingHandCursor)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(8 + depth * 16, 6, 8, 6)
        header_lay.setSpacing(6)

        self.chevron_lbl = QLabel("\u25b8")
        self.chevron_lbl.setObjectName("headerChevron")
        header_lay.addWidget(self.chevron_lbl)

        name_text = Path(path).name or path
        self.name_lbl = QLabel(name_text + (" \u26a0" if is_deep else ""))
        self.name_lbl.setObjectName("subHeaderName" if depth > 0 else "folderHeaderName")
        tooltip = path
        if is_deep:
            tooltip += f"\n\u26a0 Nested {depth} levels deep \u2014 past the recommended {RECURSION_DEPTH_WARN}-level guideline. Still fully functional, just flagged for awareness."
        self.name_lbl.setToolTip(tooltip)
        header_lay.addWidget(self.name_lbl)

        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("hint")
        header_lay.addWidget(self.count_lbl)
        header_lay.addStretch(1)

        if closable:
            close_btn = QToolButton()
            close_btn.setObjectName("iconButton")
            close_btn.setText("\u00d7")
            close_btn.setToolTip("Hide for this session (not removed from Settings)")
            close_btn.setCursor(Qt.PointingHandCursor)
            close_btn.clicked.connect(self._close_for_session)
            header_lay.addWidget(close_btn)

        header.mousePressEvent = self._header_clicked
        outer.addWidget(header)

        self.body = QWidget()
        body_lay = QVBoxLayout(self.body)
        body_lay.setContentsMargins(depth * 16, 0, 0, 0)
        body_lay.setSpacing(6)

        self.grid = ThumbnailGrid()
        self.grid.thumbnailClicked.connect(self.browser.thumbnailClicked)
        body_lay.addWidget(self.grid)

        self.children_container = QWidget()
        self.children_lay = QVBoxLayout(self.children_container)
        self.children_lay.setContentsMargins(0, 0, 0, 0)
        self.children_lay.setSpacing(4)
        body_lay.addWidget(self.children_container)

        outer.addWidget(self.body)
        self.body.hide()

    def _header_clicked(self, event):
        self.toggle()

    def _close_for_session(self):
        self.browser.close_tab_for_session(self.path)

    # -- expand / collapse -------------------------------------------------
    def toggle(self):
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded, persist=True):
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self.chevron_lbl.setText("\u25be" if expanded else "\u25b8")
        self.body.setVisible(expanded)
        if expanded:
            if not self._rough_done:
                # Rough pass may already be running (e.g. kicked off when the
                # tab was first opened) — ensure_rough_pass() no-ops in that case.
                self.ensure_rough_pass()
            elif not self._thumb_started and self._files:
                # Rough pass already completed (count was showing on the
                # still-collapsed header) — the thumbnail pass is the only
                # thing expand still needs to trigger.
                self._start_thumbnail_pass()
        if persist:
            self.browser.note_expanded(self.path, expanded)

    def is_expanded(self):
        return self._expanded

    # -- lazy loading (spec 3.5) -----------------------------------------
    def ensure_rough_pass(self):
        """Kick off the rough (filename-listing) pass if it hasn't run yet,
        independent of expand state. Called both on tab activation (per the
        spec 3.5/6 fix: a still-collapsed header should show a count as soon
        as its tab is opened) and, as a fallback, on expand."""
        if self._rough_done or self._rough_started:
            return
        self._rough_started = True
        self.count_lbl.setText("\u2026")
        worker = _RoughScanWorker(self.path, self.recursive, self.browser.hide_dotted_bang())
        worker.signals.done.connect(self._on_rough_done)
        worker.signals.failed.connect(self._on_rough_failed)
        self.browser.thread_pool.start(worker)

    def _on_rough_failed(self, path, message):
        if path != self.path:
            return
        # Allow a retry: re-expanding (or the tab being reactivated) will
        # attempt the rough pass again since it never completed.
        self._rough_started = False
        labels = {
            "not_found": "(folder not found)",
            "permission_denied": "(permission denied)",
        }
        self.count_lbl.setText(labels.get(message, "(unreadable)"))
        self._set_count_state("error")

    def _set_count_state(self, state):
        self.count_lbl.setProperty("state", state)
        self.count_lbl.style().unpolish(self.count_lbl)
        self.count_lbl.style().polish(self.count_lbl)

    def _on_rough_done(self, path, files, subdirs):
        if path != self.path:
            return
        self._rough_done = True
        self._files = files
        if files or subdirs:
            self.count_lbl.setText(f"({len(files)})")
            self._set_count_state("normal")
        else:
            self.count_lbl.setText("(empty)")
            self._set_count_state("muted")
        # Reserve grid layout space (filenames only, no thumbnails yet) —
        # safe to do even while the section is still collapsed.
        self.grid.set_files(files)

        for sub_path in subdirs:
            child = FolderSection(self.browser, sub_path, depth=self.depth + 1, recursive=True)
            self.children_lay.addWidget(child)
            self._child_sections.append(child)
            if sub_path in self.browser.saved_expanded_paths:
                child.set_expanded(True, persist=False)

        # Only decode thumbnails if the section is actually expanded — the
        # rough pass alone must never trigger image decoding.
        if self._expanded and files and not self._thumb_started:
            self._start_thumbnail_pass()

    def _start_thumbnail_pass(self):
        self._thumb_started = True
        worker = _ThumbnailWorker(list(self._files), self.browser.thumb_cache_dir)
        worker.signals.ready.connect(self.grid.set_thumbnail)
        self.browser.thread_pool.start(worker)


# ---------------------------------------------------------------------------
# Center — Image Browser (spec section 3). A single, persistent, global
# instance shared by every workflow: one tab per configured top-level
# folder, closed-by-default headers/sub-headers, lazy rough+thumbnail
# passes off the UI thread, and a disk thumbnail cache. Switching the
# active workflow never touches this widget — only the roster below it.
# ---------------------------------------------------------------------------
class ImageBrowser(QWidget):
    thumbnailClicked = Signal(str)   # filepath — MainWindow feeds this to the armed roster slot

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setObjectName("imageBrowserPanel")
        self.thread_pool = QThreadPool.globalInstance()
        self.thumb_cache_dir = THUMB_CACHE_DIR
        self._closed_this_session = set()   # paths ×'d away — in-memory only, cleared on relaunch

        state = main_window.config_data.get("image_browser_state") or {}
        self.saved_expanded_paths = set(state.get("expanded_headers") or [])
        self._saved_active_tab = state.get("active_tab")
        self._top_sections = []   # top-level FolderSection per tab index, in tab order

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("browserTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Restore-hidden-folder affordance, in its own row so it stays
        # visible even when every tab is currently hidden (finishes Section
        # 5's one loose thread: a ×'d tab previously had no in-session way
        # back short of restarting the app).
        top_row = QHBoxLayout()
        top_row.setContentsMargins(6, 4, 6, 0)
        top_row.addStretch(1)
        self.restore_hidden_btn = QToolButton()
        self.restore_hidden_btn.setObjectName("iconButton")
        self.restore_hidden_btn.setText("\u21bb")
        self.restore_hidden_btn.setPopupMode(QToolButton.InstantPopup)
        self.restore_hidden_btn.setCursor(Qt.PointingHandCursor)
        self.restore_hidden_btn.hide()
        top_row.addWidget(self.restore_hidden_btn)
        layout.addLayout(top_row)

        layout.addWidget(self.tabs, 1)

        self.empty_hint = QLabel(
            "No folders configured yet.\n"
            "Open Settings \u2192 Image Selection to add one."
        )
        self.empty_hint.setObjectName("hint")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty_hint, 1)

        self.reload_folders()

    def hide_dotted_bang(self):
        return bool(self.main_window.config_data.get("hide_dotted_bang_folders", True))

    # -- (re)building tabs from Settings -----------------------------------
    def reload_folders(self):
        prev_active = self._current_tab_path() or self._saved_active_tab
        self.tabs.clear()
        self._top_sections = []
        folders = self.main_window.config_data.get("image_selection_folders", [])
        configured_paths = {f.get("path", "") for f in folders}
        # A folder removed from Settings entirely shouldn't linger in the
        # restore-hidden menu.
        self._closed_this_session &= configured_paths
        has_folders = len(folders) > 0

        restore_index = 0
        for i, folder_cfg in enumerate(folders):
            path = folder_cfg.get("path", "")
            recursive = bool(folder_cfg.get("recursive", False))
            if path in self._closed_this_session:
                continue

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            section = FolderSection(self, path, depth=0, recursive=recursive, closable=True)
            wrapper = QWidget()
            wrapper_lay = QVBoxLayout(wrapper)
            wrapper_lay.setContentsMargins(12, 12, 12, 12)
            wrapper_lay.addWidget(section)
            wrapper_lay.addStretch(1)
            scroll.setWidget(wrapper)

            name = Path(path).name or path
            self.tabs.addTab(scroll, name)
            self.tabs.setTabToolTip(self.tabs.count() - 1, path)
            self._top_sections.append(section)
            if path == prev_active:
                restore_index = self.tabs.count() - 1
            if path in self.saved_expanded_paths:
                section.set_expanded(True, persist=False)

        if self.tabs.count() > 0:
            self.tabs.setCurrentIndex(restore_index)
            # currentChanged doesn't fire if restore_index is already 0 (the
            # default), so make sure the initially-active tab still gets its
            # rough pass kicked off.
            self._trigger_rough_pass_for_tab(restore_index)

        self._update_empty_state()
        self._refresh_restore_hidden_button()

    def _update_empty_state(self):
        has_tabs = self.tabs.count() > 0
        self.tabs.setVisible(has_tabs)
        self.empty_hint.setVisible(not has_tabs)
        if not has_tabs:
            if self._closed_this_session:
                self.empty_hint.setText(
                    "All configured folders are hidden for this session.\n"
                    "Click \u21bb above to restore them."
                )
            else:
                self.empty_hint.setText(
                    "No folders configured yet.\n"
                    "Open Settings \u2192 Image Selection to add one."
                )

    def _refresh_restore_hidden_button(self):
        if not self._closed_this_session:
            self.restore_hidden_btn.hide()
            return
        self.restore_hidden_btn.setToolTip(
            f"{len(self._closed_this_session)} folder(s) hidden for this session \u2014 click to restore"
        )
        menu = QMenu(self.restore_hidden_btn)
        for path in sorted(self._closed_this_session, key=str.lower):
            name = Path(path).name or path
            action = menu.addAction(name)
            action.setToolTip(path)
            action.triggered.connect(lambda checked=False, p=path: self._restore_closed_folder(p))
        menu.addSeparator()
        restore_all = menu.addAction("Restore all")
        restore_all.triggered.connect(self._restore_all_closed_folders)
        self.restore_hidden_btn.setMenu(menu)
        self.restore_hidden_btn.show()

    def _restore_closed_folder(self, path):
        self._closed_this_session.discard(path)
        self.reload_folders()

    def _restore_all_closed_folders(self):
        self._closed_this_session.clear()
        self.reload_folders()

    def _trigger_rough_pass_for_tab(self, index):
        """Spec 3.5/6 fix: the rough pass (filename listing + count) must
        run as soon as a tab is opened, independent of whether its header is
        expanded — not only when the user expands it."""
        if 0 <= index < len(self._top_sections):
            self._top_sections[index].ensure_rough_pass()

    def close_tab_for_session(self, path):
        """Hide a folder's tab for the rest of this session (spec 3.3's ×).
        Removes the tab immediately, rather than only on the next full
        reload_folders() call — and stays undoable via the restore-hidden
        control, since nothing here touches Settings/disk."""
        self._closed_this_session.add(path)
        for i, section in enumerate(self._top_sections):
            if section.path == path:
                self.tabs.removeTab(i)
                del self._top_sections[i]
                break
        self._update_empty_state()
        self._persist_state()
        self._refresh_restore_hidden_button()

    def note_expanded(self, path, expanded):
        if expanded:
            self.saved_expanded_paths.add(path)
        else:
            self.saved_expanded_paths.discard(path)
        self._persist_state()

    def _current_tab_path(self):
        idx = self.tabs.currentIndex()
        if idx < 0:
            return None
        tooltip = self.tabs.tabToolTip(idx)
        return tooltip or None

    def _on_tab_changed(self, index):
        self._trigger_rough_pass_for_tab(index)
        self._persist_state()

    def _persist_state(self):
        self.main_window.config_data["image_browser_state"] = {
            "expanded_headers": sorted(self.saved_expanded_paths),
            "active_tab": self._current_tab_path(),
        }
        self.main_window.persist_all()


# ---------------------------------------------------------------------------
# Bottom bar — the "input roster" (spec section 2.3), plus the run/queue
# controls and optional-parameter form for whichever workflow is active.
# Only this bar changes when the active workflow changes; it never resets
# the Image Browser above it.
# ---------------------------------------------------------------------------
class RosterBar(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setObjectName("rosterBar")
        self.state = None
        self.icons = []
        self.armed_index = None
        self.param_widgets = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 8, 14, 10)
        outer.setSpacing(6)

        toolbar = QHBoxLayout()
        self.status_label = QLabel("No workflow selected.")
        self.status_label.setObjectName("hint")
        toolbar.addWidget(self.status_label)
        toolbar.addStretch(1)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("dangerButton")
        self.clear_btn.clicked.connect(self._on_clear_clicked)
        toolbar.addWidget(self.clear_btn)
        self.queue_btn = QPushButton("+ Add to Queue")
        self.queue_btn.clicked.connect(self._on_queue_clicked)
        toolbar.addWidget(self.queue_btn)
        self.run_btn = QPushButton("\u25b6  Run")
        self.run_btn.setObjectName("accentButton")
        self.run_btn.clicked.connect(self._on_run_clicked)
        toolbar.addWidget(self.run_btn)
        outer.addLayout(toolbar)

        self.progress = QProgressBar()
        self.progress.setMaximumHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.hide()
        outer.addWidget(self.progress)

        self.param_form_widget = QWidget()
        self.param_form = QFormLayout(self.param_form_widget)
        self.param_form.setContentsMargins(0, 4, 0, 4)
        outer.addWidget(self.param_form_widget)
        self.param_form_widget.hide()

        roster_label = QLabel("INPUT ROSTER")
        roster_label.setObjectName("hint")
        outer.addWidget(roster_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setFixedHeight(RosterIcon.SIZE + 16)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.roster_row_widget = QWidget()
        self.roster_row = QHBoxLayout(self.roster_row_widget)
        self.roster_row.setContentsMargins(2, 2, 2, 2)
        self.roster_row.setSpacing(8)
        self.roster_row.addStretch(1)
        scroll.setWidget(self.roster_row_widget)
        outer.addWidget(scroll)

        self.set_workflow(None)

    # -- switching the active workflow --------------------------------------
    def set_workflow(self, state):
        if self.state is not None:
            try:
                self.state.statusChanged.disconnect(self._on_status_changed)
                self.state.runStateChanged.disconnect(self._on_run_state_changed)
                self.state.slotsRebuilt.disconnect(self._on_slots_rebuilt)
            except (TypeError, RuntimeError):
                pass

        self.state = state
        self.armed_index = None

        if state is not None:
            state.statusChanged.connect(self._on_status_changed)
            state.runStateChanged.connect(self._on_run_state_changed)
            state.slotsRebuilt.connect(self._on_slots_rebuilt)

        self._rebuild_icons()
        self._rebuild_params()

        self.queue_btn.setEnabled(state is not None)
        self.clear_btn.setEnabled(state is not None)
        if state is None:
            self.run_btn.setEnabled(False)
            self.status_label.setText("No workflow selected. Add one from the workflows sidebar.")
            self.status_label.setStyleSheet("")
            self.progress.hide()
        else:
            self.run_btn.setEnabled(not state.running)
            self._on_status_changed(state.status_text, state.status_error)
            self.progress.setVisible(state.running)

    def _on_slots_rebuilt(self):
        self._rebuild_icons()
        self._rebuild_params()

    def _rebuild_icons(self):
        for icon in self.icons:
            self.roster_row.removeWidget(icon)
            icon.setParent(None)
            icon.deleteLater()
        self.icons = []
        while self.roster_row.count():
            self.roster_row.takeAt(0)

        if self.state is not None:
            for i, slot in enumerate(self.state.slots):
                icon = RosterIcon(i, slot["node_id"], slot["caption"])
                icon.filepath = slot["filepath"]
                if icon.filepath:
                    pix = QPixmap(icon.filepath)
                    if not pix.isNull():
                        icon._pixmap = pix
                icon._render()
                icon.armToggled.connect(self._on_arm_toggled)
                icon.reorderRequested.connect(self._on_reorder)
                icon.imageSwapRequested.connect(self._on_swap)
                icon.changed.connect(lambda idx=i: self._on_icon_changed(idx))
                self.roster_row.addWidget(icon)
                self.icons.append(icon)
        self.roster_row.addStretch(1)

    def _rebuild_params(self):
        while self.param_form.rowCount():
            self.param_form.removeRow(0)
        self.param_widgets = {}
        editable = self.state.editable_params() if self.state is not None else {}
        if not editable:
            self.param_form_widget.hide()
            return
        for key, (default_value, type_name) in editable.items():
            value = self.state.param_values.get(key, default_value)
            if type_name == "int":
                w = QSpinBox()
                w.setRange(-2_147_483_648, 2_147_483_647)
                w.setValue(int(value))
                w.valueChanged.connect(lambda v, k=key: self._on_param_changed(k, v))
            elif type_name == "float":
                w = QDoubleSpinBox()
                w.setRange(-1e12, 1e12)
                w.setDecimals(4)
                w.setValue(float(value))
                w.valueChanged.connect(lambda v, k=key: self._on_param_changed(k, v))
            else:
                w = QLineEdit(str(value))
                w.textChanged.connect(lambda v, k=key: self._on_param_changed(k, v))
            self.param_form.addRow(key, w)
            self.param_widgets[key] = w
        self.param_form_widget.show()

    def _on_param_changed(self, key, value):
        if self.state is not None:
            self.state.param_values[key] = value

    # -- roster interactions -------------------------------------------
    def _on_arm_toggled(self, index):
        self.armed_index = None if self.armed_index == index else index
        for icon in self.icons:
            icon.set_armed(icon.index == self.armed_index)

    def assign_armed(self, filepath):
        """Primary selection flow (spec 3.6): called when a thumbnail is
        clicked in the Image Browser while a roster slot is armed. Assigns
        the image, then auto-advances the armed state to the next empty
        slot so filling several inputs back-to-back doesn't need re-arming
        after every click."""
        if self.armed_index is None or self.armed_index >= len(self.icons):
            return
        self.icons[self.armed_index].set_image_path(filepath)
        next_empty = next(
            (i for i, icon in enumerate(self.icons) if not icon.filepath), None
        )
        self.armed_index = next_empty
        for icon in self.icons:
            icon.set_armed(icon.index == self.armed_index)

    def _on_icon_changed(self, index):
        if self.state is None or index >= len(self.state.slots):
            return
        self.state.slots[index]["filepath"] = self.icons[index].filepath
        self.main_window.persist_all()

    def _on_reorder(self, src, tgt):
        if self.state is not None:
            self.state.reorder_slot(src, tgt)

    def _on_swap(self, src, tgt):
        if self.state is None:
            return
        self.state.swap_slot_images(src, tgt)
        a, b = self.icons[src], self.icons[tgt]
        a.filepath, b.filepath = b.filepath, a.filepath
        a._pixmap, b._pixmap = b._pixmap, a._pixmap
        a._render()
        b._render()

    # -- run / queue / clear ---------------------------------------------
    def _on_run_clicked(self):
        if self.state is not None:
            self.state.run_now()

    def _on_queue_clicked(self):
        if self.state is not None:
            self.state.add_to_queue()

    def _on_clear_clicked(self):
        """Spec 3.7: resets every filled roster slot for the active
        workflow back to empty. Never touches the Image Browser's own
        state (open tabs/headers, scroll position)."""
        if self.state is None:
            return
        for icon in self.icons:
            if icon.filepath:
                icon.clear_image()
        self.armed_index = None
        for icon in self.icons:
            icon.set_armed(False)

    def _on_status_changed(self, text, error):
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color: rgba(220,140,140,0.9);" if error else "")

    def _on_run_state_changed(self, running):
        self.run_btn.setEnabled(not running)
        self.progress.setVisible(running)


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

        thread = QThread()
        worker = RunWorker(
            item["server"], item["raw_workflow"], item["image_map"],
            item["optional_node_id"], item["param_values"],
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # Keep references alive until Qt tells us the thread has actually
        # stopped (thread.quit() only *requests* a stop, it doesn't block).
        self._thread = thread
        self._worker = worker

        def on_finished(data, item=item):
            item["status"] = "Done"
            self.main_window.save_output(item["tab_name"], data)
            self.itemFinished.emit(item["id"], True, "Done")
            self.items.remove(item)
            self.queueChanged.emit()

        def on_error(message, item=item):
            item["status"] = "Error"
            self.itemFinished.emit(item["id"], False, message)
            self.queueChanged.emit()

        def on_thread_finished():
            # Runs only once the worker thread has fully stopped, so it's
            # safe to drop our references and start the next queue item.
            worker.deleteLater()
            thread.deleteLater()
            if self._thread is thread:
                self._thread = None
                self._worker = None
            self._run_next()

        worker.finished.connect(on_finished)
        worker.error.connect(on_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(on_thread_finished)
        thread.start()


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
        self.setMinimumWidth(520)
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

        # -- Image Selection (spec section 4) --------------------------
        img_title = QLabel("Image Selection")
        img_title.setObjectName("sectionTitle")
        layout.addWidget(img_title)

        self.folders = [dict(f) for f in main_window.config_data.get("image_selection_folders", [])]

        self.folder_table = QTableWidget(0, 3)
        self.folder_table.setHorizontalHeaderLabels(["Folder", "Recursive", ""])
        self.folder_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.folder_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.folder_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.folder_table.verticalHeader().hide()
        self.folder_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.folder_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.folder_table.setMaximumHeight(160)
        layout.addWidget(self.folder_table)

        folder_btns = QHBoxLayout()
        add_folder_btn = QPushButton("Add Folder\u2026")
        add_folder_btn.clicked.connect(self._add_folder)
        folder_btns.addWidget(add_folder_btn)
        folder_btns.addStretch(1)
        up_btn = QPushButton("\u2191 Move Up")
        up_btn.clicked.connect(lambda: self._move_folder(-1))
        folder_btns.addWidget(up_btn)
        down_btn = QPushButton("\u2193 Move Down")
        down_btn.clicked.connect(lambda: self._move_folder(1))
        folder_btns.addWidget(down_btn)
        layout.addLayout(folder_btns)

        self.hide_dotted_chk = QCheckBox("Hide folders starting with . or !")
        self.hide_dotted_chk.setChecked(bool(main_window.config_data.get("hide_dotted_bang_folders", True)))
        layout.addWidget(self.hide_dotted_chk)

        folder_hint = QLabel(
            "Order here sets the order of tabs in the Image Browser. \u201cRecursive\u201d\n"
            "shows a folder's subfolders as nested sub-headers instead of ignoring them."
        )
        folder_hint.setObjectName("hint")
        folder_hint.setWordWrap(True)
        layout.addWidget(folder_hint)

        # Soft warning only (spec section 9, resolved) — never blocks adding
        # more folders, just flags when things may get hard to navigate.
        self.folder_warning_lbl = QLabel("")
        self.folder_warning_lbl.setObjectName("hint")
        self.folder_warning_lbl.setProperty("state", "warning")
        self.folder_warning_lbl.setWordWrap(True)
        self.folder_warning_lbl.hide()
        layout.addWidget(self.folder_warning_lbl)

        self._refresh_folder_table()

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

    # -- Image Selection folders -----------------------------------------
    def _refresh_folder_table(self):
        self.folder_table.setRowCount(len(self.folders))
        for row, f in enumerate(self.folders):
            path_item = QTableWidgetItem(f.get("path", ""))
            path_item.setToolTip(f.get("path", ""))
            self.folder_table.setItem(row, 0, path_item)

            chk_wrap = QWidget()
            chk_lay = QHBoxLayout(chk_wrap)
            chk_lay.setContentsMargins(0, 0, 0, 0)
            chk_lay.setAlignment(Qt.AlignCenter)
            chk = QCheckBox()
            chk.setChecked(bool(f.get("recursive", False)))
            chk.stateChanged.connect(lambda state, r=row: self._set_recursive(r, state))
            chk_lay.addWidget(chk)
            self.folder_table.setCellWidget(row, 1, chk_wrap)

            remove_btn = QToolButton()
            remove_btn.setObjectName("iconButton")
            remove_btn.setText("\u00d7")
            remove_btn.setToolTip("Remove folder")
            remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            remove_btn.clicked.connect(lambda _, r=row: self._remove_folder(r))
            self.folder_table.setCellWidget(row, 2, remove_btn)

        if len(self.folders) > FOLDER_COUNT_WARN:
            self.folder_warning_lbl.setText(
                f"\u26a0 {len(self.folders)} folders configured \u2014 more than "
                f"~{FOLDER_COUNT_WARN} can get hard to navigate as tabs. Still fully "
                f"functional, just a heads-up."
            )
            self.folder_warning_lbl.show()
        else:
            self.folder_warning_lbl.hide()

    def _add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select image folder")
        if not path:
            return
        if any(f.get("path") == path for f in self.folders):
            return
        self.folders.append({"path": path, "recursive": True})
        self._refresh_folder_table()

    def _remove_folder(self, row):
        if 0 <= row < len(self.folders):
            del self.folders[row]
            self._refresh_folder_table()

    def _set_recursive(self, row, state):
        if 0 <= row < len(self.folders):
            self.folders[row]["recursive"] = bool(state)

    def _move_folder(self, delta):
        row = self.folder_table.currentRow()
        if row < 0:
            return
        new_row = row + delta
        if 0 <= new_row < len(self.folders):
            self.folders[row], self.folders[new_row] = self.folders[new_row], self.folders[row]
            self._refresh_folder_table()
            self.folder_table.selectRow(new_row)

    def _save(self):
        self.main_window.server = self.server_edit.text().strip() or DEFAULT_SERVER
        self.main_window.output_dir = self.output_edit.text().strip() or DEFAULT_OUTPUT_DIR
        self.main_window.config_data["image_selection_folders"] = self.folders
        self.main_window.config_data["hide_dotted_bang_folders"] = self.hide_dotted_chk.isChecked()
        self.main_window.persist_all()
        self.main_window.image_browser.reload_folders()
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
# Center container — holds the persistent Image Browser + the roster bar.
# Notifies MainWindow when resized so both overlay sidebars (workflows on
# the left, outputs/queue on the right) can be repositioned to match.
# ---------------------------------------------------------------------------
class CenterContainer(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.main_window._position_sidebar()
        self.main_window._position_workflow_sidebar()


# ---------------------------------------------------------------------------
# Draggable handle used to resize an overlay sidebar. `sign` controls which
# drag direction grows the sidebar: -1 for a sidebar docked to the right
# edge (handle on its left, dragging left grows it), +1 for a sidebar
# docked to the left edge (handle on its right, dragging right grows it).
# ---------------------------------------------------------------------------
class _SidebarHandle(QWidget):
    def __init__(self, sidebar, sign=-1, parent=None):
        super().__init__(parent)
        self.sidebar = sidebar
        self.sign = sign
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
            dx = self.sign * (self._start_x - event.globalPosition().toPoint().x())
            self.sidebar.set_width(self._start_width + dx, persist=False)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self.sidebar.persist_width()
            event.accept()


# ---------------------------------------------------------------------------
# Outputs / Queue sidebar — the right-hand counterpart to the workflow
# sidebar (spec 2.2). Functionally unchanged from before the revamp: it
# slides in/out over the center content, is draggable in width (remembered
# across launches), and is closed by default.
# ---------------------------------------------------------------------------
class OutputsSidebar(QWidget):
    MIN_WIDTH = 260
    MAX_WIDTH = 640

    def __init__(self, main_window):
        super().__init__(main_window.center_container)
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

        self.handle = _SidebarHandle(self, sign=-1)
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
        container = self.main_window.center_container
        start_rect = self.geometry()
        if open_:
            self.show()
            self.raise_()
            end_rect = QRect(max(0, container.width() - self._width), 0, self._width, container.height())
        else:
            end_rect = QRect(container.width(), 0, self._width, container.height())

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
# Left sidebar — workflow switcher (spec 2.1). Replaces the old top tab
# bar entirely: this list is what makes a workflow "active", drag-reorders
# workflows, and is where "Add workflow" now lives. Mirrors OutputsSidebar's
# slide animation with the anchor edge flipped, and its handle reversed.
# ---------------------------------------------------------------------------
class WorkflowSidebar(QWidget):
    MIN_WIDTH = 200
    MAX_WIDTH = 460

    def __init__(self, main_window):
        super().__init__(main_window.center_container)
        self.main_window = main_window
        self.setObjectName("workflowSidebar")
        self._width = max(self.MIN_WIDTH, min(self.MAX_WIDTH, int(
            main_window.config_data.get("workflow_sidebar_width", 260)
        )))
        self._open = False
        self._anim = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(12, 12, 8, 12)
        content_layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("WORKFLOWS")
        title.setObjectName("hint")
        header.addWidget(title)
        header.addStretch(1)
        self.add_btn = QToolButton()
        self.add_btn.setObjectName("iconButton")
        self.add_btn.setText("+")
        self.add_btn.setToolTip("Add workflow")
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.clicked.connect(lambda: main_window._new_workflow_flow())
        header.addWidget(self.add_btn)
        content_layout.addLayout(header)

        self.list = QListWidget()
        self.list.setObjectName("workflowList")
        self.list.setDragDropMode(QListWidget.InternalMove)
        self.list.currentRowChanged.connect(main_window._on_workflow_selected)
        self.list.itemDoubleClicked.connect(main_window._edit_workflow_by_item)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(main_window._on_workflow_context_menu)
        self.list.model().rowsMoved.connect(main_window._on_workflow_rows_moved)
        content_layout.addWidget(self.list, 1)

        self.empty_hint = QLabel('No workflows yet.\nClick "+" above to add a ComfyUI workflow.')
        self.empty_hint.setObjectName("hint")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setWordWrap(True)
        content_layout.addWidget(self.empty_hint)

        layout.addWidget(content, 1)
        self.handle = _SidebarHandle(self, sign=1)
        layout.addWidget(self.handle)

        self.resize(self._width, self.height())
        self.hide()
        self.update_empty_hint()

    def update_empty_hint(self):
        empty = self.list.count() == 0
        self.empty_hint.setVisible(empty)
        self.list.setVisible(not empty)


    # -- width ------------------------------------------------------------
    def set_width(self, width, persist=True):
        width = max(self.MIN_WIDTH, min(self.MAX_WIDTH, int(width)))
        if width == self._width:
            return
        self._width = width
        self.main_window._position_workflow_sidebar()
        if persist:
            self.persist_width()

    def persist_width(self):
        self.main_window.config_data["workflow_sidebar_width"] = self._width
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
        container = self.main_window.center_container
        start_rect = self.geometry()
        if open_:
            self.show()
            self.raise_()
            end_rect = QRect(0, 0, self._width, container.height())
        else:
            end_rect = QRect(-self._width, 0, self._width, container.height())

        anim = QPropertyAnimation(self, b"geometry", self)
        anim.setDuration(180)
        anim.setStartValue(start_rect)
        anim.setEndValue(end_rect)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if not open_:
            anim.finished.connect(self.hide)
        anim.start()
        self._anim = anim
        self.main_window._sync_edge_tab()


# ---------------------------------------------------------------------------
# Small always-visible open/closed indicator tab on the left edge of the
# center content, so the workflow sidebar stays discoverable even when
# collapsed (spec 2.1).
# ---------------------------------------------------------------------------
class _WorkflowEdgeTab(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setObjectName("edgeTab")
        self.setFixedWidth(14)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Workflows")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._lbl = QLabel("\u203a")
        self._lbl.setObjectName("edgeTabChevron")
        self._lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.main_window._toggle_workflow_sidebar()
        super().mousePressEvent(event)

    def set_open_state(self, open_):
        self._lbl.setText("\u2039" if open_ else "\u203a")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        if ICON_FILE.exists():
            self.setWindowIcon(QIcon(str(ICON_FILE)))
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
        self.workflow_states = []
        self.active_workflow = None

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

        self.workflows_btn = QToolButton()
        self.workflows_btn.setObjectName("iconButton")
        self.workflows_btn.setText("\u2630")
        self.workflows_btn.setToolTip("Workflows")
        self.workflows_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.workflows_btn.setCheckable(True)
        self.workflows_btn.clicked.connect(self._toggle_workflow_sidebar)
        tb_lay.addWidget(self.workflows_btn)

        self.sidebar_btn = QToolButton()
        self.sidebar_btn.setObjectName("iconButton")
        self.sidebar_btn.setText("\u25a4")
        self.sidebar_btn.setToolTip("Outputs / Queue")
        self.sidebar_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sidebar_btn.setCheckable(True)
        self.sidebar_btn.clicked.connect(self._toggle_sidebar)
        tb_lay.addWidget(self.sidebar_btn)

        self.settings_btn = QToolButton()
        self.settings_btn.setObjectName("iconButton")
        self.settings_btn.setText("\u2699")
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.clicked.connect(self.open_settings)
        tb_lay.addWidget(self.settings_btn)

        tb_lay.addSpacing(8)

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

        # ── Center: persistent Image Browser + bottom Input Roster ────────
        self.center_container = CenterContainer(self)
        outer.addWidget(self.center_container, 1)

        center_lay = QVBoxLayout(self.center_container)
        center_lay.setContentsMargins(0, 0, 0, 0)
        center_lay.setSpacing(0)

        self.image_browser = ImageBrowser(self)
        self.image_browser.thumbnailClicked.connect(self._on_browser_thumbnail_clicked)
        center_lay.addWidget(self.image_browser, 1)

        self.roster_bar = RosterBar(self)
        center_lay.addWidget(self.roster_bar, 0)

        # ── Left sidebar: workflow switcher (overlay, closed by default) ──
        self.workflow_sidebar = WorkflowSidebar(self)
        self.workflow_edge_tab = _WorkflowEdgeTab(self, self.center_container)
        self.workflow_edge_tab.raise_()

        # ── Right sidebar: Outputs / Queue (overlay, closed by default) ───
        self.outputs_sidebar = OutputsSidebar(self)
        self.outputs_tab = self.outputs_sidebar.outputs_panel
        self.queue_manager.itemFinished.connect(lambda *_: self.outputs_tab.refresh())

        for workflow_data in self.config_data.get("tabs", []):
            self._add_workflow(workflow_data, select=False)
        if self.workflow_states:
            self.workflow_sidebar.list.setCurrentRow(0)

        self._setup_hotkeys()
        self._position_sidebar()
        self._position_workflow_sidebar()


    # -- sidebars ---------------------------------------------------------
    def _toggle_sidebar(self):
        self.outputs_sidebar.toggle()
        self.sidebar_btn.setChecked(self.outputs_sidebar.is_open())

    def _toggle_workflow_sidebar(self):
        self.workflow_sidebar.toggle()

    def _sync_edge_tab(self):
        open_ = self.workflow_sidebar.is_open()
        self.workflow_edge_tab.set_open_state(open_)
        self.workflows_btn.setChecked(open_)

    def _position_sidebar(self):
        """Keep the outputs sidebar filling the center content area
        whenever the window/center area is resized."""
        sidebar = getattr(self, "outputs_sidebar", None)
        if sidebar is None:
            return
        container = self.center_container
        w = sidebar._width
        x = max(0, container.width() - w) if sidebar.is_open() else container.width()
        sidebar.setGeometry(x, 0, w, container.height())

    def _position_workflow_sidebar(self):
        """Same idea, mirrored: keeps the workflow sidebar (and its always-
        visible edge tab) anchored to the left of the center content."""
        sidebar = getattr(self, "workflow_sidebar", None)
        if sidebar is None:
            return
        container = self.center_container
        w = sidebar._width
        x = 0 if sidebar.is_open() else -w
        sidebar.setGeometry(x, 0, w, container.height())
        edge = getattr(self, "workflow_edge_tab", None)
        if edge is not None:
            edge.setGeometry(0, 0, edge.width(), container.height())

    # -- workflow bookkeeping -------------------------------------------
    def rename_workflow(self, state, name):
        if state not in self.workflow_states:
            return
        idx = self.workflow_states.index(state)
        item = self.workflow_sidebar.list.item(idx)
        if item is not None:
            item.setText(name)

    def _on_workflow_selected(self, row):
        if row < 0 or row >= len(self.workflow_states):
            self.active_workflow = None
            self.roster_bar.set_workflow(None)
            return
        self.active_workflow = self.workflow_states[row]
        self.roster_bar.set_workflow(self.active_workflow)

    def _edit_workflow_by_item(self, item):
        row = self.workflow_sidebar.list.row(item)
        if 0 <= row < len(self.workflow_states):
            self._edit_workflow(self.workflow_states[row])

    def _on_workflow_context_menu(self, pos):
        """Right-clicking a workflow in the sidebar opens a per-row menu
        (spec 2.1) with quick Edit / Delete actions, replacing the old tab
        bar's context menu."""
        item = self.workflow_sidebar.list.itemAt(pos)
        if item is None:
            return
        row = self.workflow_sidebar.list.row(item)
        if not (0 <= row < len(self.workflow_states)):
            return
        state = self.workflow_states[row]

        menu = QMenu(self)
        edit_action = menu.addAction("Edit\u2026")
        delete_action = menu.addAction("Delete")
        chosen = menu.exec(self.workflow_sidebar.list.viewport().mapToGlobal(pos))
        if chosen is edit_action:
            self._edit_workflow(state)
        elif chosen is delete_action:
            self._delete_workflow(state)

    def _delete_workflow(self, state):
        if QMessageBox.question(
            self, "Delete workflow", f"Delete workflow '{state.name}'? This cannot be undone."
        ) != QMessageBox.Yes:
            return
        idx = self.workflow_states.index(state)
        self.workflow_sidebar.list.takeItem(idx)
        self.workflow_states.remove(state)
        self.workflow_sidebar.update_empty_hint()
        self.persist_all()

    def open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()

    def _on_workflow_rows_moved(self, parent, start, end, dest_parent, dest_row):
        # Drag-reordering in the sidebar list already moved the visual rows;
        # resync the python list (and persist) to match the new order.
        lw = self.workflow_sidebar.list
        self.workflow_states = [lw.item(i).data(Qt.UserRole) for i in range(lw.count())]
        self.persist_all()

    def _new_workflow_flow(self):
        dlg = WorkflowConfigDialog(self, mode="create")
        dlg.exec()
        if dlg.result == "save":
            self._add_workflow(
                {"workflow_path": dlg.workflow_path, "optional_identifier": dlg.optional_identifier}, select=True,
            )

    def _edit_workflow(self, state):
        dlg = WorkflowConfigDialog(self, mode="edit", tab=state)
        dlg.exec()
        if dlg.result == "delete":
            idx = self.workflow_states.index(state)
            self.workflow_sidebar.list.takeItem(idx)
            self.workflow_states.remove(state)
            self.workflow_sidebar.update_empty_hint()
        elif dlg.result == "save":
            if dlg.workflow_path != state.workflow_path:
                try:
                    state.load_workflow(dlg.workflow_path)
                except Exception as e:
                    QMessageBox.critical(self, "Workflow error", str(e))
                    return
            state.optional_identifier = dlg.optional_identifier
            state.optional_node_id = (
                find_node(state.raw_workflow, dlg.optional_identifier) if dlg.optional_identifier else None
            )
            state.param_values = {k: v for k, (v, _t) in state.editable_params().items()}
            state.slotsRebuilt.emit()
        self.persist_all()

    def _add_workflow(self, data, select=True):
        state = WorkflowState(self, data)
        item = QListWidgetItem(state.name)
        item.setData(Qt.UserRole, state)
        self.workflow_sidebar.list.addItem(item)
        self.workflow_states.append(state)
        self.workflow_sidebar.update_empty_hint()
        if select:
            self.workflow_sidebar.list.setCurrentRow(self.workflow_sidebar.list.count() - 1)
        self.persist_all()
        return state

    # -- Image Browser --------------------------------------------------
    def _on_browser_thumbnail_clicked(self, filepath):
        """Primary selection flow (spec 3.6): a thumbnail click assigns the
        currently armed roster slot, if any."""
        if self.roster_bar.armed_index is None:
            self.roster_bar.status_label.setText(
                "Arm a roster slot below first, then click a thumbnail to assign it."
            )
            return
        self.roster_bar.assign_armed(filepath)

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
        self.config_data["tabs"] = [s.to_dict() for s in self.workflow_states]
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
        bind("Ctrl+N", self._new_workflow_flow)
        bind("Ctrl+Tab", self._hk_next_workflow)
        bind("Ctrl+Shift+Tab", self._hk_prev_workflow)
        bind("Ctrl+O", self._toggle_sidebar)
        bind("Ctrl+Shift+W", self._toggle_workflow_sidebar)
        bind("Ctrl+,", self.open_settings)
        bind("Ctrl+Shift+O", self.outputs_tab._open_folder)
        bind("F5", self.outputs_tab.refresh)

    def _hk_run_current(self):
        if self.active_workflow:
            self.active_workflow.run_now()

    def _hk_queue_current(self):
        if self.active_workflow:
            self.active_workflow.add_to_queue()

    def _hk_next_workflow(self):
        lw = self.workflow_sidebar.list
        n = lw.count()
        if n:
            lw.setCurrentRow((lw.currentRow() + 1) % n)

    def _hk_prev_workflow(self):
        lw = self.workflow_sidebar.list
        n = lw.count()
        if n:
            lw.setCurrentRow((lw.currentRow() - 1) % n)


def apply_style(app: QApplication) -> None:
    app.setStyle("Fusion")

    if ICON_FILE.exists():
        app.setWindowIcon(QIcon(str(ICON_FILE)))

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
