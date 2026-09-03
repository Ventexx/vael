from pathlib import Path

from backup import (
    InventoryEntry,
    TIMESTAMP_TOLERANCE_NS,
    compare_inventories,
    crc32_of_file,
)


def entry(path, is_dir=False, size=0, mtime_ns=0, crc=None, physical_path=None):
    return InventoryEntry(logical_path=path, is_dir=is_dir, size=size, mtime_ns=mtime_ns, crc=crc, physical_path=physical_path)


def test_added_file_classified():
    prev = {}
    cur = {"Docs/new.txt": entry("Docs/new.txt", size=10)}
    stats = compare_inventories(prev, cur)
    assert stats.added == 1
    assert stats.added_dirs == 0


def test_added_dir_classified():
    prev = {}
    cur = {"Docs": entry("Docs", is_dir=True)}
    stats = compare_inventories(prev, cur)
    assert stats.added_dirs == 1
    assert stats.added == 0


def test_deleted_file_and_dir_classified():
    prev = {
        "Docs": entry("Docs", is_dir=True),
        "Docs/old.txt": entry("Docs/old.txt", size=5),
    }
    cur = {}
    stats = compare_inventories(prev, cur)
    assert stats.deleted == 1
    assert stats.deleted_dirs == 1
    assert set(stats.deleted_paths) == {"Docs", "Docs/old.txt"}


def test_unchanged_file_same_size_same_mtime():
    prev = {"a.txt": entry("a.txt", size=100, mtime_ns=1_000_000_000)}
    cur = {"a.txt": entry("a.txt", size=100, mtime_ns=1_000_000_000)}
    stats = compare_inventories(prev, cur)
    assert stats.unchanged == 1
    assert stats.modified == 0


def test_size_change_is_always_modified_regardless_of_mtime():
    prev = {"a.txt": entry("a.txt", size=100, mtime_ns=1_000_000_000)}
    cur = {"a.txt": entry("a.txt", size=200, mtime_ns=1_000_000_000)}
    stats = compare_inventories(prev, cur)
    assert stats.modified == 1


def test_directories_never_classified_as_modified():
    prev = {"Docs": entry("Docs", is_dir=True, mtime_ns=1)}
    cur = {"Docs": entry("Docs", is_dir=True, mtime_ns=999_999_999_999)}
    stats = compare_inventories(prev, cur)
    assert stats.modified == 0
    assert stats.unchanged == 0
    assert stats.added == 0
    assert stats.deleted == 0


def test_mtime_delta_beyond_tolerance_is_modified():
    prev = {"a.txt": entry("a.txt", size=10, mtime_ns=0)}
    cur = {"a.txt": entry("a.txt", size=10, mtime_ns=TIMESTAMP_TOLERANCE_NS + 1)}
    stats = compare_inventories(prev, cur)
    assert stats.modified == 1
    assert stats.modified_undetermined == 0


def test_mtime_delta_exactly_at_tolerance_boundary_is_still_ambiguous():
    # delta == tolerance_ns is NOT > tolerance_ns, so it falls into the
    # ambiguous branch, not the definite-modified branch.
    prev = {"a.txt": entry("a.txt", size=10, mtime_ns=0, crc=None)}
    cur = {"a.txt": entry("a.txt", size=10, mtime_ns=TIMESTAMP_TOLERANCE_NS)}
    stats = compare_inventories(prev, cur)
    assert stats.modified == 0
    assert stats.modified_undetermined == 1


def test_ambiguous_without_archive_crc_stays_undetermined():
    prev = {"a.txt": entry("a.txt", size=10, mtime_ns=0, crc=None)}
    cur = {"a.txt": entry("a.txt", size=10, mtime_ns=1000)}
    stats = compare_inventories(prev, cur)
    assert stats.modified_undetermined == 1
    assert stats.modified == 0
    assert stats.unchanged == 0


def test_ambiguous_with_crc_match_resolves_to_unchanged(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"identical content")
    real_crc = crc32_of_file(f)

    prev = {"a.txt": entry("a.txt", size=len(b"identical content"), mtime_ns=0, crc=real_crc)}
    cur = {"a.txt": entry("a.txt", size=len(b"identical content"), mtime_ns=1000, physical_path=f)}
    stats = compare_inventories(prev, cur)
    assert stats.unchanged == 1
    assert stats.modified == 0
    assert stats.modified_undetermined == 0


def test_ambiguous_with_crc_mismatch_resolves_to_modified(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"changed content!!")

    prev = {"a.txt": entry("a.txt", size=len(b"changed content!!"), mtime_ns=0, crc="DEADBEEF")}
    cur = {"a.txt": entry("a.txt", size=len(b"changed content!!"), mtime_ns=1000, physical_path=f)}
    stats = compare_inventories(prev, cur)
    assert stats.modified == 1
    assert stats.unchanged == 0
    assert stats.modified_undetermined == 0


def test_ambiguous_with_crc_but_unreadable_file_falls_back_to_undetermined(tmp_path):
    missing = tmp_path / "gone.txt"  # never created
    prev = {"a.txt": entry("a.txt", size=10, mtime_ns=0, crc="DEADBEEF")}
    cur = {"a.txt": entry("a.txt", size=10, mtime_ns=1000, physical_path=missing)}
    stats = compare_inventories(prev, cur)
    assert stats.modified_undetermined == 1


def test_multiple_simultaneous_changes_all_counted():
    prev = {
        "keep.txt": entry("keep.txt", size=1, mtime_ns=0),
        "modify.txt": entry("modify.txt", size=5, mtime_ns=0),
        "remove.txt": entry("remove.txt", size=1, mtime_ns=0),
        "RemovedDir": entry("RemovedDir", is_dir=True),
    }
    cur = {
        "keep.txt": entry("keep.txt", size=1, mtime_ns=0),
        "modify.txt": entry("modify.txt", size=99, mtime_ns=0),
        "added.txt": entry("added.txt", size=1),
        "AddedDir": entry("AddedDir", is_dir=True),
    }
    stats = compare_inventories(prev, cur)
    assert stats.unchanged == 1
    assert stats.modified == 1
    assert stats.added == 1
    assert stats.added_dirs == 1
    assert stats.deleted == 1
    assert stats.deleted_dirs == 1


def test_crc32_of_file_matches_known_value(tmp_path):
    f = tmp_path / "known.bin"
    f.write_bytes(b"123456789")
    # CRC32 of the ASCII bytes "123456789" is the standard CRC32 check
    # value 0xCBF43926 (a well-known test vector for the CRC-32/ISO-HDLC
    # polynomial that 7-Zip and zlib both use).
    assert crc32_of_file(f) == "CBF43926"


def test_crc32_of_file_returns_none_for_missing_file(tmp_path):
    assert crc32_of_file(tmp_path / "does-not-exist.bin") is None
