@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
title Voice-In Edge Live Service

echo ======================================================================
echo  Starting Voice-In Windows Edge: Unified Live Service Daemon...
echo ======================================================================

cd /d "%~dp0"
python main.py --live

pause
