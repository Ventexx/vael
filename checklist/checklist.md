# checklist

<!-- cover -->
![checklist cover](./cover.png)

---

A minimal to-do app for managing tasks with ease. Add, edit, reorder, and organize your tasks in a clean, distraction-free interface.

---

## features

- tasks — add, check off, edit in-place, delete
- separators to group tasks into sections
- drag-to-reorder
- undo / redo (Ctrl+Z / Ctrl+Y)
- profiles — save named snapshots of your list, switch between them from the sidebar
- import / export your list as a `.json` file
- three built-in themes: *focus*, *minimal*, *paper*
- zero dependencies — one self-contained `.html` file

---

## getting started

Just open `vael.html` in any browser, or drop it on a static file server. Works entirely offline.

---

## file structure

```
vael.html      — the entire app (HTML + CSS + JS)
icon.png       — favicon
cover.png      — cover image used in this readme
```

---

## local data

Everything is stored in your browser's `localStorage` for the page — nothing is written to disk. No account, no sync.

- `vael_settings` — your selected theme
- `vael_profiles` — your saved profiles (each a named snapshot of your task list)
