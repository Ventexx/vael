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
