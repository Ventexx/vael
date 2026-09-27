"""Windowless desktop entry point; keep startup errors in a local log."""
from pathlib import Path
import sys
import traceback

folder = Path(__file__).resolve().parent / "logs"
folder.mkdir(exist_ok=True)
with (folder / "desktop.log").open("a", encoding="utf-8", buffering=1) as log:
    sys.stdout = sys.stderr = log
    try:
        from app import main
        main()
    except Exception:
        traceback.print_exc()
