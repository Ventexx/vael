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

### Auto-tagging setup

Auto-tagging is optional. Sorter ships its integration and metadata manager, but
**does not distribute a WD14 backend, model weights, tag vocabulary, or Python
environment**. The entire `sorter/wd14-tagger/` directory is local-only and ignored
by Git. Third-party software and models retain their upstream terms; Vael's
project license does not relicense them. No third-party license or model files
are bundled in this repository.

To use your existing standalone WD14 tagger:

1. Put the compatible `tagger.py` and its `requirements.txt` in
   `sorter/wd14-tagger/`. Reuse the standalone backend supplied separately for
   this integration; a stock Kohya training script is **not** a drop-in backend.
   Users of a fresh GitHub checkout must supply this local backend themselves.
2. In a terminal opened in `sorter`, create its separate environment and install
   the backend dependencies:

   ```powershell
   python -m venv wd14-tagger\.venv
   .\wd14-tagger\.venv\Scripts\python.exe -m pip install -r wd14-tagger\requirements.txt
   ```

3. Keep your existing `wd14-tagger/model-cache/` if you have already downloaded
   the model. The supported backend uses ONNX and
   `SmilingWolf/wd-v1-4-convnextv2-tagger-v2`, release `v2.0`.
4. Run Sorter normally. Select images or folders in the bottom workspace and choose **Auto-Tag Image/s**.
   The worker starts silently, without a console or the standalone tagger window.
   The model stays loaded for later jobs until Sorter exits or model settings change.
5. Optionally click the bottom-right tagger status, open **Settings**, and select
   a folder containing both `model.onnx` and its matching `selected_tags.csv`.
   This bypasses downloading and supports offline use. Otherwise the compatible
   backend reuses its cache, or downloads the model on first use. That download
   requires internet access; images are processed locally.

