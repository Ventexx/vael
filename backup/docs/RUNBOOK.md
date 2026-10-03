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

## A `.backup_history.txt.pending.<run_id>.<unique-id>.json` file

**What it is:** A run's history entry that could not be written to
`backup_history.txt`. A successful backup with this artifact returns code 5;
a failed backup can also have pending failure history and still returns code 1.
The older `.backup_history.pending.*.json` naming pattern is also recognized.

**Is the backup OK?** Check the entry's status and the command's result. A
SUCCESS entry records a published backup; a FAILED entry records a failed attempt.

**How long can it sit there?** Later backup and verification runs (including
through the interactive menu) attempt recovery and remove successfully merged
records. Damaged records remain in place with a warning. Worth
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
Run with the Backup directory importable and pass the actual history directory.
Safe to repeat: recovery extracts `entry_text`, never the JSON wrapper, and skips
an exact entry already present after a crash between publication and sidecar deletion.

**If reconciliation keeps failing:** open the `.json` file (plain JSON —
`{"entry_text": "...", "meta": {"run_id": N}}`). `entry_text` is the exact
entry that should have been written. You can prepend it to
`backup_history.txt` yourself. Only delete the `.pending.*.json` file after
confirming `entry_text` is actually in `backup_history.txt`. Stop other backup and
verification processes before manually editing history. Do not paste the JSON
wrapper into the log; its escaped metadata line cannot be parsed as a run.

## Exit code 6: archive published, history not saved

The archive passed validation and was published, but saving both history and its
pending recovery record failed. Preserve the printed path, version, and checksum,
and the SUCCESS entry from `backup.log` if that log was writable. Fix permissions
or storage space before another run. There may be an incomplete `.pending.*.tmp`
file, but it is not automatically recovered and must not be assumed complete.
Verify the archive; an unknown history state is expected until its entry is
recovered. Do not treat code 6 as evidence that the archive was left unchanged.

## A `.backup_history.txt.sequence` file

This is the last reserved run ID, shared by archives using the same history
directory. Keep it with the history file. Gaps are normal after interrupted runs;
do not reset it to remove gaps. A malformed sequence stops the backup before
publication. Restore it from a known-good copy or reconcile its value against
history, pending records, and logs with all writers stopped.

---

## A `backup_history.txt.lock` file

**What it is:** The lock file used to serialize writes to
`backup_history.txt`. Normally just sits there, briefly locked during an
actual write.

The file normally persists when unlocked. The operating system releases the
lock when the owning process exits; the file's existence does not mean a lock
is held. It protects run-ID reservation as well as history writes/recovery.

**What to do:** Check for running processes and inspect the reported storage or
permission error. Never delete a lock file while a process might hold it: on some
filesystems another process could then lock a different file at the same path.

---

## Exit code 1: "Another backup process appears to already be running..."

**What it is:** The per-archive lock (`.Backup.7z.lock`, next to the
archive). A second `--new`/`--update` found the lock held and refused to
proceed. Verification holds this lock too; a busy `--verify` returns code 4.

**Is the archive OK?** Yes — this check runs before any transaction archive
is touched.

**Is it actually a problem?** Usually not — expected for an overlapping
schedule (a slow run plus a fixed-interval cron, or a manual run started
while a scheduled one is going). Only investigate further if you keep seeing
it after the other run should have finished.

**What to do:**
1. Check if `backup.py` is actually running (`ps aux | grep backup.py` /
   Task Manager). If yes, wait for it.
2. The lock file can remain after normal exit; that alone is harmless. Check
   for a verification process too. Do not delete a potentially active lock file.
3. If this keeps happening with nothing running, investigate as a
   scheduling problem (e.g. a double-firing scheduler), not a one-off.

`python backup.py --check` exercises this exact locking mechanism as part of
its self-test.

---

## A `vael-delete-*.txt` temporary file

