@echo off
setlocal
cd /d "%~dp0"

REM First-time setup or manual dependency maintenance, followed by launch.
if not exist "venv\Scripts\python.exe" (
    python --version >nul 2>&1
    if errorlevel 1 goto missing_python
    python -m venv venv
    if errorlevel 1 goto setup_failed
)

"venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto setup_failed
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto setup_failed

"venv\Scripts\python.exe" "app.py"
if errorlevel 1 goto launch_failed
exit /b 0

:missing_python
echo Python is not installed or is not on PATH. Install Python, then run start.bat again.
pause
exit /b 1

:setup_failed
echo Setup failed. Check the error above, then run start.bat again.
pause
exit /b 1

:launch_failed
echo The app stopped with an error. See the details above.
pause
exit /b 1
