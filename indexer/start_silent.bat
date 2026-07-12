@echo off

REM Generate timestamp (YYYY-MM-DD_HH-MM)
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm"') do set TIMESTAMP=%%i

REM Ensure logs folder exists
if not exist logs (
    mkdir logs
)

REM Set log file path
set LOGFILE=logs\%TIMESTAMP%.log

REM Create venv if missing
if not exist venv (
    python -m venv venv
)

REM Activate venv
call venv\Scripts\activate

REM Install dependencies (silent)
pip install -r requirements.txt >nul 2>&1

REM Run app silently with logging
start "" venv\Scripts\pythonw.exe app.py > "%LOGFILE%" 2>&1