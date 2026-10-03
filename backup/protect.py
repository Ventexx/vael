#!/usr/bin/env python3
"""Standalone 7z protection tool and optional backup.py companion (API v1).

Passwords are delivered through a private stdin pipe, never argv or environment.
Ordinary 7-Zip can recover every output. No custom cryptography is implemented.
"""
from __future__ import annotations

import argparse
import contextlib
import getpass
import hashlib
import importlib.util
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import subprocess
import sys
import tempfile
import uuid
import warnings

COMPANION_API_VERSION = 1


class ProtectionError(Exception):
    pass


def find_7zip(exe=None):
    if exe:
        found = shutil.which(str(exe))
        if not found:
            raise ProtectionError("The selected 7-Zip executable was not found. Correct --sevenzip PATH.")
        return found
    for candidate in (exe, "7z", "7zz", "7za", "NanaZipC"):
        if candidate:
            found = shutil.which(str(candidate))
            if found:
                return found
    raise ProtectionError("Install the 7-Zip command-line tool, or use --sevenzip PATH.")


def validate_password(password):
    if not password or any(c in password for c in "\r\n\x00"):
        raise ProtectionError("Use a nonempty password without line breaks or NUL characters.")


def prompt_password(confirm=False):
    if not sys.stdin.isatty():
        raise ProtectionError("A terminal is required for hidden password entry; unattended passwords are not supported.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            if confirm:
                print("Use a long, unique password and save it in your password manager. There is no password reset.")
            password = getpass.getpass("Password: ")
            validate_password(password)
            if confirm and len(password) < 12:
                raise ProtectionError("Choose a password of at least 12 characters; a longer generated password is recommended.")
            if confirm and password != getpass.getpass("Confirm password: "):
                raise ProtectionError("Passwords do not match. Nothing was changed.")
        except getpass.GetPassWarning as exc:
            raise ProtectionError("Hidden password input is unavailable in this terminal.") from exc
    return password


class PasswordSession:
    """A short-lived, in-memory credential shared with the backup runner.

    Bare -p requests a password when creating an archive. For existing archives,
    omit -p: some 7-Zip builds interpret bare -p as an empty reading password.
    The archive's encrypted header then triggers the stdin password request.
    """

    def __init__(self, exe, password):
        validate_password(password)
        self.exe = str(exe)
        self._password = password

    def __repr__(self):
        return "PasswordSession(<redacted>)"

    def close(self):
        self._password = None

    def run(self, args, cwd=None):
        if self._password is None:
            raise ProtectionError("Password session is closed.")
        command = list(args)
        if any(a.startswith("-p") for a in command):
            raise ProtectionError("Passwords must not be passed as command-line options.")
        # Backup and this tool always put the archive immediately after the verb,
        # except technical listing, where -slt precedes it.
        archive_args = [a for a in command[1:] if not a.startswith("-")]
        archive = Path(archive_args[0]) if archive_args else None
        if command[0] in ("a", "u", "d", "rn"):
            if "-t7z" not in command:
                command.append("-t7z")
            command.append("-mhe=on")
            if archive is not None and not archive.exists():
                command.append("-p")
        result = run_process(self.exe, command, cwd, self._password)
        # Do not retain a password accidentally echoed by an unsupported archiver.
        result.stderr = result.stderr.replace(self._password, "<redacted>")
        return result


def run_process(exe, args, cwd=None, password=None):
    """Finite stdin avoids hanging on password prompts when no secret is available.

    UTF-8 is explicit on both ends. Suppress progress spam in captured output.
    subprocess.run kills/waits for its child on cancellation before we clean up.
    """
    return subprocess.run(
        [str(exe), *map(str, args), "-sccUTF-8", "-bsp0"], cwd=cwd,
        input=(password + "\n") * 4 if password is not None else "",
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def require_ok(result, operation):
    if result.returncode != 0:
        # No raw child output: it can contain secrets, paths or terminal controls.
        raise ProtectionError(f"{operation} failed (7-Zip code {result.returncode}). Check the password, archive integrity, access and free space. No output was published.")


def listing_blocks(text):
    # Ignore the archive header and parse only member records.
    if "----------" not in text:
        raise ProtectionError("The archiver returned an unsupported file listing.")
    records = []
    block = {}
    for line in text.split("----------", 1)[1].splitlines() + [""]:
        if not line.strip():
            if block:
                records.append(block)
                block = {}
        elif " = " in line:
            key, value = line.split(" = ", 1)
            block[key] = value
    return records


def needs_password(exe, archive):
    if not Path(archive).is_file():
        raise ProtectionError(f"Archive does not exist: {archive}")
    probe = run_process(exe, ["l", "-slt", str(archive)])
    # A failed header read may also mean damage; the subsequent authenticated
    # test distinguishes usable archives without guessing localized messages.
    return probe.returncode != 0 or any(b.get("Encrypted") == "+" for b in listing_blocks(probe.stdout))


def open_session(exe, archive, password=None):
    if not needs_password(exe, archive):
        return None
    session = PasswordSession(exe, password if password is not None else prompt_password())
    try:
        require_ok(session.run(["t", str(archive)]), "Unlock / integrity check")
        return session
    except BaseException:
        session.close()
        raise


def assert_protected(session, archive):
    """Reject plaintext headers and any unencrypted file before publication."""
    probe = run_process(session.exe, ["l", "-slt", str(archive)])
    if probe.returncode == 0:
        raise ProtectionError("File names are not encrypted; refusing to publish this archive.")
    result = session.run(["l", "-slt", str(archive)])
    require_ok(result, "Encrypted listing check")
    for member in listing_blocks(result.stdout):
        if member.get("Folder") != "+" and int(member.get("Size", "0")) > 0:
            if member.get("Encrypted") != "+":
                raise ProtectionError("An archive member is not encrypted; refusing to publish.")


@contextlib.contextmanager
def archive_lock(path):
    """Same sidecar name and OS lock protocol as backup.py."""
    path = Path(path).resolve()
    lock = path.parent / f".{path.name}.lock"
    with open(lock, "a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if lock.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ProtectionError("Archive is busy; retry when the other operation finishes.") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ProtectionError("Archive is busy; retry when the other operation finishes.") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def safe_members(blocks):
    """Fail closed on paths or links that could escape/alias an extraction root."""
    names = set()
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    reserved |= {f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)}
    for block in blocks:
        name = block.get("Path", "").replace("\\", "/")
        parts = name.split("/")
        if (not name or PurePosixPath(name).is_absolute()
                or any(p in ("", ".", "..") or ":" in p or p.endswith((".", " "))
                       or p.split(".")[0].upper() in reserved for p in parts)
                or any(ord(c) < 32 or c in '<>"|?*' for c in name)):
            raise ProtectionError("Archive contains an unsafe or unsupported member path.")
        attributes = block.get("Attributes", "")
        if (any(block.get(key) for key in ("Symbolic Link", "Hard Link", "Reparse"))
                or "l" in attributes or "L" in attributes):
            raise ProtectionError("Archives with links or reparse points cannot be extracted by this tool.")
        key = name.casefold()
        if key in names:
            raise ProtectionError("Archive contains duplicate or case-colliding paths.")
        names.add(key)
    return blocks


def fingerprint(path):
    s = Path(path).stat()
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hashes(root):
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ProtectionError("Extracted links are unsupported.")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = file_hash(path)
        elif path.is_dir():
            result[path.relative_to(root).as_posix() + "/"] = None
        else:
            raise ProtectionError("Extracted special files are unsupported.")
    return result


def _extract(exe, source, destination, session):
    call = session.run if session else lambda args: run_process(exe, args)
    listing = call(["l", "-slt", str(source)])
    require_ok(listing, "Read archive")
    blocks = safe_members(listing_blocks(listing.stdout))
    estimated = sum(int(b.get("Size", "0")) for b in blocks)
    if shutil.disk_usage(destination.parent).free < estimated + 50 * 1024 * 1024:
        raise ProtectionError("Not enough free space to extract the archive.")
    destination.mkdir(mode=0o700)
    require_ok(call(["x", str(source), f"-o{destination}", "-y", "-spd"]), "Extraction")
    # Match exactly what the listing promised, including directories and empties.
    actual = {p.relative_to(destination).as_posix(): p for p in destination.rglob("*")}
    expected = {b["Path"].replace("\\", "/"): b for b in blocks}
    for name, path in actual.items():
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ProtectionError("Extracted links are unsupported.")
        if name not in expected and not path.is_dir():
            raise ProtectionError("Extracted contents do not match the archive listing.")
    for name, block in expected.items():
        path = actual.get(name)
        is_dir = block.get("Folder") == "+" or block.get("Attributes", "").startswith("D")
        if path is None or path.is_dir() != is_dir:
            raise ProtectionError("An archive member was not restored correctly.")
        if not is_dir and path.stat().st_size != int(block.get("Size", "0")):
            raise ProtectionError("An extracted file has an unexpected size.")


def transform(source, output, *, exe=None, password=None, new_password=None,
              mode="encrypt", work_dir=None):
    """Rebuild a 7z archive or protect a standalone file, retaining the input.

    No replace mode: output must be new. Repacking uses local plaintext scratch;
    protected backup create/update use PasswordSession directly and avoid this.
    """
    if mode not in ("encrypt", "decrypt", "password"):
        raise ProtectionError("Unknown transformation.")
    source, output = Path(source).resolve(), Path(output).resolve()
    exe = find_7zip(exe)
    if source == output or output.exists():
        raise ProtectionError("Choose a new output path. Existing files are never overwritten.")
    if not source.is_file() or not output.parent.is_dir():
        raise ProtectionError("The input file and output folder must exist.")
    if output.suffix.lower() != ".7z":
        raise ProtectionError("The output archive must end in .7z.")
    if mode != "decrypt":
        validate_password(new_password)
    stage = output.parent / f".{output.name}.{uuid.uuid4().hex}.new"
    sessions = []
    try:
        with contextlib.ExitStack() as locks:
            for path in sorted((source, output), key=lambda p: str(p).casefold()):
                locks.enter_context(archive_lock(path))
            if output.exists():
                raise ProtectionError("Output already exists.")
            original = fingerprint(source)
            with tempfile.TemporaryDirectory(prefix="vael-protect-", dir=work_dir) as temp:
                root = Path(temp)
                contents = root / "contents"
                with source.open("rb") as stream:
                    is_7z = stream.read(6) == b"7z\xbc\xaf\x27\x1c"
                if is_7z:
                    old = open_session(exe, source, password)
                    if old:
                        sessions.append(old)
                    if mode in ("decrypt", "password") and old is None:
                        raise ProtectionError("This archive is not encrypted.")
                    _extract(exe, source, contents, old)
                else:
                    if mode != "encrypt":
                        raise ProtectionError("Decrypt/change password expects a 7z archive.")
                    contents.mkdir(mode=0o700)
                    safe_members([{"Path": source.name}])
                    shutil.copyfile(source, contents / source.name)
                hashes = tree_hashes(contents)
                new = PasswordSession(exe, new_password) if mode != "decrypt" else None
                if new:
                    sessions.append(new)
                call = new.run if new else lambda args, cwd=None: run_process(exe, args, cwd)
                # Relative ./ names prevent leading '-' or '@' in a filename
                # from being interpreted as an option or listfile.
                names = root / "members.txt"
                names.write_text("".join("./" + p.name + "\n" for p in contents.iterdir()), encoding="utf-8")
                require_ok(call(["a", str(stage), "-t7z", "-mx=5", "-sse", "-spd", "-scsUTF-8", f"-i@{names}"], cwd=contents), "Rebuild archive")
                require_ok(call(["t", str(stage)]), "Output integrity check")
                if new:
                    assert_protected(new, stage)
                # A real extraction and SHA-256 comparison verifies the rebuilt
                # content, rather than trusting only the archiver's CRC test.
                checked = root / "checked"
                _extract(exe, stage, checked, new)
                if tree_hashes(checked) != hashes:
                    raise ProtectionError("Rebuilt file contents differ from the input.")
            # Finish plaintext cleanup before publication, so a cleanup failure
            # cannot be reported as a failed operation with a published output.
            if fingerprint(source) != original:
                raise ProtectionError("Input changed during the operation. Retry when it is idle.")
            if output.exists():
                raise ProtectionError("Output appeared during the operation; refusing to overwrite it.")
            # Windows rename refuses an existing destination (also works on
            # exFAT). POSIX link gives atomic, no-clobber publication.
            if os.name == "nt":
                os.rename(stage, output)
            else:
                os.link(stage, output)
        return output
    finally:
        for session in sessions:
            session.close()
        with contextlib.suppress(OSError):
            stage.unlink()


def restore(source, output, *, exe=None, password=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    exe = find_7zip(exe)
    if output.exists() or not output.parent.is_dir():
        raise ProtectionError("Choose a new restore folder inside an existing directory.")
    with archive_lock(source), archive_lock(output):
        original = fingerprint(source)
        session = open_session(exe, source, password)
        try:
            # Extraction scratch is intentionally beside the destination: these
            # are the plaintext files the user explicitly requested there.
            with tempfile.TemporaryDirectory(prefix=".restore-", dir=output.parent) as temp:
                stage = Path(temp) / "files"
                _extract(exe, source, stage, session)
                if fingerprint(source) != original:
                    raise ProtectionError("Archive changed during restoration.")
                if output.exists():
                    raise ProtectionError("Restore destination already exists.")
                # Reserve rather than merge into any existing directory.
                output.mkdir(mode=0o700)
                try:
                    for child in stage.iterdir():
                        shutil.move(str(child), str(output / child.name))
                except BaseException:
                    # Do not delete potentially useful partial restored files.
                    raise ProtectionError(f"Restore could not finish. Partial files may be in {output}; the archive is unchanged.")
            return output
        finally:
            if session:
                session.close()


def load_backup():
    path = Path(__file__).resolve().with_name("backup.py")
    if not path.is_file():
        raise ProtectionError("Place the compatible backup.py beside protect.py to use this action.")
    spec = importlib.util.spec_from_file_location("_vael_backup_companion", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if getattr(module, "COMPANION_API_VERSION", None) != COMPANION_API_VERSION:
        raise ProtectionError("The backup companion is incompatible. Update both scripts together.")
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description="Protect files and 7z archives; recover with this tool or ordinary 7-Zip.")
    actions = parser.add_mutually_exclusive_group()
    for action in ("encrypt", "decrypt", "restore", "change-password", "verify", "update-backup"):
        actions.add_argument("--" + action, metavar="FILE")
    actions.add_argument("--new-backup", action="store_true")
    actions.add_argument("--generate-password", action="store_true")
    parser.add_argument("--output", type=Path, help="New archive path, or new folder for --restore; never overwrites.")
    parser.add_argument("--sevenzip", help="7-Zip executable.")
    parser.add_argument("--history", type=Path, help="History location for companion backup actions only.")
    parser.add_argument("--work-dir", type=Path, help="Existing private LOCAL folder for plaintext conversion scratch.")
    parser.add_argument("--accept-config-changes", action="store_true", help="For --update-backup only.")
    args = parser.parse_args(argv)
    try:
        explicit_action = any(getattr(args, key) for key in (
            "encrypt", "decrypt", "restore", "change_password", "verify",
            "new_backup", "update_backup", "generate_password"))
        if args.accept_config_changes and not args.update_backup:
            parser.error("--accept-config-changes requires --update-backup")
        if args.history and explicit_action and not (args.new_backup or args.update_backup):
            parser.error("--history is for backup companion actions only")
        if args.work_dir and explicit_action and not (args.encrypt or args.decrypt or args.change_password):
            parser.error("--work-dir is for archive conversion actions only")
        if args.output and (args.verify or args.generate_password or args.update_backup):
            parser.error("--output is not valid for this action")
        backup_options = []
        for option in ("sevenzip", "history"):
            if getattr(args, option):
                backup_options += ["--" + option, str(getattr(args, option))]
        if args.generate_password:
            print("Save this generated password in your password manager. It is not saved by this tool:")
            print(secrets.token_urlsafe(24))
            return 0
        if args.new_backup or args.update_backup:
            backup = load_backup()
            options = ["--new", "--encrypt"] if args.new_backup else ["--update", args.update_backup]
            if args.output and args.new_backup:
                options += ["--output", str(args.output)]
            elif args.output:
                raise ProtectionError("--output is only for --new-backup, not --update-backup.")
            options += backup_options
            if args.accept_config_changes:
                options.append("--accept-config-changes")
            return backup.main(options)
        action = next((key for key in ("encrypt", "decrypt", "restore", "change_password", "verify") if getattr(args, key)), None)
        if action is None:
            print("Protect\n[1] Encrypt a file / archive\n[2] Restore files\n[3] Export unencrypted archive\n[4] Change password\n[5] Verify archive")
            if Path(__file__).with_name("backup.py").is_file():
                print("[6] Create protected backup\n[7] Update backup")
            print("[Q] Quit")
            choice = input("> ").strip().lower()
            if choice in ("q", ""):
                return 0
            if choice == "6":
                options = ["--new", "--encrypt", *backup_options]
                if args.output:
                    options += ["--output", str(args.output)]
                return load_backup().main(options, interactive=True)
            if choice == "7":
                archive = input("Archive path: ").strip().strip('"')
                if not archive:
                    raise ProtectionError("An archive path is required.")
                return load_backup().main(["--update", archive, *backup_options], interactive=True)
            action = {"1": "encrypt", "2": "restore", "3": "decrypt", "4": "change_password", "5": "verify"}.get(choice)
            if action is None:
                raise ProtectionError("Unknown menu choice.")
            source = input("Input file: ").strip().strip('"')
            if action != "verify":
                output = input("New output path: ").strip().strip('"')
                if not output:
                    raise ProtectionError("An output path is required.")
                args.output = Path(output)
        else:
            source = getattr(args, action)
        if not source:
            raise ProtectionError("An input path is required.")
        exe = find_7zip(args.sevenzip)
        source = Path(source).resolve()
        if action == "verify":
            with archive_lock(source):
                original = fingerprint(source)
                session = open_session(exe, source)
                try:
                    result = session.run(["t", str(source)]) if session else run_process(exe, ["t", str(source)])
                    require_ok(result, "Integrity check")
                    if session:
                        assert_protected(session, source)
                    if fingerprint(source) != original:
                        raise ProtectionError("Archive changed during verification.")
                    print("Archive verified" + ("; contents and file names are encrypted." if session else "; it is NOT encrypted."))
                finally:
                    if session:
                        session.close()
            return 0
        if args.output is None:
            raise ProtectionError("Provide --output with a new destination path.")
        if action == "restore":
            result = restore(source, args.output, exe=exe)
        else:
            if action == "decrypt":
                print("The output will be UNENCRYPTED. Choose a private destination.")
            print("Conversion uses temporary plaintext files in the local work folder. The input will be retained.")
            with source.open("rb") as stream:
                is_7z = stream.read(6) == b"7z\xbc\xaf\x27\x1c"
            old_password = prompt_password() if is_7z and needs_password(exe, source) else None
            new_password = None
            if action != "decrypt":
                print("Choose the password for the output:")
                new_password = prompt_password(confirm=True)
            result = transform(source, args.output, exe=exe, password=old_password,
                               new_password=new_password, mode="password" if action == "change_password" else action,
                               work_dir=args.work_dir)
        print(f"Saved and verified: {result}\nInput retained: {source}")
        return 0
    except (ProtectionError, OSError, ValueError) as exc:
        print(f"Protection operation failed: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Cancelled. Input files were retained.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
