"""vael. sorter — configurable media browsing and confined folder management."""


# ========================================================================
# SETTINGS
# ========================================================================
"""Local, atomic settings. Exported profiles deliberately exclude local paths."""
import copy
import json
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / ".local"
DEFAULTS = {
    "sources": [], "sort_root": "", "pins": [],
    "fields": {"GENRE": "g", "CHAR": "ch", "SCENE": "s"},
    "rules": [{"pattern": "[GENRE]-[CHAR]_[SCENE]", "scope": ""},
              {"pattern": "[CHAR];[GENRE]", "scope": ""}],
    "inherit": True, "ignored": [".git", "venv", ".local", ".sorter-*"],
    "flags": [], "layout": {}, "collision": "rename",
}


def read_settings():
    config = copy.deepcopy(DEFAULTS)
    try:
        saved = json.loads((DATA_DIR / "settings.json").read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            config.update(saved)
    except FileNotFoundError:
        pass
    except (ValueError, OSError) as exc:
        raise RuntimeError(f"Cannot read local settings: {exc}") from exc
    return config


def save_settings(config):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = DATA_DIR / "settings.tmp"
    temp.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, DATA_DIR / "settings.json")


def profile(config):
    return {key: copy.deepcopy(config[key]) for key in
            ("fields", "rules", "inherit", "ignored", "collision")}


# ========================================================================
# NAMING
# ========================================================================
"""User-defined named placeholders; no executable expressions or raw regex."""
import re
import shlex
from pathlib import Path


def compile_pattern(pattern, fields):
    parts = re.split(r"(\[[A-Za-z][A-Za-z0-9_]*\])", pattern.strip())
    names, regex, previous = [], [], False
    if not pattern.strip():
        raise ValueError("A folder pattern cannot be empty.")
    for part in parts:
        if not part:
            continue
        if part.startswith("[") and part.endswith("]"):
            name = part[1:-1]
            if previous:
                raise ValueError("Place a separator between fields.")
            if name not in fields:
                raise ValueError(f"Unknown field: {name}")
            if name in names:
                raise ValueError(f"Repeated field: {name}")
            names.append(name)
            regex.append(f"(?P<{name}>.+?)")
            previous = True
        else:
            if "[" in part or "]" in part:
                raise ValueError("Use named placeholders such as [GENRE].")
            regex.append(re.escape(part))
            previous = False
    return re.compile("".join(regex), re.IGNORECASE), names


def validate_config(config):
    if not isinstance(config.get('fields'), dict) or not isinstance(config.get('rules'), list):
        raise ValueError('Fields must be a mapping and rules must be a list.')
    fields = config["fields"]
    aliases = []
    for name, alias in fields.items():
        if not isinstance(name, str) or not isinstance(alias, str):
            raise ValueError('Field names and aliases must be text.')
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid field name: {name}")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", alias):
            raise ValueError(f"Invalid search alias: {alias}")
        aliases.append(alias.casefold())
    if len(aliases) != len(set(aliases)):
        raise ValueError("Search aliases must be unique.")
    for rule in config["rules"]:
        if not isinstance(rule, dict) or not isinstance(rule.get('pattern'), str) or not isinstance(rule.get('scope', ''), str):
            raise ValueError('Each rule needs a text pattern and an optional scope path.')
        compile_pattern(rule["pattern"], fields)
    if config.get("collision") not in ("rename", "skip", "error"):
        raise ValueError("Choose a valid filename conflict policy.")


class NamingRules:
    def __init__(self, config):
        self.rules = [(compile_pattern(r["pattern"], config["fields"])[0], r.get("scope", ""))
                      for r in config["rules"]]
        self.inherit = config.get("inherit", True)

    def extract(self, folder, root):
        folder, root = Path(folder), Path(root)
        try:
            relative = folder.relative_to(root)
        except ValueError:
            return {}
        folders = [root]
        for part in relative.parts:
            folders.append(folders[-1] / part)
        if not self.inherit:
            folders = folders[-1:]
        result = {}
        for candidate in folders:
            for regex, scope in self.rules:
                if scope and not candidate.is_relative_to(Path(scope)):
                    continue
                match = regex.fullmatch(candidate.name)
                if match:
                    result.update({k: v.strip() for k, v in match.groupdict().items()})
                    break
        return result


def parse_query(text, fields):
    """Support g:Pokemon, g:\"two words\", and the user's Pokemon g syntax."""
    aliases = {alias.casefold(): name for name, alias in fields.items()}
    try:
        words = shlex.split(text, posix=True)
    except ValueError as exc:
        raise ValueError("Close the quotation marks in your search.") from exc
    terms, i = [], 0
    while i < len(words):
        word = words[i]
        prefix, sep, value = word.partition(":")
        if sep and prefix.casefold() in aliases:
            if not value:
                raise ValueError(f"Enter a value after {prefix}:")
            terms.append((aliases[prefix.casefold()], value.casefold()))
        elif i + 1 < len(words) and words[i + 1].casefold() in aliases:
            terms.append((aliases[words[i + 1].casefold()], word.casefold()))
            i += 1
        else:
            terms.append((None, word.casefold()))
        i += 1
    return terms


def matches(entry, terms):
    general = " ".join([entry.name, str(entry.path.parent), *entry.fields.values()]).casefold()
    return all(value in (entry.fields.get(field, "").casefold() if field else general)
               for field, value in terms)


# ========================================================================
# CATALOG
# ========================================================================
"""Cancellable filesystem discovery with a disposable SQLite metadata index."""
import fnmatch
import json
import os
import sqlite3
import time
from contextlib import closing
import hashlib
from dataclasses import dataclass
from pathlib import Path


IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".avif"}
VIDEOS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".wmv", ".mpeg", ".mpg"}


@dataclass
class Entry:
    path: Path
    is_dir: bool
    size: int
    mtime: float
    fields: dict

    @property
    def name(self):
        return self.path.name

    @property
    def kind(self):
        if self.is_dir:
            return "Folder"
        ext = self.path.suffix.lower()
        return "GIF" if ext == ".gif" else "Video" if ext in VIDEOS else "Image" if ext in IMAGES else "Other"


def is_link(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def catalog_key(roots, config, recursive):
    value = [list(map(str, roots)), recursive, config['fields'], config['rules'], config['inherit'], config['ignored'], config['sources'], config['sort_root']]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def cached_scan(database, roots, config, recursive):
    if not Path(database).exists():
        return []
    try:
        with closing(sqlite3.connect(database, timeout=20)) as db:
            row = db.execute('SELECT payload FROM snapshots WHERE scope = ?', (catalog_key(roots, config, recursive),)).fetchone()
            if row:
                return [Entry(Path(p), directory, size, mtime, fields) for p, directory, size, mtime, fields in json.loads(row[0])]
    except (sqlite3.Error, ValueError, TypeError):
        pass
    return []


def scan(roots, config, recursive, cancel, database=None):
    naming = NamingRules(config)
    result, warnings, seen = [], [], set()
    for root_text in roots:
        root = Path(root_text).absolute()
        candidates = [normalized(p) for p in config.get('sources', []) + [config.get('sort_root', '')] if p and root.is_relative_to(normalized(p))]
        metadata_root = max(candidates, key=lambda p: len(p.parts)) if candidates else root
        if not root.is_dir():
            warnings.append(f"Unavailable folder: {root}")
            continue
        stack = [root]
        while stack and not cancel.is_set():
            folder = stack.pop()
            try:
                with os.scandir(folder) as listing:
                    for item in listing:
                        if cancel.is_set():
                            return [], []
                        path = Path(item.path)
                        if any(fnmatch.fnmatch(item.name.casefold(), p.casefold())
                               for p in config.get("ignored", [])):
                            continue
                        try:
                            if is_link(path):
                                continue
                            directory = item.is_dir(follow_symlinks=False)
                            if not directory and not item.is_file(follow_symlinks=False):
                                continue
                            if recursive and directory:
                                stack.append(path)
                            key = os.path.normcase(str(path))
                            if key in seen:
                                continue
                            seen.add(key)
                            stat = item.stat(follow_symlinks=False)
                            result.append(Entry(path, directory, stat.st_size if not directory else 0,
                                                stat.st_mtime, naming.extract(path if directory else folder, metadata_root)))
                        except OSError as exc:
                            warnings.append(f"{path}: {exc}")
            except OSError as exc:
                warnings.append(f"{folder}: {exc}")
    if database and not cancel.is_set():
        database = Path(database)
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(database, timeout=20)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS snapshots (scope TEXT PRIMARY KEY, payload TEXT, seen REAL)')
            payload = json.dumps([(str(e.path), e.is_dir, e.size, e.mtime, e.fields) for e in result])
            db.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?,?)', (catalog_key(roots, config, recursive), payload, time.time()))
            db.execute('DELETE FROM snapshots WHERE scope NOT IN (SELECT scope FROM snapshots ORDER BY seen DESC LIMIT 24)')
    return result, warnings


# ========================================================================
# OPERATIONS
# ========================================================================
"""All mutations pass through one root-confined service, never UI-only checks."""
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path



def normalized(path):
    return Path(os.path.abspath(path))


def signature(path):
    """Conservative undo guard: include every descendant and file timestamp."""
    path = Path(path)
    items = [path]
    if path.is_dir():
        for folder, dirs, files in os.walk(path, followlinks=False):
            items.extend(Path(folder) / name for name in dirs + files)
    return tuple((str(p.relative_to(path)), p.lstat().st_size, p.lstat().st_mtime_ns)
                 for p in sorted(items) if p.exists() or p.is_symlink())


@dataclass
class UndoRecord:
    action: str
    source: Path
    target: Path
    stamp: tuple


