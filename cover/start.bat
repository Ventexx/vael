@echo off

REM Check if Python exists
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python is not installed. Please install it first.
    exit /b
)

REM Create virtual environment if it doesn't exist
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate

REM Upgrade pip
python -m pip install --upgrade pip

REM Install dependencies
echo Installing dependencies...
pip install -r requirements.txt

REM Run the app
echo Starting app...
python app.py