# Vael: project context and improvement backlog

Last updated: 2026-09-23.

## Read this first

Vael is the owner's collection of personal, focused apps for removing repetitive
work and saving time. Optimize for their actual workflow, useful automation, and
fast interaction. The apps share a visual language but remain independently useful.
Chess is peripheral: it primarily shares the design language and repository.

This backlog is context, not permission to implement everything. Only implement
the work requested in the current conversation. Optional improvements and new
feature candidates below require the owner's selection before implementation.
Recheck the current code before treating an old finding as still present.

### Working conventions

- Use Conventional Commits, without app scopes: `fix: ...`, `feat: ...`,
  `docs: ...`, or `chore: ...`; do not add `(cover)` or `(indexer)`.
- Stage explicitly named files within the app being changed. Avoid broad staging
  that could include another app's work. Stage root files separately when the
  requested change concerns repository configuration or documentation.
- Make each coherent fix/change its own commit. Verify it, stage it, commit it,
  and push it before moving on to the next fix. Preserve unrelated local changes.
- Brainstorming and review requests are read-only unless changes are requested.
- Keep Cover's local Python regression checks in `cover/tests/` excluded through
  the root `.gitignore`. They were removed from tracking but retained locally.
  A fresh clone will not contain them. The pre-existing backup tests remain tracked.
- Keep this document current when approved backlog work is completed. Distinguish
  implemented behavior, remaining fixes, optional improvements, and unapproved ideas.

## App map

| App | Main files | Purpose |
| --- | --- | --- |
| cover | `cover/app.py`, `cover/cover.md` | Python/Qt front end for ComfyUI workflows, image inputs, and a run queue |
| indexer | `indexer/app.py`, `indexer/indexer.md` | Python/Qt image + JSON library, SQLite search, identifiers, and notes |
| editor | `editor/editor.html`, `editor/main.js`, `editor/preload.js` | Electron image pixelation/blur, selections, categories, and batch editing |
| reviewer | `reviewer/reviewer.html`, `reviewer/main.js`, `reviewer/preload.js` | Electron comparison of generated iterations, requeue flags, and trash decisions |
| checklist | `checklist/vael.html`, `checklist/checklist.md` | Browser-based tasks, sections, notes, and saved profiles |
| backup | `backup/backup.py`, `backup/tests/`, `backup/docs/` | Python/7-Zip backup creation, updating, verification, and recovery |

Most implementation lives in a few self-contained files. Shared design: near-black
surfaces, teal accents, restrained borders, compact controls, small typography,
and collapsible panels. Preserve the established style when adding controls.

## Completed work and current limits

- Indexer multi-entry search was committed as `f0d90dc`: semicolon-separated
  searches combine and deduplicate results while retaining per-term modifiers.
- Cover reliability fixes C1-C6 were implemented and pushed individually:
  - C1 / `ce3ec0e`: failed queue jobs no longer loop automatically; explicit retry.
  - C2 / `2825935`: retain prompt IDs and reconnect without resubmitting; uncertain
    submission responses are kept separate from definitive failures.
  - C3 / `9c86556`: stop local execution monitoring and wait for worker cleanup
    before closing; do not edit/delete a workflow while it runs.
  - C4 / `cb2d613`: repeated Run actions cannot replace an active worker.
  - C5 / `7cf11cc`: output cleanup uses the OS trash, supports selection, reports
    failures, and never falls back to permanent deletion.
  - C6 / `531aa6e`: scan outputs and decode thumbnails off the UI thread; retain
    unchanged previews, refresh changed files, batch list updates, and cap caching.
- Cover usage notes were added in `046b602`. Test ignore/untracking was done in
  `6262e8c`; `.pytest_cache` directories are also ignored.
- Fifteen local Cover regression checks passed, including an offscreen window
  smoke check. ComfyUI requests and trash operations were mocked. Real-server
  integration and actual OS-trash behavior were not verified in that pass.
- Cover queue items, prompt IDs, and assigned input images remain session-only.
  Closing Cover stops local monitoring, not server jobs. The output panel still
  browses PNGs directly inside a configured local folder.