class FileService:
    def __init__(self, root):
        self.root = normalized(root) if root else None
        self.undo_stack = []

    def inside(self, path, allow_root=False):
        if self.root is None:
            return False
        path = normalized(path)
        try:
            # Reject links in every ancestor, including ancestors of the root.
            for component in [path, *path.parents]:
                if is_link(component):
                    return False
            return (path != self.root or allow_root) and path.resolve().is_relative_to(self.root.resolve())
        except (OSError, RuntimeError):
            return False

    def require(self, path, allow_root=False):
        if not self.inside(path, allow_root):
            raise ValueError("Changes are allowed only inside the configured sorting folder. Links and the sorting root itself cannot be changed.")
        return normalized(path)

    @staticmethod
    def valid_name(name):
        if (not name or name in (".", "..") or name != name.strip() or name.endswith(".")
                or any(c in name for c in '<>:"/\\|?*') or any(ord(c) < 32 for c in name)
                or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}):
            raise ValueError(f"Invalid filename: {name!r}")
        return name

    @staticmethod
    def check_source(source):
        for parent in [source, *source.parents]:
            if is_link(parent):
                raise ValueError(f"Linked paths cannot be transferred: {source}")
        if not source.exists():
            raise FileNotFoundError(str(source))
        if source.is_dir():
            for folder, dirs, files in os.walk(source, followlinks=False):
                for name in dirs + files:
                    child = Path(folder) / name
                    if is_link(child) or (not child.is_dir() and not child.is_file()):
                        raise ValueError(f"Folder contains a link or unsupported file: {child}")
        elif not source.is_file():
            raise ValueError(f"Unsupported file: {source}")

    def available(self, target, policy):
        if not target.exists() and not target.is_symlink():
            return target
        if policy == "skip":
            return None
        if policy != "rename":
            raise FileExistsError(f"Already exists: {target}")
        stem, suffix = (target.name, "") if target.is_dir() else (target.stem, target.suffix)
        i = 2
        while True:
            candidate = target.with_name(f"{stem} ({i}){suffix}")
            if not candidate.exists() and not candidate.is_symlink():
                return candidate
            i += 1

    def transfer(self, source, destination, move=False, policy="rename"):
        source = normalized(source)
        destination = self.require(destination, allow_root=True)
        if not destination.is_dir():
            raise ValueError("The destination must be an existing folder.")
        self.check_source(source)
        if move:
            self.require(source)
        if source.is_dir() and destination.resolve().is_relative_to(source.resolve()):
            raise ValueError("A folder cannot be placed inside itself.")
        if move and source.parent == destination:
            return None
        target = self.available(destination / source.name, policy)
        if target is None:
            return None
        self.require(target)
        if move:
            # Both locations are in the same configured tree. A rename avoids
            # deleting an original that could change while a copy is running.
            source.rename(target)
            self.undo_stack.append(UndoRecord("move", source, target, signature(target)))
            return target
        # Copy to a private staging path; failed copies never become final files.
        stage = destination / f".sorter-{uuid.uuid4().hex}.tmp"
        try:
            if source.is_dir():
                shutil.copytree(source, stage, symlinks=True)
            else:
                shutil.copy2(source, stage, follow_symlinks=False)
            self.check_source(stage)
            self.require(target)
            if target.exists():
                raise FileExistsError(str(target))
            stage.rename(target)
            self.undo_stack.append(UndoRecord("copy", source, target, signature(target)))
            return target
        finally:
            if stage.exists():
                self.require(stage)
                if stage.is_dir():
                    shutil.rmtree(stage)
                else:
                    stage.unlink()

    def mkdir(self, parent, name):
        parent = self.require(parent, allow_root=True)
        target = self.require(parent / self.valid_name(name))
        target.mkdir()
        self.undo_stack.append(UndoRecord("copy", target, target, signature(target)))
        return target

    def rename(self, source, name):
        source = self.require(source)
        self.check_source(source)
        target = self.require(source.with_name(self.valid_name(name)))
        if source == target:
            return None
        if target.exists():
            raise FileExistsError(str(target))
        source.rename(target)
        self.undo_stack.append(UndoRecord("move", source, target, signature(target)))
        return target

    def trash(self, source):
        source = self.require(source)
        self.check_source(source)
        from PySide6.QtCore import QFile
        result = QFile.moveToTrash(str(source))
        if not result[0]:
            raise OSError(f"Could not move to the system trash: {source}")

    def undo(self):
        if not self.undo_stack:
            raise ValueError("Nothing to undo in this session.")
        record = self.undo_stack[-1]
        target = self.require(record.target)
        self.check_source(target)
        if signature(target) != record.stamp:
            raise ValueError("This item changed after the operation. Undo was stopped to preserve those changes.")
        if record.action == "move":
            source = self.require(record.source)
            if source.exists():
                raise FileExistsError(f"Original location is occupied: {source}")
            target.rename(source)
        elif target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        self.undo_stack.pop()
        return record


def top_level_selection(paths):
    """Avoid moving both a selected folder and its selected descendants."""
    unique = sorted(set(normalized(p) for p in paths), key=lambda p: (len(p.parts), str(p)))
    result = []
    for path in unique:
        if not any(path.is_relative_to(parent) for parent in result):
            result.append(path)
    return result


# ========================================================================
# BACKGROUND TASKS AND MEDIA MODELS
# ========================================================================
import sys
import threading
from collections import OrderedDict
from datetime import datetime

