#!/usr/bin/env python3
"""
Backup Utility — app.py

Implementation of "Backup Utility — Complete Technical Specification (Revised v3)".

Single-entry-point Python application built around the 7-Zip (7z) CLI.
Internally organized into the module boundaries described in Section 46
of the spec, even though everything lives in one file for now:

    Config / dataclasses
    SevenZipRunner
    SourceInventoryManager
    ManifestManager
    ArchiveTransactionManager
    ArchiveManager
    HistoryManager
    VerificationManager
    BackupManager
    CLI / main

IMPORTANT — things that need validation against your real 7-Zip build
before you trust this with real data (the spec explicitly calls these
out as implementation-time decisions, not settled facts):

  1. SEVENZIP_UPDATE_SWITCHES / SEVENZIP_ROOT_MAPPING_TECHNIQUE below.
     This build uses "Technique B": each source is added to the
     transaction archive using 7z with `cwd` set to the source's own
     parent directory (so the archive root becomes the source's
     physical basename), and then, if the configured logical name
     differs from the basename, a `7z rn` pass renames that root to
     the logical name before validation. This avoids relying on a
     single global cwd (Section 37.1) and avoids symlink/junction
     alias tricks (Technique A), which are more OS-specific.
  2. The exact `-u` action-set string (Section 21/33.3) is defined in
     one place (SEVENZIP_UPDATE_SWITCHES) so it can be corrected for
     your installed 7-Zip version without touching the rest of the
     code.
  3. No filesystem clone/snapshot optimization is implemented
     (Section 33.2) — version 1 always uses the staged-new-archive
     transaction, as the spec allows.
  4. VERIFIED-BROKEN AND WORKED AROUND: Section 33's transaction
     design assumes 7-Zip's `-u<state>!<newArchiveName>` output form
     leaves the archive named on the command line untouched. Tested
     live against 7-Zip 23.01 (the current Linux "7zip" package): it
     does NOT. `7z u Backup.7z Documents -u...!staged.new` updated
     `Backup.7z` in place *and* wrote only the incremental delta (not
     a full merged archive) to `staged.new`, which would have silently
     corrupted the "known-good primary archive stays untouched until
     validated" guarantee (Invariant 4) had it shipped as originally
     written. This build therefore never passes the real destination
     archive to a write-capable 7-Zip command: ArchiveTransactionManager
     makes one real filesystem copy of the existing archive into the
     `.new` transaction path up front (or starts empty, for --new),
     and every item is synchronized in place against that copy only.
     This reintroduces the one full-archive copy that Section 33.1's
     redesign wanted to eliminate, but Design Principle 1
     ("correctness over convenience") requires it given the above.
     If you can confirm the non-mutating form actually works on your
     specific 7-Zip build/version, SevenZipRunner.add_or_update and
     its callers can be restored to the copy-free design.

Run `python app.py --new / --update <archive> / --verify <archive>`
or just `python app.py` for the interactive menu.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as _dt
import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ======================================================================
# 1. CONFIGURATION  (Section 4, 59)
# ======================================================================


@dataclass(frozen=True)
class BackupItem:
    """A single configured backup source (Section 4.1)."""

    name: str  # logical archive-side folder name
    path: Path  # physical source path


# ----------------------------------------------------------------------
# Edit this section to configure your backups.
# ----------------------------------------------------------------------

BACKUP_ITEMS: list[BackupItem] = [
    BackupItem("Documents", Path(r"D:\Documents")),
    BackupItem("Projects", Path(r"E:\Projects")),
    BackupItem("Photos", Path(r"F:\Photos")),
]

ARCHIVE_NAME = "Backup.7z"
COMPRESSION_LEVEL = 7  # 0-9, see Section 20
HISTORY_FILENAME = "backup_history.txt"
MANIFEST_FILENAME = "manifest.json"
SEVEN_ZIP_PATH: Optional[str] = None  # None = auto-detect on PATH

TIMESTAMP_TOLERANCE_NS = 2_000_000_000  # Section 19.1

HISTORY_RETRY_ATTEMPTS = 5
HISTORY_RETRY_BACKOFF_SECONDS = (0.25, 0.5, 1.0, 2.0, 4.0)  # Section 18A.2

REQUIRE_SAME_FILESYSTEM_ATOMIC_REPLACE = True  # Section 33.6
ENABLE_FILESYSTEM_CLONE_OPTIMIZATION = False  # Section 33.2 (not implemented)

# SEVENZIP_UPDATE_SWITCHES was originally meant to hold a 7-Zip -u
# "mirror the source, delete archive members no longer present"
# action-set string (e.g. "-up0q3r2x2y2z1w2"), applied per BackupItem.
#
# VERIFIED BROKEN for that purpose (tested live against 7-Zip 23.01):
# that switch's delete/sync state is evaluated against the WHOLE
# ARCHIVE, not scoped to the file spec given on the command line.
# Running it once per BackupItem in sequence caused every item's sync
# call to delete all *other* already-synced items' content, because
# only the current item matched the "still present in source" set.
# Reproduced directly: after adding Documents, syncing Projects logged
# "Delete data from archive: 2 folders, 2 files" (Documents' content)
# before adding Projects' single file — a direct violation of Section
# 7.1/7.2 and Invariant 10 (managed roots must be isolated).
#
# This build therefore does NOT use any -u delete/sync state switch.
# Each item is synchronized with a plain `7z u <archive> <item>` call
# (adds new/changed files, keeps everything else — verified to leave
# unrelated already-synced items untouched). Deletions of files/dirs
# removed from a source are instead computed precisely in Python from
# the pre/post inventory comparison (Section 19.1/40, which the spec
# already requires as the source of truth for change statistics) and
# applied with an explicit, listfile-based `7z d` pass scoped to the
# exact managed paths that disappeared — never a wildcard/sync switch
# that could reach outside the intended root.
SEVENZIP_UPDATE_SWITCHES: list[str] = []  # intentionally empty; see above

LOG_FILENAME = "backup.log"  # Section 1.1 — operational log, distinct from
# backup_history.txt: this is a rotating-by-nature, machine-oriented debug
# trail for a cron/scheduler wrapper to alert on; backup_history.txt stays
# the deliberately human+machine readable audit record it was designed as.
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5

# ----------------------------------------------------------------------

# ======================================================================
# 1A. LOGGING  (Roadmap Phase 1.1)
# ======================================================================

logger = logging.getLogger("backup_utility")


def setup_logging(app_dir: Path, level: str = "INFO", log_file: Optional[Path] = None) -> logging.Logger:
    """Configure the module-wide operational logger.

    Writes timestamped, leveled records to a log file (default:
    `backup.log` beside the archive/history files) so an unattended
    cron/task-scheduler wrapper has something to grep or tail for real
    failures, distinct from the prose-formatted backup_history.txt audit
    trail. Also mirrors WARNING-and-above to stderr so an interactive
    user or a scheduler's own captured-output log sees problems without
    needing to open the log file.

    Safe to call more than once (e.g. interactive menu + main()); it
    clears and re-installs handlers rather than stacking duplicates.
    """
    resolved_level = getattr(logging, level.upper(), logging.INFO)
    target = log_file or (app_dir / LOG_FILENAME)

    logger.setLevel(resolved_level)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    try:
        from logging.handlers import RotatingFileHandler

        file_handler = RotatingFileHandler(
            target, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(resolved_level)
        logger.addHandler(file_handler)
    except OSError as exc:
        # Logging is a diagnostic aid, not a correctness requirement — a
        # read-only or missing log directory must not prevent a backup
        # from running. Fall back to stderr-only and say so once.
        stream_only = logging.StreamHandler(sys.stderr)
        stream_only.setFormatter(fmt)
        stream_only.setLevel(logging.WARNING)
        logger.addHandler(stream_only)
        logger.warning("Could not open log file %s (%s); logging to stderr only.", target, exc)
        return logger

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(fmt)
    stderr_handler.setLevel(logging.WARNING)
    logger.addHandler(stderr_handler)
    return logger


# ======================================================================
# 2. SMALL UTILITIES
# ======================================================================


def now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def iso(ts: _dt.datetime) -> str:
    return ts.astimezone().isoformat(timespec="seconds")


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TiB"


class BackupError(Exception):
    """Raised for expected, user-facing operational failures (exit code 1)."""


class ConfigError(Exception):
    """Raised for configuration/usage errors (exit code 2)."""


class DependencyError(Exception):
    """Raised when a required dependency (7-Zip) is missing (exit code 3)."""


# ======================================================================
# 3. NAME / PATH VALIDATION  (Section 4.4, 37.4)
# ======================================================================


def validate_logical_name(name: str, all_names: list[str]) -> None:
    if not name:
        raise ConfigError("A configured logical name is empty.")
    if name in (".", ".."):
        raise ConfigError(f"Logical name {name!r} is not allowed.")
    if name.startswith("/") or name.startswith("\\"):
        raise ConfigError(f"Logical name {name!r} must not be an absolute path.")
    if ":" in name:
        raise ConfigError(f"Logical name {name!r} must not contain a drive letter.")
    parts = Path(name).parts
    if ".." in parts:
        raise ConfigError(f"Logical name {name!r} must not contain '..'.")
    if name == MANIFEST_FILENAME:
        raise ConfigError(f"Logical name {name!r} collides with reserved metadata.")
    if all_names.count(name) > 1:
        raise ConfigError(f"Logical name {name!r} is configured more than once.")
    # prefix collision check: "Data" vs "Data/Sub" is rejected in EITHER
    # order, "Data" vs "Data2" is fine. Comparing only
    # other.parts[:len(name.parts)] == name.parts (the original form) is
    # asymmetric: it only catches the case where `other` is the longer
    # (nested) name and misses it when `name` itself is the longer one
    # being checked against a shorter `other` already in the list — e.g.
    # calling this for "Data/Sub" against "Data" would silently pass.
    # Comparing against whichever of the two is shorter makes it
    # order-independent.
    name_parts = Path(name).parts
    for other in all_names:
        if other == name:
            continue
        other_parts = Path(other).parts
        shorter, longer = (name_parts, other_parts) if len(name_parts) <= len(other_parts) else (other_parts, name_parts)
        if longer[: len(shorter)] == shorter:
            raise ConfigError(
                f"Logical names {name!r} and {other!r} collide (prefix overlap)."
            )


def _path_parts_relation(a: Path, b: Path) -> Optional[str]:
    """Return 'same' if a and b are the same path, 'nested' if one is an
    ancestor of the other, else None. Compares raw parts (case-sensitive,
    no symlink resolution) — this is a configuration sanity check, not a
    filesystem-identity check; two BackupItems that turn out to be the
    same physical directory via a symlink are out of scope here and would
    instead surface as the SourceInventoryManager loop-guard skip.
    """
    pa, pb = a.parts, b.parts
    if pa == pb:
        return "same"
    shorter, longer = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
    if longer[: len(shorter)] == shorter:
        return "nested"
    return None


def validate_configuration(items: list[BackupItem]) -> None:
    if not items:
        raise ConfigError("BACKUP_ITEMS is empty; nothing configured to back up.")
    names = [i.name for i in items]
    for item in items:
        validate_logical_name(item.name, names)

    # Relative source paths are a footgun: add_or_update / _dir_size /
    # preflight_space etc. all resolve a BackupItem.path either directly
    # or via a subprocess `cwd=item.path.parent`, so a relative path's
    # meaning silently depends on the process's current working directory
    # at invocation time (Section 4.1 assumes stable, unambiguous sources).
    non_absolute = [i for i in items if not i.path.is_absolute()]
    if non_absolute:
        details = "\n".join(f"  {i.name} -> {i.path}" for i in non_absolute)
        raise ConfigError(
            "Configured source path(s) must be absolute (relative paths depend on the "
            f"current working directory, which is not safe for automation):\n{details}"
        )

    # Two logical names backing the same (or a nested) physical directory
    # would silently double-count and double-compress that content, and
    # would race each other inside the same transaction archive.
    for i, a in enumerate(items):
        for b in items[i + 1 :]:
            relation = _path_parts_relation(a.path, b.path)
            if relation is not None:
                raise ConfigError(
                    f"Configured sources {a.name!r} ({a.path}) and {b.name!r} ({b.path}) "
                    f"are the {relation} physical path; each BackupItem must be an "
                    "independent, non-overlapping directory."
                )


# ======================================================================
# 4. SevenZipRunner  (Section 21, 47)
# ======================================================================


@dataclass
class SevenZipResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def warning(self) -> bool:
        return self.returncode == 1

    @property
    def fatal(self) -> bool:
        return self.returncode not in (0, 1)


class SevenZipRunner:
    """Owns every invocation of the 7-Zip executable (Section 47).

    No shell string concatenation is used; all commands are passed as
    argument lists to subprocess (Section 21).
    """

    def __init__(self, exe: Optional[str] = None):
        self.exe = exe or self._detect()

    @staticmethod
    def _detect() -> str:
        for candidate in (SEVEN_ZIP_PATH, "7z", "7za", "7zr", "7z.exe"):
            if not candidate:
                continue
            found = shutil.which(candidate) if not os.path.isabs(candidate) else candidate
            if found and os.path.exists(found):
                return found
        raise DependencyError(
            "Could not locate a 7-Zip executable (tried 7z / 7za / 7zr on PATH "
            "and SEVEN_ZIP_PATH). Install 7-Zip or set SEVEN_ZIP_PATH."
        )

    def _run(self, args: list[str]) -> SevenZipResult:
        logger.debug("7z %s", " ".join(args))
        proc = subprocess.run(
            [self.exe, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            logger.debug("7z exit=%s stderr=%s", proc.returncode, proc.stderr.strip()[:2000])
        return SevenZipResult(args=args, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def version_check(self) -> str:
        result = self._run([])
        if result.fatal:
            raise DependencyError(f"7-Zip at {self.exe!r} did not respond as expected.")
        return result.stdout.splitlines()[0] if result.stdout else "unknown"

    def test(self, archive: Path) -> SevenZipResult:
        """`7z t <archive>` — Section 22, exit-code semantics in 21.3."""
        return self._run(["t", str(archive)])

    def list_technical(self, archive: Path) -> SevenZipResult:
        """`7z l -slt <archive>` — Section 19.1 / 40.2."""
        return self._run(["l", "-slt", str(archive)])

    def add_or_update(
        self,
        working_archive: Path,
        source_basename: str,
        cwd: Path,
        compression_level: int,
    ) -> SevenZipResult:
        """
        Add/synchronize `source_basename` (a single directory, resolved
        relative to `cwd`) into `working_archive` in place, using the
        update action-set from SEVENZIP_UPDATE_SWITCHES (Section 21, 33.3).

        IMPORTANT (verified against a real, current 7-Zip 23.01 build):
        7-Zip's documented `-u<state>!<newArchiveName>` output form does
        **not** leave the archive named on the command line untouched. In
        testing, invoking `7z u Backup.7z Documents -u...!staged.new`
        updated `Backup.7z` in place AND wrote only the incremental delta
        (not a full merged archive) to `staged.new`. That directly
        violates Invariant 4 (existing archive remains the primary
        known-good state until validated replacement).

        Because of that, this build never passes the real destination
        archive to any 7-Zip command that can write to it. Instead
        (Section 33.1's *intent* — avoid a redundant full copy — is
        overridden here per Design Principle 1, "correctness over
        convenience," since the optimization was empirically unsafe):

        `ArchiveTransactionManager` copies the existing archive to the
        `.new` transaction path *once* per update (or starts from an
        empty transaction path for `--new`), and every item in the
        transaction is synchronized in place against that transaction
        copy only, via this method. The original archive is never opened
        by a write-capable 7-Zip command until `os.replace()` publishes
        the validated transaction file over it.

        If a future 7-Zip build is confirmed (by a repeat of this test)
        to correctly implement the non-mutating `!newArchiveName` form,
        this method can be restored to use it and avoid the copy.
        """
        args = ["u", str(working_archive), source_basename, f"-mx={compression_level}", "-sse"]
        # -sse: stop archive creation if unable to open an input file, rather
        # than silently continuing and returning exit code 1 (Section 21.2).
        args.extend(SEVENZIP_UPDATE_SWITCHES)
        proc = subprocess.run(
            [self.exe, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return SevenZipResult(args=args, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def add_files(self, archive: Path, files: list[Path], cwd: Path, compression_level: int) -> SevenZipResult:
        """Add specific files (used for manifest.json at archive root)."""
        args = ["a", str(archive), *[str(f) for f in files], f"-mx={compression_level}"]
        proc = subprocess.run(
            [self.exe, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return SevenZipResult(args=args, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def rename(self, archive: Path, old_path: str, new_path: str) -> SevenZipResult:
        """`7z rn <archive> old new` — Section 37.2 Technique B."""
        return self._run(["rn", str(archive), old_path, new_path])

    def delete_paths(self, archive: Path, logical_paths: list[str]) -> SevenZipResult:
        """Delete an exact, explicit set of archive-relative paths.

        Section 19.1/40 already requires computing Added/Modified/
        Deleted precisely in Python from the pre/post inventory
        comparison rather than trusting 7-Zip's own sync heuristics.
        This method applies that Python-computed deletion set exactly,
        via an `@listfile` (`-i@listfile`) so it works for large sets
        and paths containing spaces/unicode without shell-quoting
        concerns, and — critically — never touches any path outside
        the explicit list (see SEVENZIP_UPDATE_SWITCHES comment for why
        a wildcard/sync-state switch is not used here).
        """
        if not logical_paths:
            return SevenZipResult(args=[], returncode=0, stdout="", stderr="")
        listfile = archive.parent / f".{archive.name}.{uuid.uuid4().hex[:8]}.dellist.txt"
        try:
            listfile.write_text("\n".join(logical_paths), encoding="utf-8")
            return self._run(["d", str(archive), f"-i@{listfile}"])
        finally:
            with contextlib.suppress(OSError):
                listfile.unlink()

    def extract_to_string(self, archive: Path, member: str) -> Optional[str]:
        """Extract a single small member (e.g. manifest.json) to stdout."""
        result = self._run(["e", str(archive), member, "-so", "-y"])
        if not result.ok and not result.warning:
            return None
        return result.stdout


# ======================================================================
# 5. Inventory model + SourceInventoryManager  (Section 5, 19.1, 40, 48B)
# ======================================================================


@dataclass
class InventoryEntry:
    logical_path: str  # archive-relative, forward-slash normalized
    is_dir: bool
    size: int = 0
    mtime_ns: int = 0
    crc: Optional[str] = None  # only meaningful for archive-side entries
    physical_path: Optional[Path] = None  # only meaningful for source-side entries;
    # lets compare_inventories re-read the real file on disk to resolve a
    # timestamp-tolerance ambiguity against an archive-side CRC (Section 19.1).


@dataclass
class SkippedEntry:
    source_path: str
    reason: str


@dataclass
class SourceInventory:
    entries: dict[str, InventoryEntry] = field(default_factory=dict)
    skipped: list[SkippedEntry] = field(default_factory=list)


def _is_link_like_dir(entry: os.DirEntry) -> bool:
    """Section 5.1: symlinks, junctions, other redirecting reparse points."""
    try:
        if entry.is_symlink():
            return True
        if platform.system() == "Windows":
            is_junction = getattr(os.path, "isjunction", None)
            if is_junction and is_junction(entry.path):
                return True
    except OSError:
        return True
    return False


class SourceInventoryManager:
    """Recursively scans configured sources (Section 5, 40.1, 48B)."""

    def scan(self, items: list[BackupItem]) -> SourceInventory:
        inventory = SourceInventory()
        for item in items:
            self._scan_root(item, inventory)
        return inventory

    def _scan_root(self, item: BackupItem, inventory: SourceInventory) -> None:
        root = item.path
        if not root.is_dir():
            # Caller is expected to have already validated availability
            # (Section 8) before calling scan(); this is defense in depth.
            raise BackupError(f"Required source unavailable: {root}")

        # A *nested* unreadable subdirectory is a partial-skip (handled by
        # walk()'s per-directory OSError catch below, recorded as a
        # SkippedEntry). But if the top-level configured root itself can't
        # be listed (e.g. permissions were revoked on the root directory,
        # while root.is_dir() above still succeeded because that only
        # needs execute permission on the *parent*), that is not a partial
        # skip — it means this required source is effectively unavailable
        # (Invariant 1), and silently treating it as an empty directory
        # would let a backup "succeed" while quietly dropping an entire
        # configured source's content.
        try:
            os.scandir(root).close()
        except OSError as exc:
            raise BackupError(f"Required source unavailable: {root} ({exc})")

        visited_identities: set[tuple] = set()

        def identity(path: Path) -> Optional[tuple]:
            try:
                st = path.stat()
                return (st.st_dev, st.st_ino)
            except OSError:
                return None

        root_id = identity(root)
        if root_id:
            visited_identities.add(root_id)

        # The root itself is always a directory entry, even if empty.
        inventory.entries[item.name] = InventoryEntry(
            logical_path=item.name, is_dir=True, mtime_ns=self._safe_mtime_ns(root)
        )

        def walk(current: Path, logical_prefix: str) -> None:
            try:
                scanner = os.scandir(current)
            except OSError as exc:
                inventory.skipped.append(SkippedEntry(str(current), f"unreadable directory: {exc}"))
                return
            with scanner:
                for entry in scanner:
                    logical = f"{logical_prefix}/{entry.name}"
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if _is_link_like_dir(entry):
                                inventory.skipped.append(
                                    SkippedEntry(entry.path, "excluded link/junction/reparse-point directory")
                                )
                                continue
                            ident = identity(Path(entry.path))
                            if ident is not None and ident in visited_identities:
                                inventory.skipped.append(
                                    SkippedEntry(entry.path, "excluded: directory identity already visited (loop guard)")
                                )
                                continue
                            if ident is not None:
                                visited_identities.add(ident)
                            inventory.entries[logical] = InventoryEntry(
                                logical_path=logical,
                                is_dir=True,
                                mtime_ns=self._safe_mtime_ns(Path(entry.path)),
                            )
                            walk(Path(entry.path), logical)
                        else:
                            if entry.is_symlink():
                                inventory.skipped.append(
                                    SkippedEntry(entry.path, "excluded: symlink file not dereferenced by default")
                                )
                                continue
                            st = entry.stat(follow_symlinks=False)
                            inventory.entries[logical] = InventoryEntry(
                                logical_path=logical,
                                is_dir=False,
                                size=st.st_size,
                                mtime_ns=st.st_mtime_ns,
                                physical_path=Path(entry.path),
                            )
                    except OSError as exc:
                        inventory.skipped.append(SkippedEntry(entry.path, f"unreadable: {exc}"))

        walk(root, item.name)

    @staticmethod
    def _safe_mtime_ns(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0


# ----------------------------------------------------------------------
# Archive-side technical listing parser (Section 19.1, 40.2)
# ----------------------------------------------------------------------


def parse_archive_listing(slt_output: str) -> dict[str, InventoryEntry]:
    """Parse `7z l -slt` output into logical_path -> InventoryEntry.

    The -slt format is a series of blank-line-separated blocks of
    "Key = Value" lines, one block per archive member. This parser treats
    it as structured key/value data rather than scraping human-oriented
    progress text (Section 19.1/40.2), as required.
    """
    entries: dict[str, InventoryEntry] = {}
    block: dict[str, str] = {}
    seen_separator = False

    def flush(block: dict[str, str]) -> None:
        path = block.get("Path")
        if not path:
            return
        logical = path.replace("\\", "/")
        is_dir = block.get("Folder") == "+" or block.get("Attributes", "").startswith("D")
        size = int(block.get("Size", "0") or "0")
        mtime_ns = 0
        modified = block.get("Modified")
        if modified:
            mtime_ns = _parse_7z_timestamp_to_ns(modified)
        crc = block.get("CRC") or None
        entries[logical] = InventoryEntry(logical_path=logical, is_dir=is_dir, size=size, mtime_ns=mtime_ns, crc=crc)

    for line in slt_output.splitlines():
        line = line.rstrip("\n")
        if not seen_separator:
            # `7z l -slt` prints one archive-level metadata block (Path =
            # the archive file itself, Type, Physical Size, ...) before a
            # line of dashes, followed by one block per archive member.
            # Without skipping to the separator, the archive's own path
            # gets parsed as if it were a member — it then looks like an
            # "unexpected extra file" during validation, since it can
            # never match anything in the source inventory.
            if line.strip().startswith("----"):
                seen_separator = True
            continue
        if not line.strip():
            if block:
                flush(block)
                block = {}
            continue
        if " = " in line:
            key, _, value = line.partition(" = ")
            block[key.strip()] = value.strip()
    if block:
        flush(block)
    return entries


def crc32_of_file(path: Path) -> Optional[str]:
    """Uppercase-hex CRC32 of a file's contents, matching 7-Zip's `-slt` CRC
    format, so it can be compared directly against an archive-side entry's
    stored CRC. Returns None if the file can't currently be read (e.g. it
    was deleted or became unreadable between the directory scan and this
    call) rather than raising, since the caller treats that as "could not
    resolve" and falls back to the undetermined classification.
    """
    import binascii

    crc = 0
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                crc = binascii.crc32(chunk, crc)
    except OSError:
        return None
    return f"{crc & 0xFFFFFFFF:08X}"


def _parse_7z_timestamp_to_ns(value: str) -> int:
    # Typical 7z -slt format: "2026-09-02 11:46:12.1234567"
    try:
        date_part, time_part = value.split(" ", 1)
        if "." in time_part:
            hms, frac = time_part.split(".", 1)
        else:
            hms, frac = time_part, "0"
        frac = (frac + "0000000")[:7]  # 100ns units -> pad
        dt = _dt.datetime.strptime(f"{date_part} {hms}", "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=_dt.timezone.utc)
        ns = int(dt.timestamp() * 1_000_000_000) + int(frac) * 100
        return ns
    except Exception:
        return 0


# ----------------------------------------------------------------------
# Comparison  (Section 19.1)
# ----------------------------------------------------------------------


@dataclass
class ChangeStats:
    added: int = 0
    modified: int = 0
    modified_undetermined: int = 0
    deleted: int = 0
    unchanged: int = 0
    added_dirs: int = 0
    deleted_dirs: int = 0
    deleted_paths: list = field(default_factory=list)  # exact archive-relative paths removed from source


def compare_inventories(
    previous_archive: dict[str, InventoryEntry],
    current_source: dict[str, InventoryEntry],
    tolerance_ns: int = TIMESTAMP_TOLERANCE_NS,
) -> ChangeStats:
    stats = ChangeStats()
    prev_keys = set(previous_archive)
    cur_keys = set(current_source)

    for key in cur_keys - prev_keys:
        entry = current_source[key]
        if entry.is_dir:
            stats.added_dirs += 1
        else:
            stats.added += 1

    for key in prev_keys - cur_keys:
        entry = previous_archive[key]
        stats.deleted_paths.append(key)
        if entry.is_dir:
            stats.deleted_dirs += 1
        else:
            stats.deleted += 1

    for key in prev_keys & cur_keys:
        old = previous_archive[key]
        new = current_source[key]
        if old.is_dir or new.is_dir:
            continue  # directories are structural, not "modified" by content
        if old.size != new.size:
            stats.modified += 1
            continue
        delta = abs(old.mtime_ns - new.mtime_ns)
        if delta == 0:
            stats.unchanged += 1
        elif delta > tolerance_ns:
            stats.modified += 1
        else:
            # Ambiguous within tolerance: the timestamps are too close to
            # trust on their own (Section 19.1 — some filesystems/transfer
            # tools round or jitter sub-second mtimes). Resolve it for real
            # by reading the current file's bytes and comparing CRC32
            # against the archive-side CRC, rather than leaving it
            # unresolved. This only runs for files that land in this narrow
            # window, so it's not a full re-hash of every file on every run.
            resolved = False
            if old.crc and new.physical_path is not None:
                live_crc = crc32_of_file(new.physical_path)
                if live_crc is not None:
                    resolved = True
                    if live_crc == old.crc:
                        stats.unchanged += 1
                    else:
                        stats.modified += 1
            if not resolved:
                # No archive-side CRC to compare against, or the file
                # couldn't be re-read (e.g. vanished mid-comparison): we
                # genuinely can't determine changed-vs-unchanged here.
                # Post-sync validation (validate_new_archive) is the
                # backstop for anything this leaves ambiguous.
                stats.modified_undetermined += 1
    return stats


# ======================================================================
# 6. ManifestManager  (Section 12, 36, 49)
# ======================================================================


@dataclass
class Manifest:
    format_version: int
    backup_uuid: str
    backup_version: int
    created: str
    last_modified: str
    items: list[dict]

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), indent=4)

    @staticmethod
    def from_json(text: str) -> "Manifest":
        data = json.loads(text)
        required = ["format_version", "backup_uuid", "backup_version", "created", "last_modified", "items"]
        missing = [k for k in required if k not in data]
        if missing:
            raise BackupError(f"manifest.json is missing required fields: {missing}")
        return Manifest(**{k: data[k] for k in required})


class ManifestManager:
    def create(self, items: list[BackupItem]) -> Manifest:
        ts = iso(now_utc())
        return Manifest(
            format_version=1,
            backup_uuid=str(uuid.uuid4()),
            backup_version=1,
            created=ts,
            last_modified=ts,
            items=[{"name": i.name, "source": str(i.path)} for i in items],
        )

    def update(self, previous: Manifest, items: list[BackupItem]) -> Manifest:
        return Manifest(
            format_version=previous.format_version,
            backup_uuid=previous.backup_uuid,
            backup_version=previous.backup_version + 1,
            created=previous.created,
            last_modified=iso(now_utc()),
            items=[{"name": i.name, "source": str(i.path)} for i in items],
        )

    def read_from_archive(self, runner: SevenZipRunner, archive: Path) -> Optional[Manifest]:
        text = runner.extract_to_string(archive, MANIFEST_FILENAME)
        if text is None:
            return None
        try:
            return Manifest.from_json(text)
        except (json.JSONDecodeError, BackupError):
            return None

    def detect_config_changes(self, manifest_items: list[dict], current: list[BackupItem]) -> dict:
        """Section 9 — deterministic configuration-change plan."""
        old_by_name = {i["name"]: i["source"] for i in manifest_items}
        new_by_name = {i.name: str(i.path) for i in current}

        removed = [n for n in old_by_name if n not in new_by_name]
        added = [n for n in new_by_name if n not in old_by_name]
        path_changed = [
            n for n in old_by_name if n in new_by_name and old_by_name[n] != new_by_name[n]
        ]

        # Section 9.5: a logical-name change is a managed-root rename, not an
        # unrelated remove+add. Detect it by matching an old (removed) name's
        # source path against a new (added) name's source path.
        renamed = []
        removed_remaining = list(removed)
        added_remaining = list(added)
        for old_name in list(removed_remaining):
            old_source = old_by_name[old_name]
            for new_name in list(added_remaining):
                if new_by_name[new_name] == old_source:
                    renamed.append({"old_name": old_name, "new_name": new_name, "source": old_source})
                    removed_remaining.remove(old_name)
                    added_remaining.remove(new_name)
                    break

        return {
            "removed": removed_remaining,
            "added": added_remaining,
            "renamed": renamed,
            "path_changed": path_changed,
            "has_changes": bool(removed_remaining or added_remaining or renamed or path_changed),
        }


# ======================================================================
# 7. ArchiveTransactionManager + ArchiveManager  (Section 33, 47, 48, 48A)
# ======================================================================


class ArchiveTransactionManager:
    """Owns the staged-new-archive transaction lifecycle (Section 33, 48A).

    It is the only component allowed to publish a replacement for the
    primary archive.
    """

    def __init__(self, runner: SevenZipRunner):
        self.runner = runner

    def new_transaction_path(self, destination: Path) -> Path:
        unique = uuid.uuid4().hex[:12]
        return destination.parent / f".{destination.name}.{unique}.new"

    def stage_copy(self, existing_archive: Optional[Path], txn_archive: Path) -> None:
        """Populate the transaction path before any item is synchronized.

        For --update: a real filesystem copy of the existing (known-good)
        archive. This is the one deliberate exception to "no redundant
        full-archive copy" (Section 33.1) — see add_or_update's docstring
        for the empirical reason it's required for correctness.
        For --new (existing_archive is None): nothing to copy; 7-Zip
        creates the transaction archive fresh on the first synchronize.
        """
        if existing_archive is not None:
            shutil.copy2(str(existing_archive), str(txn_archive))

    def preflight_space(self, destination: Path, estimated_needed_bytes: int) -> None:
        """Roadmap 0.4 — disk-space estimate, decided and documented:

        `estimated_needed_bytes` is intentionally a raw, *uncompressed*
        byte count, not a prediction of the compressed transaction size:
          - --new:    sum of configured source directory sizes.
          - --update: existing archive's on-disk size (what stage_copy()
                      will physically copy into the transaction path) +
                      sum of configured source directory sizes (the
                      uncompressed upper bound on what could still need
                      to be added/changed).
        This is a deliberate over-estimate, not a best-effort prediction:
        7-Zip's compressed output for any given input is essentially
        always <= that input's uncompressed size (the rare cases where
        compression slightly *expands* small/already-compressed data are
        covered by the flat 50 MiB headroom below, not by the 10%
        multiplier). So this bound is safe in the direction that matters
        — it will not let a job start that's actually going to run out of
        space — at the cost of being conservative: on a source tree that's
        mostly already-compressed media (photos/video), the real
        transaction may need meaningfully less room than this estimate
        asks for, which can make preflight_space reject a run that would
        have technically fit. That's the intended trade-off (a false
        "insufficient space" abort is recoverable; a mid-run ENOSPC into a
        transaction archive is a "silent failure late in a long-running
        job" risk per Section 46.1) — treat a rejection here as "free up
        space or free up margin," not as a bug, unless it's rejecting a
        run that has genuinely enormous headroom to spare.

        Known gap, not fixed here (see Roadmap 2.3): this estimate walks
        `_dir_size` per BackupItem, which is proportional to *file count*
        (one stat() per file), not just total bytes. On a source tree
        with hundreds of thousands of small files, per-file filesystem
        overhead (block rounding, directory entries) is not represented
        in `estimated_needed_bytes` at all — it only ever undercounts
        actual space consumed by the *source* re-scan, not by the
        transaction archive itself, so it doesn't threaten this specific
        check, but it does mean `_dir_size` is not a general-purpose
        "how much disk does this directory use" answer for any other
        caller that might reuse it.
        """
        usage = shutil.disk_usage(destination.parent)
        headroom = int(estimated_needed_bytes * 1.1) + 50 * 1024 * 1024
        if usage.free < headroom:
            raise BackupError(
                f"Insufficient free space for staged-new transaction in {destination.parent}: "
                f"need ~{human_size(headroom)}, have {human_size(usage.free)}."
            )

    def find_leftover_transactions(self, destination: Path) -> list[Path]:
        """Section 33.8 — scan for abandoned .new files from prior interrupted runs."""
        pattern = f".{destination.name}.*.new"
        return list(destination.parent.glob(pattern))

    def validate_new_archive(
        self,
        new_archive: Path,
        expected_source_inventory: dict[str, InventoryEntry],
    ) -> tuple[bool, list[str]]:
        """Roadmap 0.2 — documented, accepted limitation (not fixed here):

        This only checks *presence* (every expected logical path exists in
        the new archive, and no unexpected files linger) plus 7-Zip's own
        `7z t` structural/CRC-of-what-was-actually-stored integrity check.
        It does NOT re-compare each archived entry's size/mtime/CRC against
        `expected_source_inventory`'s values.

        That means a file modified *during* the run — after
        SourceInventoryManager.scan() recorded its pre-run size/mtime, but
        before 7-Zip read it for `add_or_update` — is invisible to this
        check: the archive will contain whatever bytes were on disk at the
        moment 7-Zip read the file, `7z t` will report that as internally
        consistent (its stored CRC matches its stored bytes, which is all
        `7z t` verifies), and this method will see the logical path present
        either way, since it does not check content. The backup completes
        successfully with content that no longer matches the source
        snapshot the run believed it was taking.

        This is accepted as a known limitation, not silently unhandled:
        the transaction window is short relative to typical file-modify
        patterns, `7z t` still catches on-disk archive corruption (a
        different, more likely failure mode), and closing this gap
        properly would mean re-scanning every source file's exact bytes a
        second time post-sync (re-reading the whole tree = doubling I/O
        cost of every backup) purely to catch a race that's already an
        edge case for a single-writer backup source. If write-in-progress
        sources become a real scenario (e.g. backing up a live database
        file, or Documents while an editor autosaves), the correct fix is
        source-level exclusion/quiescing before the run starts, not a
        heavier post-hoc check here.
        """
        problems: list[str] = []

        test_result = self.runner.test(new_archive)
        if test_result.fatal:
            problems.append(f"7z t failed with exit code {test_result.returncode}: {test_result.stderr.strip()}")
            return False, problems
        if test_result.warning:
            problems.append("7z t reported warnings (exit code 1); treating as not-clean for promotion.")
            return False, problems

        listing = self.runner.list_technical(new_archive)
        if not listing.ok:
            problems.append("Could not obtain technical listing of new archive for validation.")
            return False, problems

        # Detect duplicate archive members before they get collapsed into a
        # dict by parse_archive_listing (a duplicate path is a real 7-Zip
        # archive corruption/data-quality bug — e.g. it silently doubles a
        # file's stored bytes — but is invisible to a dict-keyed comparison
        # since the second occurrence just overwrites the first).
        raw_paths = [
            line.partition(" = ")[2].strip()
            for line in listing.stdout.splitlines()
            if line.startswith("Path = ")
        ][1:]  # [0] is the archive file's own header entry, see parse_archive_listing
        dup_counts: dict[str, int] = {}
        for p in raw_paths:
            dup_counts[p] = dup_counts.get(p, 0) + 1
        duplicates = {p: c for p, c in dup_counts.items() if c > 1}
        if duplicates:
            problems.append(
                f"{len(duplicates)} archive path(s) appear more than once "
                f"(e.g. {next(iter(duplicates))!r} x{next(iter(duplicates.values()))}) — "
                "archive is corrupt/duplicated and cannot be promoted."
            )

        archive_entries = parse_archive_listing(listing.stdout)

        managed = {k: v for k, v in expected_source_inventory.items()}
        archive_managed = {k: v for k, v in archive_entries.items() if k != MANIFEST_FILENAME}

        missing = set(managed) - set(archive_managed)
        extra_files = {
            k for k in (set(archive_managed) - set(managed))
            if not archive_managed[k].is_dir  # extra dirs can be legit ancestors; files must not linger
        }
        if missing:
            problems.append(f"{len(missing)} expected source entries are missing from the new archive.")
        if extra_files:
            problems.append(f"{len(extra_files)} unexpected files remain in the new archive after synchronization.")

        return (not problems), problems

    def publish(self, new_archive: Path, destination: Path) -> None:
        """Section 33.5 — atomic replace, same filesystem required by default."""
        if REQUIRE_SAME_FILESYSTEM_ATOMIC_REPLACE:
            try:
                same_fs = new_archive.parent.stat().st_dev == destination.parent.stat().st_dev
            except OSError:
                same_fs = False
            if not same_fs:
                raise BackupError(
                    "Refusing non-atomic replacement: new archive and destination are not "
                    "confirmed to be on the same filesystem."
                )
        os.replace(str(new_archive), str(destination))

    def cleanup(self, path: Path) -> Optional[str]:
        try:
            if path.exists():
                path.unlink()
            return None
        except OSError as exc:
            return f"Could not remove leftover transaction archive {path}: {exc}"


class ArchiveManager:
    """Synchronizes source content into the transaction archive (Section 48)."""

    def __init__(self, runner: SevenZipRunner):
        self.runner = runner

    def synchronize_item(
        self,
        item: BackupItem,
        transaction_archive: Path,
        compression_level: int,
        existing_roots: frozenset[str] = frozenset(),
    ) -> SevenZipResult:
        """Synchronize one source into the transaction archive in place.

        The real destination archive is never referenced here — the
        caller (ArchiveTransactionManager, via BackupManager) is
        responsible for having already copied it (or started fresh, for
        --new) into `transaction_archive` before any item is processed.
        See SevenZipRunner.add_or_update for why.

        `existing_roots` is the set of top-level path segments already
        present in the transaction archive (from the previous version,
        for --update; empty for --new). It matters only when the
        configured logical name differs from the source's physical
        basename (Technique B). VERIFIED BUG this guards against: 7-Zip
        always syncs by matching the *physical* basename on disk. Once a
        root has been renamed to its logical name (e.g. Documents ->
        MyDocs), a later update that just syncs "Documents" again and
        renames the result to "MyDocs" doesn't touch the existing
        "MyDocs" entries at all — it adds a second, disconnected
        "Documents"-derived tree and renames *that* to "MyDocs" too,
        producing duplicate archive members with the same path (silently
        doubling stored data; caught by validate_new_archive's duplicate
        check if this guard is ever bypassed). The fix: if the logical
        name already exists as a root in the archive, rename it back to
        the physical basename first, so 7-Zip's incremental "keep old
        data / add new data" matching works correctly, then rename
        forward to the logical name again afterward.
        """
        needs_rename = item.path.name != item.name
        if needs_rename and item.name in existing_roots:
            reverted = self.runner.rename(transaction_archive, item.name, item.path.name)
            if reverted.fatal:
                return reverted

        result = self.runner.add_or_update(
            working_archive=transaction_archive,
            source_basename=item.path.name,
            cwd=item.path.parent,
            compression_level=compression_level,
        )
        if result.fatal:
            return result
        if needs_rename:
            # Technique B: rename physical basename root -> configured logical name.
            renamed = self.runner.rename(transaction_archive, item.path.name, item.name)
            if renamed.fatal:
                return renamed
        return result

    def write_manifest(self, archive: Path, manifest: Manifest, scratch_dir: Path) -> SevenZipResult:
        scratch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = scratch_dir / MANIFEST_FILENAME
        manifest_path.write_text(manifest.to_json(), encoding="utf-8")
        return self.runner.add_files(archive, [Path(MANIFEST_FILENAME)], cwd=scratch_dir, compression_level=1)


# ======================================================================
# 8. HistoryManager  (Section 14-18A, 50)
# ======================================================================


class _CrossPlatformLock:
    """Exclusive advisory lock on a sidecar file (Section 18A.1)."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._fh = None

    def __enter__(self):
        self._fh = open(self.lock_path, "a+")
        try:
            if platform.system() == "Windows":
                import msvcrt

                msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            self._fh.close()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if platform.system() == "Windows":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()