- Editor reliability fixes E1-E7 were implemented and pushed individually:
  - E1 / `a36d4ad`: batch blur no longer rejects its own cooldown; callers await
    completion, and Template + Save saves only after successful filtering.
  - E2 / `2a998c5`: saved state follows unique pixel revisions, so branching after
    undo, selection-only steps, and history trimming report unsaved changes correctly.
  - E3 / `2c2cbfa`: PNG/JPEG/WebP bytes match the selected extension; Save As chooses
    the destination before encoding. Save options control quality and JPEG background.
  - E4 / `b0816f7`: private session files retain pixels, masks, and undo/redo when
    images leave memory; simultaneous hydration requests share one load.
  - E5 / `1162a11`: a 256 MiB retained-data budget counts dimensions and shared
    history buffers; older buffers spill to disk, closed images release their cache,
    and oversized filter allocations are rejected before starting.
  - E6 / `68a8bd0`: write and flush a sibling temporary file before publication;
    check source/destination content versions and serialize same-path saves.
    Conflicts retain unsaved edits and explain Save As/reopen options.
  - E7 / `f09eb91`: explicitly remove source metadata by default, or retain PNG
    text chunks for PNG-to-PNG saves. Original metadata remains inspectable after
    editing/eviction. EXIF and preview chunks are not copied. Usage notes updated.
- Editor checks passed in actual offscreen Electron on Windows: output encoding,
  cancellation, eviction/undo/redo, forced memory pressure, file-version conflicts,
  PNG metadata retention/removal, and Save options layout. Separate Node checks
  covered history branching, batch dispatch, damaged metadata, and simulated
  write/publication failures. Regression scripts are local, outside the repository.
- Editor remains a desktop Electron app. Its cache is session-only, keeps at most
  40 history steps per image, and is removed on normal exit; crashes may leave OS
  temporary files. The memory budget is not a total-process RAM limit. PNG-text
  retention is not general metadata preservation. Final file-version checks are
  not a cross-application lock. Linux, production-scale peak RAM, and power-loss
  durability were not verified. See `editor/editor.md` for the detailed limits.

- Indexer reliability fixes I1-I8 and I12 were implemented and pushed:
  - I1 / `35013e1`: folder metadata refreshes even with no changed image/JSON pairs.
  - I2 / `6ee9053`: tag edits reject malformed or unreadable source metadata.
  - I3 / `e1e434a`: source-version checks, atomic file publication, explicit write
    errors, and rollback of earlier sidecar writes when an identifier batch fails.
  - I4 / `34abb9b`: settings/notes failures are visible, corrupt files are preserved,
    and failed new-note saves retain the dialog contents.
  - I5 / `c79d999`: failed or cancelled indexing rolls back; UI controls recover;
    shutdown waits for workers instead of destroying running threads.
  - I6 / `ddb4a5f`, `a6dbe76`: background filesystem scans and searches, separate
    SQLite connections, stale-query suppression, incremental card rendering, and
    worker shutdown. The owner's transparent-load fix is retained in `ddb4a5f`.
  - I7 / `7857989`: complete counted search results with 500-item pages instead of
    the UI's silent 2,000-result limit; OR searches deduplicate across terms.
  - I8 / `59c53c2`, `8a8120b`: run scripts with the current interpreter and no shell,
    show failures, validate saved entries, stop the sequence on failure, and support
    cancellation plus orderly shutdown.
  - I12 / `d933402`: version-aware, 32 MiB / 1,024-entry decoded-image cache;
    background QImage decoding; GUI-thread QPixmap conversion; stale requests
    discarded; viewer previews capped at 2,048 pixels per side.
- Fixed the startup-script dependency regression: Indexer's requirements now include
  Pillow (`PIL`), needed by the owner's metadata extractor in Indexer's environment.
  Verified the PIL import and extractor `--help` through the actual ScriptRunner;
  the owner's image library was not processed during this check.
- The owner's tested folder-focus feature was included separately in `14e082a`.
  Expanded results and note sections scroll into view; state restoration does not
  trigger focus scrolling. User Cover edits present during this work were left intact.
- Twelve local Indexer checks passed on Windows, including an offscreen Qt desktop
  smoke check, 2,450-match paging, live temporary script execution/cancellation,
  simulated write failures, invalid/stale metadata, rollback, preview replacement,
  transparency, cache limits, and shutdown. Checks are local outside the repository.
  Offscreen fonts rendered as missing glyphs, so final font appearance was not
  verified. Linux/macOS, network filesystems, power-loss durability, and large-library
  peak RAM were not verified. File-version checks are not cross-application locks;
  batch rollback can itself fail and reports that outcome. Cache limits are not a
  total-process RAM cap, freshness uses file stats rather than content hashes, and
  detached script children may survive cancellation. Usage and limits are in
  `indexer/indexer.md` (`3323c19`).

