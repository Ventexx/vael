"""Local, atomic settings. Exported profiles deliberately exclude local paths."""
import copy
import json
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = APP_DIR / ".local"
DEFAULTS = {
    "sources": [], "sort_root": "", "pins": [],
    "fields": {"GENRE": "g", "CHAR": "ch", "SCENE": "s"},
    "rules": [{"pattern": "[GENRE]-[CHAR]_[SCENE]", "scope": ""},
              {"pattern": "[CHAR];[GENRE]", "scope": ""}],
    "inherit": True, "ignored": [".git", "venv", ".local", ".sorter-*"],
    "flags": [], "layout": {}, "collision": "rename",
}


def read_settings():
    config = copy.deepcopy(DEFAULTS)
    try:
        saved = json.loads((DATA_DIR / "settings.json").read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            config.update(saved)
    except FileNotFoundError:
        pass
    except (ValueError, OSError) as exc:
        raise RuntimeError(f"Cannot read local settings: {exc}") from exc
    return config


def save_settings(config):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = DATA_DIR / "settings.tmp"
    temp.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, DATA_DIR / "settings.json")


def profile(config):
    return {key: copy.deepcopy(config[key]) for key in
            ("fields", "rules", "inherit", "ignored", "collision")}
