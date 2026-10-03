"""Resolve folder metadata even after the containing folder is renamed."""
from pathlib import Path


def resolve_folder_metadata(folder: Path, filenames=None) -> Path:
    """Prefer the current name, otherwise accept exactly one !F-*.json file.

    Return the conventional path when no file exists so callers can create it.
    Never guess between multiple metadata files left by earlier renames.
    """
    expected = f"!F-{folder.name}.json"
    if filenames is None:
        filenames = [p.name for p in folder.iterdir() if p.is_file()]
    candidates = sorted(name for name in filenames
                        if name.casefold().startswith("!f-")
                        and name.casefold().endswith(".json"))
    matching = [name for name in candidates if name.casefold() == expected.casefold()]
    if len(matching) == 1:
        return folder / matching[0]
    if len(candidates) == 1:
        return folder / candidates[0]
    if candidates:
        raise ValueError(f"Ambiguous folder metadata in {folder}: " + ", ".join(candidates))
    return folder / expected
