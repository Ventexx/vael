# cover

A desktop front end for running ComfyUI workflows with local image inputs.

## Start

Run `start.bat` on Windows or `start.sh` on Linux from this folder. For an
existing Python environment, install `requirements.txt` and run `python app.py`.

In Settings, choose the ComfyUI server and the local output folder. Add a
workflow saved in ComfyUI's API format, assign its image inputs in the roster,
and select **Run** or **Add to Queue**. The workflow's Save Image node determines
where ComfyUI actually writes its results; Cover's output folder is a local
folder to browse, not a remote download destination.

## Queue and reconnecting

- **Run Queue** processes waiting jobs once, in order. A failed job stays in
  the list and does not block later jobs. Hover over it to read the error.
- **Retry Failed** explicitly retries failed jobs. It does not resubmit jobs
  whose outcome is still unknown.
- **Check Pending** reconnects to submitted jobs using their existing ComfyUI
  prompt IDs. A monitoring timeout does not mean the server stopped the job.
- If a submission response is lost before Cover receives a prompt ID, the
  queue shows **Submission unknown**. Check ComfyUI before clearing that entry
  and creating another run.
- For an individual run, press **Run** again after a monitoring timeout to check
  the existing job. If the submission response was lost, Cover asks before
  allowing a new submission.
- Repeated Run clicks or shortcuts cannot start a second execution while the
  current worker is still active.

Closing during an active run asks for confirmation. Cover stops local monitoring,
waits for the current network request and worker cleanup, then closes. Already
submitted jobs can continue on the server. Closing does not cancel ComfyUI jobs.

## Outputs

The Outputs panel lists PNGs directly inside the configured local output folder.
Refresh scans in the background, retains unchanged previews and selection, and
reloads previews when a file's modification time or size changes. Only nearby
visible thumbnails are decoded, with at most 128 thumbnails retained.

Use Ctrl/Shift selection and **Trash Selected**, or **Trash All PNGs**. The
confirmation names the folder and file count. These actions cover the chosen
files regardless of which app created them. Failed trash operations are reported
and never fall back to permanent deletion.

## Local state

`workflows_config.json` beside the app stores settings, workflow definitions,
parameter values, and layout. Queue items, prompt IDs, and assigned input images
are session-only. They are not restored after restarting Cover.

## Local regression checks

The development checks in `cover/tests/` are kept locally and excluded from Git.
They are not included in a fresh checkout. If that local test directory is present,
run the checks as follows.

From the repository root:

```text
python -B -m unittest discover -s cover/tests -v
```

The checks use mocked ComfyUI requests, temporary files, and an offscreen Qt
window. They do not load your configuration, submit real jobs, or trash your
images. Real-server integration and OS trash behavior still need verification
on the intended environment.
