# indexer

<!-- cover -->
![indexer cover](./cover.png)

---

A desktop app for browsing, searching, and editing structured data paired with visual assets — built for scanning folders of image + JSON pairs (e.g. AI-generated images with their prompt/tag metadata) into a fast, searchable visual library. Windows and Linux, with partial Wayland support.

---

## features

- **folder-based indexing** — scans directories for `.png` + `.json` pairs into a SQLite index, organized into collapsible, nested folder sections
- **incremental indexing** — background scans compare file timestamps and sizes, skip unchanged image/JSON pairs, and refresh folder metadata on every reload
- **background search**, with name, folder, JSON, identifier, and combined searches; counted pages show up to 500 matches at a time
- thumbnail grid with lazy-loaded previews and a bounded cache; replaced images refresh when their folder is reopened or results reload
- in-window image viewer with background decoding and arrow-key / on-screen navigation
- **drag & drop** — drag a card out of the app to copy the image file itself into another program
- per-field copy menu (right-click a card to copy any JSON field), plus a quick "Add Tag" action
- inline JSON editor for both individual assets and folder-level metadata (`!F-<folder>.json`)
- **notes window** — a separate panel for reusable text snippets, organized into optional categories
- **multiple databases** — index and switch between several folders; removing one only deletes its index, never the source folder
- **startup scripts** — register Python scripts to run automatically on launch or reload
- **dev mode** (`--dev`) — a safe, in-memory session that touches no real data

---

## installation

From inside this folder, the provided scripts handle everything — creating a virtual environment, installing dependencies, and launching the app:

- **Windows:** `start.bat`
- **Linux / macOS:** `chmod +x start.sh && ./start.sh`

Both also have a `_silent` variant (`start_silent.bat` / `start_silent.sh`) that launches without a console window.

**Manual setup**, if you'd rather do it yourself:
```bash
pip install -r requirements.txt
python app.py
```

---

## getting started

Click **≡ → Add Folder** and pick a directory containing `.png` images with matching `.json` files — it's indexed automatically and shown grouped by subfolder. Use the search bar (or press `/` to jump to it) to filter by asset name, or end a query with ` f` to filter by folder name instead.

Left-click a card to open it full-screen; right-click one for a menu to copy any JSON field, add a tag, or open the full JSON editor. Folder headers support the same right-click actions for folder-level metadata. The notes panel (top-right button) holds separate, reusable snippets like prompts or LoRA weights.

To reindex later, use **≡ → Reload Database** — with or without first running your configured startup scripts.

To try the app without touching real data, launch it with:
```bash
python app.py --dev
```

---

## file structure

```
app.py                    — application entry point and all app logic
requirements.txt          — Python dependencies
start.bat / start.sh      — sets up a venv, installs dependencies, and launches the app
start_silent.bat / .sh    — same, without a console window
icon.png                  — app icon
cover.png                 — cover image used in this readme
```

Startup scripts can live anywhere; choose each Python file through **≡ → Startup Scripts**.

---

## local data

The index and app settings live separately from your asset folders. Indexing reads the source files; explicit JSON, tag, and persistent identifier edits write the selected source sidecars.

- **Windows:** `C:\Users\<YourUser>\.vael_indexer\`
- **Linux / macOS:** `~/.vael_indexer/`

The old `.asset_indexer` folder is migrated on startup when the new folder does not already exist.

It holds:
- indexed databases (`.db` files)
- file caches (`*_file_cache.json`) used for incremental re-indexing
- preferences (`prefs.json`)
- notes (`notes.json`)
- startup-scripts config (`startup_scripts.json`)
- registered library roots (`roots.json`)

To fully remove the app, delete the project folder along with this data folder. Removing a database from within the app only deletes its index file — your original asset folder is never touched.

## search and identifiers

| Query | Matches |
| --- | --- |
| `portrait` | Asset names containing `portrait` |
| `characters f` | Folder paths containing `characters` |
| `;short hair` | Raw JSON containing `short hair` |
| `id-favorites` | Persistent or temporary identifier text containing `favorites` |
| `portrait;landscape` | Either name, with duplicates removed |
| `portrait;characters f;id-favorites` | Any of the three searches |
| `portrait;;short hair` | Name matches OR JSON matches |

Combined searches use **OR**. Previous/Next changes the 500-result page, and the counter shows the total matches. A folder can span several pages; the image viewer navigates the cards on the current page. Search and ordering run in a worker, while cards are added in short UI batches. Counting and sorting still take time for large libraries.

Persistent identifiers live in the asset's `Identifier` JSON field. Temporary memberships live only in the current database session; closing or unloading that database loses them. Converting an identifier to persistent state writes its member sidecars. Rename/remove operations roll back earlier writes if a later sidecar fails, and report any rollback that could not be completed safely.

## saves and reload failures

- Tag and identifier changes reject malformed or unreadable JSON. The JSON editor can repair malformed text explicitly.
- Saves write a sibling temporary file, flush it, check the original bytes, and publish the replacement. A stale editor refuses to overwrite a changed source; reopen it to review the current contents.
- Preferences, notes, library registration, and script settings report read/write failures. A failed new-note save leaves the note dialog open.
- Failed or cancelled indexing rolls back its transaction and restores usable controls. Closing waits for active workers to stop.
- Conflict checks are not a cross-application lock, and a batch of files is not one atomic filesystem transaction. Backups remain useful for important metadata.

## startup scripts

Indexer setup includes Pillow (the `PIL` module) for the metadata extractor. If you maintain the environment manually, reinstall `requirements.txt` after updating.

Scripts run sequentially with the **same Python interpreter as Indexer**, using each script's parent folder as its working directory. Install their dependencies in that environment. Arguments are passed directly, without a command shell; quote paths containing spaces. Shell operators, environment-variable expansion, and redirection are not interpreted.

A failure stops the sequence and shows the filename, exit code, and up to the last 16 KiB of captured output. Automatic indexing does not continue after failure or cancellation. Fix the script and retry **Reload Database → with Scripts**, or choose a database/reload without scripts when you want to proceed independently.

**Cancel scripts** stops execution; closing the app also cancels and waits. Windows cancellation attempts to stop the script's process tree, with a direct-process fallback. Other platforms stop the direct process. Detached child processes may survive, and cancellation cannot undo work a script already performed. Script output is held in a temporary file until the process exits; it is not a live console.

## preview limits and verification

Decoded images share a 32 MiB / 1,024-entry least-recently-used cache. Keys include path, preview type, modification/creation timestamps, and file size. A same-size replacement that preserves those timestamps may require restarting to invalidate its cached preview. There is no live filesystem watcher.

The viewer decodes a preview up to 2,048 pixels per side. Qt's decoder allocation limit is 128 MiB; formats may need intermediate decoding memory. These limits are not a total-process RAM cap: visible cards, database results, and widget copies also consume memory. Oversized/unreadable previews show a failure instead of retaining the previous image.

Twelve local regression checks passed on Windows, including an offscreen Qt desktop smoke check. They cover metadata refresh, invalid/stale/failed saves, rollback, indexing failure, responsive search and paging beyond 2,000 matches, settings errors, real script execution/cancellation, preview freshness/transparency/cache bounds, and shutdown. The local scripts are outside the repository. Offscreen font rendering was unavailable, so the screenshot does not establish final font appearance. Linux/macOS behavior, network filesystems, power-loss durability, and production-scale memory were not verified.
