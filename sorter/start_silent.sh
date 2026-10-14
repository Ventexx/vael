#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if [ ! -x "venv/bin/python" ]; then
    echo "Run bash start.sh once to set up sorter first."
    exit 1
fi
nohup venv/bin/python app.py >/dev/null 2>&1 &
