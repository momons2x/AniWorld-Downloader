@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\aniworld.exe" (
    echo Dependencies are not installed yet.
    echo.
    echo Double-click install.bat first, then run this again.
    echo.
    pause
    exit /b 1
)

echo Starting the AniWorld Downloader Web UI...
echo Your browser should open at http://localhost:8080
echo Close this window to stop the server.
echo.

".venv\Scripts\aniworld.exe" --web-ui %*
set "EXITCODE=%errorlevel%"

echo.
echo Server stopped.
pause
exit /b %EXITCODE%
