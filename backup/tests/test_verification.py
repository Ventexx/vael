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
