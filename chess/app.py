"""Launch Vael Chess: python app.py (console) or pythonw app.py (windowless)."""
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import sys
import traceback


def main():
    # pythonw has no console streams. Keep startup failures available locally.
    if sys.stdout is None or sys.stderr is None:
        log_dir = Path(__file__).resolve().parent / "logs"
        log_dir.mkdir(exist_ok=True)
        with (log_dir / "desktop.log").open("a", encoding="utf-8", buffering=1) as log:
            with redirect_stdout(log), redirect_stderr(log):
                try:
                    from backend.application import main as run
                    run()
                except Exception:
                    traceback.print_exc()
                    raise
    else:
        from backend.application import main as run
        run()


if __name__ == "__main__":
    main()
