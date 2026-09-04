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
| `test_integration.py` | Full `--new`→`--update`→`--verify` cycle, multi-item isolation regression, Technique-B double-rename regression, deletion sync, interrupted-process recovery, duplicate-member validation, concurrent-invocation rejection (Roadmap 2.2), `--history` dir wiring for `--new`/`--update` (Roadmap 2.4) | a real `7z`/`7za`/`7zr` on `PATH` — auto-skips otherwise |
| `test_locking.py` | `_CrossPlatformLock(blocking=False)`, `BackupManager._with_archive_lock`'s rejection/release paths (Roadmap 2.2) | nothing — uses a fake 7-Zip path, since the lock-busy path never shells out |
| `test_permissions.py` | Permission-denied on a required root (→ `BackupError`) vs. a nested subdirectory (→ partial skip) | running as root + a `testuser` account + `runuser` — auto-skips otherwise (see `RUNBOOK.md`) |

`pytest.ini` registers the `integration` marker (used by both
`test_integration.py` and `test_permissions.py`) so `-m "not integration"`
selects the fast, dependency-free subset.

## Environment / packaging

`pyproject.toml` pins the Python floor (`>=3.9` — a deliberately
conservative floor rather than one the syntax actually requires; see
the comments in that file for what was actually checked) and documents
the supported 7-Zip version range (verified: 23.01 on Linux only — see
the same file). `python backup.py --check` (Roadmap 2.1) is a runtime
self-test — 7-Zip detection, read/write, and locking against `app.py`'s
own directory — worth running once on a new deployment before pointing
it at real data; it isn't part of this pytest suite since it's meant to
be run standalone against the *actual* target environment, not a tmp
fixture.

## Windows 7-Zip verification — now empirically confirmed (Roadmap 0.1 platform matrix)

The full `pytest` suite (77 passed, 2 skipped — the 2 skips are
`test_permissions.py`'s root/`testuser` cases, which don't apply on
Windows) has been run against real 7-Zip on a real Windows 11 machine,
via NanaZip 6.5 (a Windows Store archiver app wrapping the 7-Zip core —
its own version number, not the underlying 7-Zip core version; run
`python backup.py --check` to confirm the exact core version
`SevenZipRunner.version_check()` detects on that machine, and record it
here once known). All 8 `test_integration.py` tests passed, including
the two that specifically target the bug classes the original module
docstring's mutation-bug finding exists to prevent:

- `test_multi_item_sync_never_touches_other_items_content` — the
  "delete data from archive" bug class, across 3 update rounds.
- `test_logical_name_rename_updated_twice_no_duplicates` — the
  Technique-B duplicate-member bug class, across 2 updates.

Worth noting explicitly: the original empirical concern (whether
`-u<state>!<newArchiveName>` mutates the primary archive in place) is
**moot for this codebase as shipped** — `SEVENZIP_UPDATE_SWITCHES` is
empty and `add_or_update` never uses that switch at all (see the module
docstring's explanation of why it was found unsafe even on Linux, for a
different reason: it operates on the whole archive's sync state, not
just the item being updated). The design instead always stages a full
copy and runs a plain `7z u <archive> <item>` against that copy, so what
actually needs Windows validation is whether plain add/update/rename/
delete stay correctly isolated per item on Windows — which is precisely
what the two tests above check, and both passed for real.

**What this does *not* prove:** only that this specific installed
build (NanaZip 6.5 / its bundled 7-Zip core) behaves correctly — not
every Windows 7-Zip distribution or version. If you deploy against a
different Windows 7-Zip build (official 7-Zip.org installer, a
different NanaZip version, etc.), re-run at least
`test_integration.py`'s multi-item and rename tests against it before
trusting that combination.

## What's intentionally NOT covered here

Per the roadmap, these need a real target environment this sandbox can't
provide and are called out rather than silently skipped:

- **Large-scale/long-run behavior — partially closed (Roadmap 2.3).**
  Memory scaling of `SourceInventoryManager.scan()` and
  `parse_archive_listing()`, and `preflight_space`'s headroom formula,
  have all been empirically benchmarked (see `bench_2_3.py` and its
  results, summarized below) and confirmed sane. **Still open:**
  wall-clock cost of the full staging copy on a real large archive —
  needs an actual multi-GB+ archive to measure, not automatable.

  Benchmark results (Windows 11, real filesystem, `tracemalloc`-measured):

  | files | traced_peak | per_file |
  |---|---|---|
  | 10,000 | 5.4 MiB | ~570 B |
  | 50,000 | 28.1 MiB | ~590 B |
  | 150,000 | 83.0 MiB | ~580 B |
  | 300,000 | 166.4 MiB | ~582 B |

  `per_file` stayed flat across a 30x range in file count — confirms
  `SourceInventoryManager.scan()` scales linearly, not worse.
  `parse_archive_listing()` showed the same flat ~770–783 B/member
  across the same range on a synthetic `-slt` fixture.

  `preflight_space`'s `1.1x + 50 MiB` headroom formula was confirmed
  sane (headroom always exceeds the raw estimate, never overshoots it
  by more than rounding noise) from 1 GiB up through 1 TiB.

- **True cross-*device* publish/verify.** `test_history_dir_override_used_by_new_and_update`
  (Roadmap 2.4) does confirm `--history` now works for `--new`/`--update`
  (previously silently ignored by both — see `EXIT_CODES.md`) and that
  the audit log can live in a wholly different directory tree than the
  archive; but it can't force that directory onto an actually distinct
  block device inside this sandbox, so the specific "different
  filesystem" failure/degradation mode (as opposed to "different
  directory," which is what's actually tested and is the case that
  matters for this design — the transaction file is always created in
  the *archive's own* directory, never near the history file, so a
  history-vs-archive device mismatch was already structurally
  impossible to hit through that path) remains unverified against real
  hardware.