- Checklist reliability fixes L1-L6, L11, and L12 were implemented and pushed:
  - L1 / `9ee3adb`: recover unnamed lists and unfinished task input after reload;
    retain deliberately emptied drafts and warn before discarding unsaved work.
  - L2 / `61e7d3a`: validate imports before changing the list, assign fresh IDs,
    and include note IDs when allocating new items.
  - L3 / `40c923f`: leave text undo to the browser; handle list redo once and
    recognize Ctrl+Shift+Z correctly.
  - L4 / `ecf6dfe`, `4bf17cc`: surface failed storage operations, preserve corrupt
    saved data and unsaved edits, and keep warnings visible above the sidebar.
  - L5 / `52b3bd7`: clear completed/rejected tasks immediately as one undoable
    change; no delayed callback can alter a newly opened list.
  - L6 / `ece0a86`: retain the 20 most recently deleted profiles in browser storage
    and restore them through the sidebar; deleting the active profile retains a draft.
  - L11 / `d442160`: use Web Locks to permit one editing tab; other tabs show
    updates in view-only mode and receive editing access after the writer leaves.
  - L12 / `f44a6ac`: use an offline system monospace stack instead of Google Fonts.
- Ten local Checklist checks passed in isolated offscreen Electron/Chromium windows
  on Windows. They cover actual file-import events, validation/IDs, blocked/full
  storage, corrupt data, draft reloads, text/list undo, immediate clearing, deletion
  recovery, two-window updates and editing transfer, offline resources, and layout.
  Dark and white screenshots were visually inspected. Tests remain outside the repo.
  Checklist remains a browser app; Electron was only the test runner.
- Checklist limits: saving requires Web Locks; unsupported environments are view-only.
  Older app versions do not participate in tab coordination. Undo history is session-only;
  uncommitted inline text can be lost in an abrupt crash. Browser-data clearing removes
  drafts and deleted-profile recovery. Export includes only the current committed list,
  not all profiles/settings or unfinished input. Firefox, Safari, mobile browsers,
  abrupt power loss, and production-size lists were not verified. Details are in
  `checklist/checklist.md` (`4cca095`).

## All remaining optional improvements from the review

IDs refer to the original brainstorming review. None of the following is a blanket
implementation instruction. C7 is partly addressed by explicit retry/check-pending
controls, and C9 has some status distinctions; the remaining parts are listed here.

### Editor

- **E8 — Save and close:** add Save / Discard / Cancel to the unsaved-work dialog.
  Keep the window open when saving fails or is cancelled.
- **E9 — Supervise batch operations:** current filename, completed/total count,
  cancellation after the current image, actionable failure details, and retry.
- **E10 — Reusable region templates:** save normalized selections or multiple
  regions for similarly framed images; preview and adjust before batch application.
- **E11 — Before/after comparison:** hold a key to show the original, or use a
  split view to inspect boundaries and filter strength.
- **E12 — Explicit import scope:** choose direct files, one subfolder level, or
  recursive import; report unreadable files and duplicate paths.

### Cover

- **C7 — Queue controls:** pause after the current job, reorder waiting jobs,
  retry selected failures, remove selected waiting jobs, and display input filenames.
  Retry Failed and Check Pending already exist; do not duplicate those actions.
- **C8 — Optional restart recovery:** persist waiting job definitions and submitted
  prompt IDs, then offer to resume after restart. Preserve an optional clean-session
  mode and never resubmit a recovered job merely because its status is unknown.
- **C9 — Useful execution progress:** show uploading, waiting on server, generating,
  elapsed time, and connection state. Allow a workflow-appropriate configurable
  monitoring timeout. Existing timeout handling reconnects to the same prompt.
- **C10 — Job-to-output tracking:** link completed jobs to the actual outputs in
  their ComfyUI history, including nested output folders and remote-server setups.
- **C11 — Curated parameter controls:** expose chosen parameters from several
  nodes, including booleans, with clear labels and reusable presets.
- **C12 — Batch input assignment:** one reference image against a folder, pairing
  by basename, and advancing through unprocessed inputs. Preview assignments
  before adding jobs to the queue.

### Indexer

- **I9 — Discoverable search:** retain the compact syntax while adding mode chips,
  examples, recent searches, and saved searches. Explain that multiple terms
  currently combine results using OR rather than requiring every term to match.
