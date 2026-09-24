# indexer

<!-- cover -->
![indexer cover](./cover.png)

---

A desktop library for images and their accompanying information. Turn folders of image + JSON pairs into a searchable visual collection, with quick access to tags, notes, and reusable text.

---

## features

- index PNG images with matching JSON information files
- group results into collapsible folders and subfolders
- refresh libraries without rebuilding unchanged entries
- search by filename, folder, JSON content, or identifier
- combine several searches with semicolons; show matching images once
- view all results in one continuous list
- browse thumbnails and open a larger image viewer
- drag image files into other applications
- copy individual information fields, add tags, and edit image or folder information
- organize images with persistent or temporary identifiers
- keep reusable notes in optional categories
- switch between several indexed libraries
- run configured Python scripts on startup or reload, with cancellation and error reporting
- background searching, scanning, and preview loading
- scroll expanded folders and note sections into view

---

## installation & removal

**Install**

1. Install [Python](https://www.python.org/downloads/). Enable **Add Python to PATH** if the installer offers it.
2. Download Vael using **Code → Download ZIP** on GitHub, then extract it.
3. Open the `indexer` folder. On Windows, double-click `start.bat`. On Linux, open a terminal in this folder and run `bash start.sh`.
4. Wait while the launcher creates a local `venv` folder, downloads the required packages, and opens the app.

Use the same launcher each time. Internet access is needed when it installs or updates packages. The `start_silent` variants are alternate launchers without the normal console output.

Use **Add Folder** in the app to choose a library. Each indexed image needs a matching `.json` file containing its information. Indexer does not generate missing information files automatically.

**Uninstall**

Close Indexer and delete its `indexer` folder, including `venv`. For a complete reset, also remove the `.vael_indexer` data folder below after saving any notes you want to keep. Your image library is separate; keep it when removing the app.

---

## local data

Indexer stores its databases, notes, and preferences in your user folder:

- **Windows:** press **Win+R** and enter `%USERPROFILE%\.vael_indexer`.
- **Linux / macOS:** `~/.vael_indexer/`.

That folder contains:

- `.db` files — searchable library indexes
- `*_file_cache.json` — information used to speed up rescans
- `prefs.json` — preferences
- `notes.json` — reusable notes
- `startup_scripts.json` — registered scripts and their options
- `roots.json` — registered library folders

The older `.asset_indexer` folder is migrated when the new folder does not already exist.

Indexing reads your source files. Explicit tag, JSON, and persistent-identifier edits save changes to the JSON files beside your images. Temporary identifiers last only while that database remains loaded. Removing a library from Indexer removes its index, not the original images.

---

## file structure

```text
app.py                       — the desktop app
requirements.txt             — packages needed by the app
start.bat / start.sh          — Windows / Linux setup and launch
start_silent.bat / .sh        — alternate launchers
indexer.md                   — this guide
icon.png / .ico               — app icons
cover.png                    — the cover image above
venv/                        — downloaded Python packages; created by the launcher
```

Optional startup scripts can be stored anywhere; select them through the app's **Startup Scripts** settings.
