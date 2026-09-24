import json
from types import SimpleNamespace

from backup import HistoryManager, VerificationManager, sha256_of_file


def make_verifier(family="family-a"):
    runner = SimpleNamespace(test=lambda archive: SimpleNamespace(ok=True))
    manifests = SimpleNamespace(read_from_archive=lambda runner, archive:
                                SimpleNamespace(backup_uuid=family))
    return VerificationManager(runner, manifests)


def record(history, run, family, version, checksum):
    meta = dict(run_id=run, status="SUCCESS", operation="NEW", archive="old/location.7z",
                backup_uuid=family, backup_version=version, sha256=checksum)
    history.record("[BACKUP_META] " + json.dumps(meta) + "\n", {"run_id": run})


def test_latest_is_scoped_to_family_even_after_archive_moves(tmp_path):
    archive = tmp_path / "moved.7z"
    archive.write_bytes(b"archive-a")
    history = HistoryManager(tmp_path)
    record(history, 1, "family-a", 1, sha256_of_file(archive))
    record(history, 2, "family-b", 9, "unrelated")
    result = make_verifier().verify(archive, history)
    assert result.is_latest
    assert result.summary == "VALID — LATEST BACKUP"


def test_recovered_older_entry_does_not_become_latest(tmp_path):
    archive = tmp_path / "old.7z"
    archive.write_bytes(b"old")
    history = HistoryManager(tmp_path)
    record(history, 2, "family-a", 2, "newer")
    record(history, 1, "family-a", 1, sha256_of_file(archive))
    result = make_verifier().verify(archive, history)
    assert not result.is_latest
    assert "latest #2" in result.summary


def test_unrelated_family_cannot_supply_checksum_match(tmp_path):
    archive = tmp_path / "a.7z"
    archive.write_bytes(b"a")
    history = HistoryManager(tmp_path)
    record(history, 1, "family-b", 1, sha256_of_file(archive))
    result = make_verifier().verify(archive, history)
    assert result.match is None
    assert "UNKNOWN HISTORY" in result.summary


def test_busy_archive_is_not_verified(tmp_path):
    from backup import _CrossPlatformLock
    archive = tmp_path / "a.7z"
    archive.write_bytes(b"a")
    verifier = make_verifier()
    verifier.runner.test = lambda path: (_ for _ in ()).throw(AssertionError("must not read"))
    with _CrossPlatformLock(tmp_path / ".a.7z.lock", blocking=False):
        result = verifier.verify(archive, None)
    assert not result.ok
    assert result.integrity_pass is None
    assert "busy" in result.summary


def test_verification_holds_writer_lock_through_all_reads(tmp_path):
    import pytest
    from backup import _CrossPlatformLock, LockBusyError
    archive = tmp_path / "a.7z"
    archive.write_bytes(b"a")
    verifier = make_verifier()
    def test(path):
        with pytest.raises(LockBusyError):
            with _CrossPlatformLock(tmp_path / ".a.7z.lock", blocking=False):
                pass
        return SimpleNamespace(ok=True)
    verifier.runner.test = test
    assert verifier.verify(archive, None).ok
    with _CrossPlatformLock(tmp_path / ".a.7z.lock", blocking=False):
        pass


def test_external_replacement_cannot_report_success(tmp_path):
    import os
    archive = tmp_path / "a.7z"
    archive.write_bytes(b"old")
    verifier = make_verifier()
    def test(path):
        replacement = tmp_path / "replacement"
        replacement.write_bytes(b"new content")
        os.replace(replacement, path)
        return SimpleNamespace(ok=True)
    verifier.runner.test = test
    result = verifier.verify(archive, None)
    assert not result.ok
    assert "changed during verification" in result.summary


def test_missing_manifest_is_not_success(tmp_path):
    archive = tmp_path / "a.7z"
    archive.write_bytes(b"a")
    verifier = make_verifier()
    verifier.manifest_mgr.read_from_archive = lambda *args: None
    result = verifier.verify(archive, None)
    assert result.integrity_pass is True
    assert not result.ok
    assert "MANIFEST" in result.summary


def test_read_error_returns_incomplete_result(tmp_path):
    archive = tmp_path / "a.7z"
    archive.write_bytes(b"a")
    verifier = make_verifier()
    verifier.runner.test = lambda path: (_ for _ in ()).throw(OSError("read denied"))
    result = verifier.verify(archive, None)
    assert not result.ok
    assert "read denied" in result.summary
