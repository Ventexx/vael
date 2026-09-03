from pathlib import Path

from backup import BackupItem, ManifestManager


def mgr():
    return ManifestManager()


def old_items(*pairs):
    return [{"name": n, "source": s} for n, s in pairs]


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
    assert plan["renamed"] == [{"old_name": "Documents", "new_name": "MyDocs", "source": "/d"}]
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
    assert plan["renamed"] == [{"old_name": "Documents", "new_name": "MyDocs", "source": "/d"}]
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
    assert plan["renamed"] == [{"old_name": "B", "new_name": "B2", "source": "/b"}]
    assert plan["removed"] == ["A"]
    assert plan["added"] == ["A2"]
