# backup

<!-- cover -->
<img src="./icon.png" alt="backup icon" width="128">

---

A local backup tool for keeping selected folders together in a compressed archive. Create a backup, update it as your files change, and check that the archive can still be read.

---

## features

- create and update compressed `.7z` backups from several source folders
- give each source folder a clear name inside the archive
- add changed files and remove files that no longer exist in the selected sources
- review configuration changes before applying them to an existing backup
- prepare and check an updated archive before replacing the previous one
- verify archive integrity and compare it with its recorded backup history
- keep a readable history of runs, versions, and file checksums
- recover history entries that could not be saved immediately
- prevent simultaneous operations from interfering with the same archive

---

## installation & removal

**Install**

1. Download Vael using **Code → Download ZIP** on GitHub, then extract it.
2. Install [Python](https://www.python.org/downloads/) and a [7-Zip](https://www.7-zip.org/) command-line tool.
3. Open `backup.py` in a text editor. In the clearly marked configuration section, replace the example source folders in `BACKUP_ITEMS` with your own. This app currently requires that one-time text-file setup.
4. Open a terminal in the `backup` folder: on Windows, open the folder in File Explorer, type `powershell` in its address bar, and press Enter.
5. Run `python backup.py --check` to check the setup, then `python backup.py` to open the text menu. There is no separate graphical window.

If the archiver is not found, use `python backup.py --sevenzip "C:\path\to\7z.exe"` with its actual location. On systems where Python is called `python3`, use that name instead.

**Uninstall**

Close the app and delete its `backup` folder. **Move any archives and history you want to keep out of that folder first.** Python and 7-Zip are separate applications and can remain installed.

---

## usage

Open a terminal in the `backup` folder and use the commands below. Replace example paths with your own, keeping quotation marks around paths with spaces. Use `python3` instead of `python` if that is how Python is installed on your system.

### choose what to back up

Open `backup.py` in a text editor and find `BACKUP_ITEMS`. Each line gives a folder a name inside the archive and points to its full location on your computer:

```python
BACKUP_ITEMS = [
    BackupItem("Documents", Path(r"D:\Documents")),
    BackupItem("Projects", Path(r"E:\Projects")),
]
```

Replace the example folders; do not leave entries for drives you do not have. Each source must exist, use a full path, and be separate from the other sources. Do not include the same folder twice or include both a folder and one of its subfolders. Use distinct, simple names such as `Documents` and `Projects`.

The nearby `ARCHIVE_NAME` setting controls the new archive's name; the default is `Backup.7z` beside the app. `COMPRESSION_LEVEL` sets the default compression level, from 0 to 9; the default is 7. These settings remain in the file between runs.

### menu or direct commands

Run `python backup.py` for the text menu: **1** creates a backup, **2** updates one, **3** verifies one, and **Q** quits. For update and verify, paste the full archive path when asked. The menu uses the configured defaults. Use an explicit command below when supplying options such as compression or a different history folder.

| Command | What it does |
| --- | --- |
| `python backup.py --help` | Lists all commands and options. |
| `python backup.py --check` | Checks the archiver, folder access, locking, and available space; does not create a backup. |
| `python backup.py --new` | Creates a new backup from the configured sources. Refuses to overwrite an existing archive at the destination. |
| `python backup.py --update "D:\Backups\Backup.7z"` | Updates the selected archive from the currently configured sources. |
| `python backup.py --verify "D:\Backups\Backup.7z"` | Checks archive integrity, its internal record of sources/version, and its checksum against available history. |

Choose one command at a time. For a fresh backup when the default archive already exists, move or rename the previous archive first, or change `ARCHIVE_NAME`.

**Updating mirrors your configured sources:** it adds and updates files, and removes archived files that have disappeared from those sources. It does not retain a separate older version automatically. An unavailable source stops the update.

### preview and approve configuration changes

If you add, remove, rename, or relocate a configured source, first inspect the proposed change:

```text
python backup.py --update "D:\Backups\Backup.7z" --dry-run
```

This checks source availability and shows the configuration-change plan without changing the archive. It is not a file-by-file preview of every addition or deletion. Routine diagnostics may still be written to the log.

Once the plan is correct, apply it:

```text
python backup.py --update "D:\Backups\Backup.7z" --accept-config-changes
```

Removing a configured source removes its archived contents when accepted. The interactive menu asks for confirmation instead. Both options above belong to `--update`.

### optional settings

Add these options after a direct command:

| Option | Purpose |
| --- | --- |
| `--sevenzip "C:\Program Files\7-Zip\7z.exe"` | Selects the archiver executable when automatic detection cannot find it. Also works with `--check`. |
| `--compression 5` | Sets compression for this creation/update only. 0 stores without compression; 9 requests the strongest compression and can take longer. |
| `--history "D:\Backup History"` | Uses an existing folder for shared backup history during creation, update, or verification. You can also provide the path to `backup_history.txt` itself. |
| `--log-level DEBUG` | Sets diagnostic detail: `DEBUG`, `INFO`, `WARNING`, or `ERROR`. The default is `INFO`. |
| `--log-file "D:\Backup History\backup.log"` | Changes where diagnostic messages are written. Create the containing folder first. |

For example:

```text
python backup.py --new --compression 5 --history "D:\Backup History"
python backup.py --verify "D:\Backups\Backup.7z" --history "D:\Backup History"
```

Keep using the same history location when you move an archive. Verification can check an archive without matching history, but cannot identify it as a known latest version. These options do not select a new archive destination: `--new` uses `ARCHIVE_NAME`, and `--update` uses the archive path you provide.

### understand the result

The printed message explains the outcome. For scheduled commands, the exit code is the number returned when the program finishes:

| Code | Meaning |
| --- | --- |
| 0 | Operation succeeded. For verification, also read whether history was matched. |
| 1 | Backup operation failed or was cancelled before publication; the previous archive was not replaced. |
| 2 | Configuration or command options need correcting. |
| 3 | The required archiver could not be found or used. |
| 4 | Verification failed or could not complete, for example because the archive is busy. |
| 5 | Backup was saved; its history is waiting in a recovery record. |
| 6 | Backup was saved, but neither its history nor the recovery record could be saved. Preserve the printed details. |

Backup and verification cannot run against the same archive at the same time; retry a busy operation after the other finishes. Interrupted runs can leave temporary files. See the [recovery runbook](docs/RUNBOOK.md) for recovery and the [exit-code guide](docs/EXIT_CODES.md) for details. To restore files, open the archive in your archiver and extract them to your chosen folder; this app has no separate restore command.

---

## local data

By default, the following are stored beside `backup.py`:

- `Backup.7z` — the backup itself
- `backup_history.txt` — the history of backup attempts
- `backup.log` and rotated copies — diagnostic messages
- `.backup_history.txt.sequence` — the shared run counter
- pending history files — records waiting to be added to history

The source-folder configuration is in `backup.py`. Each archive contains a `manifest.json` describing its sources and version. Source paths are recorded in the archive and history.

Updates modify the archive you select. Archive lock and temporary files sit beside that archive; history can be placed elsewhere with `--history`. Nothing is uploaded by this tool. See the [recovery runbook](docs/RUNBOOK.md) and [exit-code guide](docs/EXIT_CODES.md) if an operation reports a problem.

---

## file structure

```text
backup.py          — the app and your source-folder configuration
pyproject.toml     — Python project information
README.md          — this guide
docs/              — recovery instructions and command outcomes
icon.png / .ico    — app icons
```
