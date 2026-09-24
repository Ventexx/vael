# checklist

<!-- cover -->
![checklist cover](./cover.png)

---

A minimal to-do app for keeping tasks, notes, and repeatable lists together. Organize your work in a clean, distraction-free browser window.

---

## features

- add, edit, complete, reject, and delete tasks
- group tasks with headers and separators, and attach notes
- drag items to reorder them
- undo and redo list changes
- save named profiles and choose a startup profile
- automatically recover unnamed drafts and unfinished task input
- restore the 20 most recently deleted profiles
- import and export the current list as a `.json` file
- coordinate open tabs so they do not overwrite each other's changes
- dark and white themes, with fonts available offline
- works locally without an account or installation

---

## installation & removal

**Open**

1. Download Vael using **Code → Download ZIP** on GitHub, then extract it.
2. Open the `checklist` folder.
3. Double-click `vael.html`, or right-click it and choose **Open with → Google Chrome / Microsoft Edge**.

Keep the file in a stable location so the browser can continue finding its saved data. If your browser shows a view-only notice about unsupported saving, open it in Chrome or Edge. No terminal commands are needed.

**Remove**

Close its tabs and delete the `checklist` folder. To remove saved lists too, clear the page's stored data through your browser's privacy/site-data settings. Export any list you want to keep first; exporting saves the current list, not all profiles.

---

## local data

Lists, profiles, theme preferences, draft recovery, and deleted-profile recovery are stored in **the browser profile used to open the app**. They are not saved into `vael.html` and are not synced to an account.

Changing browsers, using a different browser profile, or moving the HTML file may show a different set of saved data. Clearing browser data removes those saved lists and recovery copies. Exported lists go to the download location chosen by your browser. Undo history lasts only while the page is open.

---

## file structure

```text
vael.html          — the complete app; open this file to use it
checklist.md       — this guide
icon.png           — browser-tab icon
cover.png          — the cover image above
```
