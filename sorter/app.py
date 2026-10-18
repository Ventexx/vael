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
from tagging import Tagging
DATA_DIR = APP_DIR / ".local"
DEFAULTS = {
    "sources": [], "source_names": {}, "sort_root": "", "pins": [],
    "fields": {}, "rules": [],
    "inherit": False, "ignored": [],
    "layout": {}, "collision": "rename", "groups": [], "notes": [],
}


def group_for(config, path):
    text = str(path)
    return next((group for group in config.get('groups', []) if text in group['members']), None)


def new_group_color(used):
    # Random hues with a restrained saturation; no duplicate within a folder.
    import random
    import colorsys
    used_hues = [colorsys.rgb_to_hsv(*(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)))[0] for color in used]
    candidates = []
    for step in range(1440):
        hue = step / 1440
        color = '#' + ''.join(f'{round(c * 255):02x}' for c in colorsys.hsv_to_rgb(hue, .48, .72))
        if color not in used:
            distance = min((min(abs(hue - other), 1 - abs(hue - other)) for other in used_hues), default=1)
            candidates.append((distance, color))
    if candidates:
        best = max(distance for distance, color in candidates)
        return random.choice([color for distance, color in candidates if distance >= best * .8])
    # Very large folders can exhaust the hue palette; vary saturation as well.
    while True:
        color = '#' + ''.join(f'{round(c * 255):02x}' for c in colorsys.hsv_to_rgb(random.random(), random.uniform(.35, .65), .72))
        if color not in used:
            return color


def organize_groups(config):
    """Keep groups folder-local after moves, retaining their assigned notes."""
    import uuid
    result, colors, fragments = [], {}, {}
    for group in config.get('groups', []):
        family = group.get('family', group['id'])
        for member in dict.fromkeys(group['members']):
            key = (family, str(Path(member).parent))
            if key not in fragments:
                fragments[key] = dict(group, family=family, members=[], notes=list(group['notes']))
            fragment = fragments[key]
            if member not in fragment['members']:
                fragment['members'].append(member)
            fragment['notes'] = list(dict.fromkeys(fragment['notes'] + group['notes']))
    ids = set()
    for (family, folder), group in fragments.items():
        used = colors.setdefault(folder, set())
        if group['color'] in used:
            group['color'] = new_group_color(used)
        used.add(group['color'])
        if group['id'] in ids:
            group['id'] = uuid.uuid4().hex
        ids.add(group['id'])
        result.append(group)
    config['groups'] = result


def create_group(config, paths):
    import uuid
    paths = list(dict.fromkeys(str(normalized(p)) for p in paths))
    if len(paths) < 2 or len({str(Path(p).parent) for p in paths}) != 1:
        raise ValueError('Select at least two files in the same folder.')
    if any(not Path(p).is_file() for p in paths):
        raise ValueError('Groups contain files, not folders.')
    for group in config.setdefault('groups', []):
        group['members'] = [p for p in group['members'] if p not in paths]
    config['groups'] = [g for g in config['groups'] if g['members']]
    folder = Path(paths[0]).parent
    used = {g['color'] for g in config['groups'] if Path(g['members'][0]).parent == folder}
    group = dict(id=uuid.uuid4().hex, members=paths, color=new_group_color(used), notes=[])
    config['groups'].append(group)
    return group


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
    config.pop('flags', None)  # Retire legacy flag data when loading an existing installation.
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
                               for p in ['.sorter-*', *config.get("ignored", [])]):
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
    backup: Path | None = None


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
        if policy == "overwrite":
            return target
        if policy == "skip":
            return None
        if policy != "rename":
            raise FileExistsError(f"Already exists: {target}")
        stem, suffix = (target.name, "") if target.is_dir() else (target.stem, target.suffix)
        i = 1
        while True:
            candidate = target.with_name(f"{stem}{i}{suffix}")
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
        if target == source:
            return None
        if policy == 'overwrite' and target.is_dir() and source.is_dir():
            for child in list(source.iterdir()):
                self.transfer(child, target, move, policy)
            if move:
                source.rmdir()
                self.undo_stack.append(UndoRecord('rmdir', source, source, ()))
            return target
        backup = None
        if target.exists():
            self.check_source(target)
            backup = self.require(self.root / f'.sorter-{uuid.uuid4().hex}.backup')
            if target.is_dir() != source.is_dir():
                raise ValueError('Cannot overwrite a folder with a file or a file with a folder.')
        if move:
            if backup:
                target.rename(backup)
            try:
                source.rename(target)
            except Exception:
                if backup:
                    backup.rename(target)
                raise
            self.undo_stack.append(UndoRecord('move', source, target, signature(target), backup))
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
                if not backup:
                    raise FileExistsError(str(target))
                target.rename(backup)
            try:
                stage.rename(target)
            except Exception:
                if backup and backup.exists():
                    backup.rename(target)
                raise
            self.undo_stack.append(UndoRecord("copy", source, target, signature(target), backup))
            return target
        finally:
            if stage.exists():
                self.require(stage)
                if stage.is_dir():
                    shutil.rmtree(stage)
                else:
                    stage.unlink()

    def discard_backups(self):
        for record in self.undo_stack:
            if record.backup and record.backup.exists():
                backup = self.require(record.backup)
                if backup.is_file() and not is_link(backup):
                    backup.unlink()
        self.undo_stack.clear()

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
        if record.action == 'rmdir':
            self.require(record.source).mkdir()
            self.undo_stack.pop()
            return record
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
        if record.backup and record.backup.exists():
            self.require(record.backup).rename(target)
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
import math
import queue
import threading
from collections import OrderedDict
from datetime import datetime

from PySide6.QtCore import (
    Qt, QObject, Signal, QRunnable, QThreadPool, QTimer, QUrl, QSize, QRect,
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel, QMimeData, QByteArray,
    QThread, QFile, QIODevice, QPoint, QPointF, QRectF, QEvent,
)
from PySide6.QtGui import (
    QColor, QImage, QImageReader, QPixmap, QIcon, QPainter, QPen, QDrag, QKeySequence,
    QAction, QDesktopServices, QMovie, QShortcut, QPalette, QPainterPath, QGuiApplication,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox, QSplitter, QStackedWidget,
    QListView, QTableView, QAbstractItemView, QStyledItemDelegate, QStyle,
    QMenu, QWidgetAction, QMessageBox, QFileDialog, QInputDialog, QSlider, QProgressBar,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QListWidget,
    QListWidgetItem, QPlainTextEdit, QFormLayout, QDialogButtonBox, QSpinBox,
    QScrollArea, QGroupBox, QCompleter, QToolButton, QFrame, QGridLayout, QSizePolicy,
    QTreeWidget, QTreeWidgetItem, QSplitterHandle, QGraphicsDropShadowEffect, QGraphicsBlurEffect, QGraphicsScene, QGraphicsPixmapItem, QSizeGrip,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from shiboken6 import isValid


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
        if entry.kind not in ('Image', 'GIF') or key in self.pending or len(self.pending) >= 12:
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
            if entry.is_dir:
                return self.owner.folder_icon
            if entry.kind in ('Image', 'GIF'):
                pixmap = self.thumbnail(entry)
                if pixmap and not pixmap.isNull():
                    return QIcon(pixmap)
            if entry.kind == 'Other':
                return QIcon(document_pixmap(entry.path, self.owner.entry_zoom / 100))
            return self.owner.style().standardIcon(QStyle.SP_MediaPlay if entry.kind == 'Video' else QStyle.SP_FileIcon)

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
        ext = self.browser.extension.text().strip().lower().lstrip('.')
        if ext and (entry.is_dir or entry.path.suffix.lower().lstrip('.') not in [v.strip().lstrip('.') for v in ext.split(',')]):
            return False
        return matches(entry, self.terms)

    def lessThan(self, left, right):
        a, b = self.sourceModel().entries[left.row()], self.sourceModel().entries[right.row()]
        if a.is_dir != b.is_dir:
            return a.is_dir
        if self.browser.editable and not a.is_dir:
            ga = group_for(self.browser.window.config, a.path)
            gb = group_for(self.browser.window.config, b.path)
            if bool(ga) != bool(gb):
                return bool(ga)
            if ga and gb:
                if ga['id'] != gb['id']:
                    return ga['id'] < gb['id']
                return (a.name.casefold(), a.name) < (b.name.casefold(), b.name)
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
        mime.setData('application/x-vael-sorter', QByteArray(b'workspace' if self.browser.editable else b'source'))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction, Qt.CopyAction)

    def dragEnterEvent(self, event):
        if self.browser.editable and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if self.browser.editable and event.mimeData().hasUrls():
            internal = bytes(event.mimeData().data('application/x-vael-sorter')) == b'workspace'
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
        internal = bytes(event.mimeData().data('application/x-vael-sorter')) == b'workspace'
        move = internal and bool(paths) and all(self.browser.window.service.inside(p) for p in paths) and not (event.modifiers() & Qt.ControlModifier)
        self.browser.window.transfer(paths, destination, move)
        event.setDropAction(Qt.CopyAction)
        event.accept()




class WorkspaceEntryDelegate(QStyledItemDelegate):
    """Compact, evenly spaced tiles with predictable thumbnail and caption bounds."""

    def sizeHint(self, option, index):
        size = round(96 * self.parent().browser.entry_zoom / 100)
        return QSize(size, size + 32)

    def paint(self, painter, option, index):
        entry = index.data(Qt.UserRole)
        if entry is None:
            return
        browser = self.parent().browser
        size = round(96 * browser.entry_zoom / 100)
        tile = QRect(option.rect).adjusted(0, 6, 0, -6)
        thumbnail = QRect(tile.x(), tile.y(), size, size)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        selected = bool(option.state & QStyle.State_Selected)
        painter.setPen(QPen(QColor('#00d4a0'), 2) if selected else Qt.NoPen)
        painter.setBrush(QColor('#174b42') if selected else QColor('#181818'))
        painter.drawRoundedRect(QRectF(tile).adjusted(1, 1, -1, -1), 6, 6)
        icon = index.data(Qt.DecorationRole)
        if isinstance(icon, QIcon):
            icon_size = round(size * .67) if entry.is_dir else size
            pixmap = icon.pixmap(QSize(icon_size, icon_size))
            if not pixmap.isNull():
                if entry.kind in ('Image', 'GIF'):
                    pixmap = pixmap.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                    pixmap = pixmap.copy((pixmap.width() - size) // 2, (pixmap.height() - size) // 2, size, size)
                painter.save()
                clip = QPainterPath()
                clip.addRoundedRect(QRectF(thumbnail).adjusted(3, 3, -3, -3), 4, 4)
                painter.setClipPath(clip)
                painter.drawPixmap(thumbnail.center().x() - pixmap.width() // 2,
                                   thumbnail.center().y() - pixmap.height() // 2, pixmap)
                painter.restore()
        painter.setPen(QColor('#eeeeee'))
        caption = QRect(tile.x() + 4, tile.y() + size, size - 8, 20)
        painter.drawText(caption, Qt.AlignCenter, option.fontMetrics.elidedText(entry.name, Qt.ElideRight, caption.width()))
        painter.restore()


class WorkspaceGrid(DragViewMixin, QListView):
    def group_bands(self):
        bands = []
        for row in range(self.model().rowCount()):
            index = self.model().index(row, 0)
            rect = self.visualRect(index)
            entry = index.data(Qt.UserRole)
            group = group_for(self.browser.window.config, entry.path)
            if not group:
                continue
            if bands and bands[-1][0]['id'] == group['id'] and bands[-1][1].top() == rect.top():
                bands[-1][1] = bands[-1][1].united(rect)
                bands[-1][2].append(rect.adjusted(0, 6, 0, -6))
            else:
                bands.append([group, QRect(rect), [rect.adjusted(0, 6, 0, -6)]])
        return bands

    def group_at(self, point):
        for group, rect, tiles in self.group_bands():
            if rect.adjusted(-3, 0, 3, 0).contains(point) and not any(tile.contains(point) for tile in tiles):
                return group
        return None

    def paintEvent(self, event):
        super().paintEvent(event)
        from PySide6.QtGui import QRegion
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        for group, rect, tiles in self.group_bands():
            if not rect.intersects(event.rect()):
                continue
            band = rect.adjusted(-3, 0, 3, 0)
            region = QRegion(band)
            for tile in tiles:
                region -= QRegion(tile)
            painter.setClipRegion(region)
            color = QColor(group['color'])
            color.setAlpha(85)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(band).adjusted(0, 1, 0, -1), 7, 7)
        painter.end()


class DetailView(DragViewMixin, QTableView):
    pass


# ========================================================================
# INDEPENDENT EXPLORERS
# ========================================================================


# ========================================================================
# COVER EXPLORER COMPONENTS
# ========================================================================
RECURSION_DEPTH_WARN = 5
FOLDER_COUNT_WARN = 20

IMAGE_FILE_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff",
)

import re as _re
_NATSORT_SPLIT_RE = _re.compile(r"(\d+)")


def _natural_sort_key(path_str):
    """Case-insensitive filename ordering: symbols, numbers, then letters.

    Numeric runs compare by value, so img2 appears before img10.
    """
    name = Path(path_str).name.casefold()
    # Explicit character groups keep punctuation (including '_' and '[')
    # ahead of digits, then letters; numeric runs still sort naturally.
    return [(1, int(part)) if part.isdecimal() else
            (2 if part.isalnum() else 0, part)
            for part in _re.findall(r'\d+|[^\d]', name)]

# 2:3 portrait thumbnail, identical crop-to-fill sizing to vael. indexer.
THUMB_W = 106
THUMB_H = 159

_PIXMAP_CACHE: dict[str, QImage] = {}


def _load_pixmap(path: str) -> QImage:
    """Return a cached thumbnail image, safe to decode on a worker thread."""
    if Path(path).suffix.lower() not in IMAGES:
        return QImage()
    cached = _PIXMAP_CACHE.get(path)
    if cached is None:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid():
            size.scale((THUMB_W * 2), (THUMB_H * 2), Qt.KeepAspectRatioByExpanding)
            reader.setScaledSize(size)
        pix = reader.read()
        if not pix.isNull():
            scaled = pix.scaled(
                (THUMB_W * 2), (THUMB_H * 2),
                Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation,
            )
            x = (scaled.width() - (THUMB_W * 2)) // 2
            y = (scaled.height() - (THUMB_H * 2)) // 2
            pix = scaled.copy(x, y, (THUMB_W * 2), (THUMB_H * 2))
        _PIXMAP_CACHE[path] = pix
        return pix
    return cached


class PixmapWorker(QThread):
    """Loads and scales thumbnail pixmaps off the main thread, one shared
    long-lived instance for the whole app session (mirrors vael. indexer)."""

    pixmap_ready = Signal(object, QImage)   # (card, pixmap)

    def __init__(self):
        super().__init__()
        self._queue = queue.Queue()
        self._stop = False

    def submit(self, card, path):
        self._queue.put((card, path))

    def stop(self):
        """Ask the loop to exit at its next queue-poll and wake it up
        immediately with a no-op sentinel so shutdown doesn't wait out the
        full poll timeout. Called from MainWindow.closeEvent."""
        self._stop = True
        self._queue.put((None, None))

    def run(self):
        while not self._stop:
            try:
                card, path = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if card is None:
                    continue
            except RuntimeError:
                continue
            try:
                self.pixmap_ready.emit(card, _load_pixmap(path))
            except (RuntimeError, OSError):
                pass


PIXMAP_WORKER = PixmapWorker()



# ---------------------------------------------------------------------------
# Rough-scan cache — a session-lifetime dict of folder_path -> (files,
# subdirs), shared by every FolderSection. Normally a section fills its own
# entry the moment it's expanded (see FolderSection.ensure_rough_pass). The
# "Load Selected Folders" bulk path (BulkFolderLoadManager, below) walks
# whole trees ahead of time and fills this cache for every folder it visits
# — together with pre-decoding every thumbnail into _PIXMAP_CACHE — so that
# actually expanding any of those folders afterwards is instant: no rescan,
# no per-thumbnail decode wait, because everything is already warm.
# ---------------------------------------------------------------------------
_ROUGH_SCAN_CACHE: dict[str, tuple[list, list]] = {}

# Bulk loading uses its own small, bounded thread pools, kept separate from
# ImageBrowser.thread_pool (used for single-folder interactive expand/scan)
# so that a huge "Load Selected Folders" run can't starve or stall ordinary
# click-to-expand browsing while it works through the backlog. One pool
# walks directories (I/O bound, cheap), the other decodes+scales images
# (CPU/I-O bound, the expensive part) — both capped at a small thread count
# so a "giant total amount of images" run degrades to "takes a while in the
# background" instead of spawning thousands of concurrent threads.
_BULK_SCAN_POOL = QThreadPool()
_BULK_SCAN_POOL.setMaxThreadCount(4)

_BULK_PIXMAP_POOL = QThreadPool()
_BULK_PIXMAP_POOL.setMaxThreadCount(max(2, min(6, os.cpu_count() or 4)))

# Images per background decode task. Batching (rather than one QRunnable per
# file) keeps task-scheduling overhead sane when a bulk load touches tens of
# thousands of images.
BULK_PRELOAD_CHUNK = 40
# Purely advisory (same spirit as FOLDER_COUNT_WARN / RECURSION_DEPTH_WARN
# above) — crossing this never stops or slows anything down, it just adds a
# note to the status line so the user understands why decoding is still
# trickling in after the folder scan itself has finished.
BULK_IMAGE_COUNT_WARN = 3000


class _BulkScanSignals(QObject):
    folder_ready = Signal(str, list, list)   # folder_path, files, subdirs
    error = Signal(str, str)                 # folder_path, message
    branch_done = Signal()                   # this root's whole subtree is exhausted


class _BulkScanWorker(QRunnable):
    """Recursively walks one selected folder's entire tree off the UI
    thread. Streams a folder_ready signal for every folder it visits (the
    root first, then every descendant, depth-first) so each one's result
    can be cached / absorbed by its FolderSection the moment it's ready,
    rather than waiting for the whole tree to finish."""

    def __init__(self, root_path, ignore_patterns, cancel_flag):
        super().__init__()
        self.root_path = root_path
        self.ignore_patterns = list(ignore_patterns)
        self.cancel_flag = cancel_flag
        self.signals = _BulkScanSignals()

    def run(self):
        try:
            self._walk(self.root_path)
        finally:
            self.signals.branch_done.emit()

    def _walk(self, path):
        if self.cancel_flag.is_set():
            return
        files, subdirs = [], []
        try:
            if any(is_link(p) for p in [Path(path), *Path(path).parents]):
                raise OSError('Linked folders are not followed')
            with os.scandir(path) as it:
                for entry in it:
                    if self.cancel_flag.is_set():
                        return
                    name = entry.name
                    try:
                        if is_link(Path(entry.path)) or _folder_is_ignored(name, self.ignore_patterns):
                            continue
                        if entry.is_dir():
                            if not _folder_is_ignored(name, self.ignore_patterns):
                                subdirs.append(entry.path)
                        elif entry.is_file():
                            files.append(entry.path)
                    except OSError:
                        continue
        except OSError as e:
            self.signals.error.emit(path, str(e))
            return
        files.sort(key=_natural_sort_key)
        subdirs.sort(key=_natural_sort_key)
        self.signals.folder_ready.emit(path, files, subdirs)
        for sub_path in subdirs:
            self._walk(sub_path)


class _BulkPixmapSignals(QObject):
    chunk_done = Signal(int)   # number of images processed in this chunk


class _BulkPixmapPreloadWorker(QRunnable):
    """Decodes+scales a batch of thumbnails straight into _PIXMAP_CACHE, on
    one of _BULK_PIXMAP_POOL's threads. Cheap no-op for anything already
    cached (e.g. a thumbnail the user already scrolled past normally)."""

    def __init__(self, filepaths, cancel_flag):
        super().__init__()
        self.filepaths = filepaths
        self.cancel_flag = cancel_flag
        self.signals = _BulkPixmapSignals()

    def run(self):
        done = 0
        for path in self.filepaths:
            if self.cancel_flag.is_set():
                break
            if path not in _PIXMAP_CACHE:
                try:
                    _load_pixmap(path)
                except Exception:
                    pass
            done += 1
        self.signals.chunk_done.emit(done)


