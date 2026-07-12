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
- **flag** sets for requeue — manually, or automatically when every image in a set is marked for deletion (and auto-unflags if you undo that)
- a flagged-sets overview, one click away
- zoomed focus view with arrow-key stepping between a set's iterations
- toggleable right-click behavior: mark for deletion vs. flag for requeue
- expand/collapse all sets at once
- deletions go to the OS trash, never a hard delete
- remembers window size, position, maximized state, and zoom level between launches

---

## installation

to be expanded

---

## getting started

Open the folder-settings panel and add one or more root folders to watch, then rescan. In the default **iteration** view, the sidebar lists every folder that has sets needing a decision; pick one to see its sets, expand a set, and mark the images you don't want. Switch to **general** review from the sidebar footer to browse everything instead of just the sets in question.

Once you've marked everything you want gone, hit **Execute** to send it all to the trash in one confirmation.

---

## file structure

```
reviewer.html          — the app's UI, styling, and all renderer-side logic
main.js                — Electron main process: window, folder scanning, config, trash
preload.js               — exposes a minimal, safe electronAPI bridge to the renderer
package.json              — app metadata and electron-builder configuration
package-lock.json           — locked dependency tree
installer.nsh             — custom NSIS installer script (Windows build)
icon.png                 — app / taskbar icon
cover.png                — cover image used in this readme
```

---

## local data

Marks and flags are session-only — they live in memory while the app is running and are gone the moment you close it. Nothing about which images you've marked or flagged is ever written to disk.

The only thing that *is* written to disk is a small config file holding your watched folders and window state (size, position, maximized, zoom level):

- **Windows:** `%APPDATA%\reviewer.\vael-reviewer-config.json`
- **macOS:** `~/Library/Application Support/reviewer./vael-reviewer-config.json`
- **Linux:** `~/.config/reviewer./vael-reviewer-config.json`

It's a plain, human-readable JSON file. Nothing about your images themselves — thumbnails, marks, or flags — is ever written there.
