# Backup

A Python backup utility using a 7-Zip-compatible command-line tool. Configure
`BACKUP_ITEMS` in `backup.py` with the source folders to back up.

## Usage

Run these commands from the Backup directory:

```text
python backup.py --check
python backup.py --new
python backup.py --update "path/to/Backup.7z"
python backup.py --verify "path/to/Backup.7z"
```

Run `python backup.py` for the interactive menu, or `python backup.py --help`
for all options. `--check` checks the installed archiver, directory access,
and locking in the actual environment.

Backups are prepared and validated in a temporary archive before replacing the
destination. Review detected configuration changes carefully: removing a source
from the configuration removes its content from the archive on an accepted update.

## Recovery and outcomes

- [Exit codes](docs/EXIT_CODES.md) explains success, failures, and published backups
  whose history could not be saved.
- [Recovery runbook](docs/RUNBOOK.md) covers interrupted runs, pending history,
  archive locks, verification, and unavailable sources.

The automated test suite was removed at the owner's request. Earlier versions
remain available in Git history.
