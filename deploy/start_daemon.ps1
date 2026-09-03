# Voice Operating Hub Daemon Startup Script (PowerShell)
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $PSScriptRoot
Set-Location $ScriptDir

Write-Host "[Voice-In] Initializing Zero-UI Real-Time Multimodal Personal Co-pilot..." -ForegroundColor Cyan

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

if (Test-Path ".\.env") {
    Get-Content ".\.env" | Where-Object { $_ -match "^[^#].+=.+" } | ForEach-Object {
        $parts = $_.Split('=', 2)
        [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), [System.EnvironmentVariableTarget]::Process)
    }
}

python main.py --daemon
