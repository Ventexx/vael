# chess

<!-- cover -->
<img src="./icon.png" alt="chess icon" width="128">

---

A desktop chess board for exploring positions and reviewing games with a local analysis engine. It shares Vael's quiet, dark interface.

---

## features

- move pieces on an interactive board with legal-move checking
- step backward and forward through a game's move history
- flip the board and start a fresh position
- import a position as FEN or a game as PGN — standard chess text formats
- export a game as PGN text to copy and keep
- connect a locally installed Stockfish engine for live evaluation and suggested lines
- adjust engine strength, analysis limits, and resource settings
- mirror a board from a selected screen area, continuously or with manual captures
- use optional piece-image templates to help screen recognition

---

## installation & removal

**Install on Windows**

1. Install [Python](https://www.python.org/downloads/). Enable **Add Python to PATH** if the installer offers it.
2. Download Vael using **Code → Download ZIP** on GitHub, then extract it.
3. Open the `chess` folder and double-click `start.bat`. It downloads the required packages into a local `venv` folder and opens the app. Allow time for the initial setup.
4. For engine analysis, download and extract [Stockfish](https://stockfishchess.org/download/). In Chess's engine panel, choose **Browse**, select the Stockfish executable, then **Connect**.

Use `start.bat` again to launch later. `start_silent.bat` is the alternate launcher without the normal console window. The launchers may contact the internet to check dependencies. A browser alone cannot run this app's Python and engine functions.

**Uninstall**

Close Chess and delete its `chess` folder, including the generated `venv` folder. Save any game you want to keep using PGN export first. Stockfish is installed separately; delete its extracted folder separately if you no longer need it.

---

## local data

- `.vael_chess_settings.json`, beside `app.py`, stores the selected engine path and engine options. The leading dot is part of its name.
- Optional custom piece images live under `piece_templates/` in the Chess folder.
- The current board and move history are held for the session. Copy exported PGN text into a file if you want to retain a game.
- Screen capture is processed locally; the selected region is used to recognize the board.

---

## file structure

```text
app.py                — desktop app, board rules, and saved settings
engine.py             — connection to the chess analysis engine
capture.py            — recognition of boards in a selected screen area
frontend/index.html   — the visible interface
frontend/style.css    — colors and layout
frontend/app.js       — board controls and interactions
frontend/pieces.js    — piece artwork
requirements.txt      — packages needed by the app
start.bat             — Windows setup and launch
start_silent.bat      — alternate Windows launcher
chess.md              — this guide
icon.png / .ico       — app icons
```
