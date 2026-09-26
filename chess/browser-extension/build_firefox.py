"""Generate the Firefox package from the shared Chromium companion sources."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILES = ("background.js", "read-board.js", "poll.js", "popup.js", "popup.html")

def build():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    manifest.pop("minimum_chrome_version", None)
    manifest["background"] = {"scripts": ["read-board.js", "background.js"]}
    manifest["browser_specific_settings"] = {"gecko": {
        "id": "vael-chess-live@vael.local", "strict_min_version": "128.0",
        "data_collection_permissions": {"required": ["none"]}}}
    target = ROOT / "firefox"
    target.mkdir(exist_ok=True)
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    for name in FILES:
        (target / name).write_bytes((ROOT / name).read_bytes())

if __name__ == "__main__":
    build()
    print("Firefox companion refreshed.")
