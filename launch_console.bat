@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 console.py
    goto :finished
)

where python >nul 2>nul
if %errorlevel%==0 (
    python console.py
    goto :finished
)

echo Python 3 was not found. Install Python 3.10 or newer from https://www.python.org/downloads/windows/
echo During setup, enable "Add python.exe to PATH".
pause

:finished
endlocal
