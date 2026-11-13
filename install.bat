@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   AniWorld Downloader - one-time setup
echo ============================================
echo.

python --version >nul 2>nul
if errorlevel 1 (
    echo Python was not found on your PATH.
    echo.
    echo Install Python 3.11 or newer from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup, then run this again.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating a virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :fail
)

echo Installing dependencies, this can take a few minutes...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail

".venv\Scripts\python.exe" -m pip install -e ".[all,test]"
if errorlevel 1 goto :fail

echo.
echo Done. Run start.bat to launch the Web UI.
echo.
pause
exit /b 0

:fail
echo.
echo Setup failed - check the messages above.
echo.
pause
exit /b 1
