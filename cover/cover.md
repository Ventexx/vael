# cover

<!-- cover -->
<img src="./icon.png" alt="cover icon" width="128">

---

A desktop companion for ComfyUI. Browse your image folders, assign inputs to saved workflows, and run individual jobs or a queue from one compact workspace.

---

## features

- keep several ComfyUI workflows available and switch between them
- browse local image folders with thumbnails and a larger image viewer
- assign images to workflow input slots through the Input Roster
- adjust exposed workflow parameters before running
- run one workflow or process queued jobs in order
- retry failed jobs and check jobs whose server status is still pending
- reconnect to submitted jobs without automatically submitting duplicates
- browse generated PNGs in a chosen local output folder
- move selected output files, or all PNGs in that folder, to the system trash
- background output scanning and thumbnail loading
- keyboard shortcuts for workflows, folders, queue actions, and sidebars
- save workflow settings and the window layout between launches

---

## installation & removal

**Install**

1. Install [Python](https://www.python.org/downloads/). Enable **Add Python to PATH** if the installer offers it.
2. Download Vael using **Code → Download ZIP** on GitHub, then extract it.
3. Open the `cover` folder. On Windows, double-click `start.bat`. On Linux, open a terminal in this folder and run `bash start.sh`.
4. Wait while the launcher creates a local `venv` folder, downloads the required packages, and opens the app.

Use the same launcher each time. Internet access is needed when it installs or updates packages. The `start_silent` variants are alternate launchers without the normal console output.

ComfyUI must be installed and running separately. In Cover's Settings, enter its server address and choose the local output folder to browse. Add workflows exported in ComfyUI's **API format**; Cover does not install the models or extra nodes they require.

**Uninstall**

Close Cover and delete its `cover` folder, including `venv`. Move any workflow files or output images you have kept inside that folder somewhere safe first. ComfyUI and image folders elsewhere are separate from this installation.

---

## local data

- `workflows_config.json`, beside `app.py`, stores settings, workflow references, parameter values, and layout.
- Workflow JSON files and input images stay wherever you put them; the app remembers their locations.
- Generated images are saved by ComfyUI. Cover's output-folder setting selects a local folder to browse; it does not change where ComfyUI saves or download remote results.
- Queue entries, submitted-job IDs, and assigned input images last only for the current session.

Closing Cover stops its local monitoring. Jobs already submitted can continue on ComfyUI.

---

## file structure

```text
app.py                       — the desktop app
requirements.txt             — packages needed by the app
start.bat / start.sh          — Windows / Linux setup and launch
start_silent.bat / .sh        — alternate launchers
cover.md                     — this guide
icon.png / .ico               — app icons
workflows_config.json        — your saved settings; created locally
venv/                        — downloaded Python packages; created by the launcher
```