from PySide6.QtCore import (
    Qt, QObject, Signal, QRunnable, QThreadPool, QTimer, QUrl, QSize, QRect,
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel, QMimeData, QByteArray,
    QFile, QIODevice,
)
from PySide6.QtGui import (
    QColor, QImageReader, QPixmap, QIcon, QPainter, QPen, QDrag, QKeySequence,
    QAction, QDesktopServices, QMovie, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox, QSplitter, QStackedWidget,
    QListView, QTableView, QAbstractItemView, QStyledItemDelegate, QStyle,
    QMenu, QMessageBox, QFileDialog, QInputDialog, QSlider, QProgressBar,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QListWidget,
    QListWidgetItem, QPlainTextEdit, QFormLayout, QDialogButtonBox, QSpinBox,
    QScrollArea, QGroupBox, QCompleter,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget


class TaskSignals(QObject):
    result = Signal(object)
    partial = Signal(object)
    error = Signal(str)
    progress = Signal(int, str)
    done = Signal()


class Task(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = TaskSignals()
        self.cancel = threading.Event()

    def run(self):
        try:
            self.signals.result.emit(self.function(self))
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.done.emit()


class Jobs(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.tasks = set()

    def start(self, function, result, error=None, progress=None, partial=None):
        task = Task(function)
        self.tasks.add(task)
        task.signals.result.connect(result)
        if error:
            task.signals.error.connect(error)
        if progress:
            task.signals.progress.connect(progress)
        if partial:
            task.signals.partial.connect(partial)
        task.signals.done.connect(lambda: self.tasks.discard(task))
        self.pool.start(task)
        return task


def readable_size(size):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024 or unit == 'TB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024


class MediaModel(QAbstractTableModel):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.entries = []
        self.cache = OrderedDict()
        self.pending = set()
        self.generation = 0
        self.headers = ['Name', 'Type', 'Size', 'Modified', 'Folder', 'Fields']

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]

    def set_entries(self, entries):
        self.beginResetModel()
        self.entries = entries
        self.generation += 1
        self.pending.clear()
        self.endResetModel()

    def thumbnail(self, entry):
        key = (str(entry.path), entry.mtime, entry.size)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        if entry.kind != 'Image' or key in self.pending or len(self.pending) >= 12:
            return None
        self.pending.add(key)
        generation = self.generation

        def decode(task):
            reader = QImageReader(str(entry.path))
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid():
                size.scale(360, 260, Qt.KeepAspectRatio)
                reader.setScaledSize(size)
            return reader.read()

        def ready(image):
            if generation != self.generation:
                return
            self.pending.discard(key)
            self.cache[key] = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
            while len(self.cache) > 160:
                self.cache.popitem(last=False)
            self.owner.grid.viewport().update()
            self.owner.table.viewport().update()

        self.owner.window.jobs.start(decode, ready, lambda _: self.pending.discard(key))
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.entries):
            return None
        entry = self.entries[index.row()]
        if role == Qt.UserRole:
            return entry
        if role == Qt.ToolTipRole:
            values = ' · '.join(f'{k}: {v}' for k, v in entry.fields.items())
            return f'{entry.path}\n{entry.path.suffix.upper() or "Folder"}  {readable_size(entry.size)}\n{values}'
        if role == Qt.DisplayRole:
            return [entry.name, entry.kind, '' if entry.is_dir else readable_size(entry.size),
                    datetime.fromtimestamp(entry.mtime).strftime('%Y-%m-%d %H:%M'),
                    str(entry.path.parent), ' · '.join(f'{k}: {v}' for k, v in entry.fields.items())][index.column()]
        if role == Qt.DecorationRole and index.column() == 0:
            if str(entry.path) in self.owner.window.config['flags']:
                return self.owner.style().standardIcon(QStyle.SP_DialogApplyButton)
            return self.owner.style().standardIcon(QStyle.SP_DirIcon if entry.is_dir else QStyle.SP_FileIcon)

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemIsDropEnabled
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled


class MediaFilter(QSortFilterProxyModel):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.terms = []

    def filterAcceptsRow(self, row, parent):
        entry = self.sourceModel().entries[row]
        kind = self.browser.kind.currentText()
        if kind != 'All types' and entry.kind != kind:
            return False
        if self.browser.flagged.isChecked() and str(entry.path) not in self.browser.window.config['flags']:
            return False
        ext = self.browser.extension.text().strip().lower().lstrip('.')
        if ext and (entry.is_dir or entry.path.suffix.lower().lstrip('.') not in [v.strip().lstrip('.') for v in ext.split(',')]):
            return False
        return matches(entry, self.terms)

    def lessThan(self, left, right):
        a, b = self.sourceModel().entries[left.row()], self.sourceModel().entries[right.row()]
        if a.is_dir != b.is_dir:
            return a.is_dir
        column = left.column()
        if column == 2:
            return a.size < b.size
        if column == 3:
            return a.mtime < b.mtime
        if column == 4:
            return (str(a.path.parent).casefold(), a.name.casefold()) < (str(b.path.parent).casefold(), b.name.casefold())
        def natural(value):
            return [(0, int(p)) if p.isdigit() else (1, p.casefold()) for p in re.split(r'(\d+)', value)]
        return natural(a.name if column == 0 else a.kind) < natural(b.name if column == 0 else b.kind)


class TileDelegate(QStyledItemDelegate):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser

    def paint(self, painter, option, index):
        entry = index.data(Qt.UserRole)
        if entry is None:
            return
        painter.save()
        box = option.rect.adjusted(5, 5, -5, -5)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor('#a8c7aa' if selected else '#3e5d70' if entry.kind == 'Video' else '#635b83' if entry.kind == 'GIF' else '#303336'), 2 if selected else 1))
        painter.setBrush(QColor('#303b34' if selected else '#24282b' if hovered else '#202326'))
        painter.drawRoundedRect(box, 9, 9)
        image_rect = box.adjusted(9, 8, -9, -52)
        pixmap = self.browser.model.thumbnail(entry) if entry.kind == 'Image' else None
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(image_rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            painter.drawPixmap(image_rect.center().x() - scaled.width() // 2,
                               image_rect.center().y() - scaled.height() // 2, scaled)
        else:
            symbol = QStyle.SP_DirIcon if entry.is_dir else QStyle.SP_MediaPlay if entry.kind in ('GIF', 'Video') else QStyle.SP_FileIcon
            icon = self.browser.style().standardIcon(symbol)
            icon.paint(painter, QRect(image_rect.center().x() - 22, image_rect.center().y() - 22, 44, 44))
        painter.setPen(QColor('#ebeeea'))
        title = painter.fontMetrics().elidedText(entry.name, Qt.ElideMiddle, box.width() - 18)
        painter.drawText(QRect(box.left() + 9, box.bottom() - 46, box.width() - 18, 21), Qt.AlignLeft | Qt.AlignVCenter, title)
        painter.setPen(QColor('#959d97'))
        subtitle = entry.path.parent.name if self.browser.recursive.isChecked() else ('Folder' if entry.is_dir else readable_size(entry.size))
        if hovered and not entry.is_dir:
            subtitle = entry.path.suffix.upper() + '  ·  ' + subtitle
        painter.drawText(QRect(box.left() + 9, box.bottom() - 24, box.width() - 18, 18), Qt.AlignLeft, painter.fontMetrics().elidedText(subtitle, Qt.ElideMiddle, box.width() - 18))
        if str(entry.path) in self.browser.window.config['flags']:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#e2c47c'))
            painter.drawEllipse(box.right() - 16, box.top() + 8, 8, 8)
        painter.restore()

    def sizeHint(self, option, index):
        size = self.browser.tile_size.value()
        return QSize(size, int(size * .86) + 52)


class DragViewMixin:
    def setup_drag(self, browser):
        self.browser = browser
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(browser.editable)
        self.setDropIndicatorShown(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda point: browser.context_menu(self, point))
        self.doubleClicked.connect(browser.open_index)

    def startDrag(self, actions):
        paths = self.browser.selected_paths()
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
        # Always advertise copy to external apps, so an external drop cannot
        # request deletion of a protected original. Internal drops are explicit.
        mime.setData('application/x-vael-sorter', QByteArray(b'1'))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction, Qt.CopyAction)

    def dragEnterEvent(self, event):
        if self.browser.editable and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if self.browser.editable and event.mimeData().hasUrls():
            internal = event.mimeData().hasFormat('application/x-vael-sorter')
            paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
            move = internal and paths and all(self.browser.window.service.inside(p) for p in paths) and not (event.modifiers() & Qt.ControlModifier)
            self.browser.window.statusBar().showMessage('Move into sorting folder · hold Ctrl to copy' if move else 'Copy into sorting folder')
            event.setDropAction(Qt.CopyAction)
            event.accept()

    def dropEvent(self, event):
        if not self.browser.editable:
            return
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        index = self.indexAt(event.position().toPoint())
        entry = index.data(Qt.UserRole) if index.isValid() else None
        destination = entry.path if entry and entry.is_dir else self.browser.current
        internal = event.mimeData().hasFormat('application/x-vael-sorter')
        move = internal and bool(paths) and all(self.browser.window.service.inside(p) for p in paths) and not (event.modifiers() & Qt.ControlModifier)
        self.browser.window.transfer(paths, destination, move)
        event.setDropAction(Qt.CopyAction)
        event.accept()


class TileView(DragViewMixin, QListView):
    pass


class DetailView(DragViewMixin, QTableView):
    pass


# ========================================================================
# INDEPENDENT EXPLORERS
# ========================================================================
class Browser(QWidget):
    def __init__(self, window, editable=False, title='Library'):
        super().__init__()
        self.window, self.editable = window, editable
        self.current = None
        self.history, self.history_pos = [], -1
        self.scan_task = None
        self.scan_serial = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(7)
        bar = QHBoxLayout()
        self.heading = QLabel(title)
        self.heading.setObjectName('sectionTitle')
        bar.addWidget(self.heading)
        for text, callback, tip in [('‹', self.back, 'Back'), ('›', self.forward, 'Forward'), ('↑', self.up, 'Parent folder'), ('↻', self.refresh, 'Refresh files')]:
            button = QPushButton(text)
            button.setFixedWidth(30)
            button.setToolTip(tip)
            button.clicked.connect(callback)
            bar.addWidget(button)
        self.path = QLineEdit()
        self.path.setMinimumWidth(60)
        self.path.setPlaceholderText('Choose folders in Settings' if not editable else 'Choose a sorting folder in Settings')
        self.path.returnPressed.connect(lambda: self.navigate(self.path.text()))
        bar.addWidget(self.path, 1)
        self.mode = QComboBox()
        self.mode.addItems(['Tiles', 'Details'])
        bar.addWidget(self.mode)
        layout.addLayout(bar)
        search_bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setMinimumWidth(70)
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText('Search here…  Pokemon g  or  g:Pokemon ch:Misty')
        search_bar.addWidget(self.search, 1)
        self.kind = QComboBox()
        self.kind.addItems(['All types', 'Image', 'GIF', 'Video', 'Folder', 'Other'])
        search_bar.addWidget(self.kind)
        self.extension = QLineEdit()
        self.extension.setPlaceholderText('png, jpg…')
        self.extension.setMaximumWidth(105)
        self.extension.setToolTip('Filter by filename extensions, separated with commas')
        search_bar.addWidget(self.extension)
        self.recursive = QCheckBox('Subfolders')
        search_bar.addWidget(self.recursive)
        self.flagged = QCheckBox('Flagged')
        self.flagged.setVisible(editable)
        search_bar.addWidget(self.flagged)
        layout.addLayout(search_bar)
        tools = QHBoxLayout()
        self.roots = QComboBox()
        self.roots.setMinimumWidth(150)
        self.roots.setMaximumWidth(310)
        self.roots.activated.connect(self.choose_root)
        tools.addWidget(self.roots)
        self.optional_buttons = []
        if editable:
            for text, callback in [('New folder', lambda: window.new_folders(self)), ('Paste', lambda: window.paste(self)), ('Edit ▾', self.edit_menu), ('Pin folder', lambda: window.pin(self))]:
                button = QPushButton(text)
                button.clicked.connect(callback)
                tools.addWidget(button)
                if text != 'Edit ▾':
                    self.optional_buttons.append(button)
        else:
            button = QPushButton('Add selection to shelf')
            button.clicked.connect(lambda: window.shelf.add_paths(self.selected_paths()))
            tools.addWidget(button)
        tools.addStretch()
        self.order = QComboBox()
        self.order.setMaximumWidth(160)
        self.order.addItems(['Name ↑', 'Name ↓', 'Size ↑', 'Size ↓', 'Date ↑', 'Date ↓', 'Group by folder'])
        tools.addWidget(self.order)
        self.tile_size = QSlider(Qt.Horizontal)
        self.tile_size.setRange(130, 300)
        self.tile_size.setValue(180)
        self.tile_size.setFixedWidth(90)
        self.tile_size.setToolTip('Thumbnail size')
        tools.addWidget(self.tile_size)
        layout.addLayout(tools)
        self.model = MediaModel(self)
        self.proxy = MediaFilter(self)
        self.proxy.setSourceModel(self.model)
        self.grid = TileView()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setMouseTracking(True)
        self.grid.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.grid.setItemDelegate(TileDelegate(self))
        self.grid.setModel(self.proxy)
        self.grid.setup_drag(self)
        self.table = DetailView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 230)
        self.table.setColumnWidth(3, 145)
        self.table.setColumnWidth(4, 260)
        self.table.setSortingEnabled(True)
        self.table.setup_drag(self)
        self.table.setSelectionModel(self.grid.selectionModel())
        self.stack = QStackedWidget()
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.table)
        layout.addWidget(self.stack, 1)
        self.status = QLabel('Configure your folders in Settings to begin.')
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.mode.currentIndexChanged.connect(self.stack.setCurrentIndex)
        self.order.currentIndexChanged.connect(self.sort_rows)
        self.tile_size.valueChanged.connect(lambda _: self.grid.doItemsLayout())
        self.recursive.toggled.connect(self.refresh)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(150)
        self.search_timer.timeout.connect(self.filter_rows)
        self.search.textChanged.connect(lambda _: self.search_timer.start())
        self.extension.textChanged.connect(lambda _: self.search_timer.start())
        self.kind.currentIndexChanged.connect(self.filter_rows)
        self.flagged.toggled.connect(self.filter_rows)
        self.grid.selectionModel().selectionChanged.connect(self.update_count)
        self.install_shortcuts()
        self.sort_rows()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, 'optional_buttons'):
            return
        compact = self.width() < 850
        self.heading.setVisible(not compact)
        for button in self.optional_buttons:
            button.setVisible(not compact)
        self.tile_size.setVisible(not compact)
        self.roots.setMaximumWidth(200 if compact else 310)
        self.roots.setMinimumWidth(110 if compact else 150)
        self.extension.setMaximumWidth(75 if compact else 105)

    def install_shortcuts(self):
        actions = {
            'Ctrl+C': lambda: self.window.copy_selection(self, False),
            'Ctrl+A': lambda: self.active_view().selectAll(),
            'Ctrl+L': lambda: (self.path.setFocus(), self.path.selectAll()),
            'Ctrl+F': self.search.setFocus,
            'F5': self.refresh,
            'Alt+Up': self.up,
        }
        if self.editable:
            actions.update({'Ctrl+X': lambda: self.window.copy_selection(self, True),
                            'Ctrl+V': lambda: self.window.paste(self),
                            'F2': lambda: self.window.rename_selection(self),
                            'Delete': lambda: self.window.trash_selection(self),
                            'F': lambda: self.window.toggle_flags(self),
                            'Ctrl+Shift+N': lambda: self.window.new_folders(self)})
        for key, callback in actions.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda cb=callback: cb() if not isinstance(QApplication.focusWidget(), (QLineEdit, QPlainTextEdit, QSpinBox)) else None)

    def active_view(self):
        return self.stack.currentWidget()

    def selected_paths(self):
        return [index.data(Qt.UserRole).path for index in self.grid.selectionModel().selectedIndexes() if index.column() == 0]

    def selected_entries(self):
        return [index.data(Qt.UserRole) for index in self.grid.selectionModel().selectedIndexes() if index.column() == 0]

    def update_roots(self):
        self.roots.clear()
        if self.editable:
            root = self.window.config['sort_root']
            if root:
                self.roots.addItem('Sorting root', root)
            for path in self.window.config['pins']:
                if self.window.service.inside(path, True):
                    self.roots.addItem('★ ' + Path(path).name, path)
        else:
            self.roots.addItem('All configured sources', '')
            for path in self.window.config['sources']:
                self.roots.addItem(Path(path).name or path, path)

    def choose_root(self, index):
        self.navigate(self.roots.itemData(index) or None)

    def navigate(self, path, remember=True):
        target = normalized(path) if path else None
        if self.editable and (target is None or not self.window.service.inside(target, True)):
            self.window.notice('Choose a folder inside your configured sorting folder.')
            return
        if target and not target.is_dir():
            self.window.notice('This folder is unavailable.')
            return
        self.current = target
        self.model.set_entries([])
        if remember:
            self.history = self.history[:self.history_pos + 1]
            self.history.append(target)
            self.history_pos = len(self.history) - 1
        self.path.setText(str(target) if target else '')
        self.refresh()

    def back(self):
        if self.history_pos > 0:
            self.history_pos -= 1
            self.navigate(self.history[self.history_pos], False)

    def forward(self):
        if self.history_pos + 1 < len(self.history):
            self.history_pos += 1
            self.navigate(self.history[self.history_pos], False)

    def up(self):
        if self.current:
            if self.editable and self.current == self.window.service.root:
                return
            self.navigate(self.current.parent)

    def refresh(self, *_):
        if self.scan_task:
            self.scan_task.cancel.set()
        self.scan_serial += 1
        serial = self.scan_serial
        roots = [str(self.current)] if self.current else ([] if self.editable else self.window.config['sources'])
        if not roots:
            self.model.set_entries([])
            self.status.setText('Choose a sorting folder in Settings.' if self.editable else 'Add source folders in Settings, or enter a folder path above.')
            return
        self.status.setText('Reading folders… You can continue working.')
        config = copy.deepcopy(self.window.config)
        recursive = self.recursive.isChecked()
        database = DATA_DIR / 'catalog.sqlite'
        use_cache = not self.model.entries

        def discover(task):
            if use_cache:
                cached = cached_scan(database, roots, config, recursive)
                if cached and not task.cancel.is_set():
                    task.signals.partial.emit(cached)
            return scan(roots, config, recursive, task.cancel, database)

        def cached_loaded(entries):
            if serial == self.scan_serial:
                self.model.set_entries(entries)
                self.filter_rows()
                self.status.setText(f'{len(entries)} cached items · checking for changes…')

        def loaded(result):
            if serial != self.scan_serial:
                return
            entries, errors = result
            self.model.set_entries(entries)
            self.filter_rows()
            if errors:
                self.status.setText(f'{len(entries)} items · {len(errors)} folder/file warnings (hover for details)')
                self.status.setToolTip('\n'.join(errors[:30]))
            else:
                self.status.setToolTip('')
            aliases = config['fields']
            suggestions = sorted({f'{aliases[k]}:"{v}"' for e in entries for k, v in e.fields.items() if k in aliases})
            completer = QCompleter(suggestions[:3000], self.search)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            self.search.setCompleter(completer)

        self.scan_task = self.window.jobs.start(
            discover, loaded,
            lambda error: self.status.setText(error) if serial == self.scan_serial else None,
            partial=cached_loaded)

    def filter_rows(self, *_):
        try:
            self.proxy.terms = parse_query(self.search.text(), self.window.config['fields'])
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.proxy.invalidate()
        self.update_count()

    def sort_rows(self, *_):
        choice = self.order.currentIndex()
        column = [0, 0, 2, 2, 3, 3, 4][choice]
        self.proxy.sort(column, Qt.DescendingOrder if choice in (1, 3, 5) else Qt.AscendingOrder)

    def update_count(self, *_):
        count = len(self.selected_paths())
        terms = ' · '.join(f'{field or "Any"}: {value}' for field, value in self.proxy.terms)
        self.status.setText(f'{self.proxy.rowCount():,} / {len(self.model.entries):,} items  ·  {count} selected' + ('  ·  ' + terms if terms else ''))

    def open_index(self, index):
        entry = index.data(Qt.UserRole)
        if entry:
            if entry.is_dir:
                self.navigate(entry.path)
            else:
                entries = [self.proxy.index(row, 0).data(Qt.UserRole) for row in range(self.proxy.rowCount())]
                self.window.preview(entry.path, [e.path for e in entries if e.kind in ('Image', 'GIF', 'Video')])

    def edit_menu(self):
        self.context_menu(self.active_view(), self.active_view().viewport().rect().topLeft())

    def context_menu(self, view, point):
        self.window.active_browser = self
        menu = QMenu(self)
        selected = self.selected_paths()
        if selected:
            menu.addAction('Open / preview', lambda: self.open_index(next(i for i in self.grid.selectionModel().selectedIndexes() if i.column() == 0)))
            menu.addAction('Add to session shelf', lambda: self.window.shelf.add_paths(selected))
            menu.addAction('Copy', lambda: self.window.copy_selection(self, False))
            menu.addAction('Copy to folder…', lambda: self.window.transfer_dialog(self, False))
        if self.editable:
            menu.addSeparator()
            if selected:
                menu.addAction('Cut', lambda: self.window.copy_selection(self, True))
                menu.addAction('Move to folder…', lambda: self.window.transfer_dialog(self, True))
                menu.addAction('Move into new folder…', lambda: self.window.move_to_new(self))
                menu.addAction('Duplicate', lambda: self.window.duplicate(self))
                menu.addAction('Rename…', lambda: self.window.rename_selection(self))
                menu.addAction('Batch rename…', lambda: self.window.batch_rename(self))
                menu.addAction('Toggle flag', lambda: self.window.toggle_flags(self))
                menu.addAction('Move to trash…', lambda: self.window.trash_selection(self))
            menu.addAction('Paste', lambda: self.window.paste(self))
            menu.addAction('New folders…', lambda: self.window.new_folders(self))
            menu.addAction('Undo last file operation', self.window.undo)
            menu.addAction('Pin / unpin current folder', lambda: self.window.pin(self))
        menu.addSeparator()
        menu.addAction('Select all', view.selectAll)
        menu.addAction('Invert selection', self.invert_selection)
        if selected:
            menu.addAction('Information…', lambda: self.window.information(selected))
            menu.addAction('Copy paths', lambda: QApplication.clipboard().setText('\n'.join(map(str, selected))))
            menu.addAction('Open containing folder externally', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(selected[0].parent))))
        menu.exec(view.viewport().mapToGlobal(point))

    def invert_selection(self):
        selection = self.grid.selectionModel()
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        if self.proxy.rowCount():
            selection.select(QItemSelection(self.proxy.index(0, 0), self.proxy.index(self.proxy.rowCount() - 1, 5)), QItemSelectionModel.Toggle | QItemSelectionModel.Rows)

    def state(self):
        return {'path': str(self.current) if self.current else '', 'mode': self.mode.currentIndex(),
                'size': self.tile_size.value(), 'recursive': self.recursive.isChecked(),
                'order': self.order.currentIndex(), 'header': bytes(self.table.horizontalHeader().saveState().toBase64()).decode()}

    def restore(self, state):
        self.mode.setCurrentIndex(state.get('mode', 0))
        self.tile_size.setValue(state.get('size', 180))
        self.recursive.blockSignals(True)
        self.recursive.setChecked(state.get('recursive', not self.editable))
        self.recursive.blockSignals(False)
        self.order.setCurrentIndex(state.get('order', 0))
        if state.get('header'):
            self.table.horizontalHeader().restoreState(QByteArray.fromBase64(state['header'].encode()))
        path = state.get('path')
        if self.editable:
            path = path if path and self.window.service.inside(path, True) and Path(path).is_dir() else self.window.config['sort_root']
        if path and Path(path).is_dir():
            self.navigate(path)
        else:
            self.refresh()


