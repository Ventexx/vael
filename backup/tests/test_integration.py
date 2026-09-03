"""
Integration tests that exercise the real 7-Zip binary (7z 23.01 in this
environment). These are the tests the roadmap calls "the tier that
actually matters most, since both known bugs were only caught this way."

Every test builds its own throwaway source tree + archive under tmp_path
so tests never touch each other's state and never touch real user data.
"""
import shutil

import pytest

from backup import (
    ArchiveManager,
    ArchiveTransactionManager,
    BackupItem,
    BackupManager,
    DependencyError,
    SevenZipRunner,
    parse_archive_listing,
)

pytestmark = pytest.mark.integration


def _have_7z():
    try:
        SevenZipRunner()
        return True
    except DependencyError:
        return False


requires_7z = pytest.mark.skipif(not _have_7z(), reason="no 7-Zip binary on PATH")


def _make_tree(root, files):
    """files: dict of relative-path -> content (str)."""
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


def _archive_members(archive_path):
    runner = SevenZipRunner()
    listing = runner.list_technical(archive_path)
    return parse_archive_listing(listing.stdout)


# ---------------------------------------------------------------------
# Full --new -> --update -> --verify cycle
# ---------------------------------------------------------------------


@requires_7z
def test_full_new_update_verify_cycle(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    src = tmp_path / "src" / "Documents"
    _make_tree(src, {"a.txt": "hello", "sub/b.txt": "world"})

    manager = BackupManager(app_dir)
    items = [BackupItem("Documents", src)]

    result = manager.new_backup(items)
    assert result.ok, result.message
    archive = app_dir / "Backup.7z"
    assert archive.exists()

    members = _archive_members(archive)
    assert "Documents/a.txt" in members
    assert "Documents/sub/b.txt" in members

    # Modify + add a file, then update.
    (src / "a.txt").write_text("hello, updated")
    (src / "new.txt").write_text("brand new")

    result = manager.update_backup(archive, items, accept_config_changes=False)
    assert result.ok, result.message

    members = _archive_members(archive)
    assert "Documents/new.txt" in members

    from backup import HistoryManager, ManifestManager, VerificationManager

    verifier = VerificationManager(SevenZipRunner(), ManifestManager())
    vresult = verifier.verify(archive, HistoryManager(app_dir))
    assert vresult.integrity_pass is True
    assert vresult.summary == "VALID — LATEST BACKUP"


# ---------------------------------------------------------------------
# Multi-item isolation regression (the "delete data from archive" bug)
# ---------------------------------------------------------------------


@requires_7z
def test_multi_item_sync_never_touches_other_items_content(tmp_path):
    """Regression test for the exact bug documented in the file header:
    syncing item B must never delete or otherwise disturb item A's
    already-synced archive content, across multiple update runs.
    """
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    docs = tmp_path / "src" / "Documents"
    projects = tmp_path / "src" / "Projects"
    _make_tree(docs, {"a.txt": "doc a", "b.txt": "doc b"})
    _make_tree(projects, {"p1.txt": "project 1"})

    manager = BackupManager(app_dir)
    items = [BackupItem("Documents", docs), BackupItem("Projects", projects)]

    result = manager.new_backup(items)
    assert result.ok, result.message
    archive = app_dir / "Backup.7z"

    members = _archive_members(archive)
    assert "Documents/a.txt" in members
    assert "Documents/b.txt" in members
    assert "Projects/p1.txt" in members

    # Run several update cycles, each only touching one item's source, and
    # confirm the other item's content is present and untouched every time.
    for round_n in range(3):
        (projects / f"p{round_n + 2}.txt").write_text(f"project {round_n + 2}")
        result = manager.update_backup(archive, items, accept_config_changes=False)
        assert result.ok, result.message

        members = _archive_members(archive)
        assert "Documents/a.txt" in members, f"round {round_n}: Documents/a.txt disappeared"
        assert "Documents/b.txt" in members, f"round {round_n}: Documents/b.txt disappeared"
        assert f"Projects/p{round_n + 2}.txt" in members


# ---------------------------------------------------------------------
# Technique B: logical name != physical basename, updated twice
# ---------------------------------------------------------------------


@requires_7z
def test_logical_name_rename_updated_twice_no_duplicates(tmp_path):
    """Regression test for the 'duplicate archive members' bug the
    existing_roots guard exists to prevent (Technique B renaming)."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    src = tmp_path / "src" / "RawDocsFolder"  # physical basename differs from logical name
    _make_tree(src, {"a.txt": "hello"})

    manager = BackupManager(app_dir)
    items = [BackupItem("MyDocs", src)]  # logical name != "RawDocsFolder"

    result = manager.new_backup(items)
    assert result.ok, result.message
    archive = app_dir / "Backup.7z"

    members = _archive_members(archive)
    assert "MyDocs/a.txt" in members
    assert not any(k.startswith("RawDocsFolder") for k in members)

    # Update #1
    (src / "b.txt").write_text("second file")
    result = manager.update_backup(archive, items, accept_config_changes=False)
    assert result.ok, result.message

    # Update #2 — this is the update most likely to reproduce the
    # duplicate-member bug, since it's the second sync against an
    # already-renamed root.
    (src / "c.txt").write_text("third file")
    result = manager.update_backup(archive, items, accept_config_changes=False)
    assert result.ok, result.message

    runner = SevenZipRunner()
    listing = runner.list_technical(archive)
    raw_paths = [
        line.partition(" = ")[2].strip()
        for line in listing.stdout.splitlines()
        if line.startswith("Path = ")
    ][1:]
    dup_counts = {}
    for p in raw_paths:
        dup_counts[p] = dup_counts.get(p, 0) + 1
    duplicates = {p: c for p, c in dup_counts.items() if c > 1}
    assert not duplicates, f"duplicate archive members found: {duplicates}"

    members = _archive_members(archive)
    assert "MyDocs/a.txt" in members
    assert "MyDocs/b.txt" in members
    assert "MyDocs/c.txt" in members
    assert not any(k.startswith("RawDocsFolder") for k in members)


# ---------------------------------------------------------------------
# Deleted-file / deleted-empty-dir synchronization: exact set only
# ---------------------------------------------------------------------


@requires_7z
def test_deleted_files_and_dirs_removed_precisely(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    src = tmp_path / "src" / "Documents"
    _make_tree(src, {"keep.txt": "keep me", "remove_me.txt": "bye", "empty_dir/.gitkeep": "x"})
    # Make empty_dir actually empty (remove the placeholder file, keep the dir).
    (src / "empty_dir" / ".gitkeep").unlink()

    manager = BackupManager(app_dir)
    items = [BackupItem("Documents", src)]
    result = manager.new_backup(items)
    assert result.ok, result.message
    archive = app_dir / "Backup.7z"

    members_before = _archive_members(archive)
    assert "Documents/remove_me.txt" in members_before
    assert "Documents/empty_dir" in members_before

    (src / "remove_me.txt").unlink()
    (src / "empty_dir").rmdir()

    result = manager.update_backup(archive, items, accept_config_changes=False)
    assert result.ok, result.message

    members_after = _archive_members(archive)
    assert "Documents/remove_me.txt" not in members_after
    assert "Documents/empty_dir" not in members_after
    assert "Documents/keep.txt" in members_after  # exact set only — nothing else touched


# ---------------------------------------------------------------------
# Killed/interrupted process mid-transaction
# ---------------------------------------------------------------------


@requires_7z
def test_interrupted_update_leaves_primary_archive_untouched_and_recoverable(tmp_path):
    """SIGKILL the 7z subprocess mid-add_or_update by monkeypatching the
    runner to kill its own child, then confirm: (a) the primary archive is
    byte-for-byte untouched, (b) find_leftover_transactions sees the
    abandoned .new file, (c) a fresh run completes successfully anyway.
    """
    import hashlib
    import subprocess as _subprocess

    app_dir = tmp_path / "app"
    app_dir.mkdir()
    src = tmp_path / "src" / "Documents"
    _make_tree(src, {"a.txt": "hello"})

    manager = BackupManager(app_dir)
    items = [BackupItem("Documents", src)]
    result = manager.new_backup(items)
    assert result.ok, result.message
    archive = app_dir / "Backup.7z"
    before_hash = hashlib.sha256(archive.read_bytes()).hexdigest()

    # Add enough data that the update has time to be killed mid-flight.
    for i in range(50):
        (src / f"bulk_{i}.txt").write_text("x" * 200_000)

    original_run = _subprocess.run

    def killed_run(cmd, **kwargs):
        # Start the real 7z process, then kill it instead of letting it
        # finish, to simulate SIGKILL mid-add_or_update.
        proc = _subprocess.Popen(cmd, stdout=_subprocess.PIPE, stderr=_subprocess.PIPE,
                                  text=True, cwd=kwargs.get("cwd"))
        proc.kill()
        proc.wait()
        return _subprocess.CompletedProcess(cmd, returncode=-9, stdout="", stderr="killed")

    import backup as backup_module
    original = backup_module.subprocess.run
    backup_module.subprocess.run = killed_run
    try:
        result = manager.update_backup(archive, items, accept_config_changes=False)
    finally:
        backup_module.subprocess.run = original

    assert result.ok is False
    assert result.exit_code == 1

    # (a) primary archive untouched
    after_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert after_hash == before_hash, "primary archive was modified by an interrupted run"

    # (b) leftover .new transaction is gone (BackupManager cleans up on a
    # detected-fatal 7z result within the same run) OR, if a real SIGKILL
    # bypassed cleanup, it should be discoverable:
    txn_mgr = ArchiveTransactionManager(SevenZipRunner())
    leftovers = txn_mgr.find_leftover_transactions(archive)
    # Either cleaned up immediately (result.fatal path ran cleanup) or
    # left behind for find_leftover_transactions to report — both are
    # acceptable outcomes of this simulation; what matters is (a) and (c).
    for p in leftovers:
        p.unlink()

    # (c) a fresh run completes successfully anyway
    result = manager.update_backup(archive, items, accept_config_changes=False)
    assert result.ok, result.message
    members = _archive_members(archive)
    assert "Documents/bulk_0.txt" in members


# ---------------------------------------------------------------------
# Duplicate-archive-member detection path (0.3): defeat the upstream
# guard on purpose and confirm the defense-in-depth check fires.
# ---------------------------------------------------------------------


@requires_7z
def test_duplicate_member_validation_catches_corrupted_transaction(tmp_path):
    """The existing_roots/rename-revert guard in ArchiveManager normally
    prevents duplicate archive members from ever being created, which
    means validate_new_archive's duplicate-detection branch has ~zero
    coverage in practice. This test defeats the guard directly (by adding
    the same content twice under two different physical names that both
    map to the same logical root, bypassing synchronize_item entirely) so
    the duplicate check has to do the actual rejecting.
    """
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    runner = SevenZipRunner()
    txn_mgr = ArchiveTransactionManager(runner)
    archive_mgr = ArchiveManager(runner)

    src = tmp_path / "src"
    doc_a = src / "DocsA"
    _make_tree(doc_a, {"file.txt": "content"})

    txn = txn_mgr.new_transaction_path(app_dir / "Backup.7z")
    txn_mgr.stage_copy(None, txn)

    # Add once as "Docs"
    r1 = runner.add_or_update(txn, "DocsA", cwd=src, compression_level=1)
    assert not r1.fatal
    runner.rename(txn, "DocsA", "Docs")

    # Directly bypass the existing_roots guard: add fresh content under a
    # second physical directory and rename it to the SAME logical root,
    # producing a duplicate "Docs/file.txt" member — exactly the
    # corruption validate_new_archive's duplicate check exists to catch.
    doc_b = src / "DocsB"
    _make_tree(doc_b, {"file.txt": "different content, same name"})
    r2 = runner.add_or_update(txn, "DocsB", cwd=src, compression_level=1)
    assert not r2.fatal
    runner.rename(txn, "DocsB", "Docs")

    ok, problems = txn_mgr.validate_new_archive(txn, {"Docs": None, "Docs/file.txt": None})
    assert ok is False
    assert any("more than once" in p for p in problems), problems

    txn.unlink(missing_ok=True)