class BulkFolderLoadManager(QObject):
    """Backs the "Load Selected Folders" context-menu action: recursively
    scans every selected folder tree and pre-decodes every thumbnail found
    in it, entirely off the UI thread, via the two bounded pools above.
    Once it's done (or even while it's still running), expanding any folder
    it touched is instant instead of triggering a fresh scan + per-image
    decode queue. Handles being pointed at a large number of folders and/or
    a huge total image count by streaming results through the pools in
    small batches rather than blocking on the whole thing at once, and
    supports folding a second selection into an already-running load
    instead of starting a competing one."""

    statusChanged = Signal(int, int, str)   # files_preloaded, files_queued, detail_text
    started = Signal()
    finished = Signal()

    def __init__(self, browser):
        super().__init__()
        self.browser = browser
        self.active = False
        self._cancel_flag = threading.Event()
        self._pending_branches = 0
        self._roots = []
        self._folders_seen = 0
        self._files_seen = 0
        self._files_queued = 0
        self._files_preloaded = 0

    def start(self, root_paths):
        new_roots = [p for p in root_paths if p not in self._roots]
        if self.active:
            # Already running — fold the newly-requested roots into this
            # run instead of starting a second, overlapping one.
            self._roots.extend(new_roots)
            for p in new_roots:
                self._start_branch(p)
            return
        self.active = True
        self._cancel_flag = threading.Event()
        self._pending_branches = 0
        self._roots = list(root_paths)
        self._folders_seen = 0
        self._files_seen = 0
        self._files_queued = 0
        self._files_preloaded = 0
        self.started.emit()
        for p in self._roots:
            self._start_branch(p)
        self._emit_status()

    def cancel(self):
        self._cancel_flag.set()
        if self.active:
            # Don't wait for every in-flight worker to notice the flag and
            # report back (a straggling chunk that was interrupted
            # mid-batch would otherwise never bring _files_preloaded up to
            # _files_queued, leaving _maybe_finish() waiting forever) —
            # cancelling means "stop now" from the UI's point of view.
            self.active = False
            self.finished.emit()
            self.statusChanged.emit(
                self._files_preloaded, self._files_queued,
                f"Load canceled \u2014 {self._folders_seen} folder(s) scanned,\n"
                f"{self._files_seen} image(s) found are still cached."
            )

    def _start_branch(self, root_path):
        self._pending_branches += 1
        worker = _BulkScanWorker(root_path, self.browser.ignore_folder_patterns(), self._cancel_flag)
        worker.signals.folder_ready.connect(self._on_folder_ready)
        worker.signals.error.connect(self._on_scan_error)
        worker.signals.branch_done.connect(self._on_branch_done)
        _BULK_SCAN_POOL.start(worker)

    def _on_folder_ready(self, path, files, subdirs):
        if self._cancel_flag.is_set():
            return
        _ROUGH_SCAN_CACHE[path] = (files, subdirs)
        self._folders_seen += 1
        self._files_seen += len(files)
        # If a FolderSection already exists for this path (e.g. the user
        # had it partway expanded before triggering the bulk load), feed it
        # the fresh result right away instead of leaving it stale.
        section = self.browser.section_for_path(path)
        if section is not None:
            section.absorb_bulk_scan(files, subdirs)
        if files:
            self._files_queued += len(files)
            for i in range(0, len(files), BULK_PRELOAD_CHUNK):
                chunk = files[i:i + BULK_PRELOAD_CHUNK]
                worker = _BulkPixmapPreloadWorker(chunk, self._cancel_flag)
                worker.signals.chunk_done.connect(self._on_chunk_done)
                _BULK_PIXMAP_POOL.start(worker)
        self._emit_status()

    def _on_scan_error(self, path, message):
        if self._cancel_flag.is_set():
            return
        self._emit_status()

    def _on_chunk_done(self, n):
        if self._cancel_flag.is_set():
            return
        self._files_preloaded += n
        self._maybe_finish()
        self._emit_status()

    def _on_branch_done(self):
        if self._cancel_flag.is_set():
            return
        self._pending_branches -= 1
        self._maybe_finish()
        self._emit_status()

    def _maybe_finish(self):
        if not self.active:
            return
        scanning_done = self._pending_branches <= 0
        preloading_done = self._files_preloaded >= self._files_queued
        if scanning_done and preloading_done:
            self.active = False
            self.finished.emit()

    def _emit_status(self):
        scanning_done = self._pending_branches <= 0 and bool(self._roots)
        fully_done = scanning_done and self._files_preloaded >= self._files_queued
        if fully_done:
            self.statusChanged.emit(
                self._files_preloaded, self._files_queued,
                f"Loaded {self._folders_seen} folder(s), {self._files_seen} image(s)\n"
                f"across {len(self._roots)} selected folder(s)."
            )
            return
        warn = ""
        if self._files_seen > BULK_IMAGE_COUNT_WARN:
            warn = "\nLarge batch \u2014 thumbnails keep loading in the background."
        self.statusChanged.emit(
            self._files_preloaded, self._files_queued,
            f"Loading selected folders\u2026\n"
            f"{self._folders_seen} folder(s) scanned, {self._files_seen} image(s) found{warn}"
        )


# ---------------------------------------------------------------------------
# Folder scanning (still needed here, unlike indexer, since this app has no
# pre-built database of images — it walks the filesystem live). A single
# pass now always recurses into every subfolder (the "recursive" per-folder
# flag has been removed: everything configured in Settings is always fully
# recursive), skipping only folders that match a configured ignore pattern.
# ---------------------------------------------------------------------------
class _ScanSignals(QObject):
    done = Signal(str, list, list)   # section_path, files, subdirs
    failed = Signal(str, str)        # section_path, message


def _folder_is_ignored(name, ignore_patterns):
    return name.startswith('.sorter-') or any(fnmatch.fnmatch(name.casefold(), rule.casefold()) for rule in ignore_patterns)


class _RoughScanWorker(QRunnable):
    """Rough pass: list image filenames + immediate subfolders for one
    folder section. Fast — no image decoding at all. Always recurses (every
    subfolder is reported, to be turned into its own nested section) except
    folders matching a configured ignore pattern."""

    def __init__(self, section_path, ignore_patterns):
        super().__init__()
        self.section_path = section_path
        self.ignore_patterns = list(ignore_patterns)
        self.signals = _ScanSignals()

    def run(self):
        files, subdirs = [], []
        try:
            if any(is_link(p) for p in [Path(self.section_path), *Path(self.section_path).parents]):
                raise OSError('Linked folders are not followed')
            with os.scandir(self.section_path) as it:
                for entry in it:
                    name = entry.name
                    try:
                        if is_link(Path(entry.path)) or _folder_is_ignored(name, self.ignore_patterns):
                            continue
                        if entry.is_dir():
                            if not _folder_is_ignored(name, self.ignore_patterns):
                                subdirs.append(entry.path)
                        elif entry.is_file():
                            files.append(entry.path)
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
        files.sort(key=_natural_sort_key)
        subdirs.sort(key=_natural_sort_key)
        self.signals.done.emit(self.section_path, files, subdirs)


# ---------------------------------------------------------------------------
# Thumbnail card — ported one-to-one from vael. indexer's ThumbnailCard:
# image only (no filename caption), a faint neutral border on hover and
# nothing more, and a drag source so a card can be dragged straight onto a
# roster icon below (or anywhere else that accepts a file URL drop).
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# QScrollArea with middle-mouse-drag autopan, ported from vael. indexer's
# ResultsPanel: press the middle button anywhere in the viewport and drag
# up/down to scroll, exactly like indexer's image browser.
# ---------------------------------------------------------------------------
class _AutopanScrollArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._autopan_active = False
        self._autopan_origin = QPoint()
        self._autopan_timer = QTimer(self)
        self._autopan_timer.setInterval(16)  # ~60 fps
        self._autopan_timer.timeout.connect(self._autopan_tick)
        self.viewport().setMouseTracking(True)
        self.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.viewport():
            t = event.type()
            if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MiddleButton:
                self._autopan_active = True
                self._autopan_origin = event.position().toPoint()
                self.viewport().setCursor(Qt.CursorShape.SizeVerCursor)
                self._autopan_timer.start()
                return True
            if t == QEvent.Type.MouseButtonRelease and event.button() == Qt.MiddleButton:
                self._stop_autopan()
                return True
        return super().eventFilter(obj, event)

    def _autopan_tick(self):
        if not self._autopan_active:
            return
        cursor_pos = self.viewport().mapFromGlobal(self.viewport().cursor().pos())
        delta_y = cursor_pos.y() - self._autopan_origin.y()
        if abs(delta_y) < 8:
            return
        speed = int((delta_y - (8 if delta_y > 0 else -8)) * 0.4)
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + speed)

    def _stop_autopan(self):
        self._autopan_active = False
        self._autopan_timer.stop()
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)


def document_pixmap(path, scale=1):
    pixmap = QPixmap(round(96 * scale), round(76 * scale))
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.scale(scale, scale)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QColor('#b8bec6'))
    painter.setBrush(QColor('#ffffff'))
    painter.drawRoundedRect(23, 3, 50, 69, 3, 3)
    painter.setPen(QColor('#26313e'))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(11)
    painter.setFont(font)
    extension = Path(path).suffix.lstrip('.').upper() or 'FILE'
    painter.drawText(QRect(25, 8, 46, 60), Qt.AlignCenter | Qt.TextWrapAnywhere, extension)
    painter.end()
    return pixmap


class CardImageLabel(QLabel):
    """Paint selection above the thumbnail, within the label's own dirty region."""

    def paintEvent(self, event):
        super().paintEvent(event)
        card = self.parentWidget()
        if card is None:
            return
        selected = (card.item.isSelected() if hasattr(card, 'item') else
                    bool(card.browser and card.filepath in card.browser._selected_file_paths))
        media = Path(card.filepath).suffix.lower() in VIDEOS | {'.gif'}
        if not (selected or card._hovered or media):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor('#00d4a0') if selected else QColor(103, 139, 169, 110) if media else QColor(235, 235, 240, 90)
        painter.setPen(QPen(color, 3 if selected else 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5), 7, 7)
        if card._hovered and media:
            badge = QRect(6, self.height() - 23, self.width() - 12, 17)
            painter.fillRect(badge, QColor(10, 10, 10, 190))
            painter.setPen(QColor('#b4b4b4'))
            painter.drawText(badge, Qt.AlignCenter, Path(card.filepath).suffix.upper())
        painter.end()


