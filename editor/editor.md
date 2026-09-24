# editor

<!-- cover -->
![editor cover](./cover.png)

---

A dark desktop image editor built for one job: pixelating or blurring parts of a large batch of images, fast.

---

## features

- pixelate and bokeh blur with adjustable strength and saved presets
- rectangle, ellipse, and lasso selections, with add/remove selection controls
- import individual images or folders and organize them into categories
- background folder previews, with priority given to the visible category
- drag thumbnails between categories
- apply filters to selected images and save an entire category
- per-image undo/redo, retained while moving between open images
- automatically advance to the next image after saving
- export PNG, JPEG, or WebP with quality and JPEG background options
- remove source metadata, or retain PNG text such as ComfyUI prompts
- inspect original image metadata
- custom preset shortcuts and an in-app hotkey guide
- hide the controls for a distraction-free canvas
- detect changed files before overwriting and report save failures

---

## installation & removal

**Install from this repository**

1. Install [Node.js](https://nodejs.org/) with its included `npm` tool.
2. Download Vael using **Code → Download ZIP** on GitHub, then extract it.
3. Open the `editor` folder in a terminal. On Windows, open the folder in File Explorer, type `powershell` in the address bar, and press Enter.
4. Run these commands one at a time:

```text
npm install
npm start
```

The first command downloads the app's dependencies and needs internet access. For later launches, open a terminal in the same folder and run `npm start`. This opens a desktop window; opening the HTML file in a browser does not provide the complete app.

**Uninstall**

Close the app and delete its `editor` folder, including `node_modules`. If you installed a packaged Windows version, use **Settings → Apps** to uninstall it; for a Linux AppImage, delete the AppImage. To remove saved preferences too, delete only this app's data folder listed below. Keep any images you have stored inside the installation folder before deleting it.

---

## local data

The app's data folder is named `vael-editor` or `editor.`, depending on whether you launched the repository version or a packaged app.

- **Windows:** press **Win+R**, enter `%APPDATA%`, and find that app folder.
- **Linux:** look under `~/.config/` (or your custom `XDG_CONFIG_HOME`).
- **macOS:** look under `~/Library/Application Support/` if running from source.

Saved filter presets and their shortcuts live in the app folder's `Local Storage` subfolder. This is managed by the app; it is not a text file to edit manually.

Images are written only when you choose **Save** or **Save As**. Saving can replace the source image; Save As lets you choose a separate file. Open categories and editing history are session-only.

Temporary editing data is stored in your system's temporary directory in folders beginning `vael-editor-cache-`. Normal exit removes this cache; an unexpected crash can leave files behind. It is not automatic recovery after restarting.

---

## file structure

```text
editor.html           — the interface and editing controls
main.js               — desktop window, file access, and native dialogs
preload.js            — connection between the interface and desktop features
image-files.js        — image formats and safe saving
image-metadata.js     — PNG text metadata handling
session-cache.js      — temporary image and undo-history storage
thumbnail-queue.js    — background preview scheduling
thumbnail-worker.js   — background preview generation
package.json          — app information and launch/package commands
package-lock.json     — exact dependency versions
editor.md             — this guide
icon.png / .ico       — app icons
cover.png             — the cover image above
```
