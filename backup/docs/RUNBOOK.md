# Recovery runbook

What to do about each artifact/error the tool can leave behind. See
`EXIT_CODES.md` for what the automation returns; this is what to do about it.

---

## A `.Backup.7z.<hex>.new` file next to the archive

**What it is:** A leftover transaction archive from a run that was
interrupted before it could finish or clean up.

**Is the real backup OK?** Yes. `Backup.7z` (no dot prefix, no `.new` suffix)
is the only archive the tool treats as "the backup." The `.new` file is only
promoted to replace it via one atomic `os.replace()` call at the very end of
a successful run. If a `.new` file exists, that replace never happened for
that run — `Backup.7z` is untouched. Run `python backup.py --verify
Backup.7z` to confirm.

**How long can it sit there?** Indefinitely — it won't corrupt anything. It's
flagged as a warning on every later `--new`/`--update`/`--dry-run` run
against that archive, so it won't be silently forgotten. Investigate the
same day, mainly because it's using disk space.

**What to do:**
1. Confirm the real archive: `python backup.py --verify Backup.7z`.
2. Inspect the leftover if you want: `7z l -slt .Backup.7z.<hex>.new`. It's
   a normal, independently-openable 7-Zip archive, just not the authoritative
   one.
3. Delete it: `rm .Backup.7z.<hex>.new`. Nothing reads it back in.
4. To find out why the run was interrupted, check `backup.log` around that
   file's timestamp.

---

## A `.backup_history.pending.<run_id>.json` file

**What it is:** A record of a backup run that succeeded, but whose entry
couldn't be written to `backup_history.txt`. This is exit code 5.

**Is the backup OK?** Yes — this file is only created after the archive was
already published. Only the audit-log write is pending.

**How long can it sit there?** Every later run (`--new`, `--update`,
interactive menu) automatically reconciles it and deletes the file. Worth
checking by hand only if:
- No other run is scheduled soon, or
- More than one of these has piled up — meaning whatever's blocking
  `backup_history.txt` (lock contention, permissions, full/read-only disk)
  is a standing problem.

**Reconcile manually, without waiting for another run:**
```
python -c "
from pathlib import Path
from backup import HistoryManager
HistoryManager(Path('.')).reconcile_pending()   # run from the directory containing backup_history.txt
"
```
Safe to run any time — no-op if nothing is pending.

**If reconciliation keeps failing:** open the `.json` file (plain JSON —
`{"entry_text": "...", "meta": {"run_id": N}}`). `entry_text` is the exact
entry that should have been written. You can prepend it to
`backup_history.txt` yourself. Only delete the `.pending.*.json` file after
confirming `entry_text` is actually in `backup_history.txt`.

---

## A stale `backup_history.txt.lock` file

**What it is:** The lock file used to serialize writes to
`backup_history.txt`. Normally just sits there, briefly locked during an
actual write.

**When it's a real problem:** only if a process holding it was killed
without releasing it, and every run since has been failing to acquire it
(repeated exit code 5, or `WARNING` lines about history writes in
`backup.log`).

**What to do:** Confirm no `backup.py` process is running
(`ps aux | grep backup.py` / Task Manager). If nothing is running and you're
still seeing failures, delete `backup_history.txt.lock` — it's a lock file,
not the history itself. Do **not** delete `backup_history.txt`.

---

## Exit code 1: "Another backup process appears to already be running..."

**What it is:** The per-archive lock (`.Backup.7z.lock`, next to the
archive). A second `--new`/`--update` found the lock held and refused to
proceed.

**Is the archive OK?** Yes — this check runs before any transaction archive
is touched.

**Is it actually a problem?** Usually not — expected for an overlapping
schedule (a slow run plus a fixed-interval cron, or a manual run started
while a scheduled one is going). Only investigate further if you keep seeing
it after the other run should have finished.

**What to do:**
1. Check if `backup.py` is actually running (`ps aux | grep backup.py` /
   Task Manager). If yes, wait for it.
2. If nothing is running and you're still getting this, the lock file is
   stale. Delete `.Backup.7z.lock` (or `.<archive-name>.lock`) and retry.
3. If this keeps happening with nothing running, investigate as a
   scheduling problem (e.g. a double-firing scheduler), not a one-off.

`python backup.py --check` exercises this exact locking mechanism as part of
its self-test.

---

## A `.<archive>.<hex>.dellist.txt` file

**What it is:** A short-lived scratch file listing paths to delete, used
with 7-Zip's `-i@listfile`. Created right before a delete call and removed
right after — seeing one at rest means a run was interrupted at that exact
moment.

**What to do:** Same as the `.new` file above. Confirm `--verify` passes,
then delete it. It's a plain list of paths, one per line, UTF-8.

---

## `--verify` fails

**What it is:** `7z t` failed — genuine corruption, or the file isn't a
valid 7-Zip archive.

**What this means:** This is the one case here that's a real, current
problem with the archive itself, not a leftover artifact. Exit code 4.

**What to do:**
1. Don't run `--update` on it yet.
2. Check if a `.new` transaction file exists alongside it (see above) — the
   corrupt file might actually be a stray transaction, not the real archive.
3. Check the most recent successful SHA-256 in `backup_history.txt`
   (`[BACKUP_META]` `sha256` field) against any other copies you have.
4. If you have no known-good copy, `7z t -slt` or your file manager's
   archive tool may still partially list/extract uncorrupted members. Last
   resort, not a guarantee.

---

## A required source directory is missing or unavailable

**What it is:** A configured `BackupItem.path` doesn't exist, isn't a
directory, or isn't readable. Exit code 1, raised before touching the
archive.

**Is the archive OK?** Yes — nothing was modified.

**What to do:** Usually a drive letter changed, a network share isn't
mounted, an external drive isn't plugged in, or permissions changed. Fix the
availability issue and re-run. Don't remove the item from `BACKUP_ITEMS`
just to silence the error unless you actually intend to stop backing it up
— removing it is a configuration change that `--update` will ask you to
confirm, and it will delete that source's content from the archive.

---

## What the tool stores about your folder layout

`manifest.json` (inside the archive) and `backup_history.txt` both store the
full absolute source path of every configured item (e.g.
`C:\Users\yourname\Documents`). This is intentional — the tool needs it to
re-locate and re-sync each item on every update. Nothing is transmitted
anywhere; it's only written locally.

If you ever share `backup_history.txt`, the archive, or `backup.log`
somewhere semi-public (forum post, bug report, public repo), your folder
structure — including your username, if it's in the path — goes with it in
plain text. Redact paths first if you don't want that shared.

---

## Test suite environment notes

`tests/test_permissions.py` needs to run as root with a passwordless
`testuser` account and `runuser` available — root bypasses permission bits
entirely, so there's no way to test permission-denied behavior while running
as root. If these tests are skipping and you expected them to run:
```
useradd -m -s /bin/bash testuser
which runuser
```

`tests/test_integration.py` needs a real `7z`/`7za`/`7zr` on `PATH`. If those
are skipping, install 7-Zip (`apt-get install p7zip-full` on Debian/Ubuntu,
or the Windows/macOS equivalent).
