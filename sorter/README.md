# sorter

<!-- cover -->
![sorter cover](./cover.png)

---

A local media workspace for finding content in structured folders, collecting a temporary selection, and arranging copies for promotion or any similar workflow. Browse above, organize below, and keep all content changes inside one chosen sorting folder.

---

## features

- browse any number of source folders, or enter a folder path directly
- search both explorers independently, with file-type, extension, and flag filters
- extract searchable fields from user-defined folder-name patterns
- collect files and folders on a session-only shelf without moving their originals
- copy selections into a sorting folder through drag and drop or menus
- manage real files and subfolders in tiles, a detail list, or two panes side by side
- create folders, copy, move, duplicate, rename, batch rename, and move items to the system trash
- pin frequently used sorting subfolders and flag selected items
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
5. Open **Settings**, add your source folders, and select an existing dedicated sorting folder.

Use `start.bat` for first-time setup, dependency maintenance, or visible error output. For everyday launches, including launchers such as Rovyl, use `start_silent.bat`. It reuses the installed environment without dependency checks or updates and asks you to run normal setup if the environment is missing.

The provided launchers target Windows. Codec availability can vary between systems.

**Uninstall**

Close sorter and delete its folder, including `venv` and `.local`. Your source folders and sorting folder remain wherever you chose them. Move any personal content kept inside the app's installation folder elsewhere before deleting that folder.

---

## the workspace

### library above

Use the source selector to browse all configured sources or an individual one. Enter a path to browse another folder, or double-click a folder to enter it. Back, forward, parent, and refresh controls work independently in each explorer.

Enable **Subfolders** to include descendants. Disable it to show only the current folder's immediate contents. Search narrows the currently loaded scope; it does not silently search unrelated drives. Image, GIF, Video, Folder, and Other filters can be combined with extensions such as `png,jpg`.

Choose **Tiles** for visual selection or **Details** for sortable, resizable columns. **Group by folder** sorts items together by their containing directory; tile captions show their containing folder during recursive browsing. Ctrl-click, Shift-click, and selection dragging support multiple items.

Folder scans run in the background. Recent directory snapshots can appear immediately from the local catalog while sorter checks for changes. Typing another search filters the loaded catalog without rescanning the drive. Use Refresh / F5 for changes deep inside a recursive source. Changes to the currently open directory are also checked periodically.

### session shelf

Use **Add selection to shelf**, the context menu, or drag files onto the shelf. Repeated references are not added twice. The shelf holds references, not temporary copies, and disappears when the app closes.

Drag selected shelf entries onto a sorting folder, or use **Copy to target**. That button copies selected entries, or the whole shelf when none are selected, into the last active lower pane. Copied references remain on the shelf for reuse. **Remove selected** and **Clear** only remove references; they never delete the files.

### sorting workspace below

Choose one sorting root in Settings. Both lower panes stay inside that folder. Use **Two folders side by side** to organize between two different subfolders. Each pane has its own path, search, filters, sort order, and view. Pin/unpin the current folder to make it available in the lower folder selectors.

Drop onto a visible folder to target it, or onto empty space to use the current folder. A drag from the library copies external content. An internal drag of items already inside the sorting root moves them; hold **Ctrl** to copy instead. Shelf drops and drops from other applications always copy. The status bar describes internal drag behavior.

The **Edit** menu and right-click menu contain:

- copy, cut, paste, copy to folder, and move to folder
- duplicate with an automatically unique name
- create one or several folders, with one name per line
- move a selection into a newly created folder
- rename and batch rename
- toggle flag and filter flagged items
- move to system trash
- undo the most recent individual file operation
- select all, invert selection, inspect information, copy paths, and open the containing folder externally

When a selected folder includes other selected descendants, transfer actions process the parent only. Copying a folder includes its ordinary files and subfolders. Individual files are independent: selecting an image does not automatically select accompanying JSON or text files. Select those too, or copy the containing folder.

**Batch rename** previews each new name before applying it. Combine literal find/replace, prefix, suffix, and sequential numbering; file extensions are preserved. Invalid, duplicate, or existing destination names prevent applying the preview.