@dataclass
class HistoryEntry:
    run_id: int
    status: str  # SUCCESS | FAILED
    operation: str  # NEW | UPDATE | VERIFY
    archive: str
    backup_uuid: Optional[str]
    backup_version: Optional[int]
    start: str
    end: str
    sha256: Optional[str]
    meta_line: str
    raw_text: str


class HistoryManager:
    def __init__(self, history_dir: Path):
        self.history_path = history_dir / HISTORY_FILENAME
        self.lock_path = history_dir / f"{HISTORY_FILENAME}.lock"
        self.history_dir = history_dir

    # -- pending-record reconciliation (Section 18A.3/18A.4) -----------

    def _pending_paths(self) -> list[Path]:
        return sorted(self.history_dir.glob(f".{HISTORY_FILENAME}.pending.*.json")) + \
               sorted(self.history_dir.glob(".backup_history.pending.*.json"))

    def reconcile_pending(self) -> None:
        pending = self._pending_paths()
        if not pending:
            return
        with _CrossPlatformLock(self.lock_path):
            for p in pending:
                try:
                    text = p.read_text(encoding="utf-8")
                    self._prepend_raw(text)
                    p.unlink()
                except OSError:
                    continue  # leave for a future run

    # -- writing ---------------------------------------------------------

    def _prepend_raw(self, entry_text: str) -> None:
        old = self.history_path.read_text(encoding="utf-8") if self.history_path.exists() else ""
        tmp = self.history_path.with_name(f".{HISTORY_FILENAME}.{uuid.uuid4().hex[:8]}.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(entry_text)
            fh.write(old)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(self.history_path))

    def record(self, entry_text: str, meta: dict) -> bool:
        """Attempt to prepend `entry_text` to the history file.

        Returns True if fully persisted, False if a pending record was
        written instead (Section 18A.3/18A.4) — caller must map this to
        exit code 5 rather than treating it as a backup failure.
        """
        self.reconcile_pending()
        delays = list(HISTORY_RETRY_BACKOFF_SECONDS)
        last_exc: Optional[Exception] = None
        for attempt in range(HISTORY_RETRY_ATTEMPTS):
            try:
                with _CrossPlatformLock(self.lock_path):
                    self._prepend_raw(entry_text)
                return True
            except OSError as exc:
                last_exc = exc
                if attempt < len(delays):
                    time.sleep(delays[attempt])
        # Blocked after retries: durable pending record (Section 18A.4).
        logger.warning(
            "history write failed after %d attempts (%s); writing durable pending record instead",
            HISTORY_RETRY_ATTEMPTS, last_exc,
        )
        pending_path = self.history_dir / f".{HISTORY_FILENAME}.pending.{meta.get('run_id', uuid.uuid4().hex)}.json"
        pending_path.write_text(json.dumps({"entry_text": entry_text, "meta": meta}), encoding="utf-8")
        return False

    def next_run_id(self) -> int:
        entries = self.read_entries()
        return (max((e.run_id for e in entries), default=0)) + 1

    # -- reading -----------------------------------------------------

    def read_entries(self) -> list[HistoryEntry]:
        if not self.history_path.exists():
            return []
        text = self.history_path.read_text(encoding="utf-8", errors="replace")
        entries: list[HistoryEntry] = []
        # The decorative "====" separators and prose sections are ignored;
        # only the machine-readable [BACKUP_META] line is parsed
        # (Section 18, "must not depend on decorative prose").
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("[BACKUP_META]"):
                try:
                    payload = json.loads(line[len("[BACKUP_META]"):].strip())
                except json.JSONDecodeError:
                    continue
                entries.append(
                    HistoryEntry(
                        run_id=payload.get("run_id", 0),
                        status=payload.get("status", "UNKNOWN"),
                        operation=payload.get("operation", "UNKNOWN"),
                        archive=payload.get("archive", ""),
                        backup_uuid=payload.get("backup_uuid"),
                        backup_version=payload.get("backup_version"),
                        start=payload.get("start", ""),
                        end=payload.get("completed", ""),
                        sha256=payload.get("sha256"),
                        meta_line=line,
                        raw_text="",
                    )
                )
        return entries

    def latest_successful(self) -> Optional[HistoryEntry]:
        for e in self.read_entries():
            if e.status == "SUCCESS" and e.sha256:
                return e
        return None

    def find_by_sha256(self, sha256: str) -> Optional[HistoryEntry]:
        for e in self.read_entries():
            if e.status == "SUCCESS" and e.sha256 == sha256:
                return e
        return None


