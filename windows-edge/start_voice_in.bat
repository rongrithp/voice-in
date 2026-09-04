@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
title Gemini Live Multimodal Voice Copilot (F20)

echo ======================================================================
echo  Starting Voice-In Windows Edge: Gemini Live Multimodal Service (F20)...
echo ======================================================================

cd /d "%~dp0"
if exist "..\.venv\Scripts\python.exe" (
    set "PYTHON_CMD=..\.venv\Scripts\python.exe"
) else if exist ".venv\Scripts\python.exe" (
    set "PYTHON_CMD=.venv\Scripts\python.exe"
) else (
    set "PYTHON_CMD=python"
)

%PYTHON_CMD% main.py --live


pause