# ========================================================================
# SESSION SHELF AND MEDIA VIEWER
# ========================================================================
class ShelfList(QListWidget):
    def __init__(self, shelf):
        super().__init__()
        self.shelf = shelf
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(False)
        self.setSpacing(5)
        self.setMinimumHeight(65)
        self.setMaximumHeight(115)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.shelf.add_paths([Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()])
        event.setDropAction(Qt.CopyAction)
        event.accept()

    def startDrag(self, actions):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(item.data(Qt.UserRole)) for item in self.selectedItems()])
        # Shelf drops always copy, even if references happen to be in sorting.
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


class Shelf(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        row = QHBoxLayout()
        self.toggle = QPushButton('▾ Session shelf · 0')
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        row.addWidget(self.toggle)
        text = QLabel('References only · cleared when you close sorter')
        text.setObjectName('muted')
        row.addWidget(text)
        row.addStretch()
        for name, callback in [('Copy to target', self.copy_to_target), ('Remove selected', self.remove_selected), ('Clear', self.clear)]:
            button = QPushButton(name)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.list = ShelfList(self)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.menu)
        self.list.itemDoubleClicked.connect(lambda item: window.preview(Path(item.data(Qt.UserRole))))
        layout.addWidget(self.list)
        self.toggle.toggled.connect(self.list.setVisible)

    def add_paths(self, paths):
        existing = {self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())}
        for path in paths:
            path = normalized(path)
            if str(path) in existing:
                continue
            item = QListWidgetItem(path.name)
            item.setData(Qt.UserRole, str(path))
            item.setToolTip(str(path))
            item.setIcon(self.style().standardIcon(QStyle.SP_DirIcon if path.is_dir() else QStyle.SP_FileIcon))
            self.list.addItem(item)
            existing.add(str(path))
        self.update_count()

    def update_count(self):
        self.toggle.setText(f'▾ Session shelf · {self.list.count()}')

    def remove_selected(self):
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self.update_count()

    def clear(self):
        self.list.clear()
        self.update_count()

    def copy_to_target(self):
        items = self.list.selectedItems() or [self.list.item(i) for i in range(self.list.count())]
        self.window.transfer([Path(i.data(Qt.UserRole)) for i in items], self.window.target_browser().current, False)

    def menu(self, point):
        menu = QMenu(self)
        menu.addAction('Copy selection (or all) to current target', self.copy_to_target)
        menu.addAction('Remove selected references', self.remove_selected)
        menu.addAction('Clear shelf', self.clear)
        menu.exec(self.list.mapToGlobal(point))