- **I10 — Batch metadata edits:** apply identifiers, add/remove tags, or update a
  selected field across selected assets. Preview the scope and retain an undo record.
- **I11 — Library health view:** find unmatched image/JSON pairs, malformed JSON,
  unavailable folders, and stale index entries; reveal each affected file directly.
- **I12 — Thumbnail freshness and bounds:** completed as a reliability fix; see above.
- **I13 — Temporary identifiers:** visibly distinguish temporary collections from
  persistent identifiers and offer explicit promotion to persistent state.

### Reviewer

- **R8 — Keep one, mark the rest:** choose a winner and mark other iterations for
  trash, with optional automatic advance to the next unresolved set.
- **R9 — Synchronized comparison:** side-by-side views with shared zoom/pan and a
  quick swap or blink comparison for subtle differences.
- **R10 — Undo review decisions:** reverse individual marks, whole-set actions,
  and flag changes before trash execution.
- **R11 — Optional session recovery/export:** preserve marks, flags, the active
  folder/set, and scroll position while retaining a session-only mode.
- **R12 — Requeue handoff:** export flagged sets with paths and available workflow
  references. An explicit send-to-Cover action could later prepare jobs when enough
  information is available; it should not automatically launch them.

### Checklist

- **L7 — Templates versus active profiles:** start a fresh checklist from a
  template without autosaving the active run back into the template.
- **L8 — Section actions:** move a section with its tasks, duplicate it, reset
  statuses, or complete its tasks together.
- **L9 — Complete export/import:** back up all profiles, names, startup selection,
  and settings, with explicit merge versus replace behavior.
- **L10 — Keyboard operation:** reorder and edit the focused task, insert below
  it, and provide a keyboard alternative to right-click rejection.
- **L11 — Multiple tabs:** completed as a reliability fix; see above.
- **L12 — Offline fonts:** completed as a reliability fix; see above.

### Backup

- **B7 — External configuration:** named configuration files for sources,
  destination, compression, and history, instead of editing Python for each job.
- **B8 — File-level dry run:** show expected additions, updates, deletions, space
  estimates, and an optional detailed file list.
- **B9 — Restore workflow:** browse archive contents, restore selected paths to
  a chosen directory, and verify restored data. Support sample restore checks.
- **B10 — Retained versions:** optionally keep a bounded number of earlier
  versions to recover from unwanted source edits or deletions discovered later.
- **B11 — Progress and cancellation:** show scanning, staging copy, updating,
  testing, hashing, and publication; allow safe cancellation before publication.
- **B12 — Live-source handling:** exclusions and pre/post commands to pause
  writers or create application-specific exports where a consistent snapshot matters.

## Improvements across Vael

### 1. Shared behavior rules

Establish a small design and interaction guide covering:

- Consistent meanings for saved, queued, failed, pending, and completed.
- Visible distinctions between session-only and persistent state.
- OS trash for ordinary image cleanup and clear file-operation scope.
- Consistent shortcuts, tooltips, keyboard focus, and UI scaling.
- Errors that retain the affected filenames and enough context to retry.

### 2. Fast, predictable launching

The Python launchers currently install dependencies and upgrade pip on each launch
and depend on the working directory. Separate first-run setup from normal launch,
resolve paths relative to the launcher, and stop clearly when setup fails.

### 3. Focused regression coverage

Test the outcomes the owner relies on:

- Editor: branching from saved history, batch filters, output encoding, eviction,
  save conflicts, and metadata policy. Local checks now cover these; see above.
- Cover: queue failure, timeout/reconnection, duplicate submission, shutdown, and
  output refresh/trash behavior. Existing local checks cover these with mocks.
- Indexer: local checks now cover incremental metadata refresh, failed/stale writes,
  rollback, background search/paging, script failures/cancellation, previews, and shutdown.
- Reviewer: partial trash failures and cache freshness.
- Checklist: local checks now cover imports, storage errors, draft recovery,
  text/list undo, clear timing, deleted profiles, and cross-tab coordination.
- Backup: pending recovery and shared-history behavior across archives.

Follow the owner's test-tracking preference above. Do not claim a mocked test
establishes real-server or OS integration behavior.

### 4. Accurate documentation

