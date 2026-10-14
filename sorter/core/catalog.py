"""Cancellable filesystem discovery with a disposable SQLite metadata index."""
import fnmatch
import json
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .naming import NamingRules

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


def scan(roots, config, recursive, cancel, database=None):
    naming = NamingRules(config)
    result, warnings, seen = [], [], set()
    for root_text in roots:
        root = Path(root_text).absolute()
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
                                                stat.st_mtime, naming.extract(path if directory else folder, root)))
                        except OSError as exc:
                            warnings.append(f"{path}: {exc}")
            except OSError as exc:
                warnings.append(f"{folder}: {exc}")
    if database and not cancel.is_set():
        database = Path(database)
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(database, timeout=20)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS entries (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, fields TEXT, seen REAL)")
            db.executemany("INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?)",
                           [(str(e.path), e.size, e.mtime, json.dumps(e.fields), time.time()) for e in result])
    return result, warnings
