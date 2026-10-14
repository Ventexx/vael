@echo off
setlocal
cd /d "%~dp0"

REM Everyday launch: reuse the installed environment without updates.
if not exist "venv\Scripts\pythonw.exe" (
    echo Run start.bat once to set up sorter, then use start_silent.bat.
    pause
    exit /b 1
)

start "" "venv\Scripts\pythonw.exe" "%~dp0app.py"
exit /b 0
