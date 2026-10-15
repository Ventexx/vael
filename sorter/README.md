# sorter

<!-- cover -->
![sorter cover](./cover.png)

---

A local media workspace for finding content in structured folders, collecting a temporary selection, and arranging copies for promotion or any similar workflow. Browse above, organize below, and keep all content changes inside one chosen sorting folder.

---

## features

- browse any number of source folders in Cover’s original tabbed, collapsible explorer
- search both explorers independently, with file-type and extension filters
- extract searchable fields from user-defined folder-name patterns
- collect files and folders on a session-only shelf without moving their originals
- copy selections into a sorting folder through drag and drop or menus
- browse source thumbnail cards and organize files in a workspace grid, optionally in two panes side by side
- create folders, copy, move, duplicate, rename, batch rename, and move items to the system trash
- inspect images with fit/zoom controls and play GIFs and videos in the larger viewer
- scan folders and load image thumbnails in the background
- restore folder locations, views, column layout, and window proportions between launches

Sources, field names, rules, and aliases are configuration. No particular creator, genre, folder layout, or publishing service is built into the app.

---

## installation & removal

**Install**

1. Install [Python](https://www.python.org/downloads/) 3.10 or newer. On Windows, enable **Add Python to PATH**.
2. Download Vael using **Code → Download ZIP**, then extract it.
3. Open the `sorter` folder and double-click `start.bat` on Windows.
4. The launcher creates a local `venv`, installs PySide6, and starts the app. Initial setup needs internet access.
5. Open the **gear in the lower workspace**, expand **Folders**, add your source folders, and select an existing dedicated sorting folder.

Use `start.bat` for first-time setup, dependency maintenance, or visible error output. For everyday launches, including launchers such as Rovyl, use `start_silent.bat`. It reuses the installed environment without dependency checks or updates and asks you to run normal setup if the environment is missing.

The provided launchers target Windows. Codec availability can vary between systems.

**Uninstall**

Close sorter and delete its folder, including `venv` and `.local`. Your source folders and sorting folder remain wherever you chose them. Move any personal content kept inside the app's installation folder elsewhere before deleting that folder.

---

## the workspace

### library above

The upper explorer uses Cover’s window styling, folder tabs, collapsible headers, portrait cards, spacing, scrolling, and in-window image viewer. Each configured source gets a tab. In Settings, edit the **Display name** cell beside its **Folder path** to customize the tab label without renaming the folder. Use the arrow buttons to reorder source rows. Clear the display name to use its original folder name. Click a folder header to expand it; thumbnails load as folders become visible. Source folders start with collapsed children, as in Cover. Temporarily hidden tabs can be restored from the tab controls.

Click a card to select it; double-click to inspect it. **Ctrl-click** toggles selection and **Shift-click** extends a card selection. Drag selected cards to the shelf or a sorting folder. Folder headers support selection, drag and drop, and rectangular selection across both files and folder headers. Selected media have a bright outline, including when targeted by right-click. Middle-button dragging pans the explorer. Right-click selected folders to load their full contents recursively in the background; loading can be cancelled.

The small **search icon** opens that explorer’s search and filters. Searches cover its configured folder tabs; **Subfolders** includes descendants. Image, GIF, Video, Folder, and Other filters can be combined with extensions such as `png,jpg`. The lower explorers have independent searches and filters.

The upper explorer has one view: expandable folders with media cards. The **⋯** menu beside Search contains explorer-wide options; file actions stay in the right-click menu. The two explorers retain their selections independently. Copying from the source does not refresh it.

Folder scans and thumbnail decoding run in the background. Search changes cancel outdated scans. Use Refresh / F5 to reload the active folder after external changes; the active directory is also checked periodically. Large recursive searches can take time.

### session shelf

Switch between **Workspace** and **Shelf** in the lower controls; only one is displayed at a time. Shelf is temporary storage for references. Use **Add to Shelf** in the context menu, or drag files onto the shelf. Its cards use the same portrait layout as the explorers; Ctrl-click selects references for copying or removal. Repeated references are not added twice. The shelf holds references, not temporary copies, and disappears when the app closes.

Use **Copy to Active Workspace Folder** to copy shelf entries into the workspace destination. That button copies selected entries, or the whole shelf when none are selected, into the last active lower pane. Copied references remain on the shelf for reuse. **Remove selected** and **Clear** only remove references; they never delete the files.

### sorting workspace below

Choose one workspace root in Settings. The bottom explorer uses **Grid View**, with small folder icons and media thumbnails. Double-click a folder to enter it, and click a breadcrumb in **Root › Subfolder › …** to return directly to that level. The root is the explorer itself, with no expandable root header. **Split view** opens a second grid within the same workspace, with its own navigation, selection, and search. Search filters the folder currently shown. The last active lower pane supplies the destination for **Copy to Active Workspace Folder**.

Drop onto a visible folder to target it, or onto empty space to use the current folder. A drag from the library copies external content. A drag from a workspace pane moves its selected items; hold **Ctrl** to copy instead. Shelf drops and drops from other applications always copy. The status bar describes internal drag behavior.

Use **⋯ → Entry zoom** to choose 50–200% sizes independently in each explorer. In the source explorer, only entries inside folders scale; folder headers remain unchanged. Workspace zoom scales file entries and folder icons together. Zoom levels are remembered.

The **⋯** menu contains Select All, Invert Selection, and Entry Zoom. Refresh and Search remain dedicated buttons beside it. The right-click menu shows file actions only when items are selected: Copy, Cut, Copy to Active Workspace Folder, Move to Folder, Duplicate, Rename, Batch Rename, Delete, Information, Copy as Path, Open Folder in Explorer, and Add to Shelf. Paste (when the clipboard contains files) and New Folders appear when right-clicking empty workspace space, not selected files. Batch Rename appears only for multiple selected items; a single item gets Rename. Copy to Active Workspace Folder appears only in the source explorer. Open Folder in Explorer opens the selected folder itself, or the parent of a selected file. **Delete** uses the system trash. Undo remains a separate button.

**Move to Folder** opens a folder-only dialog showing the complete workspace hierarchy. Select a folder, then choose **Save** or **Cancel**. It does not open the operating system's folder picker.

When a selected folder includes other selected descendants, transfer actions process the parent only. Copying a folder includes its ordinary files and subfolders. Individual files are independent: selecting an image does not automatically select accompanying JSON or text files. Select those too, or copy the containing folder.

**Batch rename** previews each new name before applying it. Combine literal find/replace, prefix, suffix, and sequential numbering; file extensions are preserved. Invalid, duplicate, or existing destination names prevent applying the preview.

### the editing boundary

Outside the sorting root, sorter can browse and copy content but cannot move, rename, delete, or otherwise modify it. Mutating operations validate actual filesystem paths in a shared service. The sorting root itself cannot be renamed or deleted through sorter, and destinations outside it are rejected. Symbolic links and Windows junctions are not followed or transferred, including links found within a folder being copied.

Local application preferences and the disposable catalog are stored separately in `sorter/.local/`. Profile exports must also be saved inside the configured sorting folder.

When incoming names conflict, choose **Cancel**, **Overwrite**, or **Auto Rename** before copying begins. Cancel leaves all files and the selection unchanged. Overwrite replaces matching files; when folders overlap, their contents are merged and unmatched files remain. Auto Rename changes only conflicting incoming names to `image1.png`, `image2.png`, and so on, using the first available number. Overwritten files are backed up privately for session Undo; those backups are removed when the session ends. File operations run in the background and report partial failures. **Stop after current item** stops before the next item; it does not interrupt a file midway through a copy.

**Undo** lasts for the current session and reverses one successful copy, move, rename, or folder creation at a time. Press it repeatedly to reverse a batch. It refuses to remove changed content or overwrite an occupied original location. Trashed files must be restored through the operating system's trash. Changes to content made in external programs are outside sorter's control.

---

## folder naming & search

New installations start with empty settings fields and no sample naming rules, aliases, exclusion patterns, or test text. Configure only what you need; saved user settings are retained.

**Information** shows readable file properties, image dimensions, and folder file counts and sizes. Expand full metadata for embedded image metadata and ExifTool output when ExifTool is available on PATH.

Open **Settings → Naming & search** to define fields and their unique aliases. For example:

| Field name | Search alias |
| --- | --- |
| `GENRE` | `g` |
| `CHAR` | `ch` |
| `SCENE` | `s` |

Add one or more folder patterns:

| Pattern | Example | Extracted values |
| --- | --- | --- |
| `[GENRE]-[CHAR]_[SCENE]` | `Pokemon-Misty_Beach` | GENRE: Pokemon, CHAR: Misty, SCENE: Beach |
| `[CHAR];[GENRE]` | `Misty;Pokemon` | CHAR: Misty, GENRE: Pokemon |

Names and aliases are freely configurable. Another library could use `[CLIENT]_[PROJECT]_[DATE]`. Placeholders match non-empty text, and everything between them is literal. Matching ignores letter case and covers the complete folder name. Put separators between placeholders and avoid ambiguous separators inside field values. This is a template system, not arbitrary regular expressions.

Rules run from top to bottom; the first matching rule wins at each directory level. Use **Move up / Move down** to set precedence. An optional scope path limits a rule to that folder and its descendants. **Test pattern** tests the sample folder name and displays extracted fields; a rule's scope is displayed but is not evaluated against that bare sample name.

When inheritance is enabled, recognized fields pass down from the configured source root to its descendants. Closer matching folders override the same fields. Thus `Misty;Pokemon/Set 01/image.png` can retain the character and genre from its parent. Unmatched folders stay browsable and searchable by their ordinary names.

| Search | Meaning |
| --- | --- |
| `Pokemon` | Contains Pokemon in names, containing path, or extracted fields |
| `Pokemon g` | GENRE contains Pokemon |
| `g:Pokemon ch:Misty` | GENRE contains Pokemon AND CHAR contains Misty |
| `s:"Beach Night"` | SCENE contains the phrase Beach Night |

Search is case-insensitive and uses partial matches. Multiple terms are combined with AND. Quotes keep multiword values together. The search field’s tooltip shows interpreted search terms, and autocomplete offers field values from the loaded entries. Each explorer's query and filters are independent.

**Profiles & exclusions** configures ignored folder/file names using wildcard patterns, one per line. Export/import shares fields, rules, and relevant preferences as JSON; source paths, sorting root, pins, and rule scope paths are not exported. Importing changes the dialog first; Save applies it.

---

## images, GIFs, videos & documents

Images receive background thumbnails cached for the session. GIFs and videos use static media tiles with a subtle colored outline; their exact extension appears in the tooltip and on hover. They never autoplay in the explorer.

Double-click a file to open the in-window viewer. Images support Fit, Actual size, mouse-wheel zoom, and dragging to pan; right-click for viewer actions. Escape or clicking outside closes the viewer. GIFs animate there and can be paused. Videos provide play/pause, seeking, time, and volume. Left/Right moves between media items in the current filtered view, and Space toggles playback. Add to shelf works from the viewer too.

Recognized image extensions: PNG, JPG/JPEG, WebP, BMP, TIFF, and AVIF. Video extensions: MP4, MKV, WebM, MOV, AVI, M4V, WMV, MPEG, and MPG. Actual image decoding and video playback depend on the available Qt codecs. Damaged media remain manageable, with an explanation and **Open externally** in the viewer. Text, code, JSON, unknown extensions, and extensionless files use a white page thumbnail labeled with their extension (or FILE). Opening them displays scrollable text without visible scrollbars. Use the wheel, or hold the middle mouse button and move up/down to scroll; release it to stop. UTF-8 and BOM-marked UTF-16 are supported; undecodable bytes are replaced for display.

---

## keyboard shortcuts

Shortcuts act on the focused explorer and do not run file actions while typing into its text fields.

| Shortcut | Action |
| --- | --- |
| `Ctrl+C` | Copy selected file references |
| `Ctrl+X` / `Ctrl+V` | Cut / paste in the sorting workspace |
| `Ctrl+A` | Select all |
| `Ctrl+F` | Open search and filters |
| `Ctrl+Up` / `Ctrl+Down` | Navigate sibling folders |
| `F5` / `Alt+Up` | Refresh / parent folder |
| `F2` | Rename; multiple items open batch rename |
| `Delete` | Move selection to trash after confirmation |
| `Ctrl+Shift+N` | Create folders in the current lower pane |
| `Ctrl+Z` | Undo the most recent individual file operation |

---

## local data & artwork

All application code is in `app.py`, organized into internal sections for settings, naming, catalog, file operations, background jobs, explorers, media, and dialogs. No companion Python modules are required.

```text
app.py                        — the complete desktop app
requirements.txt              — PySide6 dependency
start.bat                     — Windows setup, maintenance, and launch
start_silent.bat              — everyday launch without setup
README.md                     — this guide and the in-app Help content
icon.ico / icon.png           — application icons (preferred)
cover.png                     — optional README artwork and fallback app icon
cover.ico                     — optional fallback Windows app icon
.local/settings.json          — local preferences, paths and layout
.local/catalog.sqlite         — disposable recent directory snapshots
venv/                         — local Python environment
```

Keep artwork beside `app.py`. The README displays **`cover.png`**. On Windows the app tries **`icon.ico`**, **`cover.ico`**, **`icon.png`**, then **`cover.png`**. The supplied icons and Cover-style artwork naming both work. Restart after changing artwork. Missing artwork does not prevent launch.

`.local` and `venv` are excluded from Git. Deleting the catalog while the app is closed rebuilds it on the next scan. Deleting `settings.json` resets configuration. The shelf, internal cut selection, and undo history are session-only. Window/view preferences are saved locally; no content is uploaded or published.

---

## license

Part of Vael, licensed under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/), as described in the repository's main README and LICENSE.
