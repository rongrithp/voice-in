@echo off
setlocal enabledelayedexpansion

echo  Windows Edge: Cursor-Context Terminal Runner and HUD

cd /d "%~dp0"
python main.py --test

if %ERRORLEVEL% equ 0 (
    echo.
    echo [SUCCESS] All Windows Edge tests passed cleanly.
    exit /b 0
) else (
    echo.
    echo [FAILED] Windows Edge verification failed with code %ERRORLEVEL%.
    exit /b %ERRORLEVEL%
)
