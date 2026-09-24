# Exit codes

`backup.py` returns one of these codes from `--new`, `--update`, and `--verify`.
Anything else is an unexpected crash, not a designed outcome.

| Code | Meaning | What to do |
|---|---|---|
| 0 | Success. | Nothing. |
| 1 | `BackupError` — missing source, 7-Zip error, post-sync validation failed, insufficient disk space, config change not confirmed, interrupted mid-transaction, or another backup process already running against this archive. Archive was not modified. | Investigate. Do not treat this as success. |
| 2 | `ConfigError` — invalid configuration (empty `BACKUP_ITEMS`, name collisions, relative or overlapping paths, `--dry-run` used without `--update`). | Fix the config. Retrying without fixing it will fail the same way. |
| 3 | `DependencyError` — 7-Zip not found on `PATH` or at `--sevenzip`. | Check the 7-Zip install / `PATH` / `--sevenzip` argument. |
| 4 | Verification failed or incomplete (`--verify` only): integrity or manifest failure, busy archive, read failure, or change during verification. | Read the reason; retry busy/incomplete checks after the writer finishes. |
| 5 | Partial success — archive created/updated and published, but the entry could not be written to `backup_history.txt`. | Not a hard failure. See "Exit code 5" below. |
| 6 | Archive published, but neither history nor its pending recovery record could be saved. | Preserve the printed archive path, version, checksum, and operational log. Repair history storage; automatic recovery is not assured. |

## Concurrent invocation

Two `--new`/`--update` runs against the same archive are not supported. Each
run takes a non-blocking, per-archive lock (`.<archive-name>.lock`, next to
the archive) for the full duration of the run. A second invocation that finds
the lock already held exits immediately with code 1
("Another backup process appears to already be running against ..."), rather
than waiting.

`--verify` holds the same lock through integrity testing, hashing, and manifest
reading. A busy verification exits with code 4; a backup blocked by verification
exits with code 1. Unrelated archives can run concurrently. Shared history run IDs
are reserved under the history lock before work starts; interrupted runs may leave
gaps. Keep `.backup_history.txt.sequence` with the history directory.

## `--check`

`python backup.py --check` is a separate diagnostic mode. It does not touch
`BACKUP_ITEMS` and does not run a backup. It checks if: a 7-Zip binary is
reachable, the app directory is writable, and the locking mechanism works.
Exits 0 on pass, 1 on fail.

## The archive is always safe on failure

Expected failures before `ArchiveTransactionManager.publish()` leave the previous
archive untouched and return code 1. Failures saving history after publication
return code 5 or 6 and explicitly report the published archive. Interrupted
processes and power loss have no guaranteed exit code; inspect and verify the
archive before deciding whether to retry. Leftover transaction files may remain.

## Exit code 5 in detail

1. The archive was tested, validated, and published successfully.
2. Writing the entry to `backup_history.txt` failed after retries (lock
   contention, disk full, permissions).
3. A pending record is saved instead:
   `.backup_history.txt.pending.<run_id>.<unique-id>.json`.
4. The next backup or verification run (including through the interactive menu)
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

Code 6 is different: saving the pending record also failed. The archive is already
published, but do not assume a later run can recover its history automatically.
The success entry is sent to the operational logger and its identifying details
are printed; whether the log reaches disk depends on that destination remaining
writable. Save the console output separately and repair storage before retrying.

## Logging vs. history vs. exit code

- **Exit code** — machine-readable outcome for scripts/schedulers.
- **`backup_history.txt`** — human-readable and machine-parseable audit
  trail. Source of truth for `--verify`.
- **`backup.log`** — timestamped operational log for debugging. `--log-level`
  controls verbosity, `--log-file` sets its location.