### the editing boundary

Outside the sorting root, sorter can browse and copy content but cannot move, rename, delete, or otherwise modify it. Mutating operations validate actual filesystem paths in a shared service. The sorting root itself cannot be renamed or deleted through sorter, and destinations outside it are rejected. Symbolic links and Windows junctions are not followed or transferred, including links found within a folder being copied.

Local application preferences, flags, and the disposable catalog are stored separately in `sorter/.local/`. Flags never modify the content file. Profile exports must also be saved inside the configured sorting folder.

Existing files are never silently overwritten. In Settings, choose **Create a new name**, **Skip existing files**, or **Report a conflict**. New names use a suffix such as `image (2).png`. File operations run in the background and report partial failures. **Stop after current item** stops before the next item; it does not interrupt a file midway through a copy.

**Undo** lasts for the current session and reverses one successful copy, move, rename, or folder creation at a time. Press it repeatedly to reverse a batch. It refuses to remove changed content or overwrite an occupied original location. Trashed files must be restored through the operating system's trash. Changes to content made in external programs are outside sorter's control.

---

## folder naming & search

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

Search is case-insensitive and uses partial matches. Multiple terms are combined with AND. Quotes keep multiword values together. The status line shows interpreted search terms, and autocomplete offers field values from the loaded entries. Each explorer's query and filters are independent.

**Profiles & exclusions** configures ignored folder/file names using wildcard patterns, one per line. Export/import shares fields, rules, and relevant preferences as JSON; source paths, sorting root, pins, flags, and rule scope paths are not exported. Importing changes the dialog first; Save applies it.

---

## images, GIFs & videos

Images receive background thumbnails with a bounded memory cache. GIFs and videos use static media tiles with a subtle colored outline; their exact extension appears in the tooltip and on hover. They never autoplay in the explorer.

Double-click a file to open the viewer. Images support Fit, 100%, and zoom. GIFs animate there and can be paused. Videos provide play/pause, seeking, time, and volume. Left/Right moves between media items in the current filtered view, and Space toggles playback. Add to shelf works from the viewer too.

Recognized image extensions: PNG, JPG/JPEG, WebP, BMP, TIFF, and AVIF. Video extensions: MP4, MKV, WebM, MOV, AVI, M4V, WMV, MPEG, and MPG. Actual image decoding and video playback depend on the available Qt codecs. Unsupported or damaged files remain manageable, with an explanation and **Open externally** in the viewer.

---

## keyboard shortcuts

Shortcuts act on the focused explorer and do not run file actions while typing into its text fields.

| Shortcut | Action |
| --- | --- |
| `Ctrl+C` | Copy selected file references |
| `Ctrl+X` / `Ctrl+V` | Cut / paste in the sorting workspace |
| `Ctrl+A` | Select all |
| `Ctrl+F` / `Ctrl+L` | Focus search / path |
| `F5` / `Alt+Up` | Refresh / parent folder |
| `F2` | Rename; multiple items open batch rename |
| `F` | Toggle flags in a lower pane |
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
cover.png                     — optional README artwork and fallback app icon
cover.ico                     — optional Windows app icon
.local/settings.json          — local preferences, paths, flags, and layout
.local/catalog.sqlite         — disposable recent directory snapshots
venv/                         — local Python environment
```

Add your artwork as **`cover.png`** and **`cover.ico`** beside `app.py`. The README already references `cover.png`. Windows prefers `cover.ico` for the app/window icon and falls back to `cover.png`; other platforms use `cover.png`. Restart the app after adding artwork. Missing artwork does not prevent launch.

`.local` and `venv` are excluded from Git. Deleting the catalog while the app is closed rebuilds it on the next scan. Deleting `settings.json` resets configuration and flags. The shelf, internal cut selection, and undo history are session-only. Window/view preferences and flags are saved locally; no content is uploaded or published.

---

## license

Part of Vael, licensed under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/), as described in the repository's main README and LICENSE.