Indexer documentation now covers its renamed data directory, identifiers, OR search,
paging, persistence, scripts, and preview limits. Reviewer's flags still need review.
Editor's batch/save/cache
behavior is now documented in `editor/editor.md`; keep it current.
Cover now has `cover/cover.md`; keep it current. Review backup recovery instructions
against the actual recovery implementation. Correct outdated names and file paths.
Checklist's themes, draft recovery, storage, import/export scope, and multi-tab
behavior are documented in `checklist/checklist.md`; keep it current.

### 5. Selective shared helpers

Window chrome, styling, thumbnails, and persistence contain repeated patterns.
Extract small shared helpers when a concrete change benefits from them. Keep the
apps independently usable and avoid a repository-wide rewrite merely for uniformity.

### 6. Explicit handoffs between apps

- Indexer to Cover: assign a selected asset to an input slot.
- Reviewer to Editor: open chosen images for finishing.
- Reviewer to Cover: prepare flagged sets for another run.
- Cover to Reviewer: open newly generated sets for comparison.

Start with file paths and small export formats. Each handoff should eliminate a
specific sequence of clicks and clearly show which files/jobs are being handed over.

## Remaining correctness findings: quick orientation

These were identified in the review and remain after the Cover, Editor, Indexer, and Checklist work. Verify
them against the current implementation before fixing them.

| App | Findings |
| --- | --- |
| reviewer | R1 full-memory rescan; R2 unbounded full-resolution cache; R3 blocking scans/reads; R4 failed trash marks cleared; R5 stale replaced-file previews; R6 global shortcuts; R7 overlapping-root duplicates |
| backup | B1 pending recovery writes its JSON wrapper; B2 recovery test misses that bug; B3 shared-history run-ID race; B4 latest comparison not scoped by backup family; B5 verification races archive replacement; B6 failures after publication need accurate outcome reporting |

Editor E1-E7 are complete. E8-E12 remain optional and unapproved.
Indexer I1-I8 and I12 are complete. I9-I11 and I13 remain optional and unapproved.
Checklist L1-L6, L11, and L12 are complete. L7-L10 remain optional and unapproved.
The strongest next correctness candidates are backup B1-B2 and reviewer R4-R5;
choose the next app with the owner before starting it.

## New feature candidates for owner review

All N-items are unapproved proposals. They extend the earlier brainstorming list;
they are not required parts of the current apps or tasks.

### N1. Named workspaces

Save a work context such as a project or client with its asset folders, indexer
database, ComfyUI server/workflow, reviewer roots, and checklist profile. Reopening
it restores the relevant context across the apps without configuring each one.
Start with a workspace manifest and explicit open actions; avoid launching jobs
automatically. Review whether a small launcher or per-app workspace selector fits best.

### N2. Filename and image/JSON pair organizer

Preview batch renaming, numbering, and folder placement while keeping image/JSON
pairs together. Show conflicts and a before/after table, then retain an undo manifest.
Review the owner's naming rules before implementing any rename operations.

### N3. Local asset lineage

Optionally record which inputs, workflow, parameters, generated iteration, and
editor export produced a final file. A sidecar history could answer "how did I make
this?" without requiring embedded metadata to survive every processing step.
Review storage location and which details should be retained.

### N4. Delivery packages

Define a reusable export package: chosen images, naming convention, formats,
dimensions, folder layout, and metadata policy. Preview the package and write a
separate delivery copy. This could reduce repeated preparation for the same output
requirements. Review actual delivery requirements before designing presets.

### N5. Variable-based text snippets

Extend reusable indexer notes with fields such as `{subject}`, `{style}`, and
`{variant}`. Fill the fields, preview the final text, and copy it or assign it to an
exposed Cover input. Review whether lightweight substitution solves the real need
before considering a full prompt-building interface.

### N6. Batch duration estimates

Use locally recorded completion times per workflow to estimate a queue's duration.
Show an approximate range and how much evidence it is based on, rather than a
precise promise. This could help decide which batch fits into the available time.

### N7. A compact completion and failure inbox

Collect actionable outcomes with direct links: a batch finished, selected files
failed, a backup needs attention, or a delivery package is ready. Keep unchanged
states quiet. Review whether this should live in a launcher, a small panel, or
simple desktop notifications; do not introduce a background service by default.

### N8. Resumable workflow checklists

Connect a checklist template to explicit steps such as generate, review, edit,
package, and back up. Each step opens the relevant context and records completion
and output paths. Start with manual confirmation between steps; automation should
only be added where completion can be determined reliably and is requested.
