@echo off
setlocal
cd /d "%~dp0\.."

echo [Voice-In] Starting Voice Operating Hub Daemon...

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

start "" pythonw main.py --daemon
echo [Voice-In] Daemon process started in background.
