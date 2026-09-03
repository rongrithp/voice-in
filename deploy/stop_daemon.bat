@echo off
setlocal
cd /d "%~dp0\.."

echo [Voice-In] Stopping Voice Operating Hub Daemon...

powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*main.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('[Voice-In] Terminated Process PID: ' + $_.ProcessId) }"

timeout /t 1 /nobreak >nul
echo [Voice-In] Daemon stopped. Audio devices and system hooks released.
