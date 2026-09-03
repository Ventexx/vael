import json

from backup import HISTORY_FILENAME, HistoryManager


def write_history(tmp_path, text):
    (tmp_path / HISTORY_FILENAME).write_text(text, encoding="utf-8")


def test_missing_history_file_returns_empty_list(tmp_path):
    mgr = HistoryManager(tmp_path)
    assert mgr.read_entries() == []


def test_empty_history_file_returns_empty_list(tmp_path):
    write_history(tmp_path, "")
    mgr = HistoryManager(tmp_path)
    assert mgr.read_entries() == []


def test_decorative_prose_only_lines_ignored(tmp_path):
    write_history(tmp_path, "=" * 70 + "\nBACKUP ATTEMPT #1\nSTATUS: SUCCESS\n" + "=" * 70 + "\n")
    mgr = HistoryManager(tmp_path)
    assert mgr.read_entries() == []


def test_single_valid_meta_line_parsed(tmp_path):
    meta = {
        "run_id": 1, "status": "SUCCESS", "operation": "NEW", "archive": "/a/Backup.7z",
        "backup_uuid": "u1", "backup_version": 1, "sha256": "abc123",
        "start": "2026-01-01T00:00:00", "completed": "2026-01-01T00:01:00",
    }
    write_history(tmp_path, f"[BACKUP_META] {json.dumps(meta)}\n")
    mgr = HistoryManager(tmp_path)
    entries = mgr.read_entries()
    assert len(entries) == 1
    e = entries[0]
    assert e.run_id == 1
    assert e.status == "SUCCESS"
    assert e.sha256 == "abc123"
    assert e.backup_version == 1


def test_malformed_json_meta_line_skipped_not_fatal(tmp_path):
    good = {"run_id": 1, "status": "SUCCESS", "operation": "NEW", "archive": "a",
            "backup_uuid": None, "backup_version": None, "sha256": None,
            "start": "", "completed": ""}
    text = "[BACKUP_META] {not valid json!!\n" + f"[BACKUP_META] {json.dumps(good)}\n"
    write_history(tmp_path, text)
    mgr = HistoryManager(tmp_path)
    entries = mgr.read_entries()
    assert len(entries) == 1
    assert entries[0].run_id == 1


def test_multiple_entries_preserve_file_order(tmp_path):
    # Entries are prepended (newest-first) by the writer; read_entries
    # should just parse in the order it finds them in the file, not
    # re-sort — the writer is responsible for ordering.
    m1 = {"run_id": 2, "status": "SUCCESS", "operation": "UPDATE", "archive": "a",
          "backup_uuid": None, "backup_version": 2, "sha256": "s2", "start": "", "completed": ""}
    m2 = {"run_id": 1, "status": "SUCCESS", "operation": "NEW", "archive": "a",
          "backup_uuid": None, "backup_version": 1, "sha256": "s1", "start": "", "completed": ""}
    write_history(tmp_path, f"[BACKUP_META] {json.dumps(m1)}\n" + f"[BACKUP_META] {json.dumps(m2)}\n")
    mgr = HistoryManager(tmp_path)
    entries = mgr.read_entries()
    assert [e.run_id for e in entries] == [2, 1]


def test_next_run_id_increments_from_max(tmp_path):
    m1 = {"run_id": 5, "status": "SUCCESS", "operation": "NEW", "archive": "a",
          "backup_uuid": None, "backup_version": 1, "sha256": "s", "start": "", "completed": ""}
    write_history(tmp_path, f"[BACKUP_META] {json.dumps(m1)}\n")
    mgr = HistoryManager(tmp_path)
    assert mgr.next_run_id() == 6


def test_next_run_id_starts_at_one_when_empty(tmp_path):
    mgr = HistoryManager(tmp_path)
    assert mgr.next_run_id() == 1


def test_latest_successful_requires_sha256(tmp_path):
    m1 = {"run_id": 1, "status": "SUCCESS", "operation": "NEW", "archive": "a",
          "backup_uuid": None, "backup_version": 1, "sha256": None, "start": "", "completed": ""}
    write_history(tmp_path, f"[BACKUP_META] {json.dumps(m1)}\n")
    mgr = HistoryManager(tmp_path)
    assert mgr.latest_successful() is None  # no sha256 -> doesn't count


def test_latest_successful_skips_failed_entries(tmp_path):
    failed = {"run_id": 2, "status": "FAILED", "operation": "UPDATE", "archive": "a",
              "backup_uuid": None, "backup_version": None, "sha256": None, "start": "", "completed": ""}
    ok = {"run_id": 1, "status": "SUCCESS", "operation": "NEW", "archive": "a",
          "backup_uuid": None, "backup_version": 1, "sha256": "s1", "start": "", "completed": ""}
    write_history(tmp_path, f"[BACKUP_META] {json.dumps(failed)}\n" + f"[BACKUP_META] {json.dumps(ok)}\n")
    mgr = HistoryManager(tmp_path)
    latest = mgr.latest_successful()
    assert latest is not None
    assert latest.run_id == 1


def test_find_by_sha256(tmp_path):
    ok = {"run_id": 1, "status": "SUCCESS", "operation": "NEW", "archive": "a",
          "backup_uuid": None, "backup_version": 1, "sha256": "target-sha", "start": "", "completed": ""}
    write_history(tmp_path, f"[BACKUP_META] {json.dumps(ok)}\n")
    mgr = HistoryManager(tmp_path)
    found = mgr.find_by_sha256("target-sha")
    assert found is not None
    assert mgr.find_by_sha256("nonexistent") is None


def test_record_writes_and_prepends(tmp_path):
    mgr = HistoryManager(tmp_path)
    assert mgr.record("FIRST\n", {"run_id": 1}) is True
    assert mgr.record("SECOND\n", {"run_id": 2}) is True
    content = (tmp_path / HISTORY_FILENAME).read_text()
    # newest write should be prepended (appear first)
    assert content.index("SECOND") < content.index("FIRST")


def test_reconcile_pending_merges_orphaned_pending_files(tmp_path):
    mgr = HistoryManager(tmp_path)
    pending_path = tmp_path / f".{HISTORY_FILENAME}.pending.99.json"
    pending_path.write_text(json.dumps({"entry_text": "RECOVERED\n", "meta": {"run_id": 99}}), encoding="utf-8")
    mgr.reconcile_pending()
    assert not pending_path.exists()
    content = (tmp_path / HISTORY_FILENAME).read_text()
    assert "RECOVERED" in content