class Preview(QDialog):
    def __init__(self, path, paths, parent):
        super().__init__(parent)
        self.setWindowTitle('Media preview')
        self.resize(1040, 800)
        self.paths = paths if path in paths else [path]
        self.position = self.paths.index(path)
        self.pixmap = None
        self.movie = None
        self.gif_file = None
        self.scale = 0
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(.5)
        self.player.setAudioOutput(self.audio)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        for label, callback in [('‹ Previous', lambda: self.step(-1)), ('Next ›', lambda: self.step(1)), ('Fit', self.fit), ('100%', self.actual), ('−', lambda: self.zoom(.8)), ('+', lambda: self.zoom(1.25))]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            top.addWidget(button)
        top.addStretch()
        button = QPushButton('Add to shelf')
        button.clicked.connect(lambda: parent.shelf.add_paths([self.paths[self.position]]))
        top.addWidget(button)
        layout.addLayout(top)
        self.stack = QStackedWidget()
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.image)
        self.video = QVideoWidget()
        self.player.setVideoOutput(self.video)
        self.stack.addWidget(self.scroll)
        self.stack.addWidget(self.video)
        layout.addWidget(self.stack, 1)
        self.controls = QWidget()
        controls = QHBoxLayout(self.controls)
        self.play = QPushButton('Play / pause')
        self.play.clicked.connect(self.toggle_play)
        controls.addWidget(self.play)
        self.timeline = QSlider(Qt.Horizontal)
        self.timeline.sliderMoved.connect(self.player.setPosition)
        controls.addWidget(self.timeline, 1)
        self.time = QLabel('0:00 / 0:00')
        controls.addWidget(self.time)
        volume = QSlider(Qt.Horizontal)
        volume.setRange(0, 100)
        volume.setValue(50)
        volume.setFixedWidth(100)
        volume.setToolTip('Volume')
        volume.valueChanged.connect(lambda value: self.audio.setVolume(value / 100))
        controls.addWidget(volume)
        layout.addWidget(self.controls)
        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        bottom = QHBoxLayout()
        self.error = QLabel()
        self.error.setWordWrap(True)
        bottom.addWidget(self.error, 1)
        external = QPushButton('Open externally')
        external.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.paths[self.position]))))
        bottom.addWidget(external)
        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        self.player.positionChanged.connect(self.update_position)
        self.player.durationChanged.connect(lambda value: self.timeline.setRange(0, value))
        self.player.errorOccurred.connect(lambda _, message: self.error.setText('Playback unavailable: ' + message + '. You can open this file externally.'))
        for key, callback in [('Left', lambda: self.step(-1)), ('Right', lambda: self.step(1)), ('Space', self.toggle_play)]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
        self.finished.connect(self.stop)
        self.load()

    def stop(self, *_):
        self.player.stop()
        self.player.setSource(QUrl())
        if self.movie:
            self.movie.stop()
            self.image.setMovie(None)
            self.movie.setFileName('')
            self.movie.deleteLater()
            self.movie = None
        if self.gif_file:
            self.gif_file.close()
            self.gif_file.deleteLater()
            self.gif_file = None

    def done(self, result):
        self.stop()
        super().done(result)

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)

    def load(self):
        self.stop()
        self.error.clear()
        self.image.clear()
        self.pixmap = None
        self.scale = 0
        path = self.paths[self.position]
        self.setWindowTitle(path.name + ' — sorter')
        self.info.setText(f'{self.position + 1} / {len(self.paths)}  ·  {path}')
        ext = path.suffix.lower()
        video = ext in VIDEOS
        self.stack.setCurrentIndex(1 if video else 0)
        self.controls.setVisible(video or ext == '.gif')
        self.timeline.setVisible(video)
        self.time.setVisible(video)
        if video:
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            self.player.play()
        elif ext == '.gif':
            self.gif_file = QFile(str(path), self)
            self.gif_file.open(QIODevice.ReadOnly)
            self.movie = QMovie(self.gif_file, QByteArray(b'gif'), self)
            self.image.setMovie(self.movie)
            if not self.movie.isValid():
                self.error.setText('This GIF could not be decoded. Try opening it externally.')
            self.movie.frameChanged.connect(lambda _: self.render())
            self.movie.start()
        elif ext in IMAGES:
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            image = reader.read()
            if image.isNull():
                self.image.clear()
                self.error.setText('Cannot display this image: ' + reader.errorString())
            else:
                self.pixmap = QPixmap.fromImage(image)
                self.info.setText(self.info.text() + f'  ·  {image.width()} × {image.height()}')
                self.render()
        else:
            self.image.setText('No built-in preview for this file. Use Open externally.')
            self.image.adjustSize()

    def render(self):
        pixmap = self.pixmap or (self.movie.currentPixmap() if self.movie else None)
        if pixmap is None or pixmap.isNull():
            return
        size = pixmap.size()
        if self.scale:
            size = QSize(max(1, int(size.width() * self.scale)), max(1, int(size.height() * self.scale)))
        else:
            size.scale(self.scroll.viewport().size() - QSize(12, 12), Qt.KeepAspectRatio)
        if self.movie:
            self.image.resize(size)
            self.image.setScaledContents(True)
        else:
            self.image.setPixmap(pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.image.resize(size)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'scroll'):
            self.render()

    def fit(self):
        self.scale = 0
        self.render()

    def actual(self):
        self.scale = 1
        self.render()

    def zoom(self, factor):
        self.scale = min(8, max(.05, (self.scale or 1) * factor))
        self.render()

    def step(self, delta):
        self.position = (self.position + delta) % len(self.paths)
        self.load()

    def toggle_play(self):
        if self.movie:
            self.movie.setPaused(self.movie.state() == QMovie.Running)
        elif self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def update_position(self, position):
        if not self.timeline.isSliderDown():
            self.timeline.setValue(position)
        def stamp(value):
            seconds = value // 1000
            return f'{seconds // 60}:{seconds % 60:02d}'
        self.time.setText(f'{stamp(position)} / {stamp(self.player.duration())}')


