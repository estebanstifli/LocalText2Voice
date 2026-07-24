#!/usr/bin/env bash
# LocalText2Voice - developer launcher for Linux (equivalent of run_dev.bat)
set -euo pipefail
cd "$(dirname "$0")"

PY=python3
VENV=.venv

if [ ! -x "$VENV/bin/python" ]; then
    echo "[lt2v] creating virtual environment..."
    $PY -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip
    "$VENV/bin/pip" install -r requirements.txt
fi

# Wayland is preferred when available, X11 works out of the box too.
if [ -n "${WAYLAND_DISPLAY:-}" ] && [ -z "${QT_QPA_PLATFORM:-}" ]; then
    export QT_QPA_PLATFORM=wayland
fi

exec "$VENV/bin/python" main.py "$@"
