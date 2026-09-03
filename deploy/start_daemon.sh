#!/usr/bin/env bash
# Voice Operating Hub Daemon Startup Script (Linux/macOS)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${SCRIPT_DIR}"

echo "[Voice-In] Initializing Zero-UI Real-Time Multimodal Personal Co-pilot..."

if [ -f ".venv/bin/activate" ]; then
    source ".venv/bin/activate"
fi

if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

exec python main.py --daemon