# ======================================================================
# 9. History entry formatting  (Section 17, 18, 34)
# ======================================================================


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def format_history_entry(
    run_id: int,
    status: str,
    operation: str,
    archive: Path,
    start: _dt.datetime,
    end: _dt.datetime,
    backup_uuid: Optional[str] = None,
    backup_version: Optional[int] = None,
    sha256: Optional[str] = None,
    sources: Optional[list[BackupItem]] = None,
    change_stats: Optional[ChangeStats] = None,
    source_size: Optional[int] = None,
    archive_size: Optional[int] = None,
    warnings: Optional[list[str]] = None,
    errors: Optional[list[str]] = None,
    history_pending: bool = False,
) -> str:
    warnings = warnings or []
    errors = errors or []
    lines = []
    bar = "=" * 70
    lines.append(bar)
    lines.append(f"BACKUP ATTEMPT #{run_id}")
    lines.append(f"STATUS: {status}")
    lines.append(f"OPERATION: {operation}")
    lines.append(bar)
    lines.append("")
    lines.append("Timestamp:")
    lines.append(iso(end))
    lines.append("")
    lines.append("Archive:")
    lines.append(str(archive))
    lines.append("")
    if backup_uuid:
        lines.append("Backup UUID:")
        lines.append(backup_uuid)
        lines.append("")
    if backup_version is not None:
        lines.append("Backup Version:")
        lines.append(str(backup_version))
        lines.append("")
    lines.append("Start:")
    lines.append(iso(start))
    lines.append("")
    lines.append("End:")
    lines.append(iso(end))
    lines.append("")
    lines.append("Duration:")
    lines.append(human_duration((end - start).total_seconds()))
    lines.append("")
    lines.append("")

    if sources:
        lines.append("SOURCES")
        lines.append("-" * 70)
        lines.append("")
        for item in sources:
            lines.append(item.name)
            lines.append(f"    Source: {item.path}")
            lines.append("")
        lines.append("")

    if change_stats:
        lines.append("CHANGES")
        lines.append("-" * 70)
        lines.append("")
        lines.append(f"Added:       {change_stats.added} files, {change_stats.added_dirs} dirs")
        lines.append(f"Modified:    {change_stats.modified} files")
        if change_stats.modified_undetermined:
            lines.append(f"Modified/Undetermined: {change_stats.modified_undetermined} files")
        lines.append(f"Deleted:     {change_stats.deleted} files, {change_stats.deleted_dirs} dirs")
        lines.append(f"Unchanged:   {change_stats.unchanged} files")
        lines.append("")
        lines.append("")

    if source_size is not None or archive_size is not None or sha256:
        lines.append("DATA")
        lines.append("-" * 70)
        lines.append("")
        if source_size is not None:
            lines.append(f"Source size:          {human_size(source_size)}")
        if archive_size is not None:
            lines.append(f"Archive size:         {human_size(archive_size)}")
        if source_size and archive_size:
            ratio = 100.0 * (1 - archive_size / source_size)
            lines.append(f"Compression ratio:    {ratio:.1f} %")
        if sha256:
            lines.append("SHA-256:")
            lines.append(sha256)
        lines.append("")
        lines.append("")

    if warnings:
        lines.append("WARNINGS")
        lines.append("-" * 70)
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")
        lines.append("")

    if errors:
        lines.append("ERRORS")
        lines.append("-" * 70)
        for e in errors:
            lines.append(f"- {e}")
        lines.append("")
        lines.append("")

    if history_pending:
        lines.append("BACKUP ARCHIVE: SUCCESSFUL")
        lines.append("HISTORY PERSISTENCE: PENDING / RETRY REQUIRED")
        lines.append("")

    meta = {
        "run_id": run_id,
        "status": status,
        "operation": operation,
        "archive": str(archive),
        "backup_uuid": backup_uuid,
        "backup_version": backup_version,
        "sha256": sha256,
        "start": iso(start),
        "completed": iso(end),
    }
    lines.append(f"[BACKUP_META] {json.dumps(meta)}")
    lines.append("")
    lines.append(bar)
    lines.append("")
    lines.append("")
    return "\n".join(lines)


