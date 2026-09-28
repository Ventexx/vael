"""Stable project paths, independent of the launcher's working directory."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
TEMPLATE_DIR = PROJECT_ROOT / "piece_templates"
