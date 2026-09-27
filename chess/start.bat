@echo off
setlocal
cd /d "%~dp0"
if not exist "venv\Scripts\pythonw.exe" (
    python -m venv venv
    if errorlevel 1 exit /b 1
)
"venv\Scripts\python.exe" -c "import webview, chess, mss, numpy, PIL" >nul 2>&1
if errorlevel 1 (
    "venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 exit /b 1
)
start "" "venv\Scripts\pythonw.exe" "%~dp0launch.pyw"
exit /b