class ThumbnailCard(QWidget):
    view_requested = Signal(object)   # emits self on a plain left-click (no drag)
    # Right-click ("open context menu on an image") -- emits self plus the
    # global cursor position so the caller (FolderSection) can build and
    # show a menu listing every available roster/image input.
    context_menu_requested = Signal(object, QPoint)

    CARD_W = THUMB_W
    CARD_H = THUMB_H

    def __init__(self, filepath, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.setFixedSize(self.CARD_W, self.CARD_H)
        self.setCursor(Qt.PointingHandCursor)
        self._hovered = False
        self._drag_start_pos = None
        self._image_loaded = False
        self._image_requested = False
        self._source_pixmap = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._img_lbl = CardImageLabel(self)
        self._img_lbl.setObjectName("cardImage")
        self._img_lbl.setFixedSize(THUMB_W, THUMB_H)
        self._img_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._img_lbl)

        self.browser = None
        self.setToolTip(str(filepath))
        PIXMAP_WORKER.pixmap_ready.connect(self._on_pixmap_ready)

    def request_image(self):
        """Called by FolderSection once the section is expanded. No-op if
        this card's image is already loaded/loading."""
        if self._image_loaded or self._image_requested:
            return
        extension = Path(self.filepath).suffix.lower()
        if extension not in IMAGES:
            symbol = QStyle.SP_MediaPlay if extension in VIDEOS or extension == '.gif' else QStyle.SP_FileIcon
            self._img_lbl.setPixmap(self.style().standardIcon(symbol).pixmap(round(28 * self.width() / THUMB_W), round(28 * self.width() / THUMB_W)) if extension in VIDEOS or extension == '.gif' else document_pixmap(self.filepath, self.width() / THUMB_W))
            self._image_loaded = True
            return
        cached = _PIXMAP_CACHE.get(self.filepath)
        if cached is not None:
            self._apply_pixmap(cached)
        else:
            self._image_requested = True
            PIXMAP_WORKER.submit(self, self.filepath)

    def _on_pixmap_ready(self, card, pix):
        if card is not self:
            return
        self._apply_pixmap(pix)
        try:
            PIXMAP_WORKER.pixmap_ready.disconnect(self._on_pixmap_ready)
        except RuntimeError:
            pass

    def set_entry_zoom(self, percent):
        self.setFixedSize(round(THUMB_W * percent / 100), round(THUMB_H * percent / 100))
        self._img_lbl.setFixedSize(self.size())
        if self._source_pixmap is not None:
            self._apply_pixmap(self._source_pixmap)
        elif self._image_loaded:
            self._image_loaded = False
            self.request_image()
        self.update()

    def _apply_pixmap(self, pix):
        if isinstance(pix, QImage):
            pix = QPixmap.fromImage(pix)
        self._source_pixmap = pix
        self._image_loaded = True
        if not pix.isNull():
            rounded = QPixmap(self.size())
            rounded.fill(Qt.GlobalColor.transparent)
            painter = QPainter(rounded)
            painter.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(0, 0, self.width(), self.height(), 7, 7)
            painter.setClipPath(path)
            painter.drawPixmap(self.rect(), pix)
            painter.end()
            self._img_lbl.setPixmap(rounded)
        else:
            self._img_lbl.setText('?')

    # -- click / drag (mirrors indexer's card interactions) ---------------
    # NOTE: every one of these handlers explicitly accept()s left-button
    # events instead of falling through to QWidget's default implementation.
    # QWidget's base mouse handlers ignore() unhandled events so they can
    # bubble up to the parent -- which is exactly right for e.g. middle-
    # button autopan (see _AutopanScrollArea), but wrong here: this card
    # lives inside a FolderSection body, which itself lives inside a
    # _RubberBandArea (see ImageBrowser.reload_folders / FolderSection).
    # If a left-click on a thumbnail were left unaccepted, Qt would hand it
    # up to that ancestor's mousePressEvent, which grabs the mouse and
    # starts an Explorer-style folder rubber-band selection -- silently
    # stealing every subsequent move/release from this card and making it
    # impossible to drag an image out. Accepting the event here keeps the
    # whole press/move/release gesture on the card, where it belongs.
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.position().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos is not None and (event.buttons() & Qt.LeftButton):
            dist = (event.position().toPoint() - self._drag_start_pos).manhattanLength()
            if dist >= QApplication.startDragDistance():
                self._drag_start_pos = None
                self._start_drag()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_start_pos is not None:
            self._drag_start_pos = None
            if self.browser:
                self.browser.select_card(self, event.modifiers())
            else:
                self.view_requested.emit(self)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = None
            self.view_requested.emit(self)
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def _start_drag(self):
        mime = QMimeData()
        paths = self.browser.drag_paths(self) if self.browser else [self.filepath]
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        mime.setData('application/x-vael-sorter', QByteArray(b'workspace' if self.browser.editable else b'source'))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pix = QPixmap.fromImage(_load_pixmap(self.filepath))
        if not pix.isNull():
            drag.setPixmap(pix.scaled(THUMB_W // 2, THUMB_H // 2, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        drag.exec(Qt.CopyAction)

    def enterEvent(self, event):
        self._hovered = True
        self._img_lbl.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._img_lbl.update()
        super().leaveEvent(event)

    def contextMenuEvent(self, event):
        self.context_menu_requested.emit(self, event.globalPos())
        event.accept()



class _InertMouseArea(QWidget):
    """A plain container that deliberately swallows left-button mouse
    presses/moves/releases on its own blank space instead of letting them
    bubble up to an ancestor _RubberBandArea. Used for the thumbnail card
    grid of an *open* folder: without this, an unhandled click on the
    gaps between thumbnails would propagate to the folder header's rubber
    -band area and start a folder-selection drag, exactly the same failure
    mode a plain ThumbnailCard press would otherwise hit (see
    ThumbnailCard's mouse handlers). Right/middle clicks and anything that
    isn't a plain left-button press still pass through untouched, so
    middle-button autopan (see _AutopanScrollArea) keeps working."""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# One folder header, or (nested under it) one recursive sub-header — design
# ported one-to-one from vael. indexer's FolderSection: a flat QToolButton
# header with an arrow indicator, indent-by-depth, and a QGridLayout card
# grid that reflows its column count to the available width. Unlike
# indexer, this app has no pre-built index, so contents are still scanned
# lazily off the UI thread the first time a section is expanded — but every
# folder is now always fully recursive, and a top-level (depth 0) section
# auto-expands itself the moment its scan completes, since folders
# configured in Settings are guaranteed to hold only more folders, never
# images directly, so there's no reason to make the user click twice just
# to see the first level of real subfolders.
# ---------------------------------------------------------------------------
class _RubberBandArea(QWidget):
    """A plain container that starts a Windows-Explorer-style click-and-
    drag rectangle selection of folder headers when the drag begins on
    blank space inside it — i.e. not on a header button or a thumbnail
    card, both of which are child widgets that intercept the press first,
    exactly like clicking directly on a desktop icon doesn't start a
    rubber band in Explorer either. Used for every "background" area
    inside a browser tab that a drag might reasonably start from: the
    header row's empty stretch, the space below the last subfolder, and
    the top-level tab wrapper itself.

    No visual selection rectangle is drawn — as the drag crosses a folder
    header it's highlighted directly (via ImageBrowser.set_folder_selection
    / FolderSection.set_selected), which reads clearer than a rectangle
    that's mostly just passing over irrelevant blank space anyway."""

    def __init__(self, browser, parent=None):
        super().__init__(parent)
        self._browser = browser
        self._dragging = False
        self._origin_content = None
        self._pointer_global = None
        self._selection_scroll = None
        self._edge_timer = QTimer(self)
        self._edge_timer.setInterval(16)
        self._edge_timer.timeout.connect(self._edge_scroll_tick)

    def contextMenuEvent(self, event):
        self._browser.action_menu(event.globalPos())
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            scroll = self._browser.tabs.currentWidget()
            if not isinstance(scroll, QScrollArea) or not scroll.isAncestorOf(self):
                super().mousePressEvent(event)
                return
            self._selection_scroll = scroll
            self._pointer_global = event.globalPosition().toPoint()
            # Anchor in content coordinates so scrolling never moves the start
            # of the selection relative to its original folder.
            self._origin_content = scroll.widget().mapFromGlobal(self._pointer_global)
            additive = bool(event.modifiers() & Qt.ControlModifier)
            self._browser.begin_rubber_band_drag(additive)
            self._dragging = True
            self.grabMouse()
            self._edge_timer.start()
            event.accept()
            return
        super().mousePressEvent(event)

    def _update_drag_selection(self):
        origin = self._selection_scroll.widget().mapToGlobal(self._origin_content)
        self._browser.update_rubber_band_drag(QRect(origin, self._pointer_global).normalized())

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._pointer_global = event.globalPosition().toPoint()
            self._update_drag_selection()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def _edge_scroll_tick(self):
        if not self._dragging:
            return
        if (not QApplication.mouseButtons() & Qt.LeftButton
                or not self.isVisible()
                or self._browser.tabs.currentWidget() is not self._selection_scroll):
            self._finish_drag()
            return
        viewport = self._selection_scroll.viewport()
        point = viewport.mapFromGlobal(self._pointer_global)
        # Scroll only at the top/bottom, not when dragging into a sidebar.
        if not 0 <= point.x() < viewport.width():
            return
        edge = min(48, max(1, viewport.height() // 4))
        distance = 0
        if point.y() < edge:
            distance = point.y() - edge
        elif point.y() >= viewport.height() - edge:
            distance = point.y() - (viewport.height() - edge) + 1
        if distance:
            speed = max(1, min(24, abs(distance) // 2))
            bar = self._selection_scroll.verticalScrollBar()
            bar.setValue(bar.value() + (speed if distance > 0 else -speed))
        # Update even with a stationary mouse, including after wheel scrolling.
        self._update_drag_selection()

    def _finish_drag(self):
        if not getattr(self, "_dragging", False):
            return
        self._dragging = False
        self._edge_timer.stop()
        self.releaseMouse()
        self._selection_scroll = None
        self._browser.end_rubber_band_drag()

    def event(self, event):
        if event.type() in (QEvent.UngrabMouse, QEvent.Hide, QEvent.WindowDeactivate):
            self._finish_drag()
        return super().event(event)

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == Qt.LeftButton:
            self._pointer_global = event.globalPosition().toPoint()
            self._update_drag_selection()
            self._finish_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class FolderHeader(QToolButton):
    """The unchanged Cover header, extended with a file-transfer gesture."""
    def __init__(self, section):
        super().__init__()
        self.section = section
        self._origin = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._origin = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._origin is not None and event.buttons() & Qt.LeftButton:
            if (event.position().toPoint() - self._origin).manhattanLength() >= QApplication.startDragDistance():
                self._origin = None
                self.setDown(False)
                browser = self.section.browser
                if self.section.path not in browser.selected_folder_paths():
                    browser.clear_selection()
                    browser.toggle_folder_selection(self.section.path)
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(str(p)) for p in browser.selected_paths()])
                mime.setData('application/x-vael-sorter', QByteArray(b'workspace' if self.section.browser.editable else b'source'))
                drag = QDrag(self)
                drag.setMimeData(mime)
                drag.exec(Qt.CopyAction)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._origin = None
        super().mouseReleaseEvent(event)


class FolderSection(QWidget):
    def __init__(self, browser, path, depth=0, closable=False):
        super().__init__()
        self.browser = browser
        self.setAcceptDrops(browser.editable)
        self.path = path
        self.depth = depth
        self.closable = closable
        self._retired = False
        self.setObjectName("folderSection")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._expanded = False
        self._rough_done = False
        self._rough_started = False
        self._materialized = False
        self._files = []
        self._pending_subdirs = []
        self._cards = []
        self._child_sections = []
        self._current_cols = 0
        self._selected = False
        # Set to the FolderSection that created this one as a child (see
        # _materialize); stays None for depth-0 (tab-root) sections. Lets
        # Ctrl+Up/Down walk siblings under the same parent only.
        self.parent_section = None

        browser.register_section(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # _RubberBandArea (not plain QWidget) so a click-drag that starts on
        # the blank space beside the folder name can kick off a Windows-
        # style rectangle multi-select of folder headers (see
        # ImageBrowser.begin_rubber_band_drag and friends).
        header_wrap = _RubberBandArea(browser)
        header_wrap.setObjectName("sectionHeaderWrap")
        header_wrap.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        indent = depth * 14
        hw_lay = QHBoxLayout(header_wrap)
        hw_lay.setContentsMargins(indent, 0, 0, 0)
        hw_lay.setSpacing(4)

        is_deep = depth > RECURSION_DEPTH_WARN
        name_text = self.browser.folder_display_name(path) if depth == 0 else Path(path).name or path

        self.header = FolderHeader(self)
        self.header.setObjectName("sectionHeader")
        self.header.setProperty("depth0", "true" if depth == 0 else "false")
        if is_deep:
            self.header.setProperty("deep", True)
        self.header.setArrowType(Qt.ArrowType.RightArrow)
        self.header.setText(name_text + (" \u26a0" if is_deep else ""))
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.header.setFixedHeight(26 if depth == 0 else 22)
        self.header.setCursor(Qt.PointingHandCursor)
        # Let right-clicks fall through to FolderSection.contextMenuEvent
        # (below) instead of the button/wrap trying to handle them.
        self.header.setContextMenuPolicy(Qt.NoContextMenu)
        header_wrap.setContextMenuPolicy(Qt.NoContextMenu)
        tooltip = path
        if is_deep:
            tooltip += (
                f"\n\u26a0 Nested {depth} levels deep \u2014 past the recommended "
                f"{RECURSION_DEPTH_WARN}-level guideline. Still fully functional, "
                f"just flagged for awareness."
            )
        tooltip += (
            "\n\nCtrl+click to multi-select, or drag a rectangle over several "
            "folders, then right-click for \u201cLoad Selected Folders\u201d."
        )
        self.header.setToolTip(tooltip)
        self.header.clicked.connect(self._on_header_clicked)
        hw_lay.addWidget(self.header, 0)

        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("headerCount")
        hw_lay.addWidget(self.count_lbl, 0)
        hw_lay.addStretch(1)

        if closable:
            close_btn = QToolButton()
            close_btn.setObjectName("iconButton")
            close_btn.setText("\u00d7")
            close_btn.setToolTip("Hide for this session (not removed from Settings)")
            close_btn.setCursor(Qt.PointingHandCursor)
            close_btn.clicked.connect(self._close_for_session)
            hw_lay.addWidget(close_btn)

        outer.addWidget(header_wrap)

        self.body = QWidget()
        self.body.setObjectName("sectionBody")
        self.body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        body_lay = QVBoxLayout(self.body)
        body_lay.setContentsMargins(indent + 8, 4, 4, 6)
        body_lay.setSpacing(6)

        # _InertMouseArea (not plain QWidget, not _RubberBandArea) — this
        # holds the actual thumbnail cards of an *open* folder. When a
        # folder is open the user's blank-space click-drag here is
        # virtually always an attempt to grab an image and drag it down to
        # the input roster, not to rubber-band-select folder headers, so
        # this area deliberately doesn't participate in folder selection at
        # all: presses on blank space here are swallowed and do nothing,
        # rather than bubbling up to the header's rubber-band area, leaving
        # ThumbnailCard's own drag handling untouched.
        self.card_widget = _RubberBandArea(browser)
        self.card_widget.setObjectName("cardGrid")
        self.card_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.card_grid = QGridLayout(self.card_widget)
        self.card_grid.setContentsMargins(0, 0, 0, 0)
        self.card_grid.setSpacing(6)
        self.card_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        body_lay.addWidget(self.card_widget)

        self.children_container = _RubberBandArea(browser)
        self.children_lay = QVBoxLayout(self.children_container)
        self.children_lay.setContentsMargins(0, 0, 0, 0)
        self.children_lay.setSpacing(4)
        body_lay.addWidget(self.children_container)

        outer.addWidget(self.body)
        self.body.setVisible(False)

    def dragEnterEvent(self, event):
        self.browser.accept_drop(event)

    def dragMoveEvent(self, event):
        self.browser.accept_drop(event)

    def dropEvent(self, event):
        self.browser.drop_into(event, self.path)

    def _close_for_session(self):
        self.browser.close_tab_for_session(self.path)

    # -- multi-select (ctrl+click / rubber-band drag) -----------------------
    def _on_header_clicked(self):
        self.browser._selected_file_paths.clear()
        self.browser.repaint_content()
        if QApplication.keyboardModifiers() & Qt.ControlModifier:
            self.browser.toggle_folder_selection(self.path)
        else:
            if self.browser.has_folder_selection():
                self.browser.clear_folder_selection()
            self.toggle()

    def set_selected(self, selected):
        if selected == self._selected:
            return
        self._selected = selected
        self.header.setProperty("selected", "true" if selected else "false")
        self.header.style().unpolish(self.header)
        self.header.style().polish(self.header)

    def contextMenuEvent(self, event):
        # Mirrors Explorer: right-clicking a folder that's already part of
        # the current multi-selection keeps the whole selection; right-
        # clicking one that isn't selected replaces the selection with just
        # that folder.
        if self.path not in self.browser.selected_folder_paths():
            self.browser.clear_selection()
            self.browser.toggle_folder_selection(self.path)
        self.browser.show_folder_context_menu(self, event.globalPos())
        event.accept()

    def _on_card_view_requested(self, card):
        self.browser.open_card(card, self._cards)

    def _on_card_context_menu(self, card, global_pos):
        self.browser.card_menu(card, global_pos)

    # -- expand / collapse -------------------------------------------------
    def toggle(self):
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded, persist=True):
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self.header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.header.setProperty("expanded", "true" if expanded else "false")
        self.header.style().unpolish(self.header)
        self.header.style().polish(self.header)
        self.body.setVisible(expanded)
        if expanded:
            if not self._rough_done:
                self.ensure_rough_pass()
            else:
                if not self._materialized:
                    # A bulk "Load Selected Folders" run already scanned
                    # this folder while it was still collapsed (see
                    # absorb_bulk_scan) but deliberately held off building
                    # the actual card/child widgets until it's really
                    # needed — do that now that the user is opening it.
                    self._materialize()
                if self._cards:
                    self._current_cols = 0
                    QTimer.singleShot(0, self._relayout_cards)
                    for card in self._cards:
                        card.request_image()
        if persist:
            self.browser.note_expanded(self.path, expanded)
        if expanded and persist:
            # Universal "focus this folder" behavior: applies the same
            # whether it was opened by a mouse click (toggle() -> here with
            # persist=True) or by the Ctrl+Up/Down hotkeys (which also call
            # set_expanded(True) directly) -- but not to the programmatic
            # persist=False restores done on tab load/reload.
            self.browser.focus_folder_section(self)

    def is_expanded(self):
        return self._expanded

    # -- lazy scanning ------------------------------------------------------
    def ensure_rough_pass(self):
        if self._rough_done or self._rough_started:
            return
        if self.browser._filter_map is not None:
            files, subdirs = self.browser._filter_map.get(self.path, ([], []))
            self._on_rough_done(self.path, files, subdirs)
            return
        # A bulk load (or an earlier visit) may already have scanned this
        # exact folder — reuse that instead of scanning it all over again,
        # which is the whole point of "Load Selected Folders": expanding a
        # folder it already touched should feel instant.
        cached = _ROUGH_SCAN_CACHE.get(self.path)
        if cached is not None:
            self._on_rough_done(self.path, cached[0], cached[1])
            return
        self._rough_started = True
        self.count_lbl.setText("\u2026")
        self._set_count_state("normal")
        worker = _RoughScanWorker(self.path, self.browser.ignore_folder_patterns())
        worker.signals.done.connect(self._on_rough_done)
        worker.signals.failed.connect(self._on_rough_failed)
        self.browser.thread_pool.start(worker)

    def _on_rough_failed(self, path, message):
        if path != self.path or self._retired:
            return
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
        if path != self.path or self._retired:
            return
        self._rough_done = True
        self._files = files
        self._pending_subdirs = subdirs
        if self.browser._filter_map is None:
            _ROUGH_SCAN_CACHE[path] = (files, subdirs)
        self.count_lbl.setText(f"({len(files)})" if (files or subdirs) else "(empty)")
        self._set_count_state("normal" if (files or subdirs) else "muted")
        self._materialize()

        # A top-level folder is guaranteed (by convention — see Settings)
        # to hold only more folders, not images. Auto-expand it the moment
        # its scan finishes so the user lands straight on the first level
        # of real subfolders instead of clicking once just to reveal them.
        if self.depth == 0 and not self._expanded:
            self.set_expanded(True, persist=False)
        elif self._expanded and self._cards:
            self._current_cols = 0
            QTimer.singleShot(0, self._relayout_cards)
            for card in self._cards:
                card.request_image()

    def absorb_bulk_scan(self, files, subdirs):
        """Called by BulkFolderLoadManager when a background 'Load Selected
        Folders' scan reaches this exact folder. Unlike the interactive
        ensure_rough_pass()/_on_rough_done() path, this deliberately does
        NOT build ThumbnailCard/child-FolderSection widgets while the
        section is collapsed — a bulk load can touch thousands of folders
        at once, and eagerly materializing every one of them would dump an
        enormous widget tree on the UI thread. The header count still
        updates immediately; the rest is built lazily in set_expanded()
        the moment the user actually opens it, by which point the scan
        result and every thumbnail in it are already cached."""
        if self._rough_done:
            return  # already scanned interactively — don't duplicate it
        self._rough_done = True
        self._files = files
        self._pending_subdirs = subdirs
        self.count_lbl.setText(f"({len(files)})" if (files or subdirs) else "(empty)")
        self._set_count_state("normal" if (files or subdirs) else "muted")
        if self._expanded:
            # Rare (would mean the user opened it mid-bulk-load), but if it
            # really is visible right now, build it out immediately.
            self._materialize()
            self._current_cols = 0
            QTimer.singleShot(0, self._relayout_cards)
            for card in self._cards:
                card.request_image()
        elif self.depth == 0:
            self.set_expanded(True, persist=False)

    def _materialize(self):
        """Turn a completed (possibly cached) scan result into actual
        ThumbnailCard / child-FolderSection widgets. Idempotent."""
        if self._materialized or self._retired:
            return
        self._materialized = True
        for filepath in self._files:
            card = ThumbnailCard(filepath)
            card.browser = self.browser
            card.set_entry_zoom(self.browser.entry_zoom)
            card.view_requested.connect(self._on_card_view_requested)
            card.context_menu_requested.connect(self._on_card_context_menu)
            i = len(self._cards)
            self._cards.append(card)
            self.card_grid.addWidget(card, i // 4, i % 4)

        for sub_path in self._pending_subdirs:
            child = FolderSection(self.browser, sub_path, depth=self.depth + 1)
            child.parent_section = self
            self.children_lay.addWidget(child)
            self._child_sections.append(child)
            if sub_path in self.browser.saved_expanded_paths or self.browser._filter_map is not None:
                child.set_expanded(True, persist=False)
        self._pending_subdirs = []
        self.browser.repaint_content()

    # -- reflow (ported from indexer's _relayout_cards) --------------------
    def _relayout_cards(self):
        if not isValid(self) or not isValid(self.card_widget):
            return
        available = self.browser.width() - self.card_widget.mapTo(self.browser, QPoint(0, 0)).x() - 12
        card_width = round(THUMB_W * self.browser.entry_zoom / 100)
        avail_w = min(self.card_widget.width(), max(card_width, available))
        if avail_w < card_width:
            return
        cols = max(1, avail_w // (card_width + 6))
        if cols == self._current_cols:
            return
        self._current_cols = cols
        while self.card_grid.count():
            self.card_grid.takeAt(0)
        for i, card in enumerate(self._cards):
            self.card_grid.addWidget(card, i // cols, i % cols)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._expanded and self._cards:
            self._relayout_cards()


# ---------------------------------------------------------------------------
# Full-size image viewer overlay — ported one-to-one from vael. indexer's
# ImgViewerOverlay, adapted for cover's ThumbnailCard (plain .filepath
# instead of an .asset dict, no per-card context menu to delegate to).
#
#   • Opens centred over the image browser on a plain left-click of any
#     ThumbnailCard (dragging a card down to the roster still works
#     exactly as before — that's a drag, not a click).
#   • Prev/Next arrows and the Left/Right keys navigate the cards within
#     the same folder section the clicked card came from.
#   • Click on the dim area outside the image, or the × / Esc, closes it.
# ---------------------------------------------------------------------------
class ImgViewerOverlay(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()

        self._cards = []
        self._idx = 0
        self._current_card = None

        self._close_btn = QToolButton(self)
        self._close_btn.setText("\u2715")
        self._close_btn.setObjectName("viewerCloseBtn")
        self._close_btn.setFixedSize(32, 32)
        self._close_btn.clicked.connect(self.close_viewer)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        self._img_lbl = QLabel(self)
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setObjectName("viewerImage")
        self._img_lbl.setScaledContents(False)

        self._name_lbl = QLabel(self)
        self._name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_lbl.setObjectName("viewerName")

        self._prev_btn = QToolButton(self)
        self._prev_btn.setText("\u2039")
        self._prev_btn.setObjectName("viewerNavBtn")
        self._prev_btn.setFixedSize(44, 80)
        self._prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prev_btn.clicked.connect(self._go_prev)

        self._next_btn = QToolButton(self)
        self._next_btn.setText("\u203a")
        self._next_btn.setObjectName("viewerNavBtn")
        self._next_btn.setFixedSize(44, 80)
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.clicked.connect(self._go_next)

    # -- public API ---------------------------------------------------------
    def open_viewer(self, card, cards):
        self._cards = cards
        self._idx = cards.index(card) if card in cards else 0
        self._current_card = card
        self.resize(self.parent().size())
        self._load_current()
        self._update_nav_state()
        self.show()
        self.raise_()
        self.setFocus()

    def close_viewer(self):
        self.hide()
        self._current_card = None
        self._cards = []

    # -- navigation -----------------------------------------------------------
    def _go_prev(self):
        if self._idx > 0:
            self._idx -= 1
            self._current_card = self._cards[self._idx]
            self._load_current()
            self._update_nav_state()

    def _go_next(self):
        if self._idx < len(self._cards) - 1:
            self._idx += 1
            self._current_card = self._cards[self._idx]
            self._load_current()
            self._update_nav_state()

    def _update_nav_state(self):
        self._prev_btn.setEnabled(self._idx > 0)
        self._next_btn.setEnabled(self._idx < len(self._cards) - 1)

    # -- image loading --------------------------------------------------------
    def _load_current(self):
        card = self._current_card
        if card is None:
            return
        self._name_lbl.setText(Path(card.filepath).name)

        pix = QPixmap(card.filepath)
        if pix.isNull():
            self._img_lbl.clear()
            self._img_lbl.setText("?")
        else:
            self._img_lbl.setProperty("_raw_pix", pix)
            self._scale_image()
        self._place_widgets()

    def _scale_image(self):
        pix = self._img_lbl.property("_raw_pix")
        if pix is None or pix.isNull():
            return
        max_w, max_h = self._image_area_size()
        scaled = pix.scaled(
            max_w, max_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._img_lbl.setPixmap(scaled)
        self._img_lbl.resize(scaled.width(), scaled.height())

    def _image_area_size(self):
        w, h = self.width(), self.height()
        margin_x = 110
        margin_y = 100
        return max(200, w - margin_x * 2), max(200, h - margin_y)

    # -- widget placement -------------------------------------------------------
    def _place_widgets(self):
        ow, oh = self.width(), self.height()

        pad = 14
        self._close_btn.move(ow - self._close_btn.width() - pad, pad)

        img_w, img_h = self._img_lbl.width(), self._img_lbl.height()
        img_x = (ow - img_w) // 2
        img_y = max(50, (oh - img_h - 40) // 2)
        self._img_lbl.move(img_x, img_y)

        name_h = 28
        self._name_lbl.setFixedSize(min(600, ow - 40), name_h)
        name_x = (ow - self._name_lbl.width()) // 2
        name_y = img_y + img_h + 10
        self._name_lbl.move(name_x, name_y)

        btn_y = img_y + (img_h - self._prev_btn.height()) // 2
        left_edge = img_x - self._prev_btn.width() - 12
        right_edge = img_x + img_w + 12
        self._prev_btn.move(max(8, left_edge), btn_y)
        self._next_btn.move(min(ow - self._next_btn.width() - 8, right_edge), btn_y)

    # -- events -----------------------------------------------------------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.isVisible():
            self._scale_image()
            self._place_widgets()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 180))
        p.end()

    def mousePressEvent(self, event):
        """Click outside the image panel closes the viewer."""
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._img_lbl.geometry().contains(event.position().toPoint()):
                self.close_viewer()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close_viewer()
        elif key == Qt.Key.Key_Left:
            self._go_prev()
        elif key == Qt.Key.Key_Right:
            self._go_next()
        else:
            super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Blur overlay shown while a "Load Selected Folders" bulk scan is running.
# Rather than a boxed panel sitting on a dimmed backdrop, this blurs a
# snapshot of the *entire app* (sidebars, roster, everything -- it's
# parented to the app's content root, not just the image browser) to read
# as "the whole UI is held up right now", and floats only bare text, a
# progress bar, and a Cancel button in the middle -- no panel chrome.
# ---------------------------------------------------------------------------
class PannableTextEdit(QPlainTextEdit):
    """Hold the middle button and move vertically to scroll a document."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pan_origin = None
        self._pan_pointer = None
        self._pan_timer = QTimer(self)
        self._pan_timer.setInterval(16)
        self._pan_timer.timeout.connect(self._pan_tick)

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._pan_origin = event.position().toPoint()
            self._pan_pointer = self._pan_origin
            self.viewport().setCursor(Qt.SizeVerCursor)
            self._pan_timer.start()
            event.accept()
            return
        self.stop_pan()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan_origin is not None:
            self._pan_pointer = event.position().toPoint()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.stop_pan()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _pan_tick(self):
        if self._pan_origin is None:
            return
        delta = self._pan_pointer.y() - self._pan_origin.y()
        if abs(delta) > 8:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + (1 if delta > 0 else -1) * max(1, (abs(delta) - 8) // 16))

    def stop_pan(self):
        self._pan_timer.stop()
        self._pan_origin = self._pan_pointer = None
        self.viewport().unsetCursor()

    def hideEvent(self, event):
        self.stop_pan()
        super().hideEvent(event)

    def focusOutEvent(self, event):
        self.stop_pan()
        super().focusOutEvent(event)


class MediaViewerOverlay(ImgViewerOverlay):
    """Cover's viewer surface, with playback controls only for animated media."""
    def __init__(self, parent):
        super().__init__(parent)
        self._text = PannableTextEdit(self)
        self._text.setReadOnly(True)
        self._text.setToolTip('Hold the middle mouse button and move up or down to scroll.')
        self._text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._text.hide()
        self._movie = None
        self._gif_file = None
        self._zoom = 0
        self._pan = QPoint()
        self._pan_origin = None
        self._img_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._video = QVideoWidget(self)
        self._video.hide()
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(.5)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video)
        self._controls = QWidget(self)
        self._controls.setObjectName('browserStatusBar')
        row = QHBoxLayout(self._controls)
        row.setContentsMargins(0, 0, 0, 0)
        pause = QPushButton('Play / pause')
        pause.clicked.connect(self.toggle_play)
        row.addWidget(pause)
        self._timeline = QSlider(Qt.Horizontal)
        self._timeline.sliderMoved.connect(self._player.setPosition)
        row.addWidget(self._timeline, 1)
        self._time = QLabel('0:00 / 0:00')
        self._time.setObjectName('hint')
        row.addWidget(self._time)
        self._volume = QSlider(Qt.Horizontal)
        self._volume.setRange(0, 100)
        self._volume.setValue(50)
        self._volume.setFixedWidth(70)
        self._volume.setToolTip('Volume')
        self._volume.valueChanged.connect(lambda value: self._audio.setVolume(value / 100))
        row.addWidget(self._volume)
        self._controls.hide()
        self._player.durationChanged.connect(lambda duration: self._timeline.setRange(0, duration))
        self._player.positionChanged.connect(self._position_changed)
        self._player.errorOccurred.connect(lambda _, message: self._show_error('Playback unavailable: ' + message))

    def _stop_media(self):
        self._player.stop()
        self._player.setSource(QUrl())
        if self._movie:
            self._movie.stop()
            self._movie.deleteLater()
            self._movie = None
        if self._gif_file:
            self._gif_file.close()
            self._gif_file.deleteLater()
            self._gif_file = None

    def close_viewer(self):
        self._stop_media()
        self._img_lbl.clear()
        self._img_lbl.setProperty('_raw_pix', None)
        super().close_viewer()

    def _load_current(self):
        self._stop_media()
        self._text.stop_pan()
        self._zoom = 0
        self._pan = QPoint()
        self._img_lbl.clear()
        self._img_lbl.setProperty('_raw_pix', None)
        if self._current_card is None:
            return
        path = Path(self._current_card.filepath)
        extension = path.suffix.lower()
        text_document = extension not in IMAGES | VIDEOS | {'.gif'}
        self._text.setVisible(text_document)
        video = extension in VIDEOS
        animated = video or extension == '.gif'
        self._video.setVisible(video)
        self._img_lbl.setVisible(not video and not text_document)
        self._controls.setVisible(animated)
        self._timeline.setVisible(video)
        self._volume.setVisible(video)
        self._time.setVisible(video)
        self._name_lbl.setText(path.name)
        self._name_lbl.setToolTip(str(path))
        if video:
            self._player.setSource(QUrl.fromLocalFile(str(path)))
            self._player.play()
        elif extension == '.gif':
            self._gif_file = QFile(str(path), self)
            self._gif_file.open(QIODevice.ReadOnly)
            self._movie = QMovie(self._gif_file, QByteArray(b'gif'), self)
            self._movie.frameChanged.connect(self._movie_frame)
            if self._movie.isValid():
                self._movie.start()
            else:
                self._show_error('This GIF cannot be decoded.')
        elif extension in IMAGES:
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            image = reader.read()
            if image.isNull():
                self._show_error(reader.errorString())
            else:
                self._img_lbl.setProperty('_raw_pix', QPixmap.fromImage(image))
                self._scale_image()
        else:
            try:
                raw = path.read_bytes()
                encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
                self._text.setPlainText(raw.decode(encoding, errors='replace'))
                self._text.verticalScrollBar().setValue(0)
                self._text.setFocus()
            except OSError as exc:
                self._text.setPlainText(str(exc))
        self._place_widgets()

    def _show_error(self, message):
        if self._current_card:
            self._name_lbl.setText('Cannot preview · ' + Path(self._current_card.filepath).name)
            self._name_lbl.setToolTip(message + '\nRight-click to open externally.')

    def _movie_frame(self, *_):
        if self._movie:
            self._img_lbl.setProperty('_raw_pix', self._movie.currentPixmap())
            self._scale_image()
            self._place_widgets()

    def _image_area_size(self):
        width, height = super()._image_area_size()
        if hasattr(self, '_controls') and not self._controls.isHidden():
            height = max(120, height - 50)
        return width, height

    def _scale_image(self):
        pix = self._img_lbl.property('_raw_pix')
        if pix and not pix.isNull() and self._zoom:
            size = QSize(max(1, int(pix.width() * self._zoom)), max(1, int(pix.height() * self._zoom)))
            self._img_lbl.setPixmap(pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self._img_lbl.resize(size)
        else:
            super()._scale_image()

    def _place_widgets(self):
        if hasattr(self, '_text') and not self._text.isHidden():
            width = min(1000, max(220, self.width() - 180))
            height = min(720, max(120, self.height() - 150))
            left = (self.width() - width) // 2
            top = (self.height() - height) // 2
            self._text.setGeometry(left, top, width, height)
            self._close_btn.move(left + width - self._close_btn.width(), top - 40)
            self._name_lbl.setFixedSize(max(100, width - 100), 28)
            self._name_lbl.move(left + 50, top + height + 10)
            for button, x in ((self._prev_btn, left - 54), (self._next_btn, left + width + 10)):
                button.setFixedSize(36, 48)
                button.move(x, top + (height - button.height()) // 2)
            for widget in (self._name_lbl, self._prev_btn, self._next_btn, self._close_btn):
                widget.raise_()
            return
        self._prev_btn.setFixedSize(44, 80)
        self._next_btn.setFixedSize(44, 80)
        if hasattr(self, '_video') and not self._video.isHidden():
            width, height = self._image_area_size()
            size = QSize(16, 9)
            size.scale(width, height, Qt.KeepAspectRatio)
            self._img_lbl.resize(size)
        super()._place_widgets()
        if hasattr(self, '_video'):
            self._video.setGeometry(self._img_lbl.geometry())
            if self._zoom:
                self._img_lbl.move(self._img_lbl.pos() + self._pan)
            self._controls.setGeometry(max(60, (self.width() - 600) // 2), self.height() - 44, min(600, self.width() - 120), 30)
            self._name_lbl.move(self._name_lbl.x(), min(self._name_lbl.y(), self.height() - (78 if not self._controls.isHidden() else 40)))
            for widget in (self._name_lbl, self._prev_btn, self._next_btn, self._close_btn, self._controls):
                widget.raise_()

    def _position_changed(self, position):
        if not self._timeline.isSliderDown():
            self._timeline.setValue(position)
        def stamp(value):
            seconds = value // 1000
            return f'{seconds // 60}:{seconds % 60:02d}'
        self._time.setText(stamp(position) + ' / ' + stamp(self._player.duration()))

    def toggle_play(self):
        if self._movie:
            self._movie.setPaused(self._movie.state() == QMovie.Running)
        elif self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self.toggle_play()
        else:
            super().keyPressEvent(event)

    def set_zoom(self, value):
        self._zoom = value
        self._pan = QPoint()
        self._scale_image()
        self._place_widgets()

    def wheelEvent(self, event):
        if self._img_lbl.property('_raw_pix'):
            self.set_zoom(min(8, max(.05, (self._zoom or 1) * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15))))
            event.accept()

    def mousePressEvent(self, event):
        if self._zoom and self._img_lbl.geometry().contains(event.position().toPoint()) and event.button() == Qt.LeftButton:
            self._pan_origin = event.position().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan_origin is not None and event.buttons() & Qt.LeftButton:
            point = event.position().toPoint()
            self._pan += point - self._pan_origin
            self._pan_origin = point
            self._place_widgets()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._pan_origin = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        if self._current_card is None:
            return
        path = Path(self._current_card.filepath)
        menu = QMenu(self)
        owner = self.parent().window()
        menu.addAction('Add to Shelf', lambda: owner.shelf.add_paths([path]))
        menu.addAction('Fit image', lambda: self.set_zoom(0))
        menu.addAction('Actual size', lambda: self.set_zoom(1))
        menu.addAction('Open externally', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        menu.exec(event.globalPos())


class _BulkLoadOverlay(QWidget):
    BLUR_RADIUS = 22

    def __init__(self, parent, on_cancel):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._blurred_bg = None
        self.hide()

        self._panel = QWidget(self)
        self._panel.setObjectName("bulkLoadPanel")
        panel_lay = QVBoxLayout(self._panel)
        panel_lay.setContentsMargins(30, 22, 30, 22)
        panel_lay.setSpacing(10)

        self.progress = QProgressBar()
        self.progress.setObjectName("bulkLoadProgress")
        self.progress.setFixedWidth(280)
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        panel_lay.addWidget(self.progress, 0, Qt.AlignmentFlag.AlignHCenter)

        # Bold/bright "[done / total]" readout — deliberately its own,
        # more prominent label sitting at the very top of the text block.
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("bulkLoadCount")
        self.count_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel_lay.addWidget(self.count_lbl)

        self.detail_lbl = QLabel("")
        self.detail_lbl.setObjectName("bulkLoadDetail")
        self.detail_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_lbl.setWordWrap(True)
        panel_lay.addWidget(self.detail_lbl)

        self.cancel_btn = QToolButton()
        self.cancel_btn.setObjectName("iconButton")
        self.cancel_btn.setText("Cancel load")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(on_cancel)
        panel_lay.addWidget(self.cancel_btn, 0, Qt.AlignmentFlag.AlignHCenter)

    def set_progress(self, done, total, detail_text):
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(done, total))
            self.count_lbl.setText(f"{done} / {total}")
            self.count_lbl.show()
        else:
            self.progress.setRange(0, 0)
            self.count_lbl.hide()
        self.detail_lbl.setText(detail_text)
        self._place_panel()

    def show_overlay(self):
        self.resize(self.parent().size())
        self._capture_blurred_background()
        self._place_panel()
        self.show()
        self.raise_()

    def hide_overlay(self):
        self.hide()
        self._blurred_bg = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_panel()

    def _place_panel(self):
        self._panel.adjustSize()
        pw, ph = self._panel.width(), self._panel.height()
        self._panel.move((self.width() - pw) // 2, (self.height() - ph) // 2)

    def _capture_blurred_background(self):
        """Grab whatever's currently behind this overlay (the whole app
        content) and blur it, so the backdrop itself reads as held-up
        rather than needing a separate panel to carry that meaning."""
        self.hide()
        snapshot = self.parent().grab()
        self.show()
        if snapshot.isNull():
            self._blurred_bg = None
            return
        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(snapshot)
        blur = QGraphicsBlurEffect()
        blur.setBlurRadius(self.BLUR_RADIUS)
        item.setGraphicsEffect(blur)
        scene.addItem(item)
        result = QPixmap(snapshot.size())
        result.fill(Qt.transparent)
        p = QPainter(result)
        scene.render(p)
        p.end()
        self._blurred_bg = result

    def paintEvent(self, event):
        p = QPainter(self)
        if self._blurred_bg is not None:
            p.drawPixmap(0, 0, self._blurred_bg)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        p.end()


# ---------------------------------------------------------------------------
# Generic dimming/blur backdrop reused behind the Settings and Hotkeys
# dialogs -- visually identical treatment to _BulkLoadOverlay above (same
# blurred snapshot + dark fill), just without any panel/progress content of
# its own, so it reads the same "you can't interact with the app right now"
# way a bulk folder load does.
# ---------------------------------------------------------------------------
class _ModalDimOverlay(QWidget):
    BLUR_RADIUS = 22

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._blurred_bg = None
        self.hide()

    def show_overlay(self):
        self.resize(self.parent().size())
        self._capture_blurred_background()
        self.show()
        self.raise_()

    def hide_overlay(self):
        self.hide()
        self._blurred_bg = None

    def _capture_blurred_background(self):
        self.hide()
        snapshot = self.parent().grab()
        self.show()
        if snapshot.isNull():
            self._blurred_bg = None
            return
        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(snapshot)
        blur = QGraphicsBlurEffect()
        blur.setBlurRadius(self.BLUR_RADIUS)
        item.setGraphicsEffect(blur)
        scene.addItem(item)
        result = QPixmap(snapshot.size())
        result.fill(Qt.transparent)
        p = QPainter(result)
        scene.render(p)
        p.end()
        self._blurred_bg = result

    def paintEvent(self, event):
        p = QPainter(self)
        if self._blurred_bg is not None:
            p.drawPixmap(0, 0, self._blurred_bg)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        p.end()


# ---------------------------------------------------------------------------
# Small red "count" badge used to show how many posts are currently queued
# (spec: Queue count indicator). Draws itself as a perfect circle for
# single-digit counts and widens into a pill/stadium shape once the number
# needs more horizontal room (double/triple digits+), so the digits never
# get squeezed into an undersized circle.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Center — Image Browser. A single, persistent, global instance shared by
# every workflow: one tab per configured top-level folder (always fully
# recursive), lazy scanning off the UI thread, and an in-memory-only
# thumbnail cache. Switching the active workflow never touches this widget
# — only the roster below it.
# ---------------------------------------------------------------------------
class ImageBrowser(QWidget):
    thumbnailClicked = Signal(str)   # filepath — MainWindow feeds this to the armed roster slot

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setObjectName("imageBrowserPanel")
        self.thread_pool = QThreadPool.globalInstance()
        self._closed_this_session = set()   # paths ×'d away — in-memory only, cleared on relaunch

        # Deliberately never loaded from (or saved to) config_data: the
        # explorer always starts on the first tab with nothing expanded,
        # regardless of whatever state it was left in last session. These
        # still track expand/tab state during the current run (e.g. so
        # reload_folders() can keep the same tab open after an in-session
        # Settings change), they just don't persist across restarts.
        self.saved_expanded_paths = set()
        self._saved_active_tab = None
        self._top_sections = []   # top-level FolderSection per tab index, in tab order

        # The folder most recently opened by the user (click or the
        # Ctrl+Up/Down hotkeys) -- what "current folder" means for
        # navigate_sibling_folder(), and what gets auto-scrolled to the top
        # of the viewport. See FolderSection.set_expanded / focus_folder_section.
        self._focused_path = None

        # -- multi-select folders (ctrl+click / rubber-band drag) + bulk
        # "Load Selected Folders" background loading -----------------------
        self._sections_by_path = {}       # path -> FolderSection, every depth, current tabs only
        self._selected_folder_paths = set()
        self._rb_base_selection = set()   # selection snapshot at the start of the current drag
        self.bulk_loader = BulkFolderLoadManager(self)
        self.bulk_loader.started.connect(self._on_bulk_load_started)
        self.bulk_loader.statusChanged.connect(self._on_bulk_load_status)
        self.bulk_loader.finished.connect(self._on_bulk_load_finished)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("browserTabs")
        self.tabs.setDocumentMode(True)
        # Qt's Fusion style draws its own native "tab bar base" line under
        # the tab bar regardless of any ::pane border in the stylesheet --
        # that's the stray straight line that used to run the full width
        # of the panel above the tab strip (and the settings icon), with
        # hard corners that didn't match the tab strip's own rounded pill.
        # Turning it off leaves only our own styled pill border.
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Settings row: floats *over* the top-right corner of the tab strip
        # instead of occupying its own full-width row above it -- that used
        # to leave a whole empty bar sitting on top of the tabs. It's a
        # free-floating child of the ImageBrowser itself (positioned/raised
        # in resizeEvent / showEvent below) so it stays reachable even when
        # the tabs/empty-hint stack underneath has zero folders configured
        # and hides itself.
        status_bar = QWidget(self)
        status_bar.setObjectName("browserStatusBar")
        status_lay = QHBoxLayout(status_bar)
        # A small, deliberate gap from the true right edge -- enough that
        # the icon doesn't feel glued to the corner, without stranding it
        # out in empty space (that was the overcorrection last time: a
        # full icon-width gap read as too much on its own).
        status_lay.setContentsMargins(0, 0, 8, 0)
        status_lay.setSpacing(2)

        # The Settings button used to live here; it now lives at the
        # bottom-left of the sorting sidebar instead (see WorkflowSidebar),
        # so this row only houses the restore-hidden-folders button now.
        self.restore_hidden_btn = QToolButton()
        self.restore_hidden_btn.setObjectName("iconButton")
        self.restore_hidden_btn.setText("\u21bb")
        self.restore_hidden_btn.setPopupMode(QToolButton.InstantPopup)
        self.restore_hidden_btn.setCursor(Qt.PointingHandCursor)
        self.restore_hidden_btn.hide()
        status_lay.addWidget(self.restore_hidden_btn)

        # Reload button — always the rightmost item in the bar. Re-scans
        # just the currently open (active-tab) top-level folder from disk,
        # without touching any other configured folder's state.
        self.reload_folder_btn = QToolButton()
        self.reload_folder_btn.setObjectName("browserSettingsBtn")
        self.reload_folder_btn.setText("\u27f3")
        self.reload_folder_btn.setToolTip("Reload the current folder")
        self.reload_folder_btn.setCursor(Qt.PointingHandCursor)
        self.reload_folder_btn.clicked.connect(self.reload_current_folder)
        self.reload_folder_btn.hide()
        status_lay.addWidget(self.reload_folder_btn)

        self._status_bar = status_bar
        status_bar.adjustSize()
        status_bar.raise_()

        layout.addWidget(self.tabs, 1)

        # Dimming/blur overlay for "Load Selected Folders" bulk scans.
        # Parented to the app's content root (not just this panel) so the
        # blur covers the whole app -- sidebars, roster, everything --
        # rather than just the image browser's own rectangle.
        self.bulk_overlay = _BulkLoadOverlay(main_window._content_root, self.bulk_loader.cancel)

        self.empty_hint = QLabel(
            "No folders configured yet.\n"
            "Open the \u2699 Settings button (bottom-left of the sorting "
            "workspace) \u2192 Folders to add one."
        )
        self.empty_hint.setObjectName("hint")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty_hint, 1)

        self.reload_folders()
        self._position_status_bar()

    def folder_display_name(self, path):
        name = self.main_window.config.get('source_names', {}).get(str(normalized(path)), '')
        return name or Path(path).name or str(path)

    def ignore_folder_patterns(self):
        return self.main_window.config.get("ignored", [])

    # -- folder registry (every FolderSection currently built, any depth) --
    def register_section(self, section):
        self._sections_by_path[section.path] = section

    def section_for_path(self, path):
        return self._sections_by_path.get(path)

    # -- "current folder" focus (Ctrl+Up/Down + auto-scroll-into-view) -----
    def focus_folder_section(self, section):
        """Called by FolderSection.set_expanded() whenever a folder is
        opened by the user, whether via a mouse click or the Ctrl+Up/Down
        hotkeys. Remembers it as the "current" folder for
        navigate_sibling_folder(), and scrolls the active tab so the
        folder's header lands at the very top of the viewport, since the
        user almost certainly wants to interact with what they just
        opened. Deferred one event-loop tick because expanding a folder
        changes the layout (new cards/children appear) after this call
        returns, and the scroll target needs the settled geometry."""
        self._focused_path = section.path
        self.focus_path(section.path)
        QTimer.singleShot(0, lambda s=section: self._scroll_section_into_view(s))

    def _scroll_section_into_view(self, section):
        if not isValid(self) or not isValid(section):
            return
        scroll = self.tabs.currentWidget()
        if not isinstance(scroll, QScrollArea):
            return
        if not scroll.isAncestorOf(section):
            return
        container = scroll.widget()
        if container is None:
            return
        top_left = section.header.mapTo(container, QPoint(0, 0))
        bar = scroll.verticalScrollBar()
        bar.setValue(max(0, min(top_left.y(), bar.maximum())))

    def _is_in_active_tab(self, section):
        scroll = self.tabs.currentWidget()
        return scroll is not None and scroll.isAncestorOf(section)

    def _active_tab_root_section(self):
        idx = self.tabs.currentIndex()
        if 0 <= idx < len(self._top_sections):
            return self._top_sections[idx]
        return None

    def navigate_sibling_folder(self, direction):
        """Ctrl+Up (direction=-1) / Ctrl+Down (direction=+1): close the
        current folder and open its sibling above/below it -- the other
        folder at the same depth, under the same parent, in the active
        tab. Wraps around at either end of that sibling group; never
        crosses into folders that share a different parent, even if
        they're at the same depth."""
        section = None
        if self._focused_path:
            candidate = self._sections_by_path.get(self._focused_path)
            if (
                candidate is not None
                and candidate.is_expanded()
                and self._is_in_active_tab(candidate)
            ):
                section = candidate
        if section is None:
            # Nothing explicitly focused yet this session -- fall back to
            # the active tab's root folder, if it's open.
            root = self._active_tab_root_section()
            if root is not None and root.is_expanded():
                section = root
        if section is None:
            return

        parent = section.parent_section
        siblings = parent._child_sections if parent is not None else [section]
        if section not in siblings or len(siblings) < 2:
            return
        idx = siblings.index(section)
        target = siblings[(idx + direction) % len(siblings)]
        section.set_expanded(False)
        target.set_expanded(True)

    # -- multi-select (ctrl+click / rubber-band drag) -----------------------
    def selected_folder_paths(self):
        return set(self._selected_folder_paths)

    def has_folder_selection(self):
        return bool(self._selected_folder_paths)

    def toggle_folder_selection(self, path):
        section = self._sections_by_path.get(path)
        if section is None:
            return
        if path in self._selected_folder_paths:
            self._selected_folder_paths.discard(path)
            section.set_selected(False)
        else:
            self._selected_folder_paths.add(path)
            section.set_selected(True)

    def clear_folder_selection(self):
        for path in list(self._selected_folder_paths):
            section = self._sections_by_path.get(path)
            if section is not None:
                section.set_selected(False)
        self._selected_folder_paths.clear()

    def set_folder_selection(self, paths):
        paths = set(paths)
        if paths == self._selected_folder_paths:
            return
        for path, section in self._sections_by_path.items():
            section.set_selected(path in paths)
        self._selected_folder_paths = paths

    def selected_folder_sections(self):
        return [
            self._sections_by_path[p]
            for p in self._selected_folder_paths
            if p in self._sections_by_path
        ]

    # -- rubber-band drag-select (driven by _RubberBandArea) ----------------
    def begin_rubber_band_drag(self, additive):
        if not additive:
            self.clear_folder_selection()
            self._selected_file_paths.clear()
        self._rb_file_selection = set(self._selected_file_paths)
        self._rb_base_selection = set(self._selected_folder_paths)
        self.repaint_content()

    def update_rubber_band_drag(self, global_rect):
        hits = self._sections_intersecting_global_rect(global_rect)
        self.set_folder_selection(self._rb_base_selection | hits)
        file_hits = {card.filepath for card in self.visible_cards()
                     if QRect(card.mapToGlobal(QPoint()), card.size()).intersects(global_rect)}
        self._selected_file_paths = self._rb_file_selection | file_hits
        self.repaint_content()

    def end_rubber_band_drag(self):
        self._rb_base_selection = set()

    def _sections_intersecting_global_rect(self, global_rect):
        current_scroll = self.tabs.currentWidget()
        hits = set()
        for path, section in self._sections_by_path.items():
            if not section.isVisible():
                continue
            if current_scroll is not None and not current_scroll.isAncestorOf(section):
                continue
            top_left = section.header.mapToGlobal(QPoint(0, 0))
            rect = QRect(top_left, section.header.size())
            if rect.intersects(global_rect):
                hits.add(path)
        return hits

    # -- right-click context menu on a folder header -------------------------
    def show_folder_context_menu(self, origin_section, global_pos):
        selected_paths = self.selected_folder_paths()
        if not selected_paths:
            return
        menu = QMenu(origin_section)
        n = len(selected_paths)
        label = (
            "Load This Folder (Full, Recursively)"
            if n == 1 else
            f"Load {n} Selected Folders (Full, Recursively)"
        )
        load_action = menu.addAction(label)
        load_action.triggered.connect(lambda: self.start_bulk_load(list(selected_paths)))
        menu.addSeparator()
        clear_action = menu.addAction("Clear Selection")
        clear_action.triggered.connect(self.clear_folder_selection)
        menu.exec(global_pos)

    # -- "Load Selected Folders" bulk background load -----------------------
    def start_bulk_load(self, paths):
        self.bulk_loader.start(paths)

    def _on_bulk_load_started(self):
        self.bulk_overlay.show_overlay()

    def _on_bulk_load_status(self, done, total, text):
        self.bulk_overlay.set_progress(done, total, text)

    def _on_bulk_load_finished(self):
        # Leave the summary text ("Loaded N folders, M images...") up for a
        # few seconds so the user actually sees it, then quietly dismiss
        # the overlay.
        QTimer.singleShot(2500, self._hide_bulk_status_if_idle)

    def _hide_bulk_status_if_idle(self):
        if not self.bulk_loader.active:
            self.bulk_overlay.hide_overlay()

    # -- (re)building tabs from Settings -----------------------------------
    def reload_folders(self):
        prev_active = self._current_tab_path() or self._saved_active_tab
        for section in self._sections_by_path.values():
            section._retired = True
        old_pages = [self.tabs.widget(i) for i in range(self.tabs.count())]
        self.tabs.clear()
        for page in old_pages:
            page.deleteLater()
        self._top_sections = []
        self._sections_by_path = {}
        self._selected_folder_paths = set()
        # A reload means the configured folder set (or ignore patterns)
        # changed, or the user explicitly asked to restore a hidden
        # folder — either way it should reflect what's on disk *now*, not
        # whatever an earlier scan (interactive or bulk) cached. Any
        # in-flight bulk load is scanning against a tab tree that's about
        # to be discarded anyway, so stop it too.
        self.bulk_loader.cancel()
        _ROUGH_SCAN_CACHE.clear()
        folders = self.configured_folders()
        configured_paths = {f.get("path", "") for f in folders}
        self._closed_this_session &= configured_paths

        restore_index = 0
        for folder_cfg in folders:
            path = folder_cfg.get("path", "")
            if path in self._closed_this_session:
                continue

            scroll = _AutopanScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            section = FolderSection(self, path, depth=0, closable=False)
            wrapper = _RubberBandArea(self)
            wrapper_lay = QVBoxLayout(wrapper)
            wrapper_lay.setContentsMargins(12, 12, 12, 12)
            wrapper_lay.addWidget(section)
            wrapper_lay.addStretch(1)
            scroll.setWidget(wrapper)

            name = self.folder_display_name(path)
            self.tabs.addTab(scroll, name)
            self.tabs.setTabToolTip(self.tabs.count() - 1, path)
            self._top_sections.append(section)
            if path == prev_active:
                restore_index = self.tabs.count() - 1
            if path in self.saved_expanded_paths:
                section.set_expanded(True, persist=False)

        if self.tabs.count() > 0:
            self.tabs.setCurrentIndex(restore_index)
            self._trigger_rough_pass_for_tab(restore_index)

        self._update_empty_state()
        self._refresh_restore_hidden_button()

    # -- reload button: re-scan just the current tab's folder --------------
    def reload_current_folder(self):
        path = self._current_tab_path()
        if path:
            self._reload_folder_tab(path)

    def _reload_folder_tab(self, path):
        index = next(
            (i for i, section in enumerate(self._top_sections) if section.path == path),
            None,
        )
        if index is None:
            return

        # Drop cached scan results for this folder and everything nested
        # under it, so the rebuild below reflects what's on disk right
        # now instead of a stale rough-scan cache entry.
        prefix = path.rstrip(os.sep) + os.sep
        for cached_path in [p for p in _ROUGH_SCAN_CACHE if p == path or p.startswith(prefix)]:
            del _ROUGH_SCAN_CACHE[cached_path]
        for cached_path in list(_PIXMAP_CACHE):
            if cached_path.startswith(prefix):
                _PIXMAP_CACHE.pop(cached_path, None)
        self._purge_section_registry(path)

        old_section = self._top_sections[index]

        scroll = _AutopanScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        section = FolderSection(self, path, depth=0, closable=False)
        wrapper = _RubberBandArea(self)
        wrapper_lay = QVBoxLayout(wrapper)
        wrapper_lay.setContentsMargins(12, 12, 12, 12)
        wrapper_lay.addWidget(section)
        wrapper_lay.addStretch(1)
        scroll.setWidget(wrapper)

        name = self.folder_display_name(path)
        old_page = self.tabs.widget(index)
        self.tabs.removeTab(index)
        old_page.deleteLater()
        self.tabs.insertTab(index, scroll, name)
        self.tabs.setTabToolTip(index, path)
        self._top_sections[index] = section
        self.tabs.setCurrentIndex(index)
        old_section.deleteLater()

        if path in self.saved_expanded_paths:
            section.set_expanded(True, persist=False)

        self._trigger_rough_pass_for_tab(index)

    def _update_empty_state(self):
        has_tabs = self.tabs.count() > 0
        self.tabs.setVisible(has_tabs)
        self.empty_hint.setVisible(not has_tabs)
        self.reload_folder_btn.setVisible(has_tabs)
        if not has_tabs:
            if self._closed_this_session:
                self.empty_hint.setText(
                    "All configured folders are hidden for this session.\n"
                    "Click \u21bb above to restore them."
                )
            else:
                self.empty_hint.setText(
                    "No folders configured yet.\n"
                    "Open the \u2699 Settings button (bottom-left of the "
                    "sorting workspace) \u2192 Folders to add one."
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
            name = self.folder_display_name(path)
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
        if 0 <= index < len(self._top_sections):
            self._top_sections[index].ensure_rough_pass()

    def close_tab_for_session(self, path):
        self._closed_this_session.add(path)
        for i, section in enumerate(self._top_sections):
            if section.path == path:
                self.tabs.removeTab(i)
                del self._top_sections[i]
                self._purge_section_registry(path)
                section.deleteLater()
                break
        self._update_empty_state()
        self._refresh_restore_hidden_button()

    def _purge_section_registry(self, root_path):
        """Drop `root_path` and everything nested under it from the folder
        registry/multi-selection when its tab is closed for the session, so
        a stale reference to an now-orphaned FolderSection can't linger in
        a later rubber-band selection or "Load Selected Folders" context
        menu (it's still on disk, just no longer shown)."""
        prefix = root_path.rstrip(os.sep) + os.sep
        stale = [p for p in self._sections_by_path if p == root_path or p.startswith(prefix)]
        for p in stale:
            self._sections_by_path[p]._retired = True
            del self._sections_by_path[p]
            self._selected_folder_paths.discard(p)

    def note_expanded(self, path, expanded):
        if expanded:
            self.saved_expanded_paths.add(path)
        else:
            self.saved_expanded_paths.discard(path)

    def _current_tab_path(self):
        idx = self.tabs.currentIndex()
        if idx < 0:
            return None
        tooltip = self.tabs.tabToolTip(idx)
        return tooltip or None

    def _on_tab_changed(self, index):
        self._trigger_rough_pass_for_tab(index)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_status_bar()
        for section in getattr(self, "_sections_by_path", {}).values():
            if section._expanded and section._cards:
                QTimer.singleShot(0, section._relayout_cards)

    def _position_status_bar(self):
        """Float the settings row over the top-right corner of the tab
        strip instead of it occupying a separate row of its own."""
        bar = getattr(self, "_status_bar", None)
        if bar is None:
            return
        bar.adjustSize()
        tab_bar_height = self.tabs.tabBar().sizeHint().height()
        y = max(0, (tab_bar_height - bar.height()) // 2)
        bar.move(max(0, self.width() - bar.width()), y)
        bar.raise_()


# ---------------------------------------------------------------------------
# Bottom bar — the "input roster" (spec section 2.3), plus the run/queue
# controls and optional-parameter form for whichever workflow is active.
# Only this bar changes when the active workflow changes; it never resets
# the Image Browser above it.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Plain QScrollArea that emits a signal on resize, used by RosterBar to know
# exactly when its viewport height changes so the roster icons can be
# rescaled to fill it (see RosterBar._sync_icon_size).
# ---------------------------------------------------------------------------

# SESSION SHELF AND MEDIA VIEWER
# ========================================================================
class CoverBrowser(ImageBrowser):
    """Cover's original explorer, with Sorter's file-action hooks."""

    def __init__(self, window, editable=False, title='Library'):
        self.window = window
        self.editable = editable
        self.current = None
        self._location = None
        self._selected_file_paths = set()
        self._selection_anchor = None
        self._closing = False
        self._filter_map = None
        self._filter_task = None
        self._filter_serial = 0
        self._details = False
        self.entry_zoom = 100
        super().__init__(window)
        if not PIXMAP_WORKER.isRunning():
            PIXMAP_WORKER._stop = False
            PIXMAP_WORKER.start()
        self.path = QLineEdit(self)
        self.path.hide()
        self.filter_panel = QWidget()
        filter_layout = QHBoxLayout(self.filter_panel)
        filter_layout.setContentsMargins(4, 2, 4, 6)
        filter_layout.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search folders and files…')
        self.search.setClearButtonEnabled(True)
        filter_layout.addWidget(self.search, 1)
        self.kind = QComboBox()
        self.kind.addItems(['All types', 'Image', 'GIF', 'Video', 'Folder', 'Other'])
        filter_layout.addWidget(self.kind)
        self.extension = QLineEdit()
        self.extension.setPlaceholderText('png, jpg…')
        self.extension.setMaximumWidth(85)
        filter_layout.addWidget(self.extension)
        self.recursive = QCheckBox('Subfolders')
        self.recursive.setChecked(True)
        filter_layout.addWidget(self.recursive)
        self.layout().insertWidget(0, self.filter_panel)
        self.filter_panel.hide()
        self.search_button = QToolButton()
        self.search_button.setObjectName('browserSettingsBtn')
        self.search_button.setIcon(_make_search_icon())
        self.search_button.setToolTip('Search and filters · Ctrl+F')
        self.search_button.setCheckable(True)
        self.search_button.toggled.connect(self.toggle_search)
        self._status_bar.layout().insertWidget(0, self.search_button)
        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(180)
        self.filter_timer.timeout.connect(self.filter_rows)
        for signal in (self.search.textChanged, self.extension.textChanged, self.kind.currentIndexChanged, self.recursive.toggled):
            signal.connect(lambda *_: self.filter_timer.start())
        shortcut = QShortcut(QKeySequence('Ctrl+F'), self)
        shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        shortcut.activated.connect(lambda: (self.search_button.setChecked(True), self.search.setFocus()))
        self.folder_icon = QIcon(str(APP_DIR / 'Folder.png'))
        if self.folder_icon.isNull():
            self.folder_icon = self.style().standardIcon(QStyle.SP_DirIcon)
        self.model = MediaModel(self)
        self.proxy = MediaFilter(self)
        self.proxy.setSourceModel(self.model)
        self.table = DetailView()
        self.grid = self.table
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(3, 140)
        self.table.setColumnWidth(4, 260)
        self.table.setup_drag(self)
        self.layout().addWidget(self.table, 1)
        self.table.hide()
        self.reload_folder_btn.show()
        self.empty_hint.setText('No folders configured yet.\nOpen Settings in the sorting workspace below to add source folders.')
        for key, callback in [('Ctrl+Up', lambda: self.navigate_sibling_folder(-1)), ('Ctrl+Down', lambda: self.navigate_sibling_folder(1)), ('Ctrl+C', lambda: window.copy_selection(self, False)), ('Ctrl+A', self.select_all), ('F5', self.refresh)]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda cb=callback: cb() if not isinstance(QApplication.focusWidget(), (QLineEdit, QPlainTextEdit)) else None)
        if editable:
            for key, callback in [('Ctrl+X', lambda: window.copy_selection(self, True)), ('Ctrl+V', lambda: window.paste(self)), ('Delete', lambda: window.trash_selection(self)), ('F2', lambda: window.rename_selection(self)), ('Ctrl+Shift+N', lambda: window.new_folders(self)), ('Alt+Up', self.up)]:
                shortcut = QShortcut(QKeySequence(key), self)
                shortcut.setContext(Qt.WidgetWithChildrenShortcut)
                shortcut.activated.connect(lambda cb=callback: cb() if not isinstance(QApplication.focusWidget(), (QLineEdit, QPlainTextEdit)) else None)
            self.setAcceptDrops(True)
        self.tools_btn = QToolButton()
        self.tools_btn.setObjectName('browserSettingsBtn')
        self.tools_btn.setText('⋯')
        self.tools_btn.setToolTip('Explorer options')
        self.tools_btn.clicked.connect(self.options_menu)
        self._status_bar.layout().insertWidget(0, self.tools_btn)
        self._position_status_bar()

    def options_menu(self):
        menu = QMenu(self)
        if self.editable:
            menu.addAction('Group Notes', lambda: self.window.show_notes(True))
            menu.addSeparator()
        menu.addAction('Select all visible items', self.select_all)
        menu.addAction('Invert visible selection', self.invert_selection)
        zoom = menu.addMenu('Entry zoom')
        for percent in (50, 75, 100, 125, 150, 200):
            action = zoom.addAction(f'{percent}%')
            action.setCheckable(True)
            action.setChecked(percent == self.entry_zoom)
            action.triggered.connect(lambda checked=False, value=percent: self.set_entry_zoom(value))
        menu.exec(self.tools_btn.mapToGlobal(QPoint(0, self.tools_btn.height())))

    def set_entry_zoom(self, percent):
        self.entry_zoom = max(50, min(200, int(percent)))
        for section in self._sections_by_path.values():
            for card in section._cards:
                card.set_entry_zoom(self.entry_zoom)
            section._current_cols = 0
            section._relayout_cards()

    def toggle_search(self, visible):
        self.filter_panel.setVisible(visible)
        if visible:
            self.search.setFocus()
        self._position_status_bar()

    def _position_status_bar(self):
        super()._position_status_bar()
        if getattr(self, 'filter_panel', None) and self.filter_panel.isVisible():
            self._status_bar.move(self._status_bar.x(), self._status_bar.y() + self.filter_panel.height() + self.layout().spacing())

    def active_filter(self):
        return bool(self.search.text().strip() or self.extension.text().strip() or self.kind.currentIndex())

    def set_details(self, enabled):
        self._filter_serial += 1
        if self._filter_task:
            self._filter_task.cancel.set()
        self._details = enabled
        self.tabs.setVisible(not enabled and bool(self.tabs.count()))
        self.table.setVisible(enabled)
        self.empty_hint.hide() if enabled else self._update_empty_state()
        if enabled:
            self.filter_rows()
        else:
            self._filter_map = None
            self.reload_folders()
            if self.active_filter():
                self.filter_rows()

    def context_menu(self, view, point):
        self.action_menu(view.viewport().mapToGlobal(point))

    def open_index(self, index):
        entry = index.data(Qt.UserRole)
        if entry:
            if entry.is_dir:
                self.navigate(entry.path)
            else:
                self.window.preview(entry.path)

    def _update_empty_state(self):
        super()._update_empty_state()
        if not self.tabs.count() and not self._closed_this_session:
            self.empty_hint.setText('No sorting folder configured.\nOpen Settings below to choose one.' if self.editable else 'No source folders configured.\nOpen Settings in the sorting workspace below to add folders.')
        if getattr(self, '_details', False):
            self.tabs.hide()
            self.empty_hint.hide()

    def accept_drop(self, event):
        if self.editable and event.mimeData().hasUrls():
            event.setDropAction(Qt.CopyAction)
            event.accept()
            paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
            move = (bytes(event.mimeData().data('application/x-vael-sorter')) == b'workspace') and paths and all(self.window.service.inside(p) for p in paths) and not event.modifiers() & Qt.ControlModifier
            self.window.operation_status.setText('Move into sorting · hold Ctrl to copy' if move else 'Copy into sorting')

    def drop_into(self, event, destination=None):
        if not self.editable:
            event.ignore()
            return
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        move = (bytes(event.mimeData().data('application/x-vael-sorter')) == b'workspace') and bool(paths) and all(self.window.service.inside(p) for p in paths) and not event.modifiers() & Qt.ControlModifier
        self.window.transfer(paths, Path(destination) if destination else self.current, bool(move))
        event.setDropAction(Qt.CopyAction)
        event.accept()

    def dragEnterEvent(self, event):
        self.accept_drop(event)

    def dragMoveEvent(self, event):
        self.accept_drop(event)

    def dropEvent(self, event):
        self.drop_into(event)

    def up(self):
        if self.current and self.current != self.window.service.root:
            self.navigate(self.current.parent)

    def configured_folders(self):
        if self.editable:
            root = self.window.config['sort_root']
            if not root:
                return []
            location = self._location if self._location and self.window.service.inside(self._location, True) and self._location.is_dir() else Path(root)
            return [{'path': p} for p in dict.fromkeys([str(location), *[p for p in self.window.config['pins'] if self.window.service.inside(p, True) and Path(p).is_dir()]])]
        paths = self.window.config['sources']
        if self._location and str(self._location) not in paths:
            paths = paths + [str(self._location)]
        return [{'path': str(normalized(p)), 'display_name': self.window.config.get('source_names', {}).get(str(normalized(p)), '')} for p in paths]

    def focus_path(self, path):
        self.current = Path(path)
        self.window.active_browser = self
        if hasattr(self, 'path'):
            self.path.setText(str(path))

    def _on_tab_changed(self, index):
        self._selected_file_paths.clear()
        super()._on_tab_changed(index)
        path = self._current_tab_path()
        if path:
            self.focus_path(path)

    def update_roots(self):
        self._filter_serial += 1
        if self._filter_task:
            self._filter_task.cancel.set()
        self._filter_map = None
        self.reload_folders()
        if hasattr(self, 'search') and (self.active_filter() or self._details):
            self.filter_rows()

    def refresh(self, *_):
        files = set(self._selected_file_paths)
        folders = set(self._selected_folder_paths)
        if self.active_filter():
            self.filter_rows()
        else:
            self.reload_current_folder()
        self._selected_file_paths = files
        self.set_folder_selection(folders)
        self.repaint_content()

    def navigate(self, path, remember=True):
        target = normalized(path) if path else None
        if self.editable and (target is None or not self.window.service.inside(target, True)):
            self.window.notice('Choose a folder inside the configured sorting folder.')
            return
        if target and not target.is_dir():
            self.window.notice('This folder is unavailable.')
            return
        self._filter_serial += 1
        if self._filter_task:
            self._filter_task.cancel.set()
        self._filter_map = None
        self._location = target
        if target:
            self._saved_active_tab = str(target)
        self.reload_folders()
        if target:
            for index, section in enumerate(self._top_sections):
                if section.path == str(target):
                    self.tabs.setCurrentIndex(index)
                    break
        if self.active_filter() or self._details:
            self.filter_rows()

    def selected_paths(self):
        if self._details:
            return [i.data(Qt.UserRole).path for i in self.table.selectionModel().selectedIndexes() if i.column() == 0]
        return [Path(p) for p in sorted(self._selected_file_paths | set(self.selected_folder_paths()))]

    def select_card(self, card, modifiers=Qt.ControlModifier):
        self.window.active_browser = self
        self.clear_folder_selection()
        cards = [c for section in self._sections_by_path.values() if section.isVisible() for c in section._cards if c.isVisible()]
        if modifiers & Qt.ShiftModifier and self._selection_anchor in [c.filepath for c in cards]:
            names = [c.filepath for c in cards]
            first, last = sorted((names.index(self._selection_anchor), names.index(card.filepath)))
            self._selected_file_paths.update(names[first:last + 1])
        elif not modifiers & Qt.ControlModifier:
            self._selected_file_paths = {card.filepath}
        elif card.filepath in self._selected_file_paths:
            self._selected_file_paths.remove(card.filepath)
        else:
            self._selected_file_paths.add(card.filepath)
        self._selection_anchor = card.filepath
        self.repaint_content()

    def drag_paths(self, card):
        if card.filepath not in self._selected_file_paths:
            self._selected_file_paths = {card.filepath}
            self.clear_folder_selection()
            self.repaint_content()
        return self.selected_paths()

    def open_card(self, card, cards):
        self.window.active_browser = self
        self.window.open_image_viewer(card, cards)

    def card_menu(self, card, point):
        self.drag_paths(card)
        self.action_menu(point, card)

    def show_folder_context_menu(self, origin_section, global_pos):
        self.repaint_content()
        self.action_menu(global_pos)

    def action_menu(self, point, card=None):
        self.window.active_browser = self
        menu = self.build_action_menu()
        menu.exec(point)
        menu.deleteLater()

    def build_action_menu(self):
        paths = self.selected_paths()
        menu = QMenu(self)
        self.window.tagging.add_image_actions(menu, paths)
        folders = list(self.selected_folder_paths())
        if folders and not self.editable:
            menu.addAction('Load Selected Folders (Full, Recursively)', lambda: self.start_bulk_load(folders))
        if self.editable and len(paths) >= 2 and all(p.is_file() for p in paths):
            menu.addAction('Group selected entries', lambda: self.window.group_selection(self))
            menu.addSeparator()
        if paths:
            menu.addAction('Add to Shelf', lambda: self.window.shelf.add_paths(paths))
            menu.addAction('Copy', lambda: self.window.copy_selection(self, False))
            if not self.editable:
                menu.addAction('Copy to Active Workspace Folder', lambda: self.window.transfer(paths, self.window.target_browser().current, False))
        if self.editable:
            menu.addSeparator()
            if paths:
                menu.addAction('Cut', lambda: self.window.copy_selection(self, True))
                menu.addAction('Move to folder…', lambda: self.window.transfer_dialog(self, True))
                menu.addAction('Move into new folder…', lambda: self.window.move_to_new(self))
                menu.addAction('Duplicate', lambda: self.window.duplicate(self))
                if len(paths) == 1:
                    menu.addAction('Rename…', lambda: self.window.rename_selection(self))
                else:
                    rename = menu.addAction('Batch rename…', lambda: self.window.batch_rename(self))
                    rename.setToolTip('Rename selected items together using find/replace, prefixes, suffixes or numbering. Preview all names before applying.')
                    menu.setToolTipsVisible(True)
                menu.addAction('Delete', lambda: self.window.trash_selection(self))
            else:
                clipboard = QApplication.clipboard().mimeData()
                if clipboard and clipboard.hasUrls():
                    menu.addAction('Paste', lambda: self.window.paste(self))
                menu.addAction('New folders…', lambda: self.window.new_folders(self))
        if paths:
            menu.addSeparator()
            menu.addAction('Information…', lambda: self.window.information(paths))
            menu.addAction('Copy as Path', lambda: QApplication.clipboard().setText('\n'.join(map(str, paths))))
            if len(paths) == 1:
                folder = paths[0] if paths[0].is_dir() else paths[0].parent
                menu.addAction('Open Folder in Explorer', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))
        if paths:
            menu.addAction('Clear selection', self.clear_selection)
        return menu

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Open sorting subfolder', str(self.current or self.window.config['sort_root']))
        if folder:
            self.navigate(folder)

    def clear_selection(self):
        self._selected_file_paths.clear()
        self.clear_folder_selection()
        if hasattr(self, 'table'):
            self.table.clearSelection()
        self.repaint_content()

    def visible_cards(self):
        scroll = self.tabs.currentWidget()
        return [c for section in self._sections_by_path.values() if scroll and scroll.isAncestorOf(section) and section.isVisible() for c in section._cards if c.isVisible()]

    def select_all(self):
        if self._details:
            self.table.selectAll()
        else:
            self._selected_file_paths = {c.filepath for c in self.visible_cards()}
            self.repaint_content()

    def invert_selection(self):
        if self._details:
            from PySide6.QtCore import QItemSelectionModel, QItemSelection
            if self.proxy.rowCount():
                selection = QItemSelection(self.proxy.index(0, 0), self.proxy.index(self.proxy.rowCount() - 1, 5))
                self.table.selectionModel().select(selection, QItemSelectionModel.Toggle | QItemSelectionModel.Rows)
        else:
            self._selected_file_paths = {c.filepath for c in self.visible_cards()} - self._selected_file_paths
            self.repaint_content()

    def repaint_content(self):
        for section in self._sections_by_path.values():
            for card in section._cards:
                card._img_lbl.update()
        if hasattr(self, 'table'):
            self.table.viewport().update()

    def filter_rows(self):
        try:
            terms = parse_query(self.search.text(), self.window.config['fields'])
        except ValueError as exc:
            self.search.setToolTip(str(exc))
            return
        self._filter_serial += 1
        serial = self._filter_serial
        if self._filter_task:
            self._filter_task.cancel.set()
        self.proxy.terms = terms
        self.proxy.invalidate()
        if not self.active_filter() and not self._details:
            if self._filter_map is not None:
                self._filter_map = None
                self.reload_folders()
            self.repaint_content()
            self.search_button.setToolTip('Search and filters · Ctrl+F')
            return
        roots = [str(self.current)] if self._details and self.current else [f['path'] for f in self.configured_folders()]
        config = copy.deepcopy(self.window.config)
        recursive = self.recursive.isChecked()
        kind = self.kind.currentText()
        extensions = {part.strip().lstrip('.').lower() for part in self.extension.text().split(',') if part.strip()}
        self.search_button.setToolTip('Searching…')

        def found(result):
            if serial != self._filter_serial or self._closing:
                return
            entries, warnings = result
            self.model.set_entries(entries)
            self.proxy.invalidate()
            filtered = [e for e in entries if matches(e, terms)
                        and (kind == 'All types' or e.kind == kind)
                        and (not extensions or e.path.suffix.lower().lstrip('.') in extensions)]
            self.search_button.setToolTip(f'{len(filtered)} matches across {len(roots)} folder tabs' + ('\n' + '\n'.join(warnings[:5]) if warnings else ''))
            self.search.setToolTip(' · '.join(f'{field or "Any"}: {value}' for field, value in terms))
            if not self._details:
                mapping = {str(root): (set(), set()) for root in roots}
                for entry in filtered:
                    for root_text in roots:
                        root = Path(root_text)
                        if not entry.path.is_relative_to(root):
                            continue
                        current = entry.path if entry.is_dir else entry.path.parent
                        mapping.setdefault(str(current), (set(), set()))
                        if not entry.is_dir:
                            mapping[str(current)][0].add(str(entry.path))
                        while current != root and current.is_relative_to(root):
                            parent = current.parent
                            mapping.setdefault(str(parent), (set(), set()))[1].add(str(current))
                            current = parent
                self._filter_map = {key: (sorted(files, key=_natural_sort_key), sorted(folders, key=_natural_sort_key)) for key, (files, folders) in mapping.items()}
                self.reload_folders()
            values = sorted({f'{config["fields"][k]}:"{v}"' for e in entries for k, v in e.fields.items() if k in config['fields']})
            completer = QCompleter(values[:3000], self.search)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            self.search.setCompleter(completer)

        self._filter_task = self.window.jobs.start(lambda task: scan(roots, config, recursive, task.cancel, DATA_DIR / 'catalog.sqlite'), found,
            lambda error: self.search_button.setToolTip(error))

    def state(self):
        return {'path': str(self._location or self.current or ''), 'zoom': self.entry_zoom, 'details': self._details,
                'header': bytes(self.table.horizontalHeader().saveState().toBase64()).decode()}

    def restore(self, state):
        self.set_entry_zoom(state.get('zoom', 100))
        if self.editable and state.get('path') and Path(state['path']).is_dir() and self.window.service.inside(state['path'], True):
            self.navigate(state['path'])
        # Cover starts with the first source tab and collapsed child folders.
        if self.tabs.count():
            self.tabs.setCurrentIndex(0)
        if state.get('header'):
            self.table.horizontalHeader().restoreState(QByteArray.fromBase64(state['header'].encode()))


class WorkspaceBrowser(CoverBrowser):
    """A single-folder grid with independent selection and breadcrumb navigation."""

    def __init__(self, window, editable=True, title='Workspace'):
        super().__init__(window, True, title)
        self.tabs.hide()
        self.table.hide()
        self.recursive.setChecked(False)
        self.recursive.hide()
        self.grid = WorkspaceGrid()
        self.grid.setModel(self.proxy)
        self.grid.setModelColumn(0)
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setIconSize(QSize(96, 96))
        self.grid.setGridSize(QSize(102, 134))
        self.grid.setSpacing(0)
        self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(True)
        self.grid.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.grid.setup_drag(self)
        self.grid.setItemDelegate(WorkspaceEntryDelegate(self.grid))
        self.grid.setStyleSheet('QListView::item:selected { background: #174b42; border: 2px solid #00d4a0; border-radius: 6px; }')
        self.grid.selectionModel().selectionChanged.connect(lambda *_: self._remember_selection())
        self.layout().addWidget(self.grid, 1)
        self.navigation = QWidget()
        self.navigation_row = QHBoxLayout(self.navigation)
        self.navigation_row.setContentsMargins(4, 0, 4, 4)
        self.breadcrumbs = QHBoxLayout()
        self.navigation_row.addLayout(self.breadcrumbs, 1)
        self.navigation_row.addWidget(self._status_bar)
        self.layout().insertWidget(0, self.navigation)
        self._restoring_selection = False

    def set_entry_zoom(self, percent):
        self.entry_zoom = max(50, min(200, int(percent)))
        scale = self.entry_zoom / 100
        self.grid.setIconSize(QSize(round(96 * scale), round(96 * scale)))
        self.grid.setGridSize(QSize(round(96 * scale) + 6, round(96 * scale) + 38))
        self.grid.doItemsLayout()
        self.grid.viewport().update()

    def _position_status_bar(self):
        if not hasattr(self, 'navigation'):
            super()._position_status_bar()

    def _remember_selection(self):
        if not getattr(self, '_restoring_selection', False):
            self._selected_file_paths = {str(p) for p in self.selected_paths()}

    def selected_paths(self):
        return [i.data(Qt.UserRole).path for i in self.grid.selectionModel().selectedIndexes() if i.column() == 0]

    def selected_folder_paths(self):
        return [str(p) for p in self.selected_paths() if p.is_dir()]

    def context_menu(self, view, point):
        group = view.group_at(point)
        if group:
            menu = self.window.group_menu(group, self)
        else:
            index = view.indexAt(point)
            if not index.isValid():
                self.clear_selection()
            elif not view.selectionModel().isSelected(index):
                from PySide6.QtCore import QItemSelectionModel
                view.selectionModel().setCurrentIndex(index, QItemSelectionModel.ClearAndSelect)
            menu = self.build_action_menu()
            entry = index.data(Qt.UserRole) if index.isValid() else None
            group = group_for(self.window.config, entry.path) if entry else None
            if group:
                menu.addSeparator()
                self.window.populate_group_menu(menu, group)
        menu.exec(view.viewport().mapToGlobal(point))
        menu.deleteLater()

    def _update_empty_state(self):
        self.tabs.hide()
        self.empty_hint.setVisible(not self.current)

    def update_roots(self):
        root = self.window.service.root
        target = self.current if self.current and self.window.service.inside(self.current, True) and self.current.is_dir() else root
        self.navigate(target)

    def navigate(self, path, remember=True):
        target = normalized(path) if path else None
        if target and (not self.window.service.inside(target, True) or not target.is_dir()):
            self.window.notice('Choose an available folder inside the workspace.')
            return
        if target != self.current:
            self._selected_file_paths.clear()
        self.current = self._location = target
        self.path.setText(str(target or ''))
        self.window.active_workspace = self
        while self.breadcrumbs.count():
            item = self.breadcrumbs.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if target:
            root = self.window.service.root
            chain = [root]
            for part in target.relative_to(root).parts:
                chain.append(chain[-1] / part)
            for index, folder in enumerate(chain):
                if index:
                    self.breadcrumbs.addWidget(QLabel('›'))
                button = QToolButton()
                button.setText('Root' if index == 0 else folder.name)
                button.setToolTip(str(folder))
                button.clicked.connect(lambda checked=False, p=folder: self.navigate(p))
                self.breadcrumbs.addWidget(button)
        self.breadcrumbs.addStretch()
        self._update_empty_state()
        self.refresh()

    def reload_current_folder(self):
        self.refresh()

    def refresh(self, *_):
        self.filter_rows()

    def filter_rows(self):
        if not hasattr(self, 'navigation'):
            return
        self._filter_serial += 1
        serial = self._filter_serial
        if self._filter_task:
            self._filter_task.cancel.set()
        try:
            self.proxy.terms = parse_query(self.search.text(), self.window.config['fields'])
        except ValueError as exc:
            self.search.setToolTip(str(exc))
            return
        config = copy.deepcopy(self.window.config)
        roots = [str(self.current)] if self.current else []
        def found(result):
            if serial != self._filter_serial or self._closing:
                return
            from PySide6.QtCore import QItemSelectionModel
            selected = set(self._selected_file_paths)
            self._restoring_selection = True
            self.model.set_entries(result[0])
            self.proxy.invalidate()
            self.proxy.sort(0)
            for row in range(self.proxy.rowCount()):
                index = self.proxy.index(row, 0)
                if str(index.data(Qt.UserRole).path) in selected:
                    self.grid.selectionModel().select(index, QItemSelectionModel.Select)
            self._restoring_selection = False
            self._selected_file_paths = {str(p) for p in self.selected_paths()}
            self.search.setToolTip('\n'.join(result[1]))
        self._filter_task = self.window.jobs.start(lambda task: scan(roots, config, False, task.cancel), found,
            lambda error: self.search.setToolTip(error))

    def clear_selection(self):
        self.grid.clearSelection()
        self._selected_file_paths.clear()

    def select_all(self):
        self.grid.selectAll()

    def invert_selection(self):
        from PySide6.QtCore import QItemSelectionModel
        selection = self.grid.selectionModel()
        for row in range(self.proxy.rowCount()):
            selection.select(self.proxy.index(row, 0), QItemSelectionModel.Toggle)

    def state(self):
        return {'path': str(self.current or ''), 'zoom': self.entry_zoom}

    def restore(self, state):
        self.set_entry_zoom(state.get('zoom', 100))
        path = state.get('path')
        if path and Path(path).is_dir() and self.window.service.inside(path, True):
            self.navigate(path)


class ShelfEntryDelegate(WorkspaceEntryDelegate):
    def paint(self, painter, option, index):
        class ShelfIndex:
            def data(self, role):
                return index.data(Qt.UserRole + 1) if role == Qt.UserRole else index.data(role)
        super().paint(painter, option, ShelfIndex())


class ShelfList(QListWidget):
    def __init__(self, shelf):
        super().__init__()
        self.shelf = shelf
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        from types import SimpleNamespace
        self.browser = SimpleNamespace(window=shelf.window, entry_zoom=100)
        self.setWrapping(True)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setSpacing(0)
        self.setGridSize(QSize(102, 134))
        self.setIconSize(QSize(96, 96))
        self.setUniformItemSizes(True)
        self.setItemDelegate(ShelfEntryDelegate(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    def repaint_cards(self):
        for i in range(self.count()):
            card = self.itemWidget(self.item(i))
            if card:
                card._img_lbl.update()

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
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.toggle = QLabel('SESSION SHELF · 0')
        self.toggle.setObjectName('hint')
        row.addWidget(self.toggle)
        text = QLabel('Session references')
        text.setObjectName('hint')
        row.addWidget(text)
        row.addStretch()
        for name, callback in [('Copy to Workspace', self.copy_to_target), ('Remove', self.remove_selected), ('Clear', self.clear)]:
            button = QPushButton(name)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.list = ShelfList(self)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.menu)
        self.list.itemDoubleClicked.connect(lambda item: window.preview(Path(item.data(Qt.UserRole))))
        layout.addWidget(self.list, 1)

    def add_paths(self, paths):
        existing = {self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())}
        for path in paths:
            path = normalized(path)
            if str(path) in existing:
                continue
            item = QListWidgetItem('')
            item.setData(Qt.UserRole, str(path))
            item.setToolTip(str(path))
            self.list.addItem(item)
            self.refresh_item(item)
            existing.add(str(path))
        self.update_count()

    def refresh_item(self, item):
        from types import SimpleNamespace
        path = Path(item.data(Qt.UserRole))
        kind = 'Folder' if path.is_dir() else 'GIF' if path.suffix.lower() == '.gif' else 'Image' if path.suffix.lower() in IMAGES else 'Video' if path.suffix.lower() in VIDEOS else 'Other'
        item.setData(Qt.UserRole + 1, SimpleNamespace(path=path, name=path.name, is_dir=path.is_dir(), kind=kind))
        item.setText(path.name)
        item.setIcon(self.style().standardIcon(QStyle.SP_DirIcon if path.is_dir() else QStyle.SP_FileIcon))
        if kind in ('Image', 'GIF'):
            def decode(task):
                reader = QImageReader(str(path))
                reader.setAutoTransform(True)
                size = reader.size()
                if size.isValid():
                    size.scale(192, 192, Qt.KeepAspectRatioByExpanding)
                    reader.setScaledSize(size)
                return reader.read()
            def ready(image):
                if image.isNull():
                    return
                for i in range(self.list.count()):
                    target = self.list.item(i)
                    if target.data(Qt.UserRole) == str(path):
                        target.setIcon(QIcon(QPixmap.fromImage(image)))
            self.window.jobs.start(decode, ready)

    def update_count(self):
        self.toggle.setText(f'SESSION SHELF · {self.list.count()}')
        if hasattr(self.window, 'shelf_button'):
            self.window.shelf_button.setText(f'Shelf ({self.list.count()})' if self.list.count() else 'Shelf')

    def remove_selected(self):
        for item in self.list.selectedItems():
            card = self.list.itemWidget(item)
            if card:
                self.list.removeItemWidget(item)
                card.deleteLater()
            self.list.takeItem(self.list.row(item))
        self.update_count()

    def clear(self):
        self.list.clear()
        self.update_count()

    def copy_to_target(self):
        items = self.list.selectedItems() or [self.list.item(i) for i in range(self.list.count())]
        self.window.transfer([Path(i.data(Qt.UserRole)) for i in items], self.window.target_browser().current, False)

    def menu(self, point):
        item = self.list.itemAt(point)
        if item and not item.isSelected():
            self.list.clearSelection()
            item.setSelected(True)
        menu = QMenu(self)
        menu.addAction('Copy to Active Workspace Folder', self.copy_to_target)
        menu.addAction('Remove selected references', self.remove_selected)
        menu.addAction('Clear shelf', self.clear)
        menu.exec(self.list.viewport().mapToGlobal(point))




# ========================================================================
# SETTINGS AND BATCH RENAME DIALOGS
# ========================================================================
class CheckMenu(QMenu):
    """Keep the assignment menu open while several checkboxes are toggled."""

    def mouseReleaseEvent(self, event):
        action = self.actionAt(event.position().toPoint())
        if event.button() == Qt.LeftButton and action and action.isCheckable() and action.isEnabled():
            action.trigger()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        action = self.activeAction()
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter) and action and action.isCheckable():
            action.trigger()
            return
        super().keyPressEvent(event)


class NoteEditor(QDialog):
    def __init__(self, note, titles, parent):
        super().__init__(parent)
        self.setWindowTitle('Edit Note' if note else 'New Note')
        self.resize(440, 300)
        self.titles = titles
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Header'))
        self.header = QLineEdit(note['title'] if note else '')
        layout.addWidget(self.header)
        layout.addWidget(QLabel('Body'))
        self.body = QPlainTextEdit(note['body'] if note else '')
        layout.addWidget(self.body, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        title = self.header.text().strip()
        if not title or title.casefold() in self.titles:
            QMessageBox.warning(self, 'Header', 'Enter a unique, non-empty header.')
            return
        self.accept()


class NotesDialog(QDialog):
    def __init__(self, window, editable=True):
        super().__init__(window)
        self.setWindowTitle('Group Notes')
        self.resize(420, 380)
        self.notes = copy.deepcopy(window.config['notes'])
        layout = QVBoxLayout(self)
        hint = QLabel('Drag to reorder. Right-click a note to edit or delete it.')
        hint.setObjectName('hint')
        layout.addWidget(hint)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.menu)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        for title, callback in [('New Note', self.add_note), ('Move Up', lambda: self.move_note(-1)), ('Move Down', lambda: self.move_note(1))]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.populate()

    def populate(self, selected=None):
        self.list.clear()
        for note in self.notes:
            item = QListWidgetItem(note['title'])
            item.setData(Qt.UserRole, note['id'])
            item.setToolTip(note['body'])
            self.list.addItem(item)
            if note['id'] == selected:
                self.list.setCurrentItem(item)

    def sync_order(self):
        by_id = {note['id']: note for note in self.notes}
        self.notes = [by_id[self.list.item(i).data(Qt.UserRole)] for i in range(self.list.count())]

    def edit_note(self, key=None):
        self.sync_order()
        note = next((note for note in self.notes if note['id'] == key), None)
        titles = {n['title'].casefold() for n in self.notes if n['id'] != key}
        editor = NoteEditor(note, titles, self)
        if editor.exec() == QDialog.Accepted:
            if note is None:
                note = dict(id=uuid.uuid4().hex)
                self.notes.append(note)
            note.update(title=editor.header.text().strip(), body=editor.body.toPlainText())
            self.populate(note['id'])
        editor.deleteLater()

    def add_note(self):
        self.edit_note()

    def delete_note(self, key):
        self.sync_order()
        self.notes = [note for note in self.notes if note['id'] != key]
        self.populate()

    def move_note(self, direction):
        row = self.list.currentRow()
        target = row + direction
        if row >= 0 and 0 <= target < self.list.count():
            item = self.list.takeItem(row)
            self.list.insertItem(target, item)
            self.list.setCurrentRow(target)

    def menu(self, point):
        item = self.list.itemAt(point)
        if not item:
            return
        self.list.setCurrentItem(item)
        key = item.data(Qt.UserRole)
        menu = QMenu(self)
        menu.addAction('Edit', lambda: self.edit_note(key))
        menu.addAction('Delete', lambda: self.delete_note(key))
        menu.exec(self.list.viewport().mapToGlobal(point))
        menu.deleteLater()

    def save(self):
        self.sync_order()
        self.accept()


class SettingsDialog(QDialog):
    def __init__(self, config, parent):
        super().__init__(parent)
        self.config = copy.deepcopy(config)
        self.setWindowTitle('Settings — sorter')
        self.setMinimumWidth(560)
        self.resize(620, 480)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName('settingsScroll')
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        folders = QWidget()
        f = QVBoxLayout(folders)
        f.setContentsMargins(0, 0, 0, 0)
        f.addWidget(QLabel('Sources · browse and copy from any number of folders'))
        self.sources = QTableWidget(0, 2)
        self.sources.setHorizontalHeaderLabels(['Folder path', 'Display name'])
        self.sources.setMaximumHeight(150)
        self.sources.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sources.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.sources.setColumnWidth(1, 140)
        self.sources.verticalHeader().hide()
        for path in config['sources']:
            self.append_source(path, config.get('source_names', {}).get(str(normalized(path)), ''))
        f.addWidget(self.sources, 1)
        row = QHBoxLayout()
        add = QPushButton('Add source…')
        add.clicked.connect(self.add_source)
        remove = QPushButton('Remove selected')
        remove.clicked.connect(lambda: [self.sources.removeRow(row) for row in sorted({item.row() for item in self.sources.selectedIndexes()}, reverse=True)])
        row.addWidget(add)
        row.addWidget(remove)
        for label, delta in [('↑', -1), ('↓', 1)]:
            button = QPushButton(label)
            button.setFixedWidth(30)
            button.setToolTip('Move source up' if delta < 0 else 'Move source down')
            button.clicked.connect(lambda checked=False, step=delta: self.move_source(step))
            row.addWidget(button)
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
        self._section(layout, 'Folders', 'Source order determines the tab order. Content can be modified only inside the chosen sorting folder.').addWidget(folders)
        naming = QWidget()
        n = QVBoxLayout(naming)
        n.setContentsMargins(0, 0, 0, 0)
        n.addWidget(QLabel('Fields · freely chosen names and unique search aliases'))
        self.fields = QTableWidget(0, 2)
        self.fields.setHorizontalHeaderLabels(['Field name', 'Search alias'])
        self.fields.horizontalHeader().setStretchLastSection(True)
        self.fields.setMaximumHeight(160)
        n.addWidget(self.fields)
        n.addLayout(self.table_tools(self.fields, ['', '']))
        n.addWidget(QLabel('Folder patterns · first matching rule wins at each folder level'))
        self.rules = QTableWidget(0, 2)
        self.rules.setMaximumHeight(170)
        self.rules.setHorizontalHeaderLabels(['Pattern', 'Scope folder (empty = all sources)'])
        self.rules.horizontalHeader().setStretchLastSection(True)
        self.rules.setColumnWidth(0, 320)
        n.addWidget(self.rules)
        n.addLayout(self.table_tools(self.rules, ['', ''], ordered=True))
        self.inherit = QCheckBox('Inherit fields from parent folders')
        self.inherit.setChecked(config['inherit'])
        n.addWidget(self.inherit)
        preview_row = QHBoxLayout()
        self.sample = QLineEdit()
        self.sample.setPlaceholderText('Folder name to test')
        preview_row.addWidget(self.sample)
        test = QPushButton('Test pattern')
        test.clicked.connect(self.test_pattern)
        preview_row.addWidget(test)
        n.addLayout(preview_row)
        self.sample_result = QLabel('Use separators that do not also occur ambiguously inside your field values.')
        self.sample_result.setWordWrap(True)
        n.addWidget(self.sample_result)
        self._section(layout, 'Naming & search', 'Define fields, search aliases, and folder patterns. The first matching rule wins; closer folders override inherited fields.').addWidget(naming)
        advanced = QWidget()
        a = QVBoxLayout(advanced)
        a.setContentsMargins(0, 0, 0, 0)
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
        note = QLabel('Profiles contain naming rules and preferences. Source folders, sorting folder, pins remain local. Scope paths in rules are omitted when exporting.\n\nSettings and the disposable catalog are stored in sorter/.local/. The session shelf is never saved.')
        note.setWordWrap(True)
        a.addWidget(note)
        self._section(layout, 'Profiles & exclusions', 'Share naming rules and exclude filenames or folders with wildcard patterns.').addWidget(advanced)
        layout.addStretch(1)
        self.populate_tables(config)
        footer = QWidget()
        buttons = QHBoxLayout(footer)
        buttons.setContentsMargins(16, 8, 16, 16)
        buttons.addStretch()
        close = QPushButton('Close')
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        save = QPushButton('Save')
        save.setObjectName('accentButton')
        save.clicked.connect(self.save)
        buttons.addWidget(save)
        outer.addWidget(footer)

    def _section(self, parent_layout, title, help_text):
        header = QHBoxLayout()
        header.setSpacing(4)
        toggle = QToolButton()
        toggle.setText(title.replace("&", "&&"))
        toggle.setMinimumWidth(toggle.fontMetrics().horizontalAdvance(title) + 30)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.ArrowType.RightArrow)
        toggle.setCheckable(True)
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setStyleSheet("QToolButton { border: none; background: transparent; padding: 2px 0px; }")
        header.addWidget(toggle)
        header.addWidget(self._help_label(help_text))
        header.addStretch(1)
        parent_layout.addLayout(header)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 4, 0, 12)
        parent_layout.addWidget(body)
        body.hide()
        toggle.toggled.connect(body.setVisible)
        toggle.toggled.connect(lambda opened: toggle.setArrowType(
            Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow))
        return body_layout

    @staticmethod
    def _help_label(text):
        label = QLabel("?")
        label.setObjectName("hint")
        label.setToolTip(text)
        label.setAccessibleName("Help: " + text)
        label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        return label

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
        sources = [(str(normalized(values(self.sources, row, 0))), values(self.sources, row, 1))
                   for row in range(self.sources.rowCount()) if values(self.sources, row, 0)]
        if len({path for path, name in sources}) != len(sources):
            raise ValueError('Each source folder can only appear once.')
        config['sources'] = [path for path, name in sources]
        config['source_names'] = {path: name for path, name in sources if name}
        config['sort_root'] = str(normalized(self.sort_root.text())) if self.sort_root.text().strip() else ''
        config['inherit'] = self.inherit.isChecked()
        config['ignored'] = [line.strip() for line in self.ignored.toPlainText().splitlines() if line.strip()]
        validate_config(config)
        return config

    def append_source(self, path, display_name=''):
        path = str(normalized(path))
        row = self.sources.rowCount()
        self.sources.insertRow(row)
        item = QTableWidgetItem(path)
        item.setToolTip(path)
        self.sources.setItem(row, 0, item)
        self.sources.setItem(row, 1, QTableWidgetItem(display_name))

    def move_source(self, delta):
        row = self.sources.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.sources.rowCount():
            return
        for column in range(2):
            first = self.sources.takeItem(row, column)
            second = self.sources.takeItem(target, column)
            self.sources.setItem(target, column, first)
            self.sources.setItem(row, column, second)
        self.sources.setCurrentCell(target, 0)

    def add_source(self):
        path = QFileDialog.getExistingDirectory(self, 'Add source folder')
        if path:
            if not any(self.sources.item(row, 0).text() == str(normalized(path)) for row in range(self.sources.rowCount())):
                self.append_source(path)

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
STYLE = """
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
    border: 1px solid rgba(255,255,255,0.14);
}
#appTitleBar {
    background: #0a0a0a;
    border: none;
    border-bottom: 1px solid rgba(255,255,255,0.16);
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
    border: 1px solid rgba(255,255,255,0.18);
    border-left: none;
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
}
#edgeTabChevron {
    color: rgba(220,220,220,0.85);
    font-weight: 700;
    font-size: 11px;
}
/* Small pill-shaped open/close handle -- this widget's footprint IS the
   handle (see _WorkflowEdgeTab / _OutputsEdgeTab), so nothing outside it
   is clickable or painted. */
#edgeTabPill {
    background-color: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.30);
}
#edgeTabPill:hover {
    background-color: rgba(0,212,160,0.30);
    border-color: rgba(0,212,160,0.75);
}
#edgeTabPill:hover #edgeTabChevron {
    color: #ffffff;
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
    border: 1px solid rgba(255,255,255,0.16);
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
/* Status text along the bottom strip of free space in the roster's
   viewport (see RosterBar._position_status_label) -- plain single-line
   text, no button/pill treatment. */
QLabel#rosterStatusLabel {
    background: transparent;
    border: none;
    color: rgba(200,200,200,0.45);
    font-size: 11px;
}
QLabel#rosterStatusLabel[error="true"] {
    color: rgba(224,110,100,0.85);
}
/* The run indicator itself (see _RunIndicator) is entirely custom-painted,
   not stylesheet-driven -- no QSS rule needed here. */

/* ---- Center — Image Browser ---- */
#imageBrowserPanel {
    background-color: #0d0d0d;
}
QTabWidget#browserTabs::pane {
    /* No border here on purpose -- the tab strip itself (below) already
       has its own bordered pill box. A border-top on the pane used to
       draw a second, unrelated straight line spanning the *entire* width
       of the panel (running clean through/above the settings icon too),
       independent of the tab strip's own rounded box, which read as a
       stray bar sitting above everything. */
    border: none;
    top: -1px;
}
/* Slim bordered pill for the tab strip itself — mirrors the indexer
   app's clean status bar so this row takes up minimal vertical space. */
QTabWidget#browserTabs QTabBar {
    background: #141414;
    border: 1px solid rgba(255,255,255,0.16);
    border-radius: 5px;
    padding: 2px 3px;
    margin: 2px 2px 4px 2px;
}
QTabWidget#browserTabs QTabBar::tab {
    background: transparent;
    color: rgba(200,200,200,0.55);
    font-size: 11px;
    padding: 4px 10px;
    margin-right: 2px;
    border-radius: 3px;
    border-bottom: 2px solid transparent;
}
QTabWidget#browserTabs QTabBar::tab:selected {
    color: #e8e8e8;
    background: rgba(255,255,255,0.05);
    border-bottom: 2px solid #00d4a0;
}
QTabWidget#browserTabs QTabBar::tab:hover:!selected {
    color: rgba(220,220,220,0.85);
}
/* Status bar housing the Settings (and restore-hidden) button. This row
   is always present, even with zero folders configured, so Settings is
   never unreachable on a fresh/empty setup. */
QWidget#browserStatusBar {
    background: transparent;
}
QToolButton#browserSettingsBtn, QToolButton#workflowLockBtn {
    background: transparent;
    border: none;
    color: rgba(200,200,200,0.45);
    font-size: 14px;
    padding: 1px 2px;
    margin: -2px 0px 0px 0px;
}
QToolButton#browserSettingsBtn:hover, QToolButton#workflowLockBtn:hover {
    color: rgba(220,220,220,0.85);
}
QToolButton#browserSettingsBtn:pressed {
    color: #e8e8e8;
}

/* ---- Bulk "Load Selected Folders" overlay ----
   No panel box on purpose -- the blurred/dimmed backdrop itself (painted
   in _BulkLoadOverlay.paintEvent) already carries the "held up" meaning,
   so only the bare text/progress/button float in the middle. */
#bulkLoadPanel {
    background: transparent;
    border: none;
}
QProgressBar#bulkLoadProgress {
    background-color: rgba(255,255,255,0.10);
    border: none;
    border-radius: 3px;
}
QProgressBar#bulkLoadProgress::chunk {
    background-color: #00d4a0;
    border-radius: 3px;
}
QLabel#bulkLoadCount {
    background: transparent;
    color: #00d4a0;
    font-size: 15px;
    font-weight: 700;
}
QLabel#bulkLoadDetail {
    background: transparent;
    color: rgba(230,230,230,0.85);
    font-size: 12px;
}
/* ---- Queue count badge (red circle/pill, white digits) ---- */
QLabel#queueCountBadgeLabel {
    background: transparent;
    color: #ffffff;
    font-size: 10px;
    font-weight: 700;
}
/* ---- Folder section header (ported from vael. indexer's #sectionHeader:
   flat, text-only, no box at rest; depth conveyed by weight/color, not by
   a bordered frame; hover adds a thin left accent bar). ---- */
#folderSection     { background: transparent; }
#sectionHeaderWrap { background: transparent; }
#sectionBody       { background: transparent; }
#cardGrid           { background: transparent; }

QToolButton#sectionHeader {
    background: transparent;
    border: 1px solid transparent;
    border-left: 2px solid transparent;
    border-radius: 6px;
    text-align: left;
    padding: 0px 10px 0px 7px;
    font-size: 11px;
    font-weight: 600;
    color: rgba(200,200,200,0.68);
    letter-spacing: 0.2px;
}
QToolButton#sectionHeader[depth0="true"] {
    color: rgba(220,220,220,0.86);
    font-weight: 700;
    font-size: 12px;
}
QToolButton#sectionHeader[depth0="false"] {
    color: rgba(195,195,195,0.55);
    font-weight: 500;
}
QToolButton#sectionHeader:hover {
    background: rgba(255,255,255,0.045);
    border: 1px solid transparent;
    border-left: 2px solid #00d4a0;
    color: rgba(235,235,235,0.95);
    font-weight: 700;
}
/* Past the recommended recursion-depth guideline (soft warning only, spec
   section 9) — amber instead of teal, still fully functional. */
QToolButton#sectionHeader[deep="true"] {
    color: rgba(226,163,55,0.75);
}
QToolButton#sectionHeader[deep="true"]:hover {
    border-left: 2px solid rgba(226,163,55,0.65);
    background: rgba(226,163,55,0.05);
}
/* Multi-select (ctrl+click or rubber-band drag) — distinct from :hover so
   a selected-but-not-hovered folder still reads clearly as selected. */
QToolButton#sectionHeader[selected="true"] {
    background: rgba(0,212,160,0.16);
    border: 1px solid rgba(0,212,160,0.4);
    border-left: 2px solid #00d4a0;
    color: #00d4a0;
    font-weight: 700;
}
QToolButton#sectionHeader[selected="true"]:hover {
    background: rgba(0,212,160,0.22);
}
QLabel#headerCount {
    color: rgba(200,200,200,0.35);
    font-size: 10px;
}
QLabel#headerCount[state="error"] {
    color: rgba(224,110,100,0.85);
}

/* ---- Thumbnail card (ported from vael. indexer: image only, no
   filename caption, hover = a faint neutral border, nothing more). ---- */
#cardImage {
    background-color: #181818;
    border-radius: 7px;
}

/* ---- Bottom bar — Input Roster ----
   The actual depth cue is a real cast shadow (QGraphicsDropShadowEffect,
   see RosterBar.__init__) bleeding up from this widget onto the browser
   above it. Here we just add a crisp, slightly brighter top edge -- a
   thin "lip" highlight where the raised slab would catch light -- rather
   than stacking another gradient on top of the shadow. */
#rosterBar {
    background-color: #101010;
    border-top: 1px solid rgba(255,255,255,0.28);
}

/* ---- Splitter handle (Image Browser / Input Roster) ----
   No painted background here on purpose -- _CenterSplitterHandle draws
   its own small three-line "grip" directly in paintEvent, so the handle
   reads as a thin gap with a subtle drag affordance in the middle rather
   than a thick colored bar. */
QSplitter#centerSplitter::handle {
    background-color: transparent;
}

/* ---- Sidebar (Outputs / Queue) ---- */
#outputsSidebar {
    background-color: #121212;
    border: 1px solid rgba(255,255,255,0.18);
    border-right: none;
    border-top-left-radius: 8px;
    border-bottom-left-radius: 8px;
}
#sidebarHandle {
    background-color: transparent;
}
#sidebarHandle:hover {
    background-color: rgba(0,212,160,0.45);
}

/* ---- Outputs sidebar footer action row (Refresh/Open Folder/Clear,
   Run Queue/Clear) -- kept separate from the Outputs/Queue mode toggle at
   the top so a narrow sidebar never has to squeeze 4-5 buttons into one
   row and truncate their labels. ---- */
#outputsFooter {
    background: transparent;
    border-top: 1px solid rgba(255,255,255,0.12);
}
#outputsFooter QPushButton {
    padding: 5px 8px;
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
    border: 1px solid rgba(255,255,255,0.14);
    border-radius: 6px;
    gridline-color: rgba(255,255,255,0.10);
}
QHeaderView::section {
    background-color: transparent;
    color: rgba(200,200,200,0.55);
    padding: 6px;
    border: none;
    border-bottom: 1px solid rgba(255,255,255,0.14);
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

/* ---- Image viewer overlay (ported from vael. indexer) ---- */
#viewerCloseBtn {
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.10);
    color: rgba(220,220,220,0.50);
    font-size: 15px;
    font-weight: 400;
    border-radius: 16px;
}
#viewerCloseBtn:hover {
    background: rgba(139,51,51,0.28);
    border-color: rgba(197,79,79,0.50);
    color: rgba(255,107,107,0.95);
}
#viewerCloseBtn:pressed {
    background: rgba(139,51,51,0.18);
}
#viewerName {
    color: rgba(210,210,210,0.70);
    font-size: 12px;
    background: transparent;
}
#viewerNavBtn {
    background: rgba(18,18,18,0.72);
    border: 1px solid rgba(255,255,255,0.14);
    border-radius: 8px;
    color: rgba(220,220,220,0.82);
    font-size: 30px;
    font-weight: 300;
}
#viewerNavBtn:hover {
    background: rgba(30,30,30,0.90);
    border-color: rgba(0,212,160,0.55);
    color: rgba(80,240,195,1.0);
}
#viewerNavBtn:pressed {
    background: rgba(22,22,22,0.85);
    color: rgba(20,220,170,0.90);
}
#viewerNavBtn:disabled {
    background: rgba(10,10,10,0.30);
    border-color: rgba(255,255,255,0.05);
    color: rgba(255,255,255,0.12);
}
#viewerImage {
    background: transparent;
}

/* ---- Context / app menus (ported from vael. indexer) ----
   Kept generic (plain QMenu selector) so it applies to every popup menu
   in the app — existing ones and any added later — without needing an
   objectName on each. ---- */
QMenu {
    background-color: #0a0a0a;
    border: 1px solid rgba(255,255,255,0.14);
    border-radius: 5px;
    padding: 3px;
}
QMenu::item {
    padding: 4px 14px;
    font-size: 11px;
    border-radius: 3px;
    color: rgba(220,220,220,0.88);
}
QMenu::item:selected {
    background: rgba(0,212,160,0.16);
    color: #00d4a0;
}
QMenu::item:disabled {
    color: rgba(200,200,200,0.30);
}
QMenu::separator {
    height: 1px;
    background: rgba(255,255,255,0.10);
    margin: 3px 4px;
}
"""

def app_icon():
    # Accept the actual Vael artwork names, retaining cover.* as a fallback.
    names = ('icon.ico', 'cover.ico', 'icon.png', 'cover.png') if sys.platform == 'win32' else ('icon.png', 'cover.png')
    return next((APP_DIR / name for name in names if (APP_DIR / name).exists()), APP_DIR / names[0])

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


def _make_search_icon():
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor('#b4b4b4'), 1.3))
    painter.drawEllipse(QRectF(1.5, 1.5, 7, 7))
    painter.drawLine(QPointF(8, 8), QPointF(12, 12))
    painter.end()
    return QIcon(pixmap)


def _make_gear_icon(color: str = "#b4b4b4", size: int = 14) -> QIcon:
    """Hand-draw a small, simple line-art gear/cog icon for the Settings
    button, replacing the old emoji glyph so it matches the rest of the
    app's minimal, monochrome icon style."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(1.3)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx = cy = size / 2
    r_outer = size / 2 - 1.6
    r_inner = size / 2 - 3.8
    hole_r = size * 0.16
    p.drawEllipse(QPointF(cx, cy), hole_r, hole_r)
    p.drawEllipse(QPointF(cx, cy), r_inner, r_inner)
    teeth = 6
    for i in range(teeth):
        angle = (2 * math.pi / teeth) * i
        x1 = cx + r_inner * math.cos(angle)
        y1 = cy + r_inner * math.sin(angle)
        x2 = cx + r_outer * math.cos(angle)
        y2 = cy + r_outer * math.sin(angle)
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    p.end()
    return QIcon(pm)

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

class _CenterSplitterHandle(QSplitterHandle):
    """Replaces the old thick, fully-colored splitter bar between the
    Image Browser and the Input Roster with a slim, mostly-invisible
    strip that only shows three short horizontal "grip" lines in the
    middle -- and only responds to a drag that actually starts on that
    small grip area, so the rest of the handle's width doesn't
    accidentally resize things."""

    GRIP_W = 26
    GRIP_HIT_W = 40
    GRIP_HIT_H = 14

    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        # The cast shadow that used to sit on the roster bar itself now
        # lives on the drag handle instead, blurring down into the
        # roster below -- it marks the actual boundary the user drags to
        # resize, rather than reading as a stray bar of its own.
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 200))
        self.setGraphicsEffect(shadow)

    def _grip_rect(self):
        r = self.rect()
        return QRect(
            r.center().x() - self.GRIP_HIT_W // 2,
            r.center().y() - self.GRIP_HIT_H // 2,
            self.GRIP_HIT_W, self.GRIP_HIT_H,
        )

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        hovered = self._grip_rect().contains(self.mapFromGlobal(self.cursor().pos()))
        color = QColor("#00d4a0") if hovered else QColor(255, 255, 255, 90)
        pen = QPen(color)
        pen.setWidth(1)
        p.setPen(pen)
        cx, cy = self.rect().center().x(), self.rect().center().y()
        half = self.GRIP_W // 2
        for dy in (-3, 0, 3):
            p.drawLine(cx - half, cy + dy, cx + half, cy + dy)
        p.end()

    def mousePressEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if self._grip_rect().contains(pos):
            super().mousePressEvent(event)
        else:
            event.ignore()

    def mouseMoveEvent(self, event):
        self.update()
        super().mouseMoveEvent(event)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

class _CenterSplitter(QSplitter):
    """Vertical splitter between the Image Browser and Input Roster, using
    _CenterSplitterHandle instead of the default full-width drag bar."""

    def createHandle(self):
        return _CenterSplitterHandle(self.orientation(), self)

def apply_style(app: QApplication) -> None:
    app.setStyle("Fusion")

    if app_icon().exists():
        app.setWindowIcon(QIcon(str(app_icon())))

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

    app.setStyleSheet(STYLE)

class Window(QMainWindow):
    def __init__(self, config=None):
        super().__init__()
        self.config = config if config is not None else read_settings()
        self.config.pop('flags', None)
        self.config.setdefault('groups', [])
        self.config.setdefault('notes', [])
        validate_config(self.config)
        self.service = FileService(self.config['sort_root'])
        self.jobs = Jobs(self)
        self.operation_task = None
        self.clip_paths, self.clip_cut = [], False
        self.active_browser = None
        self.previews = []
        self.setWindowTitle('sorter. — vael')
        self._is_windows = _IS_WINDOWS
        self.resize(1280, 840)
        self.setMinimumSize(720, 520)
        if not self._is_windows:
            self.setWindowFlag(Qt.FramelessWindowHint)
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setObjectName('mainWindowFrameless')
        if app_icon().exists():
            self.setWindowIcon(QIcon(str(app_icon())))
        shell = QWidget()
        shell.setObjectName('appShell')
        shell.setProperty('maximized', 'false')
        self._shell = shell
        self.setCentralWidget(shell)
        shell_lay = QVBoxLayout(shell)
        shell_lay.setContentsMargins(1, 1, 1, 1)
        shell_lay.setSpacing(0)
        self._title_bar = _TitleBar(self)
        self._title_bar.setObjectName('appTitleBar')
        self._title_bar.setFixedHeight(34)
        title_layout = QHBoxLayout(self._title_bar)
        title_layout.setContentsMargins(14, 0, 8, 0)
        title_layout.setSpacing(2)
        brand = QLabel('<span style="color:#e8e8e8;">vael. </span><span style="color:#00d4a0;">sorter</span>')
        brand.setObjectName('brandLbl')
        title_layout.addWidget(brand)
        title_layout.addStretch()
        for name, icon_kind, callback in [('winMinBtn', 'min', self.showMinimized), ('winMaxBtn', 'max', self._toggle_maximize), ('winCloseBtn', 'close', self.close)]:
            button = QToolButton()
            button.setObjectName(name)
            button.setIcon(_make_win_icon(icon_kind))
            button.setIconSize(QSize(10, 10))
            button.setFixedSize(30, 24)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(callback)
            title_layout.addWidget(button)
            if icon_kind == 'max':
                self._max_btn = button
        shell_lay.addWidget(self._title_bar)
        center = QWidget()
        center.setObjectName('appRoot')
        self._content_root = center
        shell_lay.addWidget(center, 1)
        layout = QVBoxLayout(center)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        self.vertical = _CenterSplitter(Qt.Vertical)
        self.vertical.setObjectName('centerSplitter')
        self.vertical.setChildrenCollapsible(False)
        self.vertical.setHandleWidth(10)
        self.library = CoverBrowser(self, False, 'Library')
        self.vertical.addWidget(self.library)
        bottom = QWidget()
        bottom.setObjectName('rosterBar')
        bottom.setAttribute(Qt.WA_StyledBackground)
        b = QVBoxLayout(bottom)
        b.setContentsMargins(14, 8, 14, 10)
        b.setSpacing(6)
        self.shelf = Shelf(self)
        self.shelf.hide()
        controls = QHBoxLayout()
        settings = QToolButton()
        settings.setObjectName('browserSettingsBtn')
        settings.setIcon(_make_gear_icon())
        settings.setToolTip('Settings')
        settings.clicked.connect(self.settings)
        controls.addWidget(settings)
        help_button = QToolButton()
        help_button.setObjectName('browserSettingsBtn')
        help_button.setText('?')
        help_button.setToolTip('Help and shortcuts')
        help_button.clicked.connect(self.help)
        controls.addWidget(help_button)
        self.target_label = QLabel()
        self.target_label.setObjectName('hint')
        self.target_label.hide()
        label = QLabel('SORTING WORKSPACE')
        label.setObjectName('hint')
        controls.addWidget(label)
        shelf_button = QToolButton()
        self.shelf_button = shelf_button
        shelf_button.setObjectName('modeTab')
        shelf_button.setText('Shelf')
        shelf_button.setCheckable(True)
        shelf_button.setToolTip('Show session shelf')
        shelf_button.toggled.connect(self.shelf.setVisible)
        controls.addWidget(shelf_button)
        controls.addStretch()
        self.split_toggle = QCheckBox('Split view')
        controls.addWidget(self.split_toggle)
        self.undo_button = QPushButton('Undo last operation')
        self.undo_button.clicked.connect(self.undo)
        controls.addWidget(self.undo_button)
        b.addLayout(controls)
        b.addWidget(self.shelf, 1)
        self.horizontal = QSplitter(Qt.Horizontal)
        self.sorter = WorkspaceBrowser(self)
        self.second = WorkspaceBrowser(self)
        self.horizontal.addWidget(self.sorter)
        self.horizontal.addWidget(self.second)
        self.second.hide()
        self.split_toggle.toggled.connect(self.second.setVisible)
        b.addWidget(self.horizontal, 1)
        shelf_button.toggled.connect(lambda shelf: (self.horizontal.setVisible(not shelf), self.split_toggle.setEnabled(not shelf)))
        workspace_button = QToolButton()
        self.workspace_button = workspace_button
        workspace_button.setObjectName('modeTab')
        workspace_button.setText('Workspace')
        workspace_button.setCheckable(True)
        workspace_button.setChecked(True)
        controls.replaceWidget(label, workspace_button)
        label.deleteLater()
        workspace_button.clicked.connect(lambda: shelf_button.setChecked(False))
        shelf_button.toggled.connect(lambda shelf: workspace_button.setChecked(not shelf))
        workspace_button.clicked.connect(lambda: workspace_button.setChecked(True))
        mode_style = 'QToolButton { color: #777; background: transparent; border: 1px solid transparent; padding: 4px 8px; border-radius: 3px; } QToolButton:checked { color: #eee; background: #111; border: 1px solid #353535; } QToolButton:hover { color: #eee; }'
        shelf_button.setStyleSheet(mode_style)
        workspace_button.setStyleSheet(mode_style)
        self.vertical.addWidget(bottom)
        self.vertical.setSizes([470, 450])
        layout.addWidget(self.vertical, 1)
        operation_bar = QHBoxLayout()
        self.operation_status = QLabel('Ready')
        self.operation_status.setObjectName('rosterStatusLabel')
        operation_bar.addWidget(self.operation_status, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.hide()
        operation_bar.addWidget(self.progress)
        self.cancel_button = QPushButton('Stop after current item')
        self.cancel_button.clicked.connect(lambda: self.operation_task.cancel.set() if self.operation_task else None)
        self.cancel_button.hide()
        operation_bar.addWidget(self.cancel_button)
        self.tagging = Tagging(self, APP_DIR)
        operation_bar.addWidget(self.tagging.button)
        b.addLayout(operation_bar)
        self.statusBar().hide()
        QApplication.instance().focusChanged.connect(self.focus_changed)
        self._img_viewer = MediaViewerOverlay(self._content_root)
        self._modal_dim_overlay = _ModalDimOverlay(self._content_root)
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        viewer = getattr(self, '_img_viewer', None)
        if viewer:
            viewer.resize(self._content_root.size())
        for browser in getattr(self, 'browsers', []):
            if browser.bulk_overlay.isVisible():
                browser.bulk_overlay.resize(self._content_root.size())
        self._update_window_mask()

    def update_target_label(self):
        root = self.config['sort_root']
        self.target_label.setText('Editable: ' + (Path(root).name or root) if root else 'Choose your folders in Settings to begin')
        self.target_label.setToolTip(root)

    def focus_changed(self, old, new):
        if new:
            for browser in getattr(self, 'browsers', []):
                if browser == new or browser.isAncestorOf(new):
                    self.active_browser = browser
                    if browser.editable:
                        self.active_workspace = browser

    def target_browser(self):
        active = getattr(self, 'active_workspace', self.sorter)
        return active if active is self.sorter or self.split_toggle.isChecked() else self.sorter

    def notice(self, text):
        QMessageBox.information(self, 'sorter', str(text))

    def exec_dialog(self, dialog):
        self._modal_dim_overlay.show_overlay()
        try:
            return dialog.exec()
        finally:
            self._modal_dim_overlay.hide_overlay()

    def settings(self):
        if self.tagging.busy:
            self.tagging.error('Finish or stop the tagging job / health check before changing folders.')
            return
        if self.operation_task:
            self.notice('Wait for the current file operation before changing settings.')
            return
        dialog = SettingsDialog(self.config, self)
        if self.exec_dialog(dialog) == QDialog.Accepted:
            old_root = self.config['sort_root']
            self.config = dialog.config
            if self.config['sort_root'] != old_root:
                self.service.discard_backups()
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
            self.tagging.health()

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
        if self.tagging.busy:
            self.tagging.error('Finish or stop the tagging job / health check before changing files. Browsing is still available.')
            return
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
            for browser in (self.sorter, self.second):
                if browser.current and not browser.current.is_dir():
                    browser.current = self.service.root if browser.editable else browser.current.parent
                    browser.path.setText(str(browser.current) if browser.current else '')
                active_workspace = getattr(self, 'active_workspace', self.sorter)
                browser.navigate(browser.current)
                self.active_workspace = active_workspace
            if errors:
                box = QMessageBox(QMessageBox.Warning, 'Some operations could not be completed', '\n'.join(errors[:5]), parent=self)
                box.setDetailedText('\n'.join(errors))
                box.exec()

        self.operation_task = self.jobs.start(execute, finished,
            lambda error: finished(([], [error], False)),
            lambda value, label: (self.progress.setValue(value), self.operation_status.setText(title + ' · ' + Path(label).name)))

    def groups_changed(self):
        self.persist()
        for browser in (self.sorter, self.second):
            browser.proxy.invalidate()
            browser.proxy.sort(0)
            browser.grid.doItemsLayout()
            browser.grid.viewport().update()

    def group_selection(self, browser):
        try:
            create_group(self.config, browser.selected_paths())
        except ValueError as exc:
            self.notice(exc)
            return
        self.groups_changed()

    def dissolve_group(self, group):
        self.config['groups'] = [g for g in self.config['groups'] if g['id'] != group['id']]
        self.groups_changed()

    def show_notes(self, editable=True):
        dialog = NotesDialog(self, editable)
        if self.exec_dialog(dialog) == QDialog.Accepted and editable:
            self.config['notes'] = dialog.notes
            existing = {note['id'] for note in dialog.notes}
            for group in self.config['groups']:
                group['notes'] = [key for key in group['notes'] if key in existing]
            self.persist()
        dialog.deleteLater()

    def assign_note(self, group, note_id, checked):
        ids = set(group['notes'])
        ids.add(note_id) if checked else ids.discard(note_id)
        group['notes'] = sorted(ids)
        self.persist()

    def group_menu(self, group, parent):
        menu = QMenu(parent)
        self.populate_group_menu(menu, group)
        return menu

    def populate_group_menu(self, menu, group):
        initial_count = len(menu.actions())
        notes = self.config['notes']
        for note in notes:
            if note['id'] in group['notes']:
                action = menu.addAction(note['title'])
                action.setToolTip('Copy this note’s text')
                action.triggered.connect(lambda checked=False, text=note['body']: QApplication.clipboard().setText(text))
        if len(menu.actions()) > initial_count:
            menu.addSeparator()
        if notes:
            assignments = CheckMenu('Assign Notes', menu)
            menu.addMenu(assignments)
            for note in notes:
                action = assignments.addAction(note['title'])
                action.setCheckable(True)
                action.setChecked(note['id'] in group['notes'])
                action.toggled.connect(lambda checked, key=note['id']: self.assign_note(group, key, checked))
        menu.addAction('Dissolve Group', lambda: self.dissolve_group(group))
        return menu

    def reconcile_references(self, results):
        # Moves and renames update pins, shelf entries and browser paths.
        for old_text, value in results:
            if isinstance(value, UndoRecord):
                if value.action == 'rmdir':
                    continue
                old, new = value.target, value.source if value.action == 'move' else None
            elif isinstance(value, Path) and not Path(old_text).exists():
                old, new = Path(old_text), value
            elif value is None and Path(old_text).is_absolute() and not Path(old_text).exists():
                old, new = Path(old_text), None
            else:
                continue
            def mapped(text):
                path = Path(text)
                if path.is_relative_to(old):
                    return str(new / path.relative_to(old)) if new else None
                return text
            for group in self.config['groups']:
                group['members'] = [mapped(p) for p in group['members'] if mapped(p)]
            for key in ('pins',):
                self.config[key] = [mapped(p) for p in self.config[key] if mapped(p)]
            for i in reversed(range(self.shelf.list.count())):
                item = self.shelf.list.item(i)
                text = mapped(item.data(Qt.UserRole))
                if text:
                    item.setData(Qt.UserRole, text)
                    item.setToolTip(text)
                    self.shelf.refresh_item(item)
                    card = self.shelf.list.itemWidget(item)
                    if card:
                        card.filepath = text
                        card.setToolTip(text)
                        card._image_loaded = False
                        card.request_image()
                else:
                    self.shelf.list.takeItem(i)
            for browser in self.browsers:
                if browser.current:
                    text = mapped(str(browser.current))
                    browser.current = Path(text) if text else (self.service.root if browser.editable else None)
                    browser.path.setText(str(browser.current) if browser.current else '')
        organize_groups(self.config)
        self.config['pins'] = [p for p in self.config['pins'] if Path(p).is_dir()]
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
        paths = top_level_selection(paths)
        destination = Path(destination)
        names = set()
        conflicts = []
        for path in paths:
            key = path.name.casefold()
            if (destination / path.name).exists() or key in names:
                conflicts.append(path.name)
            names.add(key)
        policy = 'error'
        if conflicts:
            box = QMessageBox(self)
            box.setWindowTitle('Files with matching names')
            box.setText(f'{len(conflicts)} selected item(s) have names already used in this destination.')
            box.setInformativeText('Overwrite replaces matching files. Auto Rename adds an available number before the extension. Cancel leaves everything unchanged.')
            box.setDetailedText('\n'.join(conflicts))
            cancel = box.addButton('Cancel', QMessageBox.RejectRole)
            overwrite = box.addButton('Overwrite', QMessageBox.DestructiveRole)
            rename = box.addButton('Auto Rename', QMessageBox.AcceptRole)
            box.setDefaultButton(cancel)
            self.exec_dialog(box)
            if box.clickedButton() is cancel or box.clickedButton() is None:
                return
            policy = 'overwrite' if box.clickedButton() is overwrite else 'rename'
        self.run_operations('Move' if move else 'Copy',
            [(str(path), lambda p=path: self.service.transfer(p, destination, move, policy)) for path in paths])

    def transfer_dialog(self, browser, move):
        if not move:
            self.transfer(browser.selected_paths(), self.target_browser().current, False)
            return
        root = self.service.root
        if not root:
            self.notice('Choose a workspace folder in Settings first.')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Move to Folder')
        dialog.resize(520, 500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Select a destination inside the workspace.'))
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        layout.addWidget(tree)
        root_item = QTreeWidgetItem(tree, ['Root'])
        root_item.setData(0, Qt.UserRole, str(root))
        root_item.setToolTip(0, str(root))
        items = {root: root_item}
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        status = QLabel('Loading folders…')
        layout.addWidget(status)
        def folders(task):
            result, errors = [], []
            for folder, dirs, _ in os.walk(root, followlinks=False, onerror=lambda e: errors.append(str(e))):
                if task.cancel.is_set():
                    break
                dirs[:] = sorted([name for name in dirs if not name.startswith('.sorter-') and self.service.inside(Path(folder) / name)], key=str.casefold)
                result.extend(Path(folder) / name for name in dirs)
            return result, errors
        def populated(result):
            if not isValid(dialog) or not dialog.isVisible():
                return
            for path in result[0]:
                item = QTreeWidgetItem(items[path.parent], [path.name])
                item.setData(0, Qt.UserRole, str(path))
                item.setIcon(0, self.sorter.folder_icon)
                items[path] = item
            root_item.setExpanded(True)
            status.setText('\n'.join(result[1]) or f'{len(items)} folders')
        tree.setCurrentItem(root_item)
        task = self.jobs.start(folders, populated, lambda error: status.setText(error) if isValid(status) else None)
        accepted = self.exec_dialog(dialog) == QDialog.Accepted
        task.cancel.set()
        if accepted and tree.currentItem():
            self.transfer(browser.selected_paths(), Path(tree.currentItem().data(0, Qt.UserRole)), True)
        dialog.deleteLater()

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
        if self.exec_dialog(dialog) == QDialog.Accepted:
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
        dialog.setWindowTitle('File Information')
        dialog.resize(700, 560)
        layout = QVBoxLayout(dialog)
        summary = QLabel('Reading properties…')
        summary.setWordWrap(True)
        layout.addWidget(summary)
        properties = QPlainTextEdit()
        properties.setReadOnly(True)
        layout.addWidget(properties, 1)
        expand = QToolButton()
        expand.setText('Show full metadata')
        expand.setCheckable(True)
        layout.addWidget(expand)
        metadata = QPlainTextEdit()
        metadata.setReadOnly(True)
        metadata.hide()
        layout.addWidget(metadata, 1)
        expand.toggled.connect(metadata.setVisible)
        expand.toggled.connect(lambda checked: expand.setText('Hide full metadata' if checked else 'Show full metadata'))
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        def inspect(task):
            import subprocess
            rows, full, total, file_count, folder_count = [], [], 0, 0, 0
            exiftool = shutil.which('exiftool')
            for path in paths:
                if task.cancel.is_set():
                    break
                try:
                    stat = path.stat()
                    size, files, folders = stat.st_size, 1, 0
                    if path.is_dir():
                        size, files, folders = 0, 0, 1
                        for base, dirs, names in os.walk(path, followlinks=False):
                            if task.cancel.is_set():
                                break
                            dirs[:] = [name for name in dirs if not is_link(Path(base) / name) and not name.startswith('.sorter-')]
                            folders += len(dirs)
                            for name in names:
                                child = Path(base) / name
                                if is_link(child) or name.startswith('.sorter-'):
                                    continue
                                try:
                                    size += child.stat().st_size
                                    files += 1
                                except OSError:
                                    pass
                    total += size
                    file_count += files
                    folder_count += folders
                    row = f'{path.name}\nLocation: {path}\nType: {"Folder" if path.is_dir() else path.suffix.upper().lstrip(".") or "File"}\nSize: {readable_size(size)} ({size:,} bytes)\nModified: {datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M:%S}\nCreated: {datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_ctime)):%Y-%m-%d %H:%M:%S}'
                    if path.is_dir():
                        row += f'\nContents: {files:,} files, {folders - 1:,} subfolders'
                    elif path.suffix.lower() in IMAGES | {'.gif'}:
                        reader = QImageReader(str(path))
                        dimensions = reader.size()
                        if dimensions.isValid():
                            row += f'\nDimensions: {dimensions.width():,} × {dimensions.height():,} pixels'
                        for key in reader.textKeys():
                            full.append(f'{path.name} · {key}: {reader.text(key)}')
                    rows.append(row)
                    if exiftool and path.is_file():
                        try:
                            result = subprocess.run([exiftool, '-G', '-s', str(path)], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                            full.append(f'{path.name}\n{result.stdout or result.stderr}')
                        except (OSError, subprocess.TimeoutExpired) as exc:
                            full.append(f'{path.name}: {exc}')
                except OSError as exc:
                    rows.append(f'{path}: {exc}')
            if not exiftool:
                full.append('ExifTool is not installed. Standard file properties and available embedded image metadata are shown.')
            return f'{file_count:,} files · {folder_count:,} folders · {readable_size(total)}', '\n\n'.join(rows), '\n\n'.join(full) or 'No additional metadata available.'
        def loaded(result):
            if isValid(dialog) and dialog.isVisible():
                summary.setText(result[0])
                properties.setPlainText(result[1])
                metadata.setPlainText(result[2])
        task = self.jobs.start(inspect, loaded, lambda error: summary.setText(error) if isValid(summary) else None)
        self.exec_dialog(dialog)
        task.cancel.set()
        dialog.deleteLater()

    def open_image_viewer(self, card, cards):
        self._img_viewer.open_viewer(card, cards)

    def preview(self, path, paths=None):
        if path.is_dir():
            self.library.navigate(path)
            return
        if not path.exists():
            self.notice('This file is no longer at its original location.')
            return
        from types import SimpleNamespace
        paths = paths or [path]
        if path not in paths:
            paths.insert(0, path)
        cards = [SimpleNamespace(filepath=str(p)) for p in paths]
        self._img_viewer.open_viewer(cards[paths.index(path)], cards)

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
            self.exec_dialog(dialog)
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
        self.tagging.shutdown()
        self.poll_timer.stop()
        self._img_viewer.close_viewer()
        for browser in self.browsers:
            browser._closing = True
            browser.filter_timer.stop()
            if browser._filter_task:
                browser._filter_task.cancel.set()
            browser.bulk_loader.cancel()
        PIXMAP_WORKER.stop()
        PIXMAP_WORKER.wait()
        _BULK_SCAN_POOL.waitForDone()
        _BULK_PIXMAP_POOL.waitForDone()
        QThreadPool.globalInstance().waitForDone()
        for task in list(self.jobs.tasks):
            task.cancel.set()
        self.jobs.pool.waitForDone()
        self.service.discard_backups()
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
    apply_style(app)
    try:
        window = Window()
    except Exception as exc:
        QMessageBox.critical(None, 'sorter could not start', str(exc))
        return 1
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