# ========================================================================
# SETTINGS AND BATCH RENAME DIALOGS
# ========================================================================
class SettingsDialog(QDialog):
    def __init__(self, config, parent):
        super().__init__(parent)
        self.config = copy.deepcopy(config)
        self.setWindowTitle('Settings — sorter')
        self.resize(800, 660)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        folders = QWidget()
        f = QVBoxLayout(folders)
        f.addWidget(QLabel('Sources · browse and copy from any number of folders'))
        self.sources = QListWidget()
        self.sources.addItems(config['sources'])
        f.addWidget(self.sources, 1)
        row = QHBoxLayout()
        add = QPushButton('Add source…')
        add.clicked.connect(self.add_source)
        remove = QPushButton('Remove selected')
        remove.clicked.connect(lambda: [self.sources.takeItem(self.sources.row(item)) for item in self.sources.selectedItems()])
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        f.addLayout(row)
        f.addWidget(QLabel('Sorting folder · the only folder where files may be changed'))
        row = QHBoxLayout()
        self.sort_root = QLineEdit(config['sort_root'])
        row.addWidget(self.sort_root)
        choose = QPushButton('Choose…')
        choose.clicked.connect(self.choose_sort_root)
        row.addWidget(choose)
        f.addLayout(row)
        help_text = QLabel('All subfolders are included. Sources stay read-only unless they are inside this sorting folder. Linked folders are not followed.')
        help_text.setWordWrap(True)
        f.addWidget(help_text)
        self.collision = QComboBox()
        self.collision.addItems(['Create a new name', 'Skip existing files', 'Report a conflict'])
        self.collision.setCurrentIndex(['rename', 'skip', 'error'].index(config['collision']))
        form = QFormLayout()
        form.addRow('When a name already exists', self.collision)
        f.addLayout(form)
        tabs.addTab(folders, 'Folders')
        naming = QWidget()
        n = QVBoxLayout(naming)
        n.addWidget(QLabel('Fields · freely chosen names and unique search aliases'))
        self.fields = QTableWidget(0, 2)
        self.fields.setHorizontalHeaderLabels(['Field name', 'Search alias'])
        self.fields.horizontalHeader().setStretchLastSection(True)
        self.fields.setMaximumHeight(160)
        n.addWidget(self.fields)
        n.addLayout(self.table_tools(self.fields, ['FIELD', 'alias']))
        n.addWidget(QLabel('Folder patterns · first matching rule wins at each folder level'))
        self.rules = QTableWidget(0, 2)
        self.rules.setHorizontalHeaderLabels(['Pattern', 'Scope folder (empty = all sources)'])
        self.rules.horizontalHeader().setStretchLastSection(True)
        self.rules.setColumnWidth(0, 320)
        n.addWidget(self.rules)
        n.addLayout(self.table_tools(self.rules, ['[GENRE]-[CHAR]_[SCENE]', ''], ordered=True))
        self.inherit = QCheckBox('Inherit fields from parent folders; closer folders override matching fields')
        self.inherit.setChecked(config['inherit'])
        n.addWidget(self.inherit)
        preview_row = QHBoxLayout()
        self.sample = QLineEdit('Pokemon-Misty_Beach')
        self.sample.setPlaceholderText('Folder name to test')
        preview_row.addWidget(self.sample)
        test = QPushButton('Test pattern')
        test.clicked.connect(self.test_pattern)
        preview_row.addWidget(test)
        n.addLayout(preview_row)
        self.sample_result = QLabel('Use separators that do not also occur ambiguously inside your field values.')
        self.sample_result.setWordWrap(True)
        n.addWidget(self.sample_result)
        tabs.addTab(naming, 'Naming & search')
        advanced = QWidget()
        a = QVBoxLayout(advanced)
        a.addWidget(QLabel('Ignored names · one wildcard pattern per line, e.g. .git or temp-*'))
        self.ignored = QPlainTextEdit('\n'.join(config['ignored']))
        a.addWidget(self.ignored)
        profile_row = QHBoxLayout()
        export = QPushButton('Export rule profile…')
        export.clicked.connect(self.export_profile)
        import_button = QPushButton('Import rule profile…')
        import_button.clicked.connect(self.import_profile)
        profile_row.addWidget(export)
        profile_row.addWidget(import_button)
        a.addLayout(profile_row)
        note = QLabel('Profiles contain naming rules and preferences. Source folders, sorting folder, pins and flags remain local. Scope paths in rules are omitted when exporting.\n\nSettings and the disposable catalog are stored in sorter/.local/. The session shelf is never saved.')
        note.setWordWrap(True)
        a.addWidget(note)
        tabs.addTab(advanced, 'Profiles & exclusions')
        self.populate_tables(config)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def populate_tables(self, config):
        self.fields.setRowCount(0)
        self.rules.setRowCount(0)
        for name, alias in config['fields'].items():
            self.append_row(self.fields, [name, alias])
        for rule in config['rules']:
            self.append_row(self.rules, [rule['pattern'], rule.get('scope', '')])

    @staticmethod
    def append_row(table, values):
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(value))

    def table_tools(self, table, defaults, ordered=False):
        row = QHBoxLayout()
        add = QPushButton('Add')
        add.clicked.connect(lambda: self.append_row(table, defaults))
        row.addWidget(add)
        remove = QPushButton('Remove')
        remove.clicked.connect(lambda: table.removeRow(table.currentRow()) if table.currentRow() >= 0 else None)
        row.addWidget(remove)
        if ordered:
            for label, delta in [('Move up', -1), ('Move down', 1)]:
                button = QPushButton(label)
                button.clicked.connect(lambda _, d=delta: self.move_rule(d))
                row.addWidget(button)
        row.addStretch()
        return row

    def move_rule(self, delta):
        row, target = self.rules.currentRow(), self.rules.currentRow() + delta
        if row < 0 or not 0 <= target < self.rules.rowCount():
            return
        for column in range(2):
            a, b = self.rules.takeItem(row, column), self.rules.takeItem(target, column)
            self.rules.setItem(row, column, b)
            self.rules.setItem(target, column, a)
        self.rules.selectRow(target)

    def collect(self):
        config = copy.deepcopy(self.config)
        values = lambda table, row, col: table.item(row, col).text().strip() if table.item(row, col) else ''
        pairs = [(values(self.fields, row, 0), values(self.fields, row, 1)) for row in range(self.fields.rowCount())]
        if len({name for name, _ in pairs}) != len(pairs):
            raise ValueError('Field names must be unique.')
        config['fields'] = dict(pairs)
        config['rules'] = [{'pattern': values(self.rules, row, 0), 'scope': values(self.rules, row, 1)} for row in range(self.rules.rowCount())]
        config['sources'] = list(dict.fromkeys(str(normalized(self.sources.item(i).text())) for i in range(self.sources.count())))
        config['sort_root'] = str(normalized(self.sort_root.text())) if self.sort_root.text().strip() else ''
        config['inherit'] = self.inherit.isChecked()
        config['ignored'] = [line.strip() for line in self.ignored.toPlainText().splitlines() if line.strip()]
        config['collision'] = ['rename', 'skip', 'error'][self.collision.currentIndex()]
        validate_config(config)
        return config

    def add_source(self):
        path = QFileDialog.getExistingDirectory(self, 'Add source folder')
        if path:
            self.sources.addItem(str(normalized(path)))

    def choose_sort_root(self):
        path = QFileDialog.getExistingDirectory(self, 'Choose the sorting folder', self.sort_root.text())
        if path:
            self.sort_root.setText(str(normalized(path)))

    def test_pattern(self):
        try:
            config = self.collect()
            for rule in config['rules']:
                regex, _ = compile_pattern(rule['pattern'], config['fields'])
                match = regex.fullmatch(self.sample.text())
                if match:
                    self.sample_result.setText(rule['pattern'] + ' → ' + ' · '.join(f'{k} = {v}' for k, v in match.groupdict().items()) + (' (scope: ' + rule['scope'] + ')' if rule['scope'] else ''))
                    return
            self.sample_result.setText('No rule matches this folder name. It will still be browsable.')
        except ValueError as exc:
            self.sample_result.setText(str(exc))

    def save(self):
        try:
            config = self.collect()
            if config['sort_root']:
                root = Path(config['sort_root'])
                if not root.is_dir() or not FileService(root).inside(root, True):
                    raise ValueError('Choose an existing sorting folder without symbolic links or junctions in its path.')
                if root == Path(root.anchor):
                    raise ValueError('Choose a dedicated sorting folder, not a whole drive.')
            self.config = config
            self.accept()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, 'Settings', str(exc))

    def export_profile(self):
        try:
            data = profile(self.collect())
            for rule in data['rules']:
                rule['scope'] = ''
            path, _ = QFileDialog.getSaveFileName(self, 'Export profile inside sorting folder', self.sort_root.text(), 'JSON (*.json)')
            if path:
                service = self.parent().service
                target = service.require(path)
                with target.open('x', encoding='utf-8') as stream:
                    json.dump(data, stream, indent=2, ensure_ascii=False)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, 'Export profile', str(exc))

    def import_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Import rule profile', '', 'JSON (*.json)')
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            candidate = copy.deepcopy(self.config)
            for key in ('fields', 'rules', 'inherit', 'ignored', 'collision'):
                if key in data:
                    candidate[key] = data[key]
            validate_config(candidate)
            if not isinstance(candidate['ignored'], list) or not all(isinstance(v, str) for v in candidate['ignored']):
                raise ValueError('Ignored names must be a list of text patterns.')
            self.populate_tables(candidate)
            self.inherit.setChecked(candidate['inherit'])
            self.ignored.setPlainText('\n'.join(candidate['ignored']))
            self.collision.setCurrentIndex(['rename', 'skip', 'error'].index(candidate['collision']))
        except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
            QMessageBox.warning(self, 'Import profile', 'Invalid profile: ' + str(exc))


