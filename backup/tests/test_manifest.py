from pathlib import Path

from backup import BackupItem, ManifestManager


def mgr():
    return ManifestManager()


def test_concurrent_archives_use_independent_manifest_files(tmp_path):
    import json
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from backup import ArchiveManager

    barrier = threading.Barrier(2)
    class Runner:
        def add_files(self, archive, files, cwd, compression_level):
            barrier.wait(timeout=5)
            return json.loads((cwd / "manifest.json").read_text(encoding="utf-8"))["backup_uuid"]

    manager = ArchiveManager(Runner())
    manifests = [mgr().create([]), mgr().create([])]
    scratch = tmp_path / "scratch"
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(manager.write_manifest, tmp_path / f"{i}.7z", manifest, scratch)
                for i, manifest in enumerate(manifests)]
        assert [job.result() for job in jobs] == [m.backup_uuid for m in manifests]
    assert list(scratch.iterdir()) == []


def old_items(*pairs):
    # Simulates what a previously-written manifest.json would contain:
    # the *stringified* Path, not a raw literal. The real code always
    # builds this via str(BackupItem.path), which on Windows renders
    # with backslashes (str(Path("/d")) == "\\d"), not the forward-slash
    # form. Using a raw literal here worked by accident on Linux/macOS
    # and produced false path-changed mismatches on Windows.
    return [{"name": n, "source": str(Path(s))} for n, s in pairs]


def new_items(*pairs):
    return [BackupItem(n, Path(s)) for n, s in pairs]


def test_no_changes():
    old = old_items(("Documents", "/d"), ("Projects", "/p"))
    new = new_items(("Documents", "/d"), ("Projects", "/p"))
    plan = mgr().detect_config_changes(old, new)
    assert plan == {
        "removed": [],
        "added": [],
        "renamed": [],
        "path_changed": [],
        "has_changes": False,
    }


def test_added_item():
    old = old_items(("Documents", "/d"))
    new = new_items(("Documents", "/d"), ("Photos", "/ph"))
    plan = mgr().detect_config_changes(old, new)
    assert plan["added"] == ["Photos"]
    assert plan["removed"] == []
    assert plan["has_changes"] is True


def test_removed_item():
    old = old_items(("Documents", "/d"), ("Photos", "/ph"))
    new = new_items(("Documents", "/d"))
    plan = mgr().detect_config_changes(old, new)
    assert plan["removed"] == ["Photos"]
    assert plan["added"] == []
    assert plan["has_changes"] is True


def test_renamed_item_same_source():
    old = old_items(("Documents", "/d"))
    new = new_items(("MyDocs", "/d"))
    plan = mgr().detect_config_changes(old, new)
    assert plan["renamed"] == [{"old_name": "Documents", "new_name": "MyDocs", "source": str(Path("/d"))}]
    assert plan["removed"] == []
    assert plan["added"] == []
    assert plan["has_changes"] is True


def test_path_changed_same_name():
    old = old_items(("Documents", "/old/d"))
    new = new_items(("Documents", "/new/d"))
    plan = mgr().detect_config_changes(old, new)
    assert plan["path_changed"] == ["Documents"]
    assert plan["has_changes"] is True


def test_multiple_simultaneous_changes():
    old = old_items(("Documents", "/d"), ("Projects", "/p"), ("Old", "/old"))
    new = new_items(("MyDocs", "/d"), ("Projects", "/new_p"), ("New", "/new"))
    plan = mgr().detect_config_changes(old, new)
    assert plan["renamed"] == [{"old_name": "Documents", "new_name": "MyDocs", "source": str(Path("/d"))}]
    assert plan["path_changed"] == ["Projects"]
    assert plan["removed"] == ["Old"]
    assert plan["added"] == ["New"]
    assert plan["has_changes"] is True


def test_rename_prefers_exact_source_match_not_first_added():
    # Two names removed, two added, but only one pairing shares a source
    # path — that pairing (and only that one) should be classified as a
    # rename; the rest fall through to plain removed/added.
    old = old_items(("A", "/a"), ("B", "/b"))
    new = new_items(("A2", "/other"), ("B2", "/b"))
    plan = mgr().detect_config_changes(old, new)
    assert plan["renamed"] == [{"old_name": "B", "new_name": "B2", "source": str(Path("/b"))}]
    assert plan["removed"] == ["A"]
    assert plan["added"] == ["A2"]
