"""Platform options shared by interactive analysis and game review."""
import subprocess
import sys


def engine_process_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