# ======================================================================
# 10. VerificationManager  (Section 24-28, 51, 57)
# ======================================================================


@dataclass
class VerificationResult:
    archive_exists: bool
    integrity_pass: Optional[bool]
    manifest_ok: bool
    sha256: Optional[str]
    match: Optional[HistoryEntry]
    is_latest: bool
    history_available: bool
    summary: str


class VerificationManager:
    def __init__(self, runner: SevenZipRunner, manifest_mgr: ManifestManager):
        self.runner = runner
        self.manifest_mgr = manifest_mgr

    def verify(self, archive: Path, history: Optional[HistoryManager]) -> VerificationResult:
        logger.info("verifying archive %s", archive)
        if not archive.exists():
            logger.error("verify: archive does not exist: %s", archive)
            return VerificationResult(False, None, False, None, None, False, history is not None,
                                       f"Archive does not exist: {archive}")

        test_result = self.runner.test(archive)
        integrity_pass = test_result.ok

        sha = sha256_of_file(archive)

        manifest = self.manifest_mgr.read_from_archive(self.runner, archive)
        manifest_ok = manifest is not None

        history_available = history is not None and history.history_path.exists()
        match = history.find_by_sha256(sha) if (history and history_available) else None
        latest = history.latest_successful() if (history and history_available) else None
        is_latest = bool(match and latest and match.run_id == latest.run_id)

        if not integrity_pass:
            summary = "ARCHIVE INTEGRITY FAILURE"
        elif not history_available:
            summary = "ARCHIVE VERIFIED, HISTORICAL COMPARISON UNAVAILABLE"
        elif match and is_latest:
            summary = "VALID — LATEST BACKUP"
        elif match:
            summary = (
                f"VALID — OLDER BACKUP (provided version #{match.backup_version}, "
                f"latest #{latest.backup_version if latest else '?'})"
            )
        else:
            summary = "VALID ARCHIVE — UNKNOWN HISTORY STATE (checksum not found in history)"

        (logger.info if integrity_pass else logger.error)("verify: %s (%s)", summary, archive)
        return VerificationResult(
            archive_exists=True,
            integrity_pass=integrity_pass,
            manifest_ok=manifest_ok,
            sha256=sha,
            match=match,
            is_latest=is_latest,
            history_available=history_available,
            summary=summary,
        )


