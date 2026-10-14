"""All mutations pass through one root-confined service, never UI-only checks."""
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from .catalog import is_link


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
