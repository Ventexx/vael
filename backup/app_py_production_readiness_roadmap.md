# app.py — Production Readiness Roadmap

**Subject:** Backup Utility (`app.py`), reviewed against `backup_utility_specification_v3.md`
**Current state:** Structurally complete implementation of the spec, including two undocumented-in-spec safety fixes the author discovered empirically. No automated tests exist. Validated against exactly one 7-Zip build (23.01, Linux).
**Purpose of this document:** everything that needs to happen between "structurally complete" and "safe to point at real, only-copy-you-have backup data."

This is organized by priority, not by spec section. Each item says *why* it matters, not just *what* to do.

---

## Phase 0 — Blocking, do these before anyone's real data touches it

### 0.1 Build an automated test suite
There is currently **no test file, no `pytest`/`unittest` usage, and no CI configuration** anywhere in the project. Every one of the 20 invariants in the spec, and both empirically-discovered fixes in the docstring, are currently verified only by the fact that someone ran the script by hand once. That's not durable — the next refactor can silently reintroduce either of the two bugs already found and fixed. Minimum coverage needed before anything else matters:

- **Unit tests** (no real 7-Zip binary needed) for the pure-Python logic, which is the highest-value, lowest-cost tier:
  - `validate_logical_name` / `validate_configuration` — empty names, `..`, drive letters, prefix collisions, manifest-name collision, duplicate names.
  - `compare_inventories` — added/removed/modified/unchanged classification, the timestamp-tolerance boundary (`TIMESTAMP_TOLERANCE_NS`), and the "ambiguous within tolerance" fallback path.
  - `parse_archive_listing` / `_parse_7z_timestamp_to_ns` — feed it captured real `7z l -slt` output (including the archive's own leading metadata block) and confirm it's skipped correctly, not just tested against synthetic input that happens to match your parser's assumptions.
  - `ManifestManager.detect_config_changes` — add/remove/rename/path-change detection, including cases with more than one simultaneous change.
  - `HistoryManager.read_entries` — decorative-prose-only lines, malformed `[BACKUP_META]` JSON, empty file, missing file.

- **Integration tests against a real 7-Zip binary** (the tier that actually matters most, since both known bugs were only caught this way):
  - Full `--new` → `--update` → `--verify` cycle on a throwaway directory tree.
  - A multi-item config (≥2 `BackupItem`s) updated across multiple runs, asserting that syncing item B never touches item A's archive content — this is the exact regression class that produced the "delete data from archive" bug documented in the file header. This needs to be a permanent regression test, not a one-off manual check.
  - A logical-name-differs-from-basename case (Technique B), updated **twice**, to catch the "duplicate archive members" bug the code's `existing_roots` guard exists to prevent.
  - A killed/interrupted process mid-transaction (`SIGKILL` the subprocess mid-`add_or_update`), followed by a fresh run, asserting: (a) the primary archive is untouched, (b) `find_leftover_transactions` finds the abandoned `.new` file, (c) the next run completes successfully anyway.
  - Deleted-file and deleted-empty-directory synchronization, confirming `delete_paths` only removes the exact computed set and nothing else.

- **Platform matrix — Windows now empirically verified.** The original concern (`-u<state>!<newArchiveName>` mutating the source archive) was tested against **7-Zip 23.01 on Linux only**, and is moot for this codebase as shipped — `add_or_update` never uses that switch (`SEVENZIP_UPDATE_SWITCHES` is empty; see the module docstring for why it was found unsafe even on Linux). What actually matters — whether plain add/update/rename/delete via the staged-copy design stay correctly isolated per item — has now been verified on Windows: the full `test_integration.py` suite (8/8, including the multi-item-isolation and Technique-B-rename regression tests, the exact bug classes this concern was originally about) passed against real 7-Zip on Windows 11, via NanaZip 6.5 (confirm the underlying 7-Zip core version via `python backup.py --check` for the record). This confirms one specific build; a different Windows 7-Zip distribution/version should still be spot-checked against at least those two tests before being trusted.

### 0.2 Verify Invariant 6 (post-update inventory match) actually catches what it claims to
`validate_new_archive` compares `expected_source_inventory` (from a fresh scan) against the archive's technical listing, but the *inventory scan* (`SourceInventoryManager.scan`) and the *validation* run at different points in time relative to the actual synchronization. Confirm, with a test, that a file changed *during* the backup run (mid-scan-to-mid-write race) is either safely caught by the CRC/size check or explicitly documented as an accepted limitation — right now it's neither tested nor mentioned.

### 0.3 Confirm the duplicate-archive-member detection path is reachable and correct
`validate_new_archive` has real logic for catching duplicated archive paths (the exact failure mode the `existing_roots`/rename-revert guard is meant to prevent). This is a "defense in depth" check for a bug that's supposedly already prevented upstream — which means it's very likely to have **zero test coverage exercising the actual duplicate-detection branch**, since the upstream guard normally prevents it from ever firing. Deliberately construct a case that defeats the upstream guard (e.g. manually corrupt a transaction archive in a test) and confirm the duplicate check actually rejects it.

### 0.4 Decide and document the disk-space math
`preflight_space` estimates needed space as `existing archive size × 1.1 + 50 MiB` headroom via `_dir_size` on the raw *source* directories — but the real transaction needs room for a **copy of the existing compressed archive** (per the reintroduced staging-copy fix) *plus* whatever new/changed compressed data gets added. Walk through whether the current estimate (`sum of source dir sizes`) is actually a safe upper/lower bound for that, or whether it can under-estimate on an update where sources are much larger than their compressed archive representation. This is a "silent failure late in a long-running job" risk, not a hypothetical.

---

## Phase 1 — Required before calling it "production ready" for a single user

### 1.1 Logging, not just print statements
Every operational signal in the current code goes to `stdout`/`stderr` via `print()`, with no log file, no log levels, and no timestamps in most output. For unattended/automated use (the spec's own stated goal — "non-interactive command-line operation suitable for automation") there needs to be:
- A persistent, timestamped operational log separate from `backup_history.txt` (which the spec deliberately keeps as a human-readable + machine-parseable audit record, not a debug log).
- Distinguishable log levels so a cron/task-scheduler wrapper can alert on real failures vs. routine warnings (e.g. skipped symlinks).

### 1.2 Alerting / exit-code contract documentation for automation callers
The code already returns a rich set of exit codes (0 success, 1 `BackupError`, 2 config, 3 dependency, 4 verify-failed, 5 history-persistence-pending). None of this is written down anywhere for an operator setting up a scheduled task. Before this is "automation-ready," you need a short doc (or `--help` epilog) enumerating every exit code and what a wrapper script should do with each — especially exit code 5, which is a **partial success** (backup succeeded, history didn't) and needs different handling than a hard failure.

### 1.3 Recovery runbook for the failure states the code already anticipates
The code has real handling for: leftover `.new` transaction files, pending history JSON records, and lock contention — but there's no operator-facing document describing what a human should do if they find these files after a crash, how long it's safe to leave a `.pending.*.json` file before investigating, or how to manually reconcile one. Since these are exactly the artifacts a confused user will find during a 2 AM incident, they need plain-language recovery instructions, not just code comments.

### 1.4 Sanity-test the CRC/timestamp-tolerance interaction under real filesystems
`compare_inventories` has an "ambiguous within tolerance" branch (`modified_undetermined`) that currently does the same thing regardless of whether a CRC is available (`old.crc` is checked but both branches produce identical behavior — dead-looking logic worth resolving, not just leaving as a TODO). Decide whether CRC should actually be used to resolve the ambiguity, and if so implement and test it; if not, remove the dead branch so a future reader doesn't assume it's doing something it isn't.

### 1.5 Config validation for the things `validate_configuration` doesn't check yet
Currently validated: empty names, reserved characters, collisions, missing list. **Not validated:** that configured source paths are absolute (a relative `Path` in `BACKUP_ITEMS` combined with `cwd`-dependent 7-Zip calls is a real footgun given `add_or_update`'s reliance on `cwd`), and that no two `BackupItem`s point at the same or a nested physical path (two logical names backing the same physical directory would silently double-count and double-compress it). Both are cheap to add and expensive to debug in production.

### 1.6 Permission and locked-file handling audit
`SourceInventoryManager.scan` catches `OSError` per-entry and records it as a skip — good — but confirm this is tested against real permission-denied directories (not just missing ones), and that a permission error on a *required* top-level `BackupItem.path` itself (not a nested file) is treated as "source unavailable" per Invariant 1, not silently skipped as an empty tree.

---

## Phase 2 — Required before calling it "production ready" for more than one user / environment

### 2.1 Packaging and dependency story
Right now this is "one file, run with a system Python, hope 7-Zip is on PATH." For real distribution:
- Pin a minimum Python version (the code uses `from __future__ import annotations` and modern type hints — confirm the actual floor, e.g. 3.10+, and state it).
- Decide and document the supported 7-Zip version range, since the whole design pivots on empirically-tested behavior of one specific build.
- Provide a `--check`/self-test command that runs `version_check()` plus a scratch-directory read/write/lock test, so a new deployment can confirm its environment is sane before trusting it with real backups.

### 2.2 Concurrent-invocation protection beyond the history lock
The history file has a proper lock; the **archive file itself** does not appear to have an equivalent guard against two `--update` invocations targeting the same archive concurrently (e.g. a cron overlap after a slow run). Confirm what happens today (two transaction files racing to `os.replace()` the same destination) and add an explicit lock or clear documentation that concurrent invocation against the same archive is unsupported and must be prevented by the caller (e.g. via the scheduler).

### 2.3 Large-archive / long-run behavior — **partially closed**
No evidence of testing against archive sizes or file counts representative of real use (the spec's own example config implies hundreds of GB across `Documents`/`Projects`/`Photos`). Before production use at realistic scale, validate:
- ~~Memory behavior of `parse_archive_listing` and `SourceInventoryManager.scan` against a source tree with hundreds of thousands of files (both build full in-memory dicts).~~ **Done** — benchmarked via `bench_2_3.py` up to 300,000 files/members: `SourceInventoryManager.scan()` held flat at ~570–590 bytes/file, `parse_archive_listing()` at ~767–783 bytes/member, across a 30x range in count. Linear, not worse — no finding.
- Wall-clock cost of the reintroduced full staging copy on a large archive, and whether that's acceptable, since it was explicitly reintroduced as a correctness trade-off against performance. **Still open** — needs a real large archive; not automatable. Manual recipe: `time python backup.py --update <source> <archive>` vs. timing a standalone `shutil.copy2` of the archive, to isolate the copy's share of total wall-clock.
- ~~`preflight_space`'s headroom formula at real scale (does 10% + 50 MiB still make sense at 500 GB?).~~ **Done** — confirmed sane from 1 GiB through 1 TiB via `bench_2_3.py`: headroom consistently ~10% above the raw estimate at every scale tested, no overflow or rounding anomaly.

### 2.4 Cross-filesystem restore/verify path
`REQUIRE_SAME_FILESYSTEM_ATOMIC_REPLACE` correctly refuses non-atomic publication — good, this is Invariant 20 done right. But there's no tested behavior/guidance for the *documented* supported use case in the spec ("the user may later move `Backup.7z` anywhere") when that move puts the archive on a different filesystem than its history file. Confirm `--verify` and `--update` both work correctly (and `--update`'s space/publish logic degrades safely, not silently) when archive and history live on different filesystems.

---

## Phase 3 — Polish / hardening, not blocking but expected of a "finished" tool

- **`--help` and CLI error messages review** for a first-time user who hasn't read the spec — e.g. does `--update` without `--accept-config-changes` on a changed config produce an actionable message, or a wall of JSON?
- **Interactive-mode input validation** (`_interactive_menu` currently does raw `input()` with minimal guarding) — confirm bad archive paths, empty input, and Ctrl+C mid-prompt all fail cleanly rather than throwing a raw traceback at a non-technical user.
- **Structured secrets/PII review** — confirm `manifest.json`, `backup_history.txt`, and log output never leak more of the source filesystem layout than intended (e.g. full absolute paths of a user's personal folders sitting in plaintext history — may be fine, but should be a deliberate decision, not an accident).
- **Code-level documentation pass** separate from the spec-tracking comments — the current comments are excellent at explaining *why deviations happened*, but a maintainer coming in cold still needs ordinary API docs for the public methods.

---

## What "production ready" should mean when this is done

1. Every one of the 20 invariants in Section 66A has at least one automated test that would fail if the invariant were violated.
2. The two empirically-discovered fixes (staging copy, no-sync-switch deletion) have permanent regression tests, not just explanatory comments.
3. The Windows 7-Zip behavior has been independently verified, not assumed from the Linux finding.
4. A crash/interruption at every major step (mid-scan, mid-sync, mid-validate, mid-publish, mid-history-write) has been tested and confirmed to leave the primary archive and history file in a recoverable state.
5. There's an operator-facing doc (exit codes, recovery steps for leftover/pending files) separate from the code.
6. It's been run once, successfully, against a real dataset at the scale the user actually intends to use it for.

Until all six are true, "structurally complete" is the accurate description — not "production ready."
