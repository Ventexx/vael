# reviewer

<!-- cover -->
![reviewer cover](./cover.png)

---

An image reviewer built to find and resolve requeued ComfyUI image sets. Point it at your output folders and it groups regenerated iterations of the same image together so you can quickly decide what to keep and what to trash.

---

## features

- **watched folders** — add any number of root folders; scans recursively, skipping any folder starting with `.` or `!`
- **iteration review** (default) — automatically groups files by their stable base name and only surfaces sets with more than one iteration, since those are the ones that need a decision
- **general review** — a second mode that lays out every image in a folder, including singletons, for plain browsing
- mark individual images for deletion, or mark a whole set at once
- **flag** sets for requeue — manually, or automatically when every image in a set is marked for deletion. Unmarking clears an automatic flag; manual flags remain until explicitly cleared. Executing trash retains flags.
- a flagged-sets overview, one click away
- zoomed focus view with arrow-key stepping between a set's iterations
- toggleable right-click behavior: mark for deletion vs. flag for requeue
- expand/collapse all sets at once
- deletions go to the OS trash, never a hard delete
- remembers window size, position, maximized state, and zoom level between launches

---

## installation

From the `reviewer` directory, run `npm install` once, then `npm start` to launch the Electron desktop app. The package also provides `npm run build:win` and `npm run build:linux`; packaged builds were not verified in the reliability checks below.

---

## getting started

Open the folder-settings panel and add one or more root folders to watch, then rescan. In the default **iteration** view, the sidebar lists every folder that has sets needing a decision; pick one to see its sets, expand a set, and mark the images you don't want. Switch to **general** review from the sidebar footer to browse everything instead of just the sets in question.

Once you've marked everything you want gone, hit **Execute** to send it all to the trash in one confirmation.

Only confirmed successes lose their marks. Failed or unconfirmed files stay marked for retry, with their paths and reasons reported. Mark changes and repeated execution are blocked while trash is running. If a set drops below two iterations, use General review to inspect its remaining files. Flags survive execution so the requeue list remains useful; clear them explicitly when finished. Flagging does not submit a ComfyUI job.

Review shortcuts operate within Reviewer and leave typing and open dialogs alone. Window zoom and developer-tool shortcuts are also local to Reviewer.

## scans and previews

- Scanning runs in a background worker and builds both review modes from one snapshot. Overlapping roots and aliases are deduplicated. Hidden watched descendants stay excluded even when their parent is watched; child symbolic-link directories are not traversed.
- Unreadable paths appear as scan warnings; hover the warning for details. A failed scan leaves existing results available and allows another attempt.
- Rescans collect filenames and file information, without loading every image. Thumbnails load near the visible area and are limited to 384 pixels per side. The retained thumbnail cache has a 32 MiB estimated budget and a 512-entry limit.
- Ordinary rescans detect replacement images using size and modification/change timestamps. Rescan after external changes; there is no live filesystem watcher. Full rescan clears cached thumbnails if a replacement retained the same file information.
- Focus loads the original image asynchronously. Rapid navigation discards stale results, and closing focus releases its image URL. A successful rescan closes focus.
- Image reads reject files larger than 64 MiB and files that change during the read. Unreadable or damaged previews show an error instead of stopping the review.

These limits are not a total application RAM cap: full-resolution focus, transient decoding, the page, and Chromium consume additional memory. Very large pixel dimensions can still require substantial memory. Timestamp checks are not content hashes or a lock against another application changing the file.

---

## file structure

```
reviewer.html       — UI, review decisions, focus view, and rendering
main.js             — Electron window, worker coordination, config, reads, and trash
preload.js          — electronAPI bridge to the renderer
scanner.js          — background filesystem scan worker
previews.js         — bounded thumbnail cache and visible-image loading
preview-worker.js   — background thumbnail decoding
package.json        — app metadata and electron-builder configuration
package-lock.json   — locked dependency tree
icon.png            — app / taskbar icon
cover.png           — cover image used in this readme
```

---

## local data

Marks and flags are session-only — they live in memory while the app is running and are gone the moment you close it. Nothing about which images you've marked or flagged is ever written to disk.

The only thing that *is* written to disk is a small config file holding your watched folders and window state (size, position, maximized, zoom level):

The filename is `vael-reviewer-config.json`, inside Electron's app-specific `userData` directory under the operating system's application-data location. The directory name can differ between development and packaged launches.

It's a plain, human-readable JSON file. Nothing about your images themselves — thumbnails, marks, or flags — is ever written there.

## verification and limits

Twelve local checks passed in offscreen Electron on Windows, covering partial trash failures, shortcut scope, overlapping roots, background scans, stale results, replacement previews, cache bounds, visible-image loading, focus cleanup, window-state saving, drag error reporting, and exact trash paths. A rendered General review screenshot was inspected. The test scripts remain outside the repository.

Trash outcomes were simulated; actual OS trash and successful drag delivery to another application were not verified. Linux/macOS, network drives, packaged installers, and production-scale peak memory were not tested. Marks and flags have no restart recovery.
