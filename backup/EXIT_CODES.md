# Exit codes — automation contract

This is the contract `backup.py` makes with whatever invokes it
non-interactively (cron, Task Scheduler, systemd timer, CI job, a wrapper
script). Every code below is returned from `--new`, `--update`, and
`--verify`; treat anything not listed here as an unexpected crash (Python
traceback on stderr) rather than a designed outcome.

| Code | Meaning | What a wrapper should do |
|---|---|---|
| **0** | Success. Backup created/updated, or verification passed and archive history is consistent. | Nothing. Log success and move on. |
| **1** | `BackupError` — an expected, operational failure: missing source, 7-Zip reported a warning or error, post-sync validation failed, insufficient disk space, configuration-change confirmation needed and not given, interrupted mid-transaction, etc. **The archive was not modified** — every `BackupError` path in the code is reached before `ArchiveTransactionManager.publish()`, or after it's already too late to matter (only in `--new`'s exception handler, which runs before any publish is possible). | Treat as a real failure. Alert. The primary archive is safe (see "The archive is always safe on failure" below), but the intended backup did **not** happen — do not treat "exit code 1, but I have an archive file" as success. |
| **2** | `ConfigError` — the configuration itself is invalid (empty `BACKUP_ITEMS`, name collisions, non-absolute paths, overlapping physical paths, `--dry-run` used without `--update`). | Alert as a deployment/config bug, not a transient failure. Retrying without fixing the config will fail the same way every time. |
| **3** | `DependencyError` — 7-Zip could not be located on `PATH` (or at the `--sevenzip` path given). | Alert as an environment problem. Check the 7-Zip install / `PATH` / `--sevenzip` argument on the machine running the job. |
| **4** | Verification failed (`--verify` only) — `7z t` integrity check failed on the archive. | Treat as urgent/high-severity. This means the archive itself may be corrupt, not just that the run failed — investigate promptly, don't wait for the next scheduled run to "fix itself." |
| **5** | **Partial success** — the backup archive itself was created/updated and published successfully, but the entry could not be written to `backup_history.txt` (see "Exit code 5 in detail" below). | **Do not treat as a hard failure**, but do not treat as silent success either. See below. |

## The archive is always safe on failure

Every failure path in `new_backup` / `update_backup` runs *before*
`ArchiveTransactionManager.publish()` (the only call that
`os.replace()`s over the primary archive), or cleans up the transaction
file (`txn_mgr.cleanup(txn_archive)`) before raising. A `BackupError`
(exit 1) therefore means: **the previous known-good archive is untouched**.
This is Invariant 4 from the spec, and it's exactly what the multi-item
isolation and interrupted-process integration tests in `tests/` exist to
keep true across future changes — see `tests/test_integration.py`.

## Exit code 5 in detail

Exit code 5 exists because the backup archive and the history log are
two separate durability guarantees, and the code deliberately does not
let a failure in the second one undo success in the first (rolling back
a validated, published archive because a text-file write failed would be
strictly worse for data safety). Concretely:

1. The archive was tested (`7z t`), validated against the expected
   source inventory, and published via atomic `os.replace()`.
2. `HistoryManager.record()` then tried (with retries and backoff — see
   `HISTORY_RETRY_ATTEMPTS` / `HISTORY_RETRY_BACKOFF_SECONDS`) to prepend
   an entry to `backup_history.txt`, and every attempt failed (lock
   contention, disk full on that filesystem, permissions, etc.).
3. Rather than losing the record, a durable **pending record** is written
   to a sidecar file: `.backup_history.pending.<run_id>.json` (or
   `.backup_history.txt.pending.<run_id>.json`) next to where
   `backup_history.txt` lives.
4. The next time *any* operation runs (`--new`, `--update`, or the
   interactive menu — anything that constructs a `HistoryManager` and
   calls `reconcile_pending()`), that pending record is automatically
   merged into `backup_history.txt` and the sidecar file is deleted.

**What a wrapper should do on exit code 5:**
- Log it distinctly from both 0 (full success) and 1 (real failure) —
  e.g. "backup OK, history pending."
- It is safe to keep running on the normal schedule; the next successful
  run's `reconcile_pending()` call will fold the missed entry in
  automatically, in the correct (newest-first) position.
- If you see exit code 5 repeatedly across multiple runs, that's a
  standing problem with the history file's filesystem/lock (not a
  one-off blip) and is worth investigating directly — see
  `RUNBOOK.md`'s section on stuck pending files.
- Do not manually edit or delete the `.pending.*.json` file unless you've
  read `RUNBOOK.md` — the run's outcome is only recorded once it's either
  reconciled automatically or handled per that doc.

## Logging vs. history vs. exit code

These are three different signals, on purpose, and a wrapper script
should not conflate them:

- **Exit code** — the machine-readable outcome for the wrapper's own
  branching logic. This document.
- **`backup_history.txt`** — the human-readable + machine-parseable
  audit trail of every backup attempt (see the `[BACKUP_META]` line in
  each entry). This is the source of truth `--verify` and future updates
  read from.
- **`backup.log`** (Roadmap 1.1) — a timestamped, leveled operational log
  for debugging *why* something happened, separate from the audit
  record. `--log-level` controls verbosity (`DEBUG` for full 7-Zip
  command tracing, default `INFO`), `--log-file` overrides its location.
  This is what to `tail -f` while diagnosing a stuck job, and what to
  grep for `ERROR`/`WARNING` in a periodic health check — it's not meant
  to be parsed for backup outcomes; use `backup_history.txt` for that.
