from pathlib import Path

import pytest

from backup import (
    BackupItem,
    ConfigError,
    validate_configuration,
    validate_logical_name,
)


def item(name, path):
    return BackupItem(name, Path(path))


# -- validate_logical_name -------------------------------------------------


def test_empty_name_rejected():
    with pytest.raises(ConfigError, match="empty"):
        validate_logical_name("", ["", "Other"])


def test_dot_and_dotdot_rejected():
    for bad in (".", ".."):
        with pytest.raises(ConfigError):
            validate_logical_name(bad, [bad])


def test_absolute_style_name_rejected():
    with pytest.raises(ConfigError, match="absolute"):
        validate_logical_name("/etc", ["/etc"])
    with pytest.raises(ConfigError, match="absolute"):
        validate_logical_name("\\Windows", ["\\Windows"])


def test_drive_letter_rejected():
    with pytest.raises(ConfigError, match="drive letter"):
        validate_logical_name("C:Data", ["C:Data"])


def test_dotdot_inside_name_rejected():
    with pytest.raises(ConfigError, match="\\.\\."):
        validate_logical_name("Data/../Escape", ["Data/../Escape"])


def test_manifest_name_collision_rejected():
    with pytest.raises(ConfigError, match="reserved metadata"):
        validate_logical_name("manifest.json", ["manifest.json"])


def test_duplicate_name_rejected():
    names = ["Documents", "Documents"]
    with pytest.raises(ConfigError, match="more than once"):
        validate_logical_name("Documents", names)


def test_prefix_collision_rejected():
    names = ["Data", "Data/Sub"]
    with pytest.raises(ConfigError, match="collide"):
        validate_logical_name("Data", names)
    with pytest.raises(ConfigError, match="collide"):
        validate_logical_name("Data/Sub", names)


def test_sibling_names_with_shared_prefix_allowed():
    # "Data" vs "Data2" must NOT be treated as a prefix collision.
    names = ["Data", "Data2"]
    validate_logical_name("Data", names)
    validate_logical_name("Data2", names)


def test_unrelated_names_allowed():
    names = ["Documents", "Projects", "Photos"]
    for n in names:
        validate_logical_name(n, names)


# -- validate_configuration -------------------------------------------------


def test_empty_items_rejected(tmp_path):
    with pytest.raises(ConfigError, match="empty"):
        validate_configuration([])


def test_valid_configuration_passes(tmp_path):
    a = tmp_path / "docs"
    b = tmp_path / "proj"
    validate_configuration([item("Documents", a), item("Projects", b)])


def test_relative_path_rejected(tmp_path):
    with pytest.raises(ConfigError, match="absolute"):
        validate_configuration([item("Documents", "relative/dir")])


def test_mixed_absolute_and_relative_reports_relative_only(tmp_path):
    with pytest.raises(ConfigError, match="Documents"):
        validate_configuration([
            item("Documents", "relative/dir"),
            item("Projects", tmp_path / "proj"),
        ])


def test_same_physical_path_rejected(tmp_path):
    shared = tmp_path / "shared"
    with pytest.raises(ConfigError, match="same"):
        validate_configuration([item("A", shared), item("B", shared)])


def test_nested_physical_path_rejected(tmp_path):
    parent = tmp_path / "parent"
    child = parent / "child"
    with pytest.raises(ConfigError, match="nested"):
        validate_configuration([item("Parent", parent), item("Child", child)])
    # order shouldn't matter
    with pytest.raises(ConfigError, match="nested"):
        validate_configuration([item("Child", child), item("Parent", parent)])


def test_non_overlapping_absolute_paths_pass(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "ab"  # shares a string prefix but is NOT nested (different path component)
    validate_configuration([item("A", a), item("AB", b)])