# ======================================================================
# 11. BackupManager  (Section 6, 8, 9, 33, 52)
# ======================================================================


class ProcessResult:
    def __init__(self, ok: bool, exit_code: int, message: str):
        self.ok = ok
        self.exit_code = exit_code
        self.message = message


class BackupManager:
    def __init__(self, app_dir: Path, compression_level: int = COMPRESSION_LEVEL):
        self.app_dir = app_dir.resolve()
        self.compression_level = compression_level
        self.runner = SevenZipRunner()
        self.inventory_mgr = SourceInventoryManager()
        self.manifest_mgr = ManifestManager()
        self.archive_mgr = ArchiveManager(self.runner)
        self.txn_mgr = ArchiveTransactionManager(self.runner)
        self.history = HistoryManager(app_dir)

    # -- shared helpers ------------------------------------------------

    def _validate_sources(self, items: list[BackupItem]) -> None:
        missing = [i for i in items if not i.path.is_dir()]
        if missing:
            details = "\n".join(f"  {i.name} -> {i.path}" for i in missing)
            raise BackupError(f"Required source(s) unavailable:\n{details}\nNo archive modifications were performed.")

    def _write_history(self, entry_text: str, meta: dict) -> bool:
        return self.history.record(entry_text, meta)

    def _leftover_transaction_warnings(self, archive: Path) -> list[str]:
        """Section 33.8 — report (never auto-promote) abandoned .new files
        left behind by a previous interrupted run targeting this archive."""
        leftovers = self.txn_mgr.find_leftover_transactions(archive)
        return [
            f"Leftover transaction archive from a previous interrupted run: {p} "
            "(not used; validate manually or delete it)."
            for p in leftovers
        ]

    # -- New -------------------------------------------------------------

    def new_backup(self, items: list[BackupItem]) -> ProcessResult:
        start = now_utc()
        self.history.reconcile_pending()
        run_id = self.history.next_run_id()
        archive = (self.app_dir / ARCHIVE_NAME).resolve()
        leftover_warnings = self._leftover_transaction_warnings(archive)
        for w in leftover_warnings:
            logger.warning(w)
        logger.info("run #%s: starting NEW backup -> %s (%d source item(s))", run_id, archive, len(items))

        try:
            validate_configuration(items)
            if archive.exists():
                raise BackupError(
                    f"{ARCHIVE_NAME} already exists. Use --update to update an existing archive, "
                    "or move/rename the existing archive before creating a new backup."
                )
            self._validate_sources(items)

            txn_archive = self.txn_mgr.new_transaction_path(archive)
            estimated = sum(self._dir_size(i.path) for i in items)
            self.txn_mgr.preflight_space(archive, estimated)
            self.txn_mgr.stage_copy(None, txn_archive)  # --new: nothing to copy

            source_inventory = self.inventory_mgr.scan(items)
            for s in source_inventory.skipped:
                logger.warning("skipped during scan: %s (%s)", s.source_path, s.reason)

            for item in items:
                result = self.archive_mgr.synchronize_item(item, txn_archive, self.compression_level)
                if result.fatal:
                    self.txn_mgr.cleanup(txn_archive)
                    raise BackupError(f"7-Zip failed while adding {item.name!r} (exit {result.returncode}): {result.stderr.strip()}")
                if result.warning:
                    self.txn_mgr.cleanup(txn_archive)
                    raise BackupError(f"7-Zip reported a warning (exit code 1) while adding {item.name!r}; backup not promoted.")

            manifest = self.manifest_mgr.create(items)
            manifest_result = self.archive_mgr.write_manifest(txn_archive, manifest, self.app_dir / ".manifest_scratch")
            if manifest_result.fatal:
                self.txn_mgr.cleanup(txn_archive)
                raise BackupError("Failed to write manifest.json into the new archive.")

            ok, problems = self.txn_mgr.validate_new_archive(txn_archive, source_inventory.entries)
            if not ok:
                self.txn_mgr.cleanup(txn_archive)
                raise BackupError("New archive failed validation:\n" + "\n".join(problems))

            sha = sha256_of_file(txn_archive)
            self.txn_mgr.publish(txn_archive, archive)

            end = now_utc()
            stats = ChangeStats(added=sum(1 for e in source_inventory.entries.values() if not e.is_dir),
                                 added_dirs=sum(1 for e in source_inventory.entries.values() if e.is_dir))
            entry_text = format_history_entry(
                run_id, "SUCCESS", "NEW", archive, start, end,
                backup_uuid=manifest.backup_uuid, backup_version=manifest.backup_version, sha256=sha,
                sources=items, change_stats=stats, archive_size=archive.stat().st_size,
                source_size=estimated,
                warnings=[f"{s.source_path}: {s.reason}" for s in source_inventory.skipped] + leftover_warnings,
            )
            persisted = self._write_history(entry_text, {"run_id": run_id})

            if not persisted:
                logger.warning("run #%s: NEW backup succeeded but history persistence is pending (exit 5)", run_id)
                return ProcessResult(True, 5, f"Backup created (version {manifest.backup_version}); history persistence pending.")
            logger.info("run #%s: NEW backup SUCCESS, version %s, sha256=%s", run_id, manifest.backup_version, sha)
            return ProcessResult(True, 0, f"Backup created successfully. Version {manifest.backup_version}. SHA-256: {sha}")

        except BackupError as exc:
            end = now_utc()
            logger.error("run #%s: NEW backup FAILED: %s", run_id, exc)
            entry_text = format_history_entry(run_id, "FAILED", "NEW", archive, start, end, errors=[str(exc)])
            self._write_history(entry_text, {"run_id": run_id})
            return ProcessResult(False, 1, str(exc))

    # -- Dry run -------------------------------------------------------

    def dry_run_update(self, archive: Path, items: list[BackupItem]) -> ProcessResult:
        """Section 9.6 / 60 — print the configuration-change plan and source
        availability without modifying the archive."""
        try:
            validate_configuration(items)
            archive = archive.resolve() if archive.exists() else archive.absolute()
            if not archive.exists():
                raise BackupError(f"Archive does not exist: {archive}")

            previous_manifest = self.manifest_mgr.read_from_archive(self.runner, archive)
            if previous_manifest is None:
                raise BackupError(
                    f"{archive} does not contain a valid manifest.json; it cannot be confidently "
                    "identified as belonging to this backup utility."
                )

            missing = [i for i in items if not i.path.is_dir()]
            plan = self.manifest_mgr.detect_config_changes(previous_manifest.items, items)
            leftovers = self.txn_mgr.find_leftover_transactions(archive)

            lines = ["DRY RUN — no archive modifications were performed.", ""]
            lines.append(f"Archive: {archive}")
            lines.append(f"Backup UUID: {previous_manifest.backup_uuid}")
            lines.append(f"Current version: {previous_manifest.backup_version}")
            lines.append("")
            lines.append("Source availability:")
            for i in items:
                status = "MISSING" if i in missing else "OK"
                lines.append(f"  {i.name} -> {i.path}  [{status}]")
            lines.append("")
            lines.append("Configuration-change plan:")
            lines.append(f"  Added:   {plan['added']}")
            lines.append(f"  Removed: {plan['removed']}")
            lines.append(f"  Renamed: {plan.get('renamed', [])}")
            lines.append(f"  Path changed: {plan['path_changed']}")
            if plan["has_changes"]:
                lines.append("  -> --accept-config-changes (or interactive confirmation) would be required.")
            if leftovers:
                lines.append("")
                lines.append("Leftover transaction archives found (not used):")
                for p in leftovers:
                    lines.append(f"  {p}")
            if missing:
                lines.append("")
                lines.append("An update would ABORT: required source(s) unavailable.")
            return ProcessResult(True, 0, "\n".join(lines))
        except BackupError as exc:
            return ProcessResult(False, 1, f"DRY RUN — plan could not be produced: {exc}")

    # -- Update ------------------------------------------------------

    def update_backup(self, archive: Path, items: list[BackupItem], accept_config_changes: bool, interactive_confirm=None) -> ProcessResult:
        start = now_utc()
        self.history.reconcile_pending()
        run_id = self.history.next_run_id()
        logger.info("run #%s: starting UPDATE -> %s (%d source item(s))", run_id, archive, len(items))

        try:
            validate_configuration(items)
            archive = archive.resolve() if archive.exists() else archive.absolute()
            if not archive.exists():
                raise BackupError(f"Archive does not exist: {archive}")
            leftover_warnings = self._leftover_transaction_warnings(archive)
            for w in leftover_warnings:
                logger.warning(w)

            previous_manifest = self.manifest_mgr.read_from_archive(self.runner, archive)
            if previous_manifest is None:
                raise BackupError(
                    f"{archive} does not contain a valid manifest.json; it cannot be confidently "
                    "identified as belonging to this backup utility."
                )

            self._validate_sources(items)

            plan = self.manifest_mgr.detect_config_changes(previous_manifest.items, items)
            if plan["has_changes"]:
                logger.info("run #%s: configuration changes detected: %s", run_id, plan)
                if not accept_config_changes:
                    if interactive_confirm and interactive_confirm(plan):
                        pass
                    else:
                        raise BackupError(
                            "Configuration changes detected (added="
                            f"{plan['added']}, removed={plan['removed']}, "
                            f"renamed={plan.get('renamed', [])}, path_changed={plan['path_changed']}). "
                            "Re-run with --accept-config-changes to apply this change, or confirm interactively."
                        )

            txn_archive = self.txn_mgr.new_transaction_path(archive)
            estimated = archive.stat().st_size + sum(self._dir_size(i.path) for i in items)
            self.txn_mgr.preflight_space(archive, estimated)

            pre_listing = self.runner.list_technical(archive)
            previous_archive_inventory = parse_archive_listing(pre_listing.stdout) if pre_listing.ok else {}
            # Section 40.5: manifest.json at the exact archive root is
            # application metadata, not managed source content — it must
            # never be classified as "deleted" just because the current
            # source scan (which only covers configured source roots)
            # doesn't contain it.
            previous_archive_inventory.pop(MANIFEST_FILENAME, None)

            source_inventory = self.inventory_mgr.scan(items)
            for s in source_inventory.skipped:
                logger.warning("skipped during scan: %s (%s)", s.source_path, s.reason)
            change_stats = compare_inventories(previous_archive_inventory, source_inventory.entries)
            existing_roots = frozenset(k.split("/", 1)[0] for k in previous_archive_inventory)

            try:
                self.txn_mgr.stage_copy(archive, txn_archive)
            except OSError as exc:
                self.txn_mgr.cleanup(txn_archive)
                raise BackupError(f"Failed to stage transaction copy of {archive}: {exc}")

            for item in items:
                result = self.archive_mgr.synchronize_item(item, txn_archive, self.compression_level, existing_roots)
                if result.fatal:
                    self.txn_mgr.cleanup(txn_archive)
                    raise BackupError(f"7-Zip failed while synchronizing {item.name!r} (exit {result.returncode}): {result.stderr.strip()}")
                if result.warning:
                    self.txn_mgr.cleanup(txn_archive)
                    raise BackupError(f"7-Zip reported a warning (exit code 1) while synchronizing {item.name!r}; update not promoted.")

            roots_to_remove = list(plan["removed"]) + [r["old_name"] for r in plan.get("renamed", [])]
            for removed_name in roots_to_remove:
                # Explicit deletion of a removed/renamed-away logical root,
                # scoped to that root only. Two patterns are needed: the
                # root's own directory entry ("Photos") is not matched by
                # a "Photos/*" mask (verified: that mask only matches
                # children), so both must be deleted or the empty root
                # directory entry silently survives as an orphan.
                del_result = self.runner._run(["d", str(txn_archive), removed_name, f"{removed_name}/*", "-r"])
                if del_result.fatal:
                    self.txn_mgr.cleanup(txn_archive)
                    raise BackupError(f"Failed to remove logical root {removed_name!r} for accepted configuration change.")

            # Section 7/19.1/40: files & directories removed from a still-
            # configured source (normal update-time deletions) are deleted
            # by exact path, computed in Python from the inventory diff —
            # never by a 7-Zip sync/delete switch (see SEVENZIP_UPDATE_
            # SWITCHES comment for why that approach is unsafe here).
            # Paths already covered by a whole-root removal above are
            # skipped to avoid a redundant/no-op delete call.
            already_removed_roots = tuple(f"{r}/" for r in roots_to_remove)
            remaining_deleted_paths = [
                p for p in change_stats.deleted_paths
                if not any(p == r.rstrip("/") or p.startswith(r) for r in already_removed_roots)
            ]
            if remaining_deleted_paths:
                del_result = self.runner.delete_paths(txn_archive, remaining_deleted_paths)
                if del_result.fatal:
                    self.txn_mgr.cleanup(txn_archive)
                    raise BackupError(
                        f"Failed to remove {len(remaining_deleted_paths)} source-deleted entr(y/ies) "
                        f"from the new archive (exit {del_result.returncode}): {del_result.stderr.strip()}"
                    )

            manifest = self.manifest_mgr.update(previous_manifest, items)
            manifest_result = self.archive_mgr.write_manifest(txn_archive, manifest, self.app_dir / ".manifest_scratch")
            if manifest_result.fatal:
                self.txn_mgr.cleanup(txn_archive)
                raise BackupError("Failed to update manifest.json in the new archive.")

            ok, problems = self.txn_mgr.validate_new_archive(txn_archive, source_inventory.entries)
            if not ok:
                self.txn_mgr.cleanup(txn_archive)
                raise BackupError("New archive failed post-update validation:\n" + "\n".join(problems))

            sha = sha256_of_file(txn_archive)
            self.txn_mgr.publish(txn_archive, archive)

            end = now_utc()
            entry_text = format_history_entry(
                run_id, "SUCCESS", "UPDATE", archive, start, end,
                backup_uuid=manifest.backup_uuid, backup_version=manifest.backup_version, sha256=sha,
                sources=items, change_stats=change_stats, archive_size=archive.stat().st_size,
                warnings=[f"{s.source_path}: {s.reason}" for s in source_inventory.skipped]
                + ([f"Configuration change applied: {plan}"] if plan["has_changes"] else [])
                + leftover_warnings,
            )
            persisted = self._write_history(entry_text, {"run_id": run_id})

            if not persisted:
                logger.warning("run #%s: UPDATE succeeded but history persistence is pending (exit 5)", run_id)
                return ProcessResult(True, 5, f"Backup updated (version {manifest.backup_version}); history persistence pending.")
            logger.info("run #%s: UPDATE SUCCESS, version %s, sha256=%s", run_id, manifest.backup_version, sha)
            return ProcessResult(True, 0, f"Backup updated successfully. Version {manifest.backup_version}. SHA-256: {sha}")

        except BackupError as exc:
            end = now_utc()
            logger.error("run #%s: UPDATE FAILED: %s", run_id, exc)
            entry_text = format_history_entry(run_id, "FAILED", "UPDATE", archive, start, end, errors=[str(exc)])
            self._write_history(entry_text, {"run_id": run_id})
            return ProcessResult(False, 1, str(exc))

    @staticmethod
    def _dir_size(path: Path) -> int:
        total = 0
        try:
            for root, dirs, files in os.walk(path):
                for f in files:
                    with contextlib.suppress(OSError):
                        total += (Path(root) / f).stat().st_size
        except OSError:
            pass
        return total


