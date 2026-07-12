# editor

<!-- cover -->
![editor cover](./cover.png)

---

A dark, workflow-specific image editor built for one job: pixelating or blurring parts of a large batch of images, fast. Desktop app (Electron), Windows and Linux.

---

## features

- **pixelate** and **bokeh blur** tools, each with adjustable strength and savable presets
- rectangle, ellipse, and lasso selection — additive (`Ctrl`+drag) and subtractive (`Shift`+drag)
- **batch folder import** — drop or open a whole folder and it becomes its own category
- **categories** to sort open images into buckets, drag thumbnails between them, save a whole category at once
- full undo/redo with a visual history panel
- auto-advance to the next image after saving
- multi-select thumbnails for batch actions
- built-in image metadata viewer (EXIF, dimensions, etc.)
- hide-all-ui mode for a distraction-free canvas
- assignable custom hotkeys for your saved filter presets

---

## installation

to be expanded

---

## getting started

Launch the app, then either drag images/folders onto the canvas or use the open-file / open-folder buttons in the top bar. Pick a tool, make a selection, apply a filter from the left toolbar, and save.

There is a full list of hotkeys (including any custom preset hotkeys you've assigned) available in-app via the hotkey guide button in the toolbar.

---

## file structure

```
editor.html          — the app's UI, styling, and all renderer-side logic
main.js               — Electron main process: window, native dialogs, filesystem I/O
preload.js             — exposes a minimal, safe electronAPI bridge to the renderer
package.json            — app metadata and electron-builder configuration
package-lock.json         — locked dependency tree
icon.png               — app / taskbar icon
cover.png              — cover image used in this readme
```

---

## local data

Nothing autosaves. Open images, categories, and edit history exist only for the current session — closing the app without saving discards them. Edited images are only written to disk when you explicitly **Save** or **Save As**, to wherever you choose via the native file dialog.

The one thing the app does persist locally is your saved filter presets (pixelate/blur values and any hotkeys you've assigned to them), under the key `vael-editor-presets` in the window's local storage. That lives inside Electron's per-app data folder:

- **Windows:** `%APPDATA%\editor.\Local Storage\`
- **macOS:** `~/Library/Application Support/editor./Local Storage/`
- **Linux:** `~/.config/editor./Local Storage/`

This is Chromium's internal storage format (not a plain-text file) — it isn't meant to be edited by hand.
