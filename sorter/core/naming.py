"""User-defined named placeholders; no executable expressions or raw regex."""
import re
import shlex
from pathlib import Path


def compile_pattern(pattern, fields):
    parts = re.split(r"(\[[A-Za-z][A-Za-z0-9_]*\])", pattern.strip())
    names, regex, previous = [], [], False
    if not pattern.strip():
        raise ValueError("A folder pattern cannot be empty.")
    for part in parts:
        if not part:
            continue
        if part.startswith("[") and part.endswith("]"):
            name = part[1:-1]
            if previous:
                raise ValueError("Place a separator between fields.")
            if name not in fields:
                raise ValueError(f"Unknown field: {name}")
            if name in names:
                raise ValueError(f"Repeated field: {name}")
            names.append(name)
            regex.append(f"(?P<{name}>.+?)")
            previous = True
        else:
            if "[" in part or "]" in part:
                raise ValueError("Use named placeholders such as [GENRE].")
            regex.append(re.escape(part))
            previous = False
    return re.compile("".join(regex), re.IGNORECASE), names


def validate_config(config):
    fields = config["fields"]
    aliases = []
    for name, alias in fields.items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid field name: {name}")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", alias):
            raise ValueError(f"Invalid search alias: {alias}")
        aliases.append(alias.casefold())
    if len(aliases) != len(set(aliases)):
        raise ValueError("Search aliases must be unique.")
    for rule in config["rules"]:
        compile_pattern(rule["pattern"], fields)
    if config.get("collision") not in ("rename", "skip", "error"):
        raise ValueError("Choose a valid filename conflict policy.")


class NamingRules:
    def __init__(self, config):
        self.rules = [(compile_pattern(r["pattern"], config["fields"])[0], r.get("scope", ""))
                      for r in config["rules"]]
        self.inherit = config.get("inherit", True)

    def extract(self, folder, root):
        folder, root = Path(folder), Path(root)
        try:
            relative = folder.relative_to(root)
        except ValueError:
            return {}
        folders = [root]
        for part in relative.parts:
            folders.append(folders[-1] / part)
        if not self.inherit:
            folders = folders[-1:]
        result = {}
        for candidate in folders:
            for regex, scope in self.rules:
                if scope and not candidate.is_relative_to(Path(scope)):
                    continue
                match = regex.fullmatch(candidate.name)
                if match:
                    result.update({k: v.strip() for k, v in match.groupdict().items()})
                    break
        return result


def parse_query(text, fields):
    """Support g:Pokemon, g:\"two words\", and the user's Pokemon g syntax."""
    aliases = {alias.casefold(): name for name, alias in fields.items()}
    try:
        words = shlex.split(text, posix=True)
    except ValueError as exc:
        raise ValueError("Close the quotation marks in your search.") from exc
    terms, i = [], 0
    while i < len(words):
        word = words[i]
        prefix, sep, value = word.partition(":")
        if sep and prefix.casefold() in aliases:
            if not value:
                raise ValueError(f"Enter a value after {prefix}:")
            terms.append((aliases[prefix.casefold()], value.casefold()))
        elif i + 1 < len(words) and words[i + 1].casefold() in aliases:
            terms.append((aliases[words[i + 1].casefold()], word.casefold()))
            i += 1
        else:
            terms.append((None, word.casefold()))
        i += 1
    return terms


def matches(entry, terms):
    general = " ".join([entry.name, str(entry.path.parent), *entry.fields.values()]).casefold()
    return all(value in (entry.fields.get(field, "").casefold() if field else general)
               for field, value in terms)
