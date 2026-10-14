#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Install Python 3.10 or newer, then run bash start.sh again."
    exit 1
fi
if [ ! -x "venv/bin/python" ]; then
    python3 -m venv venv
fi
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install -r requirements.txt
exec venv/bin/python app.py