class RenameDialog(QDialog):
    def __init__(self, paths, parent):
        super().__init__(parent)
        self.paths = top_level_selection(paths)
        self.mapping = []
        self.setWindowTitle('Batch rename — preview before applying')
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.find, self.replace, self.prefix, self.suffix = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        for label, field in [('Find in name', self.find), ('Replace with', self.replace), ('Prefix', self.prefix), ('Suffix (before extension)', self.suffix)]:
            form.addRow(label, field)
            field.textChanged.connect(self.update_preview)
        self.number = QCheckBox('Add sequential numbers')
        self.number.toggled.connect(self.update_preview)
        self.start = QSpinBox()
        self.start.setRange(0, 999999)
        self.start.setValue(1)
        self.start.valueChanged.connect(self.update_preview)
        form.addRow(self.number, self.start)
        layout.addLayout(form)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(['Current name', 'New name'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table)
        self.error = QLabel()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.apply = buttons.button(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_preview()

    def update_preview(self, *_):
        if not hasattr(self, 'table'):
            return
        self.mapping = []
        self.table.setRowCount(len(self.paths))
        seen, errors = set(), []
        for i, path in enumerate(self.paths):
            stem, ext = (path.name, '') if path.is_dir() else (path.stem, path.suffix)
            if self.find.text():
                stem = stem.replace(self.find.text(), self.replace.text())
            number = f'_{self.start.value() + i:03}' if self.number.isChecked() else ''
            name = self.prefix.text() + stem + self.suffix.text() + number + ext
            target = path.with_name(name) if name and '/' not in name and '\\' not in name else path
            try:
                FileService.valid_name(name)
                key = os.path.normcase(str(target))
                if key in seen or (target != path and target.exists()):
                    raise ValueError('Duplicate or existing destination: ' + name)
                seen.add(key)
            except ValueError as exc:
                errors.append(str(exc))
            self.table.setItem(i, 0, QTableWidgetItem(path.name))
            self.table.setItem(i, 1, QTableWidgetItem(name))
            self.mapping.append((path, name))
        self.error.setText(errors[0] if errors else 'Extensions are preserved. Existing files are never overwritten.')
        self.apply.setEnabled(not errors)


# ========================================================================
# APPLICATION COORDINATION
# ========================================================================
STYLE = '''
QWidget { background: #181b1e; color: #dfe4de; font-family: "Segoe UI", sans-serif; font-size: 12px; }
QMainWindow, QDialog { background: #181b1e; }
QLabel#brand { font-size: 26px; font-weight: 650; color: #e9f0e6; }
QLabel#sectionTitle { font-size: 14px; font-weight: 600; color: #b7cbb7; padding-right: 8px; }
QLabel#muted { color: #8e9993; }
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox { background: #23282b; border: 1px solid #353d3b; border-radius: 5px; padding: 6px; selection-background-color: #48634e; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: #8aa98d; }
QPushButton { background: #292f30; border: 1px solid #3a4540; border-radius: 5px; padding: 6px 10px; }
QPushButton:hover { background: #354139; border-color: #749079; }
QPushButton:pressed, QPushButton:checked { background: #405647; }
QPushButton:disabled { color: #727a75; background: #202525; }
QListView, QTableView, QListWidget { background: #191d20; alternate-background-color: #202629; border: 1px solid #303836; border-radius: 5px; outline: none; }
QTableView::item, QListWidget::item { padding: 5px; }
QTableView::item:selected, QListWidget::item:selected { background: #3c5143; color: #f1f4ee; }
QHeaderView::section { background: #292f30; border: none; padding: 7px; color: #acbbae; }
QSplitter::handle { background: #303a33; }
QSplitter::handle:hover { background: #657f69; }
QMenu { background: #252b2b; border: 1px solid #46554b; padding: 5px; }
QMenu::item { padding: 7px 24px; }
QMenu::item:selected { background: #3e5345; }
QScrollBar:vertical { width: 11px; background: #1b2021; }
QScrollBar::handle:vertical { background: #48554c; min-height: 24px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: 1px solid #354039; }
QTabBar::tab { background: #272e2c; padding: 8px 16px; }
QTabBar::tab:selected { background: #405247; }
QProgressBar { border: 1px solid #3a4940; border-radius: 4px; text-align: center; }
QProgressBar::chunk { background: #5d8267; }
QToolTip { background: #303b33; color: #eef1e9; border: 1px solid #6a8170; padding: 5px; }
QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid #657567; border-radius: 3px; background: #222a26; }
QCheckBox::indicator:checked { background: #88aa8b; border: 2px solid #c4d8c4; }
'''


class Window(QMainWindow):
    def __init__(self, config=None):
        super().__init__()
        self.config = config if config is not None else read_settings()
        validate_config(self.config)
        self.service = FileService(self.config['sort_root'])
        self.jobs = Jobs(self)
        self.operation_task = None
        self.clip_paths, self.clip_cut = [], False
        self.active_browser = None
        self.previews = []
        self.setWindowTitle('sorter. — vael')
        self.resize(1420, 1000)
        self.setMinimumSize(1000, 720)
        icon = APP_DIR / ('cover.ico' if sys.platform == 'win32' else 'cover.png')
        if not icon.exists():
            icon = APP_DIR / 'cover.png'
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        center = QWidget()
        layout = QVBoxLayout(center)
        layout.setContentsMargins(12, 10, 12, 6)
        layout.setSpacing(5)
        header = QHBoxLayout()
        brand = QLabel('sorter.')
        brand.setObjectName('brand')
        header.addWidget(brand)
        subtitle = QLabel('Browse. Collect. Arrange.')
        subtitle.setObjectName('muted')
        header.addWidget(subtitle)
        header.addStretch()
        self.target_label = QLabel('No sorting folder configured')
        self.target_label.setObjectName('muted')
        header.addWidget(self.target_label)
        settings = QPushButton('Settings')
        settings.clicked.connect(self.settings)
        header.addWidget(settings)
        help_button = QPushButton('Help')
        help_button.clicked.connect(self.help)
        header.addWidget(help_button)
        layout.addLayout(header)
        self.vertical = QSplitter(Qt.Vertical)
        self.library = Browser(self, False, 'Library')
        self.vertical.addWidget(self.library)
        bottom = QWidget()
        b = QVBoxLayout(bottom)
        b.setContentsMargins(0, 0, 0, 0)
        self.shelf = Shelf(self)
        b.addWidget(self.shelf)
        controls = QHBoxLayout()
        label = QLabel('SORTING WORKSPACE')
        label.setObjectName('muted')
        controls.addWidget(label)
        controls.addStretch()
        self.split_toggle = QCheckBox('Two folders side by side')
        controls.addWidget(self.split_toggle)
        self.undo_button = QPushButton('Undo last operation')
        self.undo_button.clicked.connect(self.undo)
        controls.addWidget(self.undo_button)
        b.addLayout(controls)
        self.horizontal = QSplitter(Qt.Horizontal)
        self.sorter = Browser(self, True, 'Sorting folder')
        self.second = Browser(self, True, 'Second folder')
        self.horizontal.addWidget(self.sorter)
        self.horizontal.addWidget(self.second)
        self.second.hide()
        self.split_toggle.toggled.connect(self.second.setVisible)
        b.addWidget(self.horizontal, 1)
        self.vertical.addWidget(bottom)
        self.vertical.setSizes([470, 450])
        layout.addWidget(self.vertical, 1)
        operation_bar = QHBoxLayout()
        self.operation_status = QLabel('Ready')
        self.operation_status.setObjectName('muted')
        operation_bar.addWidget(self.operation_status, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.hide()
        operation_bar.addWidget(self.progress)
        self.cancel_button = QPushButton('Stop after current item')
        self.cancel_button.clicked.connect(lambda: self.operation_task.cancel.set() if self.operation_task else None)
        self.cancel_button.hide()
        operation_bar.addWidget(self.cancel_button)
        layout.addLayout(operation_bar)
        self.setCentralWidget(center)
        self.statusBar().showMessage('Outside sorting: view & copy only. Inside sorting: organize freely.')
        QApplication.instance().focusChanged.connect(self.focus_changed)
        self.browsers = [self.library, self.sorter, self.second]
        for browser in self.browsers:
            browser.update_roots()
        self.restore_layout()
        self.update_target_label()
        shortcut = QShortcut(QKeySequence('Ctrl+Z'), self)
        shortcut.activated.connect(lambda: self.undo() if not isinstance(QApplication.focusWidget(), (QLineEdit, QPlainTextEdit, QSpinBox)) else None)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(5000)
        self.poll_timer.timeout.connect(self.poll_folders)
        self.poll_timer.start()
        self.folder_stamps = {}

    def update_target_label(self):
        root = self.config['sort_root']
        self.target_label.setText('Editable: ' + (Path(root).name or root) if root else 'Choose your folders in Settings to begin')
        self.target_label.setToolTip(root)

    def focus_changed(self, old, new):
        if new:
            for browser in getattr(self, 'browsers', []):
                if browser == new or browser.isAncestorOf(new):
                    self.active_browser = browser

    def target_browser(self):
        return self.active_browser if self.active_browser in (self.sorter, self.second) and self.active_browser.isVisible() else self.sorter

    def notice(self, text):
        QMessageBox.information(self, 'sorter', str(text))

    def settings(self):
        if self.operation_task:
            self.notice('Wait for the current file operation before changing settings.')
            return
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == QDialog.Accepted:
            old_root = self.config['sort_root']
            self.config = dialog.config
            if self.config['sort_root'] != old_root:
                self.service = FileService(self.config['sort_root'])
            try:
                save_settings(self.config)
            except OSError as exc:
                self.notice('Settings could not be saved: ' + str(exc))
            for browser in self.browsers:
                browser.update_roots()
                if browser.editable and self.config['sort_root']:
                    if not browser.current or not self.service.inside(browser.current, True):
                        browser.navigate(self.config['sort_root'])
                    else:
                        browser.refresh()
                elif browser.editable:
                    browser.current = None
                    browser.path.clear()
                    browser.refresh()
                else:
                    browser.refresh()
            self.update_target_label()

    def copy_selection(self, browser, cut):
        paths = browser.selected_paths()
        if not paths:
            return
        if cut and not all(self.service.inside(path) for path in paths):
            self.notice('Only items inside the sorting folder can be cut.')
            return
        self.clip_paths, self.clip_cut = paths, cut
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        # Do not set Windows' external cut marker: deletion is managed here.
        QApplication.clipboard().setMimeData(mime)
        self.statusBar().showMessage(f'{len(paths)} items ready to {"move" if cut else "copy"} inside sorter')

    def paste(self, browser):
        mime = QApplication.clipboard().mimeData()
        paths = [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()] if mime.hasUrls() else []
        move = self.clip_cut and paths == self.clip_paths
        self.transfer(paths, browser.current, move)
        if move:
            self.clip_cut = False

    def run_operations(self, title, work):
        if self.operation_task:
            self.notice('A file operation is already running. You can keep browsing or stop after the current item.')
            return
        if not work:
            return
        self.progress.setRange(0, len(work))
        self.progress.setValue(0)
        self.progress.show()
        self.cancel_button.show()
        self.operation_status.setText(title)
        self.undo_button.setEnabled(False)

        def execute(task):
            results, errors = [], []
            for i, (label, function) in enumerate(work):
                if task.cancel.is_set():
                    break
                try:
                    results.append((label, function()))
                except Exception as exc:
                    errors.append(f'{label}: {exc}')
                task.signals.progress.emit(i + 1, label)
            return results, errors, task.cancel.is_set()

        def finished(result):
            results, errors, cancelled = result
            self.operation_task = None
            self.progress.hide()
            self.cancel_button.hide()
            self.undo_button.setEnabled(True)
            self.operation_status.setText(f'{title}: {len(results)} processed · {len(errors)} errors' + (' · stopped' if cancelled else ''))
            self.reconcile_references(results)
            for browser in self.browsers:
                if browser.current and not browser.current.is_dir():
                    browser.current = self.service.root if browser.editable else browser.current.parent
                    browser.path.setText(str(browser.current) if browser.current else '')
                browser.refresh()
            if errors:
                box = QMessageBox(QMessageBox.Warning, 'Some operations could not be completed', '\n'.join(errors[:5]), parent=self)
                box.setDetailedText('\n'.join(errors))
                box.exec()

        self.operation_task = self.jobs.start(execute, finished,
            lambda error: finished(([], [error], False)),
            lambda value, label: (self.progress.setValue(value), self.operation_status.setText(title + ' · ' + Path(label).name)))

    def reconcile_references(self, results):
        # Moves and renames update flags, pins, shelf entries and browser paths.
        for old_text, value in results:
            if isinstance(value, UndoRecord):
                old, new = value.target, value.source if value.action == 'move' else None
            elif isinstance(value, Path) and not Path(old_text).exists():
                old, new = Path(old_text), value
            else:
                continue
            def mapped(text):
                path = Path(text)
                if path.is_relative_to(old):
                    return str(new / path.relative_to(old)) if new else None
                return text
            for key in ('flags', 'pins'):
                self.config[key] = [mapped(p) for p in self.config[key] if mapped(p)]
            for i in reversed(range(self.shelf.list.count())):
                item = self.shelf.list.item(i)
                text = mapped(item.data(Qt.UserRole))
                if text:
                    item.setData(Qt.UserRole, text)
                    item.setText(Path(text).name)
                    item.setToolTip(text)
                else:
                    self.shelf.list.takeItem(i)
            for browser in self.browsers:
                if browser.current:
                    text = mapped(str(browser.current))
                    browser.current = Path(text) if text else (self.service.root if browser.editable else None)
                    browser.path.setText(str(browser.current) if browser.current else '')
        self.config['flags'] = [p for p in self.config['flags'] if Path(p).exists()]
        self.config['pins'] = [p for p in self.config['pins'] if Path(p).is_dir()]
        for browser in (self.sorter, self.second):
            browser.update_roots()
        self.shelf.update_count()

    def transfer(self, paths, destination, move=False):
        if not destination or not paths:
            self.notice('Select items and open a destination inside the sorting folder first.')
            return
        try:
            self.service.require(destination, True)
        except ValueError as exc:
            self.notice(exc)
            return
        policy = self.config['collision']
        self.run_operations('Move' if move else 'Copy',
            [(str(path), lambda p=path: self.service.transfer(p, destination, move, policy)) for path in top_level_selection(paths)])

    def transfer_dialog(self, browser, move):
        destination = QFileDialog.getExistingDirectory(self, 'Choose destination inside sorting folder', self.config['sort_root'])
        if destination:
            self.transfer(browser.selected_paths(), Path(destination), move)

    def duplicate(self, browser):
        paths = top_level_selection(browser.selected_paths())
        self.run_operations('Duplicate', [(str(p), lambda path=p: self.service.transfer(path, path.parent, False, 'rename')) for p in paths])

    def new_folders(self, browser):
        if not browser.current:
            self.notice('Configure a sorting folder first.')
            return
        text, ok = QInputDialog.getMultiLineText(self, 'New folders', 'One folder name per line:')
        if ok:
            parent = browser.current
            self.run_operations('Create folders', [(name, lambda n=name: self.service.mkdir(parent, n)) for name in text.splitlines() if name.strip()])

    def move_to_new(self, browser):
        paths = top_level_selection(browser.selected_paths())
        if not paths or not browser.current:
            return
        name, ok = QInputDialog.getText(self, 'Move selection into a new folder', 'New folder name:')
        if not ok or not name:
            return
        parent = browser.current
        created = []
        def action():
            for path in paths:
                self.service.require(path)
                if path.is_dir() and parent.is_relative_to(path):
                    raise ValueError('The destination cannot be inside a selected folder.')
            target = self.service.mkdir(parent, name)
            created.append(target)
            return target
        def move_item(path):
            if not created:
                raise ValueError('The new folder could not be created; this item was not moved.')
            return self.service.transfer(path, created[0], True, self.config['collision'])
        try:
            FileService.valid_name(name)
        except ValueError as exc:
            self.notice(exc)
            return
        work = [('Create folder', action)]
        work.extend((str(path), lambda p=path: move_item(p)) for path in paths)
        self.run_operations('Move into new folder', work)

    def rename_selection(self, browser):
        paths = browser.selected_paths()
        if len(paths) > 1:
            self.batch_rename(browser)
        elif paths:
            name, ok = QInputDialog.getText(self, 'Rename', 'New name:', text=paths[0].name)
            if ok:
                self.run_operations('Rename', [(str(paths[0]), lambda: self.service.rename(paths[0], name))])

    def batch_rename(self, browser):
        if not browser.selected_paths():
            return
        dialog = RenameDialog(browser.selected_paths(), self)
        if dialog.exec() == QDialog.Accepted:
            self.run_operations('Batch rename', [(str(path), lambda p=path, n=name: self.service.rename(p, n)) for path, name in dialog.mapping])

    def trash_selection(self, browser):
        paths = top_level_selection(browser.selected_paths())
        if not paths:
            return
        if QMessageBox.question(self, 'Move to system trash', f'Move {len(paths)} selected items to the system trash?\nRestore trashed items using your system trash, not Undo.') != QMessageBox.Yes:
            return
        self.run_operations('Trash', [(str(path), lambda p=path: self.service.trash(p)) for path in paths])

    def undo(self):
        self.run_operations('Undo', [('Last operation', self.service.undo)])

    def toggle_flags(self, browser):
        paths = browser.selected_paths()
        if not paths:
            return
        if not all(self.service.inside(path) for path in paths):
            self.notice('Flags can be changed only for items inside the sorting folder.')
            return
        flags = set(self.config['flags'])
        remove = all(str(path) in flags for path in paths)
        for path in paths:
            flags.discard(str(path)) if remove else flags.add(str(path))
        self.config['flags'] = sorted(flags)
        for item in self.browsers:
            item.filter_rows()
            item.grid.viewport().update()
            item.table.viewport().update()
        self.persist()

    def pin(self, browser):
        if not browser.current:
            return
        text = str(browser.current)
        pins = self.config['pins']
        if text in pins:
            pins.remove(text)
        else:
            pins.append(text)
        for item in (self.sorter, self.second):
            item.update_roots()
        self.persist()

    def information(self, paths):
        dialog = QDialog(self)
        dialog.setWindowTitle('Selection information')
        dialog.resize(650, 440)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        layout.addWidget(text)
        rows = []
        for path in paths:
            try:
                stat = path.stat()
                rows.append(f'{path}\nType: {"Folder" if path.is_dir() else path.suffix or "File"}\nSize: {readable_size(stat.st_size) if path.is_file() else "Folder (contents not summed)"}\nModified: {datetime.fromtimestamp(stat.st_mtime)}\nEditable here: {self.service.inside(path)}\n')
            except OSError as exc:
                rows.append(f'{path}\n{exc}\n')
        text.setPlainText('\n'.join(rows))
        dialog.exec()

    def preview(self, path, paths=None):
        if path.is_dir():
            self.library.navigate(path)
            return
        if not path.exists():
            self.notice('This file is no longer at its original location.')
            return
        dialog = Preview(path, paths or [path], self)
        dialog.exec()
        dialog.deleteLater()

    def help(self):
        path = APP_DIR / 'README.md'
        if path.exists():
            dialog = QDialog(self)
            dialog.setWindowTitle('sorter — README')
            dialog.resize(850, 700)
            layout = QVBoxLayout(dialog)
            from PySide6.QtWidgets import QTextBrowser
            text = QTextBrowser()
            text.setOpenExternalLinks(True)
            text.setMarkdown(path.read_text(encoding='utf-8'))
            layout.addWidget(text)
            dialog.exec()
        else:
            self.notice('README.md is not available next to app.py.')

    def poll_folders(self):
        if self.operation_task or QApplication.activeModalWidget():
            return
        for browser in self.browsers:
            if not browser.isVisible() or not browser.current:
                continue
            try:
                stamp = browser.current.stat().st_mtime_ns
                previous = self.folder_stamps.get(str(browser.current))
                self.folder_stamps[str(browser.current)] = stamp
                if previous is not None and previous != stamp:
                    browser.refresh()
            except OSError:
                pass

    def restore_layout(self):
        state = self.config.get('layout', {})
        if state.get('geometry'):
            self.restoreGeometry(QByteArray.fromBase64(state['geometry'].encode()))
        if state.get('vertical'):
            self.vertical.restoreState(QByteArray.fromBase64(state['vertical'].encode()))
        self.split_toggle.setChecked(state.get('split', False))
        if state.get('horizontal'):
            self.horizontal.restoreState(QByteArray.fromBase64(state['horizontal'].encode()))
        for name, browser in [('library', self.library), ('sorter', self.sorter), ('second', self.second)]:
            browser.restore(state.get(name, {}))

    def persist(self):
        try:
            save_settings(self.config)
        except OSError as exc:
            self.operation_status.setText('Could not save local settings: ' + str(exc))

    def closeEvent(self, event):
        if self.operation_task:
            self.notice('A file operation is still running. Use “Stop after current item” and close once it finishes.')
            event.ignore()
            return
        self.poll_timer.stop()
        for task in list(self.jobs.tasks):
            task.cancel.set()
        self.jobs.pool.waitForDone()
        self.config['layout'] = {
            'geometry': bytes(self.saveGeometry().toBase64()).decode(),
            'vertical': bytes(self.vertical.saveState().toBase64()).decode(),
            'horizontal': bytes(self.horizontal.saveState().toBase64()).decode(),
            'split': self.split_toggle.isChecked(),
            'library': self.library.state(), 'sorter': self.sorter.state(), 'second': self.second.state(),
        }
        self.persist()
        event.accept()


def main():
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('vael.sorter')
    app = QApplication(sys.argv)
    app.setApplicationName('sorter')
    app.setOrganizationName('vael')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    try:
        window = Window()
    except Exception as exc:
        QMessageBox.critical(None, 'sorter could not start', str(exc))
        return 1
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
