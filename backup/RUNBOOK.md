# Recovery runbook

Plain-language instructions for what to do when you find one of the
artifacts below — written for a human at 2 AM who has not read the spec
and just wants to know if their backups are OK. Pair this with
`EXIT_CODES.md` for what the automation *saw*; this doc is for what to do
about it by hand.

---

## What this tool stores about your filesystem layout (by design)

`manifest.json` (inside every archive) and `backup_history.txt` both
store the **full absolute source path** of every configured
`BackupItem` — e.g. `C:\Users\yourname\Documents` or
`/home/yourname/Photos`. This is a deliberate design decision, not an
oversight (Roadmap Phase 3 secrets/PII review):

- The tool needs the exact source path to re-locate and re-sync each
  logical item on every subsequent `--update`, and `--verify`'s history
  comparison needs it too.
- Nothing about this leaves your machine on its own — it's not
  transmitted anywhere, only written to `manifest.json` (inside
  `Backup.7z` itself) and `backup_history.txt` (a plain-text file beside
  `app.py`).

**What this means for you in practice:** if you ever share, upload, or
back up `backup_history.txt` or the archive itself somewhere semi-public
(a support forum, a bug report, a public git repo, cloud storage you
don't fully control), your local folder structure — including your
Windows username, if it appears in the path (`C:\Users\<name>\...`) —
goes with it in plain text. This is normal and expected for a personal
backup tool's own bookkeeping, but it's worth knowing before you paste
`backup_history.txt` into a bug report or GitHub issue: redact the paths
first if you'd rather not share your folder layout/username publicly.

`backup.log` (Roadmap 1.1) can also contain source paths in its
operational trace, depending on `--log-level` — the same consideration
applies if you ever share a log file for troubleshooting.

---

## "I found a `.Backup.7z.<hex>.new` file next to my archive"

**What it is:** A leftover, abandoned transaction archive from a run that
was interrupted (crashed, killed, machine lost power, etc.) before it
could either finish successfully or clean up after itself.

**Is my real backup OK?** Almost certainly yes. The file named exactly
`Backup.7z` (no dot-prefix, no `.new` suffix) is the one and only archive
the tool ever treats as "the backup." The `.new` file is only ever
promoted to replace it via a single atomic `os.replace()` call at the
very end of a fully-validated, successful run (`ArchiveTransactionManager.
publish()`). If a `.new` file exists, that atomic replace either never
happened or happened for a *different*, later run — either way, the
plain `Backup.7z` you already have is untouched by the interrupted run.
Run `python backup.py --verify Backup.7z` to confirm its integrity if
you want to be sure.

**How long is it safe to leave it?** Indefinitely, in the sense that it
won't corrupt anything just by existing. It's flagged as a warning
(`_leftover_transaction_warnings`) on every subsequent `--new`/`--update`/
`--dry-run` run against that archive, specifically so it doesn't get
silently forgotten. Practically: investigate it the same day you notice
the warning, mainly because it's taking up disk space and its presence
means *some* run failed to finish cleanly — worth knowing why.

**What to do:**
1. Confirm the real archive still verifies: `python backup.py --verify
   Backup.7z`.
2. If you want to inspect the abandoned transaction file before deleting
   it (e.g. to see how far the interrupted run got): `7z l -slt
   .Backup.7z.<hex>.new`. It is a real, independently-openable 7-Zip
   archive — it's just not the one that's authoritative.
3. Once you're satisfied the real archive is fine, delete the leftover:
   `rm .Backup.7z.<hex>.new`. Nothing in the tool ever reads it back in;
   it's purely a warning-trigger until you remove it.
4. If you *want* to know why the run was interrupted, check `backup.log`
   around the timestamp in the `.new` file's name/mtime for the last
   thing that was logged before the gap.

---

## "I found a `.backup_history.pending.<run_id>.json` file"

**What it is:** A durable record of a backup run that **succeeded** (the
archive itself was published) but whose entry could not be written into
`backup_history.txt` after retrying — this is exit code 5, see
`EXIT_CODES.md`.

**Is my real backup OK?** Yes — this file only ever gets created *after*
`ArchiveTransactionManager.publish()` has already succeeded. The backup
happened; only the audit-log write is pending.

**How long is it safe to leave it?** Every subsequent run (`--new`,
`--update`, or the interactive menu) automatically calls
`HistoryManager.reconcile_pending()` before doing anything else, which
merges this file into `backup_history.txt` (in the correct position) and
deletes it. So in the common case, it resolves itself on the very next
run and you don't need to do anything. It's worth investigating by hand
only if:
- You don't expect another run soon (e.g. this is a one-off manual
  backup and there's no scheduled next run), or
- You've seen more than one of these accumulate, which means whatever
  blocked `backup_history.txt` (lock contention, permissions, that
  filesystem being full or read-only) is a standing problem, not a
  one-off blip.

**How to reconcile it manually right now**, without waiting for another
backup run:
```
python -c "
from pathlib import Path
from backup import HistoryManager
HistoryManager(Path('.')).reconcile_pending()   # run from the directory containing backup_history.txt
"
```
This is exactly what happens automatically at the start of any real run
— safe to run as many times as you like; it's a no-op if there's nothing
pending.

**If reconciliation keeps failing:** open the `.json` file directly
(it's plain JSON: `{"entry_text": "...", "meta": {"run_id": N}}`) — the
`entry_text` field is the exact, complete history entry that should have
been written. You can manually prepend it to the top of
`backup_history.txt` yourself if you need the audit trail correct
immediately and can't resolve the underlying filesystem/lock issue right
away. Only delete the `.pending.*.json` file after you've confirmed its
`entry_text` is actually present in `backup_history.txt` — deleting it
first with the merge unconfirmed permanently loses that run's audit
record.

---

## "I found a stale `backup_history.txt.lock` file"

**What it is:** The advisory lock file `HistoryManager` uses
(`_CrossPlatformLock`) to serialize writes to `backup_history.txt`. It's
created on first use and normally just sits there, locked only for the
brief moment of an actual write.

**When this is actually a problem:** only if a process holding the lock
was killed in a way that didn't release it (rare, but possible with a
hard kill on some platforms/filesystems) *and* every subsequent run has
been failing to acquire it (you'd see repeated exit-code-5 runs and/or
`WARNING` lines about history writes failing, in `backup.log`).

**What to do:** First, confirm no `backup.py` process is actually
running right now (`ps aux | grep backup.py` / Task Manager). If nothing
is running and you're still seeing lock-related failures, it's safe to
delete `backup_history.txt.lock` — it's a lock file, not the history
itself; deleting it just means the next writer creates a fresh one.
Do **not** delete `backup_history.txt` (no `.lock` suffix) — that's the
actual audit trail.

---

## "I got exit code 1: 'Another backup process appears to already be running...'"

**What it is:** The non-blocking, per-archive lock introduced in Roadmap
2.2 (`.Backup.7z.lock`, next to the archive — separate from
`backup_history.txt.lock`). A second `--new`/`--update` invocation
against the same archive found the lock already held and refused to
proceed, rather than waiting or racing the first one.

**Is my archive OK?** Yes. This check runs before any transaction
archive is touched — the rejected run made zero modifications.

**Is this actually a problem?** Usually not — it's the intended
behavior for an overlapping schedule (e.g. a slow run plus a
fixed-interval cron, or you started a manual run while a scheduled one
was already going). Only investigate further if you keep seeing it well
after the run that's supposedly "in progress" should have finished.

**What to do:**
1. Check whether a `backup.py` process is actually running right now
   (`ps aux | grep backup.py` / Task Manager). If yes, that's the
   expected cause — just wait for it (or the next scheduled run) to
   finish; nothing to fix.
2. If nothing is running and you're still getting this error, the lock
   file is stale (most likely from a hard kill that didn't get a chance
   to release it — the OS releases the underlying advisory lock
   automatically when the holding process dies, but on some
   platform/filesystem combinations that can be delayed). Delete
   `.Backup.7z.lock` (or `.<your-archive-name>.lock` next to whichever
   archive is affected) and retry. Deleting it is safe — it's a lock
   file, not a transaction archive or the backup itself.
3. If this happens repeatedly with no process actually running, that's
   worth investigating as a standing problem (e.g. a scheduler
   double-firing) rather than deleting the lock file every time.

**`python backup.py --check`** (Roadmap 2.1) exercises this exact
locking mechanism against a scratch file as part of its self-test, so
it's a good first thing to run if you suspect a filesystem-level locking
problem rather than a genuinely running process.

---

## "I found a `.<archive>.<hex>.dellist.txt` file"

**What it is:** A short-lived scratch file (`SevenZipRunner.
delete_paths`) listing exact archive-relative paths to delete, passed to
7-Zip via `-i@listfile`. It's created immediately before the delete call
and unlinked immediately after (`finally: listfile.unlink()`), so seeing
one at rest means a run was interrupted at that exact moment.

**What to do:** Same logic as the `.new` transaction file above — it's
scratch input, not output; nothing reads it back in. Confirm
`--verify` passes on the real archive, then delete the `.dellist.txt`
file. It only ever contains a plain list of paths (one per line, UTF-8),
safe to open and read if you're curious what deletion was in flight.

---

## "The archive won't `--verify`"

**What it is:** `7z t` failed — either genuine archive corruption, or the
archive isn't actually a valid 7-Zip file at that path.

**What this means:** This is the one failure mode in this document that
is a real, current-state problem with the archive itself, not a leftover
artifact from an interrupted run. Exit code 4.

**What to do:**
1. Do not run `--update` against it yet — `update_backup` requires a
   valid `manifest.json` inside the archive and will refuse to proceed
   without one anyway, but even if it didn't, updating a corrupt archive
   just compounds the problem.
2. Check whether a `.new` transaction file exists alongside it (see
   above) — if the corrupt file is actually a stray transaction that got
   renamed/copied somewhere it shouldn't be, the real `Backup.7z` may be
   elsewhere or may not have been touched by the corrupting event at all.
3. Check your most recent successful backup's SHA-256 in
   `backup_history.txt` (`latest_successful()` / the `[BACKUP_META]`
   `sha256` field) against any other copies of the archive you may have
   (cloud sync, external drive, prior manual copy) to find a known-good
   copy.
4. If you have no known-good copy, `7z t -slt` (or your file manager's
   archive tool) may still be able to partially list or extract
   uncorrupted members even though the whole-archive test fails — this
   is a last resort, not a guarantee.

---

## "A required source directory is missing / unavailable"

**What it is:** `_validate_sources` (or, for a permission-only failure on
the root itself, `SourceInventoryManager._scan_root`) raised `BackupError`
before touching the archive at all — a configured `BackupItem.path`
doesn't exist, isn't a directory, or isn't readable (Roadmap 1.6). Exit
code 1.

**Is my archive OK?** Yes — this check runs before any transaction
archive is created, so nothing was modified.

**What to do:** This is usually exactly what it looks like — a drive
letter changed, a network share isn't mounted, an external drive isn't
plugged in, or permissions were changed on that directory. Fix the
underlying availability issue, then re-run. Do not remove the item from
`BACKUP_ITEMS` just to make the error go away unless you actually intend
to stop backing up that source — removing it is a configuration change
that `--update` will ask you to confirm (`--accept-config-changes`) the
next time, and it will then delete that source's content from the
archive.

---

## Verifying this test suite's environment assumptions

`tests/test_permissions.py` needs to run as root with a passwordless
`testuser` account and `runuser` available, because root bypasses
filesystem permission bits entirely — there's no way to observe real
permission-denied behavior while running as root. If those tests are
being skipped and you expected them to run:
```
useradd -m -s /bin/bash testuser   # create the account, if it doesn't exist
which runuser                       # confirm runuser is installed
```
`tests/test_integration.py` needs a real `7z`/`7za`/`7zr` on `PATH`; if
those are skipping, install 7-Zip (`apt-get install p7zip-full` on
Debian/Ubuntu, or the equivalent for your platform) — see also Roadmap
2.1 for pinning a supported version range.