# ======================================================================
# 12. CLI / main  (Section 29-32, 60)
# ======================================================================


def _print_verification(result: VerificationResult) -> None:
    print("ARCHIVE VERIFICATION")
    print()
    if not result.archive_exists:
        print(result.summary)
        return
    print(f"7-Zip integrity: {'PASS' if result.integrity_pass else 'FAIL'}")
    print(f"Manifest:        {'PASS' if result.manifest_ok else 'FAIL/MISSING'}")
    print(f"SHA-256:         {result.sha256}")
    print(f"History:         {'available' if result.history_available else 'UNAVAILABLE'}")
    if not result.history_available:
        print()
        print(f"WARNING: {HISTORY_FILENAME} missing.")
        print("Historical checksum comparison skipped.")
    print()
    print(f"Result: {result.summary}")


def _interactive_menu(app_dir: Path) -> int:
    setup_logging(app_dir)
    print("Backup Utility\n")
    print("What would you like to do?\n")
    print("[1] Create new backup")
    print("[2] Update existing backup")
    print("[3] Verify backup")
    print("[Q] Quit")
    choice = input("> ").strip().lower()

    manager = BackupManager(app_dir)

    if choice == "1":
        result = manager.new_backup(BACKUP_ITEMS)
        print(result.message)
        return result.exit_code
    elif choice == "2":
        archive_str = input("Path to backup archive:\n> ").strip().strip('"')
        archive = Path(archive_str)

        def confirm(plan):
            print("The following configuration changes were detected:")
            print(json.dumps(plan, indent=2))
            return input("Continue? [y/N] ").strip().lower() == "y"

        result = manager.update_backup(archive, BACKUP_ITEMS, accept_config_changes=False, interactive_confirm=confirm)
        print(result.message)
        return result.exit_code
    elif choice == "3":
        archive_str = input("Path to backup archive:\n> ").strip().strip('"')
        archive = Path(archive_str)
        runner = SevenZipRunner()
        verifier = VerificationManager(runner, ManifestManager())
        history_dir = app_dir
        if not (app_dir / HISTORY_FILENAME).exists():
            print(f"\n{HISTORY_FILENAME} could not be found beside app.py.")
            print("Historical checksum comparison is unavailable.")
            alt = input(
                "Path to backup_history.txt (leave blank to continue without history):\n> "
            ).strip().strip('"')
            if alt:
                alt_path = Path(alt)
                history_dir = alt_path.parent if alt_path.name == HISTORY_FILENAME else alt_path
        history = HistoryManager(history_dir)
        result = verifier.verify(archive, history)
        _print_verification(result)
        return 0 if result.integrity_pass else 4
    else:
        print("Goodbye.")
        return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backup Utility (7-Zip based)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--new", action="store_true", help="Create a new backup archive.")
    group.add_argument("--update", metavar="ARCHIVE", help="Update an existing backup archive.")
    group.add_argument("--verify", metavar="ARCHIVE", help="Verify a backup archive.")
    parser.add_argument("--accept-config-changes", action="store_true",
                         help="Acknowledge and apply detected configuration changes (--update only).")
    parser.add_argument("--dry-run", action="store_true",
                         help="Print the configuration-change plan / source-availability check without "
                              "modifying the archive (--update only).")
    parser.add_argument("--sevenzip", metavar="PATH", help="Path to the 7-Zip executable.")
    parser.add_argument("--history", metavar="PATH", help="Path to backup_history.txt (defaults beside app.py).")
    parser.add_argument("--compression", type=int, metavar="LEVEL", default=COMPRESSION_LEVEL,
                         help="Compression level 0-9 (default: %(default)s).")
    parser.add_argument("--log-level", metavar="LEVEL", default="INFO",
                         choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                         help="Operational log verbosity written to backup.log (default: %(default)s).")
    parser.add_argument("--log-file", metavar="PATH",
                         help=f"Path to the operational log file (default: {LOG_FILENAME} beside app.py).")
    parser.epilog = (
        "Exit codes: 0 success | 1 backup/operation error | 2 configuration error | "
        "3 missing 7-Zip dependency | 4 verification failed | 5 backup succeeded, "
        "history persistence pending (see EXIT_CODES.md)."
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    global SEVEN_ZIP_PATH
    args = build_arg_parser().parse_args(argv)
    app_dir = Path(__file__).resolve().parent

    setup_logging(app_dir, level=args.log_level, log_file=Path(args.log_file) if args.log_file else None)

    if args.sevenzip:
        SEVEN_ZIP_PATH = args.sevenzip

    try:
        validate_configuration(BACKUP_ITEMS)
    except ConfigError as exc:
        print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        if args.new:
            manager = BackupManager(app_dir, compression_level=args.compression)
            result = manager.new_backup(BACKUP_ITEMS)
            print(result.message)
            return result.exit_code

        if args.update:
            manager = BackupManager(app_dir, compression_level=args.compression)
            if args.dry_run:
                result = manager.dry_run_update(Path(args.update), BACKUP_ITEMS)
                print(result.message)
                return result.exit_code
            result = manager.update_backup(
                Path(args.update), BACKUP_ITEMS, accept_config_changes=args.accept_config_changes
            )
            print(result.message)
            return result.exit_code

        if args.dry_run:
            print("--dry-run is only meaningful together with --update.", file=sys.stderr)
            return 2

        if args.verify:
            runner = SevenZipRunner()
            verifier = VerificationManager(runner, ManifestManager())
            history_dir = Path(args.history).parent if args.history else app_dir
            history = HistoryManager(history_dir)
            result = verifier.verify(Path(args.verify), history)
            _print_verification(result)
            return 0 if result.integrity_pass else 4

        # No command given -> interactive mode (Section 29).
        return _interactive_menu(app_dir)

    except DependencyError as exc:
        print(f"DEPENDENCY ERROR: {exc}", file=sys.stderr)
        return 3
    except ConfigError as exc:
        print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