Use the same general and character thresholds as your Kohya setup if you want
similar results. The initial defaults are 0.35 and 0.85. Settings are saved when
you start a tagging job. Lower thresholds return more tags and can include more
mistakes. Names come from the model's vocabulary, so review site-specific tags
before posting. Model information: [SmilingWolf WD14 ConvNextV2](https://huggingface.co/SmilingWolf/wd-v1-4-convnextv2-tagger-v2).

Backend authors can provide a compatible local implementation with this interface:

```python
class Tagger:
    def __init__(self, model_dir=None, progress=lambda text: None):
        ...

    def tags(self, path, general, character, excluded, separator):
        # Return one string of tags separated by separator; raise on failure.
        ...
```

`progress` is optional and reports human-readable loading/download phases.
`model_dir` is only supplied when the user selects an existing model folder.
The backend must support this interface, be safe to import without opening a UI,
and leave all tag persistence to Sorter. Backend installation and model downloads
remain the user's local setup, subject to the respective upstream terms.

### Auto-tagging, status and errors

**Auto-Tag Image/s** appears only in the bottom workspace (including split view),
for ordinary images, folders, or selections containing only those types. The upper
library and Shelf have no tagging or association actions. It is hidden for mixed selections containing videos, GIFs,
documents or other unsupported files. PNG, JPG/JPEG, WebP, BMP, TIFF and AVIF are
eligible; actual decoding requires backend support. Animated or multipage image
formats use the backend's first-frame policy. GIF is a separate media type and
is not queued. Folder scans include every subfolder regardless of thumbnail
loading, collapsed sections or search filters. Links/junctions and Sorter's private
operation backup/staging entries are excluded. Overlapping selections are deduplicated.

The queue processes one image at a time. **Any scan, image, model, or tag-save
error stops the queue**, preserving results already saved. No supported images
also opens an error notification. Choose the selection again to start a new job;
explicitly tagging an already-tagged image refreshes its generated tags.

The clickable status at the bottom right is separate from the file-operation
status on the left. Its window contains **Progress**, **Errors**, and **Settings**.
Progress shows the current phase/image, completed and remaining counts, elapsed
time, **Stop after current image**, and **Check saved tags…**. Loading/downloading
uses an indeterminate progress indicator when no exact progress is available.
Errors automatically open this same window with the Errors tab selected. All
error entries are kept for the current session; **Clear errors** clears
that list. Closing this window does not cancel the queue.

Only one tagging, verification, or health-check job runs at a time. Browsing stays
available; file operations and changing configured roots wait until the job ends.
The stop button finishes and saves the current inference, then stops before the
next image. During scanning/health checks it stops at the next check point.
Closing Sorter terminates its worker and keeps previously saved results.

**Copy Tags** appears directly below Auto-Tag for one tagged image. Sorter verifies
the image content before copying, then places space-separated tags on the clipboard.
Replacing an image under the same filename never intentionally reuses the old
content's tags. An empty generated list is a valid result; associated folder tags
can still be copied.

### Folder tag associations and storage

Select one folder in the bottom workspace and choose **Associate Tags**. Empty folders are supported: the association belongs to the folder and applies to images added later. Association editing does not require the tagging model and remains available during image tagging. It waits only for file operations, a manually requested saved-tag check, or unavailable tag storage; a disabled action explains the reason in its tooltip. Enter tags in the text field,
then **Save** or **Cancel**. Opening it again loads the saved text for editing.
Saving an empty field removes the association. Separate tags with spaces, commas
or newlines; use underscores within a tag name.

When copying an image's tags, Sorter places associations from outer folders first,
then inner folders, then generated image tags. Repeated exact tags appear only
once, at their first position. Associations apply throughout all subfolders and
are calculated when copying, so editing an association or moving an image takes
effect without rerunning the model. The destination workspace folders determine
the inherited tags; previously saved source-folder records are retained but the
source explorer no longer offers tagging or association actions.

All image tags and folder associations live in `sorter/.local/tags.json`. Image
records use SHA-256 content fingerprints, with separate path records; identical
copies share generated tags. Changes to image bytes require tagging again. Folder
records use filesystem identity plus their current path, so application renames,
moves and undo retain associations. Ordinary external folder renames can be
reconciled on filesystems providing stable identities; external cross-drive moves
cannot reliably retain folder identity and require reassociation.

Tagging and full-library checks run only on explicit request. **Auto-Tag Image/s** processes only the selected images or the images inside the selected folders (including their subfolders), then stops. Startup, settings changes, completed file operations, and visible-folder changes never start a tag-health scan.

**Check saved tags…** is an optional manual maintenance action. Its confirmation explains that it reads every image in all configured source folders and the workspace, potentially taking a long time and blocking file changes until finished or stopped. Cancel is the default. It compares content fingerprints to saved tags and links, finds externally moved/copied images, and removes stale references; it does **not** generate new tags or run the tagging model. Known moves and deletions made through Sorter update only affected saved references directly, without scanning the library. Copy Tags and image-link actions verify the explicitly requested image only.

Unavailable or inaccessible roots retain their records and report an error rather than being treated as deleted. Removing a root from Settings removes records no longer represented in any remaining root only at the next explicitly confirmed check.

Writes use a temporary file and atomic replacement. A damaged or unsupported
`tags.json` is reported and preserved, not silently reset. Back up this file if
your manual folder associations are important. User data, model caches, backend
files and environments are excluded from Git; do not force-add them.

### library above

The upper explorer uses Cover’s window styling, folder tabs, collapsible headers, portrait cards, spacing, scrolling, and in-window image viewer. Each configured source gets a tab. In Settings, edit the **Display name** cell beside its **Folder path** to customize the tab label without renaming the folder. Use the arrow buttons to reorder source rows. Clear the display name to use its original folder name. Click a folder header to expand it; thumbnails load as folders become visible. Source folders start with collapsed children, as in Cover. Temporarily hidden tabs can be restored from the tab controls.

Click a card to select it; double-click to inspect it. **Ctrl-click** toggles selection and **Shift-click** extends a card selection. Drag selected cards to the shelf or a sorting folder. Folder headers support selection, drag and drop, and rectangular selection across both files and folder headers. Selected media have a bright outline, including when targeted by right-click. Middle-button dragging pans the explorer. Right-click selected folders to load their full contents recursively in the background; loading can be cancelled.

The small **search icon** opens that explorer’s search and filters. Searches cover its configured folder tabs; **Subfolders** includes descendants. Image, GIF, Video, Folder, and Other filters can be combined with extensions such as `png,jpg`. The lower explorers have independent searches and filters.

The upper explorer has one view: expandable folders with media cards. The **⋯** menu beside Search contains explorer-wide options; file actions stay in the right-click menu. The two explorers retain their selections independently. Copying from the source does not refresh it.

Folder scans and thumbnail decoding run in the background. Search changes cancel outdated scans. Use Refresh / F5 to reload the active folder after external changes; the active directory is also checked periodically. Large recursive searches can take time.

### dragging files to other apps

You can drag one or more selected images directly from either explorer into a browser's image-upload drop area (for example, a Reddit image-post composer). Sorter supplies the original files with their existing names and bytes; the small drag preview is not the uploaded image. Multi-image drags follow the displayed order, although the receiving website may reorder or process uploads. Dragging out leaves the originals in place. Ctrl-click or Shift-click to select several images, then drag one of the selected images. The destination must support file drops and the selected file types.

### session shelf

Switch between **Workspace** and **Shelf** in the lower controls; only one is displayed at a time. Shelf is temporary storage for references. Use **Add to Shelf** in the context menu, or drag files onto the shelf. Its compact square thumbnails and filenames match the workspace grid, wrap across rows, and fill the available pane. Ctrl-click and Shift-click select references for copying or removal; double-click opens a preview. The active Workspace or Shelf button has a visible selected border. Repeated references are not added twice. The shelf holds references, not temporary copies, and disappears when the app closes.

Use **Copy to Active Workspace Folder** to copy shelf entries into the workspace destination. That button copies selected entries, or the whole shelf when none are selected, into the last active lower pane. Copied references remain on the shelf for reuse. **Remove selected** and **Clear** only remove references; they never delete the files.

### sorting workspace below

Choose one workspace root in Settings. The bottom explorer uses **Grid View**, with small folder icons and media thumbnails. Double-click a folder to enter it, and click a breadcrumb in **Root › Subfolder › …** to return directly to that level. The root is the explorer itself, with no expandable root header. **Split view** opens a second grid within the same workspace, with its own navigation, selection, and search. Search filters the folder currently shown. The last active lower pane supplies the destination for **Copy to Active Workspace Folder**.

Right-click a workspace folder or any breadcrumb (including **Root**) and choose **Focus Folder** to fill the app with a temporary view of that folder. Only that folder and its subfolders are shown; search, selection, grouping, clipboard shortcuts, file management, and Undo remain available. The **←** button at the top left returns to the previous layout, preserving both panes’ locations, filters, zoom, selections of surviving files, and scroll positions. File changes made while focused remain in effect. Focus mode is temporary and is not restored on the next launch.

While Focus Folder is active, all image thumbnails in the open folder load in the background at the current display quality and remain cached, including images outside the visible area or hidden by search. Scrolling does not evict them. Opening another folder releases the previous folder’s thumbnails; changing zoom reloads them at the appropriate size. Leaving focus mode releases its thumbnail cache and stops queued loading. Normal explorers keep their bounded, on-demand caches. This preloads thumbnails, not full-resolution originals; the image viewer still loads originals when opened.

Drop onto a visible folder to target it, or onto empty space to use the current folder. While dragging, a teal outline and tint highlight the destination folder, or the whole pane for the current folder. A badge names the destination and shows **Move to** or **Copy to**, updating when Ctrl is held. Shelf highlights **Add to Shelf · references only**. Feedback clears when the drag leaves or finishes. Moving items into the folder they already occupy leaves them unchanged and does not trigger a filename-conflict prompt. A drag from the library copies external content. A drag from a workspace pane moves its selected items; hold **Ctrl** to copy instead. Shelf drops and drops from other applications always copy. The status bar describes internal drag behavior. After a transfer, successfully received entries stay selected in any workspace pane showing their destination, ready for actions such as **Group selected entries**. Each receiving pane automatically scrolls to the first visible new entry in display order, including in split view. Existing search filters stay active; unrelated panes do not jump to the new content. Auto-renamed files are selected by their actual new names; the source selection is retained.

Use **⋯ → Entry zoom** to choose 50–200% sizes independently in each explorer. In the source explorer, only entries inside folders scale; folder headers remain unchanged. Workspace zoom scales file entries and folder icons together. Workspace and Shelf use the same smooth thumbnail decoder as the upper explorer; workspace thumbnail resolution adjusts to zoom and display scaling. Filenames sit over the image with translucent text and a translucent background so the content remains visible underneath. Zoom levels are remembered.

The **⋯** menu contains Select All, Invert Selection, and Entry Zoom. In the workspace it also contains **Group Notes**. Refresh and Search remain dedicated buttons beside it. The right-click menu shows file actions only when items are selected: Copy, Cut, Copy to Active Workspace Folder, Move to Folder, Duplicate, Rename, Batch Rename, Delete, Information, Copy as Path, Open Folder in Explorer, and Add to Shelf. Paste (when the clipboard contains files) and New Folders appear when right-clicking empty workspace space, not selected files. Batch Rename appears only for multiple selected items; a single item gets Rename. Copy to Active Workspace Folder appears only in the source explorer. Open Folder in Explorer opens the selected folder itself, or the parent of a selected file. **Delete** uses the system trash. Undo remains a separate button.

**Move to Folder** opens the workspace hierarchy with all folders expanded by default. Hold **Ctrl** while clicking to select several destinations, then choose **Save**. The first selected folder in tree order receives the move; the others receive copies. Extra copies are completed before moving each original. If one of those copies fails, that original stays in place; successful copies remain available, and errors are reported. Stopping a batch keeps any copies already completed. If the source folder is itself selected, its originals stay there and the other destinations receive copies. Conflict choices apply across every selected destination.

The dialog’s **Images in groups keep their group** checkbox remembers its state, including between launches (enabled by default). When enabled, each destination retains the selected members’ original groups and assigned notes. Partial groups and selections spanning several groups remain separate; unselected source members stay in their groups. Colors remain distinct within each destination, so a color may change to avoid a clash. When disabled, transferred entries are ungrouped at their destinations. The option applies to this Move to Folder dialog. Ordinary Copy/Paste and drag-and-drop transfers always preserve existing groups, assigned notes, and group links, including groups inside copied folders. Copies leave the source groups intact; shelf references themselves do not change groups.

When a selected folder includes other selected descendants, transfer actions process the parent only. Copying a folder includes its ordinary files and subfolders. Individual files are independent: selecting an image does not automatically select accompanying JSON or text files. Select those too, or copy the containing folder.

**Batch rename** previews each new name before applying it. Combine literal find/replace, prefix, suffix, and sequential numbering; file extensions are preserved. Invalid, duplicate, or existing destination names prevent applying the preview.

### context menus and associated links

The upper library menu separates **Collect**, **Browse** (folders), and **Details**.
It offers copying into the workspace and adding shelf references, without tagging,
links or workspace editing actions. The Shelf menu separates sending items to the
workspace from removing references. The viewer separates zoom/view actions from
adding items to the Shelf; note editing separates Edit from Delete.

The bottom workspace menu separates **Tags**, **Image Link**, **Files**, **Groups**,
and **Details**, showing only applicable sections. Move destinations live in the
**Move** submenu. Group actions on a selected group member live in **Current Group**;
right-clicking a group's colored background opens those group actions directly.
Delete is isolated at the bottom. Empty workspace space offers Paste and New folders.
Both workspace panes use the same menus.

For one image, choose **Image Link → Associate Link** (the Image Link label is a
section heading). For a group, use **Associate Link** in its group menu. Enter an
`https://` or `http://` URL, then Save or Cancel. Existing links are prefilled for
editing; save an empty field to remove one. **Copy Link** appears only when that
image or group has a link. It copies the URL without opening a browser.

Image and group links are independent. A group's link is not implicitly assigned
to its individual images. An image does not need generated tags before it can have
a link, and the WD14 backend is not needed for links. Image links are stored in
`.local/tags.json`, use content fingerprints, and follow the same health-check and
replacement rules as tags. Identical copies share the image link. Group links are
stored with their groups in `.local/settings.json`; they follow group moves and
renames, remain on group fragments, and disappear when the group is dissolved.

Windows folder metadata files `desktop.ini` and `Thumbs.db` are hidden from both
explorers, searches, cached results, and recursive thumbnail loading. This is a
view filter: the files are not deleted, and ordinary folder copies still preserve
their contents. Other files are not hidden merely because Windows marks them hidden.

### groups & group notes

Select two or more files in one workspace folder, then right-click → **Group selected entries**. Groups are visual collections: no folder is created and no file is changed. Folders stay first, followed by groups, then ordinary files. Each group stays together and sorts its members alphabetically, ignoring case and ordering number sequences naturally (`image2` before `image10`). The gaps and padding above and below grouped cards share a subtle, randomly chosen rainbow color; colors are distinct within a folder and chosen to remain visually distinguishable. Each occupied row has a rounded colored band, with a clear gap between separate groups. A wrapped group continues on the next row with the same color and alphabetical sequence; it remains one group. Search can hide nonmatching members without changing the group.

Right-click the colored padding or gaps for the essential group-only menu. Right-click a grouped image for its image/file actions and a separate **Current Group** submenu containing group actions and notes. **Dissolve Group** returns its members to ordinary sorting without deleting anything. Selecting entries from different groups or ordinary files lets you group that selection anew. Groups persist between launches. Renames and moves made through Sorter update memberships; moving part of a group separates it by destination folder, and undoing that move reunites the fragments. Ordinary copies preserve groups; copies made by Move to Folder follow its checkbox. External file moves or copies cannot be tracked automatically.

Open **workspace ⋯ → Group Notes**, a standard menu entry, to manage the reusable text library. **New Note** opens a small dialog with **Header** and **Body** fields. Right-click an existing entry to **Edit** or **Delete** it. Drag entries or use **Move Up / Move Down** to set their order in the assignment and group menus. Headers must be unique and non-empty. Save each entry dialog, then **Save** the main window to apply changes; **Cancel** discards pending library edits.

The first Group Note is automatically assigned to **every** group, including existing groups, and cannot be unchecked in Assign Notes. Reordering or deleting the first note makes the new first note apply to every group; earlier assigned notes remain unless removed or deleted. This rule is shown in the Group Notes window.

When notes exist, a group’s context menu offers **Assign Notes**. Tick any number of titles; the menu stays open while you choose. Assigned titles appear in the **Copy Note** submenu. Click a title to copy its entire body to the system clipboard. Editing a note updates it for all assigned groups; deleting it removes those assignments. Groups and notes are local metadata in `.local/settings.json`, separate from source content and shared naming profiles.

### the editing boundary

Outside the sorting root, sorter can browse and copy content but cannot move, rename, delete, or otherwise modify it. Mutating operations validate actual filesystem paths in a shared service. The sorting root itself cannot be renamed or deleted through sorter, and destinations outside it are rejected. Symbolic links and Windows junctions are not followed or transferred, including links found within a folder being copied.

Local application preferences and the disposable catalog are stored separately in `sorter/.local/`. Profile exports must also be saved inside the configured sorting folder.

When incoming names conflict, choose **Cancel**, **Overwrite**, or **Auto Rename** before copying begins. Cancel leaves all files and the selection unchanged. Overwrite replaces matching files; when folders overlap, their contents are merged and unmatched files remain. Auto Rename changes only conflicting incoming names to `image_1.png`, `image_2.png`, and so on, using the first available number. Overwritten files are backed up privately for session Undo; those backups are removed when the session ends. File operations run in the background and report partial failures. **Stop after current item** stops before the next item; it does not interrupt a file midway through a copy.

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

Typing or changing a filter immediately shows **Searching…** with an animated indicator beneath the search controls. It changes to the match count when finished, including **0 matches** when nothing matches. Invalid queries and failed searches show a visible error. These indicators work independently in the library, both workspace panes, and Focus Folder; older searches cannot replace the latest query’s status.

**Profiles & exclusions** configures ignored folder/file names using wildcard patterns, one per line. Export/import shares fields, rules, and relevant preferences as JSON; source paths, sorting root, pins, and rule scope paths are not exported. Importing changes the dialog first; Save applies it.

---

## images, GIFs, videos & documents

Images receive background thumbnails cached for the session. GIFs and videos use static media tiles with a subtle colored outline; their exact extension appears on hover. File entries do not show path tooltips; use **Information** or **Copy as Path** when needed. They never autoplay in the explorer.

Double-click a file to open the in-window viewer. Images support Fit, Actual size, mouse-wheel zoom, and dragging to pan; right-click for viewer actions. Escape or clicking outside closes the viewer. GIFs animate there and can be paused. Videos provide play/pause, seeking, time, and volume. Left/Right moves between media items in the current filtered view, and Space toggles playback. Add to shelf works from the viewer too.

Recognized image extensions: PNG, JPG/JPEG, WebP, BMP, TIFF, and AVIF. Video extensions: MP4, MKV, WebM, MOV, AVI, M4V, WMV, MPEG, and MPG. Actual image decoding and video playback depend on the available Qt codecs. Damaged media remain manageable, with an explanation and **Open externally** in the viewer. Text, code, JSON, unknown extensions, and extensionless files use a white page thumbnail labeled with their extension (or FILE). Opening them displays scrollable text without visible scrollbars. Use the wheel, or hold the middle mouse button and move up/down to scroll; release it to stop. UTF-8 and BOM-marked UTF-16 are supported; undecodable bytes are replaced for display.

---

## keyboard shortcuts

Shortcuts act on the focused explorer and do not run file actions while typing into its text fields. The fixed, read-only reference is the final **Hotkeys** section at the bottom of Settings. Ctrl+C copies selected files or folders in either explorer. Alt+C closes the current folder (in the workspace, it opens the parent folder). Ctrl+X and Ctrl+V cut and paste inside the sorting workspace. Text fields keep their normal copy, cut, and paste shortcuts. M in the source explorer copies protected originals to the chosen workspace destinations.

| Shortcut | Action |
| --- | --- |
| `Ctrl+C` | Copy selected files or folders in either explorer |
| `Alt+C` | Close current folder; in the workspace, return to its parent |
| `Ctrl+X` / `Ctrl+V` | Cut / paste in the sorting workspace |
| `Ctrl+A` | Select all |
| `Ctrl+F` | Open search and filters |
| `Ctrl+Up` / `Ctrl+Down` | Close current folder and open the previous / next sibling (wraps at either end) |
| `M` | Open Move to Folder when one or more images are selected |
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
