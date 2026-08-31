@echo off
setlocal
cd /d "%~dp0"

echo [Voice-In] Starting Voice Operating Hub Daemon in background...

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" main.py
) else (
    start "" pythonw main.py
)

timeout /t 1 /nobreak >nul
echo [Voice-In] Daemon launched successfully in background.
echo [Voice-In] Check system tray icon (bottom right) and HUD overlay (top center).
