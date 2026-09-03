# Test suite

```
pip install pytest --break-system-packages   # or use a venv
python -m pytest                             # everything
python -m pytest -m "not integration"        # unit tests only, no 7-Zip needed, <1s
python -m pytest tests/test_integration.py   # 7-Zip integration tests only
python -m pytest tests/test_permissions.py   # real-permission-denial tests only
```

## Layout

| File | Covers | Needs |
|---|---|---|
| `test_validation.py` | `validate_logical_name`, `validate_configuration` | nothing |
| `test_compare_inventories.py` | `compare_inventories`, the timestamp-tolerance boundary, CRC-based ambiguity resolution, `crc32_of_file` | nothing |
| `test_parse_archive_listing.py` | `parse_archive_listing`, `_parse_7z_timestamp_to_ns`, against a captured-shape `7z l -slt` fixture (including the archive's own leading metadata block) | nothing |
| `test_manifest.py` | `ManifestManager.detect_config_changes` (add/remove/rename/path-change, including simultaneous changes) | nothing |
| `test_history.py` | `HistoryManager.read_entries`, pending-record reconciliation, run-id sequencing | nothing |
| `test_integration.py` | Full `--new`→`--update`→`--verify` cycle, multi-item isolation regression, Technique-B double-rename regression, deletion sync, interrupted-process recovery, duplicate-member validation | a real `7z`/`7za`/`7zr` on `PATH` — auto-skips otherwise |
| `test_permissions.py` | Permission-denied on a required root (→ `BackupError`) vs. a nested subdirectory (→ partial skip) | running as root + a `testuser` account + `runuser` — auto-skips otherwise (see `RUNBOOK.md`) |

`pytest.ini` registers the `integration` marker (used by both
`test_integration.py` and `test_permissions.py`) so `-m "not integration"`
selects the fast, dependency-free subset.

## What's intentionally NOT covered here

Per the roadmap, these need a real target environment this sandbox can't
provide and are called out rather than silently skipped:

- **Windows 7-Zip behavior.** Every integration test here runs against
  7-Zip 23.01 on Linux. The `-u<state>!<newArchiveName>` mutation bug
  documented in the file header was only tested on that build/platform —
  repeat the empirical test from the docstring against your actual
  Windows 7-Zip build before trusting Windows deployments (Roadmap 0.1's
  platform-matrix item).
- **Large-scale/long-run behavior** (hundreds of thousands of files,
  hundreds of GB) — Roadmap 2.3, needs a real-scale environment.
- **Cross-filesystem publish/verify** — Roadmap 2.4, needs two actually
  distinct filesystems to test against.
