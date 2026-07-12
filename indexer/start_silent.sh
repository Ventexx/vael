#!/usr/bin/env bash

set -e

# Generate timestamp (YYYY-MM-DD_HH-MM)
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M")

# Ensure logs folder exists
mkdir -p logs

LOGFILE="logs/$TIMESTAMP.log"

# Check Python
if ! command -v python3 &> /dev/null
then
    echo "Python3 is not installed."
    exit 1
fi

# Create venv if missing
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install dependencies silently
pip install -r requirements.txt >/dev/null 2>&1

# Run in background with logging
nohup python app.py > "$LOGFILE" 2>&1 &