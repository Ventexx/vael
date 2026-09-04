"""
Roadmap 2.2 — concurrent-invocation protection.

These tests cover the locking mechanism itself (_CrossPlatformLock in
non-blocking mode) and BackupManager._with_archive_lock's rejection path
in isolation. Neither needs a real 7-Zip binary: the lock-busy path is
specifically designed to fail before any 7-Zip call is made, and that
property is itself part of what's being tested (a rejected concurrent
run must not touch the archive or shell out to 7z at all).

The end-to-end version of this test — two real update_backup() calls
racing for the same archive — lives in test_integration.py, since it
needs a real 7z process to create a realistic race window.
"""
import threading
import time

import pytest

import backup
from backup import (
    BackupManager,
    LockBusyError,
    ProcessResult,
    _CrossPlatformLock,
)


# ---------------------------------------------------------------------
# _CrossPlatformLock(blocking=False)
# ---------------------------------------------------------------------


def test_nonblocking_lock_fails_fast_when_already_held(tmp_path):
    lock_path = tmp_path / "test.lock"
    holder = _CrossPlatformLock(lock_path, blocking=False)
    holder.__enter__()
    try:
        start = time.monotonic()
        with pytest.raises(LockBusyError):
            with _CrossPlatformLock(lock_path, blocking=False):
                pass
        elapsed = time.monotonic() - start
        assert elapsed < 0.5, "non-blocking lock must fail immediately, not wait"
    finally:
        holder.__exit__(None, None, None)


def test_nonblocking_lock_succeeds_once_released(tmp_path):
    lock_path = tmp_path / "test.lock"
    with _CrossPlatformLock(lock_path, blocking=False):
        pass
    # Lock released at end of `with` — a fresh non-blocking acquire must
    # succeed rather than seeing a stale contention.
    with _CrossPlatformLock(lock_path, blocking=False):
        pass


def test_blocking_lock_unchanged_default_behavior(tmp_path):
    """blocking=True (the default, used by HistoryManager) must keep
    working exactly as before — no exception, just waits/acquires."""
    lock_path = tmp_path / "test.lock"
    with _CrossPlatformLock(lock_path):
        pass
    with _CrossPlatformLock(lock_path):
        pass


def test_nonblocking_lock_released_by_second_holder_after_first_exits(tmp_path):
    lock_path = tmp_path / "test.lock"
    acquired_second = threading.Event()

    def hold_then_release():
        with _CrossPlatformLock(lock_path, blocking=False):
            time.sleep(0.2)

    t = threading.Thread(target=hold_then_release)
    t.start()
    time.sleep(0.05)  # let the thread acquire first

    with pytest.raises(LockBusyError):
        with _CrossPlatformLock(lock_path, blocking=False):
            pass

    t.join()

    # Now that the holder thread is done, the lock must be free again.
    with _CrossPlatformLock(lock_path, blocking=False):
        acquired_second.set()
    assert acquired_second.is_set()


# ---------------------------------------------------------------------
# BackupManager._with_archive_lock rejection path
# ---------------------------------------------------------------------


@pytest.fixture
def fake_7z(monkeypatch):
    """BackupManager's constructor always instantiates a SevenZipRunner,
    which probes PATH for a real binary. These tests only exercise the
    lock-busy rejection path (which returns before any 7z call), so a
    fake, never-invoked executable path is enough to satisfy the
    constructor without needing a real 7-Zip on this machine."""
    monkeypatch.setattr(backup.SevenZipRunner, "_detect", staticmethod(lambda: "/fake/7z"))


def test_with_archive_lock_rejects_when_lock_held(tmp_path, fake_7z):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    manager = BackupManager(app_dir)
    archive = (app_dir / backup.ARCHIVE_NAME).resolve()
    lock_path = archive.parent / f".{archive.name}.lock"

    held = _CrossPlatformLock(lock_path, blocking=False)
    held.__enter__()
    try:
        result = manager.new_backup([])
    finally:
        held.__exit__(None, None, None)

    assert isinstance(result, ProcessResult)
    assert result.ok is False
    assert result.exit_code == 1
    assert "already" in result.message.lower()
    assert str(archive) in result.message

    # A FAILED entry should be recorded in history so the contention is
    # visible in the audit trail, not just swallowed.
    history_file = app_dir / backup.HISTORY_FILENAME
    assert history_file.exists()
    assert "FAILED" in history_file.read_text(encoding="utf-8")


def test_with_archive_lock_releases_after_body_completes(tmp_path, fake_7z):
    """The lock must not be left held after run_body returns normally,
    so a subsequent run against the same archive can proceed."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    manager = BackupManager(app_dir)
    archive = (app_dir / backup.ARCHIVE_NAME).resolve()

    calls = []
    result = manager._with_archive_lock(archive, "NEW", lambda: (calls.append(1), ProcessResult(True, 0, "ok"))[-1])
    assert result.ok is True
    assert calls == [1]

    # Lock must be free now — acquiring it directly must succeed.
    lock_path = archive.parent / f".{archive.name}.lock"
    with _CrossPlatformLock(lock_path, blocking=False):
        pass


def test_with_archive_lock_releases_after_body_raises(tmp_path, fake_7z):
    """The lock must also be released if run_body raises, not just on
    the normal-return path."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    manager = BackupManager(app_dir)
    archive = (app_dir / backup.ARCHIVE_NAME).resolve()

    def boom():
        raise RuntimeError("simulated failure inside the locked body")

    with pytest.raises(RuntimeError):
        manager._with_archive_lock(archive, "NEW", boom)

    lock_path = archive.parent / f".{archive.name}.lock"
    with _CrossPlatformLock(lock_path, blocking=False):
        pass
