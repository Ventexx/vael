"""
Roadmap 1.6 — permission and locked-file handling audit.

These tests need to run as a genuinely unprivileged OS user, because a
process running as root bypasses filesystem permission bits entirely
(os.chmod(0o000) has no effect on what root can read). This test suite is
skipped unless it detects it's running as root AND a passwordless
`testuser` exists with `runuser` available to re-exec as that user — the
setup this environment's CI/dev-container is expected to provide. See
RUNBOOK.md's "Verifying this test suite's environment assumptions"
section if this is skipping and you expected it to run.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

RUNUSER = shutil.which("runuser")
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
TESTUSER_EXISTS = shutil.which("id") and subprocess.run(
    ["id", "testuser"], capture_output=True
).returncode == 0

requires_root_and_testuser = pytest.mark.skipif(
    not (IS_ROOT and RUNUSER and TESTUSER_EXISTS),
    reason="needs to run as root with a 'testuser' account and runuser available "
           "to exercise real permission-denied behavior (root bypasses permission bits)",
)

# A tiny driver script, executed as `testuser` in a fresh Python process,
# that imports backup.py, scans the given root, and prints machine-
# readable PASS/FAIL/ERROR so the parent (root) test process can assert
# on the *unprivileged* process's actual behavior.
_DRIVER = """
import sys
sys.path.insert(0, {backup_dir!r})
from pathlib import Path
from backup import BackupItem, SourceInventoryManager, BackupError

item = BackupItem("Root", Path({root!r}))
mgr = SourceInventoryManager()
try:
    inv = mgr.scan([item])
    print("SCANNED", len(inv.entries), len(inv.skipped))
except BackupError as exc:
    print("BACKUPERROR", str(exc))
"""


def _run_as_testuser(script_text):
    return subprocess.run(
        ["runuser", "-u", "testuser", "--", sys.executable, "-c", script_text],
        capture_output=True, text=True,
    )


@pytest.fixture
def world_traversable_dir():
    """pytest's own tmp_path lives under `pytest-of-<user>/pytest-N/`,
    which is created drwx------ — an unprivileged testuser can't traverse
    into it at all, which would make every test below fail with a
    Permission denied that has nothing to do with what's being tested.
    Create a directly-under-/tmp directory instead (/tmp itself is the
    standard drwxrwxrwt sticky-bit dir), and clean it up afterward.
    """
    d = Path(tempfile.mkdtemp(prefix="backup_perm_test_"))
    os.chmod(d, 0o777)
    try:
        yield d
    finally:
        # Directories we deliberately locked down (0o000) need their
        # permissions restored before shutil.rmtree can remove them.
        for root, dirs, files in os.walk(d):
            for name in dirs:
                os.chmod(Path(root) / name, 0o755)
        shutil.rmtree(d, ignore_errors=True)


@requires_root_and_testuser
def test_permission_denied_on_required_root_raises_backup_error(world_traversable_dir):
    """A required top-level BackupItem.path that testuser cannot read at
    all must be treated as 'source unavailable' (BackupError), not
    silently scanned as an empty directory.
    """
    base = world_traversable_dir
    locked = base / "locked_root"
    locked.mkdir()
    (locked / "secret.txt").write_text("should never be readable by testuser")
    os.chmod(locked, 0o000)  # testuser: cannot even list this directory

    import backup

    script = _DRIVER.format(backup_dir=str(Path(backup.__file__).resolve().parent), root=str(locked))
    proc = _run_as_testuser(script)

    os.chmod(locked, 0o755)  # restore so fixture cleanup can remove it

    assert "BACKUPERROR" in proc.stdout, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "unavailable" in proc.stdout


@requires_root_and_testuser
def test_permission_denied_on_nested_subdir_is_a_partial_skip_not_fatal(world_traversable_dir):
    """A permission error on a NESTED subdirectory (not the configured
    root itself) should be recorded as a SkippedEntry and the scan should
    still complete for the rest of the tree — this is the existing,
    correct 'partial skip' behavior the roadmap asked to have confirmed
    against a real permission-denied directory, not just a missing one.
    """
    base = world_traversable_dir
    root = base / "partial_root"
    root.mkdir()
    (root / "readable.txt").write_text("fine")
    blocked = root / "blocked_subdir"
    blocked.mkdir()
    (blocked / "hidden.txt").write_text("unreachable")
    os.chmod(blocked, 0o000)

    import backup

    script = _DRIVER.format(backup_dir=str(Path(backup.__file__).resolve().parent), root=str(root))
    proc = _run_as_testuser(script)

    os.chmod(blocked, 0o755)

    assert "SCANNED" in proc.stdout, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    _, n_entries, n_skipped = proc.stdout.split()
    assert int(n_entries) >= 1  # root + readable.txt at least
    assert int(n_skipped) >= 1  # blocked_subdir recorded as skipped, not fatal
