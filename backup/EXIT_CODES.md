# Exit codes

`backup.py` returns one of these codes from `--new`, `--update`, and `--verify`.
Anything else is an unexpected crash, not a designed outcome.

| Code | Meaning | What to do |
|---|---|---|
| 0 | Success. | Nothing. |
| 1 | `BackupError` — missing source, 7-Zip error, post-sync validation failed, insufficient disk space, config change not confirmed, interrupted mid-transaction, or another backup process already running against this archive. Archive was not modified. | Investigate. Do not treat this as success. |
| 2 | `ConfigError` — invalid configuration (empty `BACKUP_ITEMS`, name collisions, relative or overlapping paths, `--dry-run` used without `--update`). | Fix the config. Retrying without fixing it will fail the same way. |
| 3 | `DependencyError` — 7-Zip not found on `PATH` or at `--sevenzip`. | Check the 7-Zip install / `PATH` / `--sevenzip` argument. |
| 4 | Verification failed (`--verify` only) — `7z t` integrity check failed. | Investigate promptly. The archive itself may be corrupt. |
| 5 | Partial success — archive created/updated and published, but the entry could not be written to `backup_history.txt`. | Not a hard failure. See "Exit code 5" below. |

## Concurrent invocation

Two `--new`/`--update` runs against the same archive are not supported. Each
run takes a non-blocking, per-archive lock (`.<archive-name>.lock`, next to
the archive) for the full duration of the run. A second invocation that finds
the lock already held exits immediately with code 1
("Another backup process appears to already be running against ..."), rather
than waiting.

`--verify` is not affected by this lock — it only reads the archive and can
run at the same time as anything else.

## `--check`

`python backup.py --check` is a separate diagnostic mode. It does not touch
`BACKUP_ITEMS` and does not run a backup. It checks if: a 7-Zip binary is
reachable, the app directory is writable, and the locking mechanism works.
Exits 0 on pass, 1 on fail.

## The archive is always safe on failure

Every failure path in `new_backup`/`update_backup` runs before
`ArchiveTransactionManager.publish()` (the only call that replaces the
primary archive), or cleans up the transaction file before raising. Exit
code 1 means the previous archive is untouched.

## Exit code 5 in detail

1. The archive was tested, validated, and published successfully.
2. Writing the entry to `backup_history.txt` failed after retries (lock
   contention, disk full, permissions).
3. A pending record is saved instead:
   `.backup_history.pending.<run_id>.json`.
4. The next run (`--new`, `--update`, `--verify`, or the interactive menu)
   automatically merges the pending record into `backup_history.txt` and
   deletes the sidecar file.

What to do:
- Log this separately from both 0 and 1.
- Safe to keep running on the normal schedule — the next successful run
  reconciles it automatically.
- If exit code 5 keeps happening, investigate the history file's
  filesystem/lock — see `RUNBOOK.md`.
- Don't manually edit or delete the `.pending.*.json` file unless you've
  read `RUNBOOK.md`.

## Logging vs. history vs. exit code

- **Exit code** — machine-readable outcome for scripts/schedulers.
- **`backup_history.txt`** — human-readable and machine-parseable audit
  trail. Source of truth for `--verify`.
- **`backup.log`** — timestamped operational log for debugging. `--log-level`
  controls verbosity, `--log-file` sets its location.