**What it is:** A short-lived scratch file in the system temporary directory,
listing paths to delete, used with 7-Zip's `-i@listfile`. Created right before a
delete call and removed right after. Older versions put
`.<archive>.<hex>.dellist.txt` beside the archive. Either may remain after a crash.

**What to do:** Same as the `.new` file above. Confirm `--verify` passes,
then delete it. It's a plain list of paths, one per line, UTF-8.

---

## `--verify` fails

**What it is:** Read the result. Code 4 can mean failed archive integrity, an
invalid/missing backup manifest, a busy archive, an unreadable file, or a file
that changed during verification. Busy/incomplete checks do not establish corruption.

For busy/incomplete checks, resolve the stated condition and retry. For an actual
integrity failure, follow the steps below.

**What to do:**
1. Don't run `--update` on it yet.
2. Check if a `.new` transaction file exists alongside it (see above) — the
   corrupt file might actually be a stray transaction, not the real archive.
3. Check the latest successful SHA-256 for this archive's `backup_uuid` in `backup_history.txt`
   (`[BACKUP_META]` `sha256` field) against any other copies you have.
4. If you have no known-good copy, `7z t -slt` or your file manager's
   archive tool may still partially list/extract uncorrupted members. Last
   resort, not a guarantee.

History comparisons follow the manifest's backup UUID, so moving an archive does
not change its family. Recovered records do not make an older version the latest.
An intact archive with no matching checksum is reported as unknown history, not
as a known latest backup. Locks coordinate this utility's own processes; file
identity/timestamp checks additionally detect ordinary external replacement,
but cannot lock out arbitrary external writers.

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

If you ever share `backup_history.txt`, an unencrypted archive, or `backup.log`
somewhere semi-public (forum post, bug report, public repo), your folder
structure — including your username, if it's in the path — goes with it in
plain text. Redact paths first if you don't want that shared.

Protected archives encrypt the manifest and member names. External history,
pending history records, and logs are still plaintext. Keep them local and upload
only the completed encrypted archive. See [Protect](ENCRYPTION.md).

---

## Encrypted archive: wrong password or failed verification

Retry with the correct password in a terminal. No password reset exists. A wrong
password and damaged encrypted data may produce the same archiver error; do not
assume one solely from the message. Try a known-good copy and, if needed, ordinary
7-Zip. Do not overwrite a damaged archive with a failed conversion or restore.

`protect.py` must be beside `backup.py` for automatic encrypted updates. If it is
missing or incompatible, restore the matching companion. Plain backup operations
still work independently. Install both scripts from a trusted source.

An encrypted update never deliberately removes protection. The encrypted working
copy is checked before replacement. A `.new` file left by an interrupted
protected create/update is not automatically promoted; verify it with the password
before treating it as a recoverable backup.

## Conversion or restore was interrupted

Standalone conversion retains its input. Look for `vael-protect-*` directories in
the system temp folder or your selected `--work-dir`, and `.restore-*` beside the
restore destination. These may contain **decrypted files**. Confirm no process is
using them, preserve anything needed for recovery, then remove leftover scratch.
New manifest scratch uses `vael-manifest-*` in system temp. Older `.manifest_scratch`
folders may remain beside the app from earlier versions.

A failed final restore placement can leave partial files in the requested new
directory; the error says so. Choose another new directory for retrying rather
than merging into that partial result. Original archives are never deleted by
Protect. Cleanup is not secure erasure and cannot remove cloud version history.

## Password changed / archive converted, but history says unknown

Conversion rebuilds the archive, changing its checksum while retaining its backup
manifest. The old checksum no longer matches. Use Protect to verify integrity and
protection; then a successful normal backup update records a new version in the
chosen backup history. Keep using the original history location if you want the
full run history. Old password-protected copies still use the old password.

---

## Operational limits

Power-loss durability and network-filesystem locking behavior are not guaranteed.
Live source folders are not consistent snapshots: files can change while a backup
is being made. The archive lock coordinates this utility's processes, not arbitrary
external writers. See `EXIT_CODES.md` for interpreting incomplete verification and
history-persistence failures.
