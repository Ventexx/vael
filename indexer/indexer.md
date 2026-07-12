# indexer

<!-- cover -->
![indexer cover](./cover.png)

---

A desktop app for browsing, searching, and editing structured data paired with visual assets — built for scanning folders of image + JSON pairs (e.g. AI-generated images with their prompt/tag metadata) into a fast, searchable visual library. Windows and Linux, with partial Wayland support.

---

## features

- **folder-based indexing** — scans directories for `.png` + `.json` pairs into a SQLite index, organized into collapsible, nested folder sections
- **smart incremental indexing** — a file-cache diff skips unchanged files on reload, so re-indexing a large library is near-instant
- **instant search**, with a folder-only mode (append ` f` to a query, e.g. `characters f`)
- thumbnail grid with lazy-loaded, cached previews so the UI never freezes
- full-screen image viewer with arrow-key / on-screen navigation
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

`scripts/` directory must be created manually if you want to use startup scripts.

---

## local data

Everything the app persists lives in a single folder, separate from your indexed asset folders (which are only ever read from, never restructured):

- **Windows:** `C:\Users\<YourUser>\.asset_indexer\`
- **Linux / macOS:** `~/.asset_indexer/`

It holds:
- indexed databases (`.db` files)
- file caches (`*_file_cache.json`) used for incremental re-indexing
- preferences (`prefs.json`)
- notes (`notes.json`)
- startup-scripts config (`startup_scripts.json`)

To fully remove the app, delete the project folder along with this data folder. Removing a database from within the app only deletes its index file — your original asset folder is never touched.
