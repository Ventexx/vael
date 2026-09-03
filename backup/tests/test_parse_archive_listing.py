from backup import _parse_7z_timestamp_to_ns, parse_archive_listing

# Captured-shape sample of real `7z l -slt` output: one archive-level
# metadata block (Path = the archive itself) before the dashed separator,
# then one block per member. This mirrors what SEVENZIP actually emits,
# not a synthetic shorthand, so the "skip the archive's own header" logic
# is exercised the same way it would be against the real binary.
SAMPLE_SLT_OUTPUT = """
7-Zip 23.01 (x64) : Copyright (c) 1999-2023 Igor Pavlov : 2023-06-20

Scanning the drive for archives:
1 file, 2048 bytes (2 KiB)

Listing archive: Backup.7z

--
Path = Backup.7z
Type = 7z
Physical Size = 2048
Headers Size = 200
Method = LZMA2:24
Solid = -
Blocks = 1

----------
Path = Documents
Folder = +
Size = 0
Packed Size = 0
Modified = 2026-08-01 10:00:00.0000000
Attributes = D
CRC = 
Encrypted = -
Method = 
Block = 

Path = Documents/report.txt
Folder = -
Size = 1234
Packed Size = 512
Modified = 2026-08-01 10:05:30.1234567
Attributes = A
CRC = A1B2C3D4
Encrypted = -
Method = LZMA2:24
Block = 0

Path = Documents\\Sub\\nested.txt
Folder = -
Size = 10
Packed Size = 10
Modified = 2026-08-02 00:00:00.0000000
Attributes = A
CRC = 00000000
Encrypted = -
Method = LZMA2:24
Block = 0
"""


def test_archive_own_header_is_skipped():
    entries = parse_archive_listing(SAMPLE_SLT_OUTPUT)
    assert "Backup.7z" not in entries


def test_directory_member_parsed_as_dir():
    entries = parse_archive_listing(SAMPLE_SLT_OUTPUT)
    assert entries["Documents"].is_dir is True
    assert entries["Documents"].size == 0


def test_file_member_parsed_with_size_and_crc():
    entries = parse_archive_listing(SAMPLE_SLT_OUTPUT)
    e = entries["Documents/report.txt"]
    assert e.is_dir is False
    assert e.size == 1234
    assert e.crc == "A1B2C3D4"
    assert e.mtime_ns > 0


def test_windows_backslash_paths_normalized_to_forward_slash():
    entries = parse_archive_listing(SAMPLE_SLT_OUTPUT)
    assert "Documents/Sub/nested.txt" in entries
    assert not any("\\" in k for k in entries)


def test_empty_output_returns_empty_dict():
    assert parse_archive_listing("") == {}


def test_output_with_no_separator_returns_empty_dict():
    # If the dashed separator never appears, nothing after it should be
    # parsed as a member — we should not fall back to parsing the header.
    text = "Path = Backup.7z\nType = 7z\n"
    assert parse_archive_listing(text) == {}


def test_missing_path_in_block_is_skipped():
    text = (
        "----------\n"
        "Folder = -\n"
        "Size = 5\n"
        "\n"
        "Path = real.txt\n"
        "Folder = -\n"
        "Size = 5\n"
    )
    entries = parse_archive_listing(text)
    assert list(entries.keys()) == ["real.txt"]


# -- timestamp parsing -------------------------------------------------


def test_timestamp_with_fractional_seconds():
    ns = _parse_7z_timestamp_to_ns("2026-08-01 10:05:30.1234567")
    assert ns > 0
    # Fractional part should contribute sub-second precision distinct
    # from the same timestamp with all-zero fraction.
    ns_zero_frac = _parse_7z_timestamp_to_ns("2026-08-01 10:05:30.0000000")
    assert ns != ns_zero_frac


def test_timestamp_without_fractional_seconds():
    ns = _parse_7z_timestamp_to_ns("2026-08-01 10:05:30")
    assert ns > 0


def test_timestamp_malformed_returns_zero():
    assert _parse_7z_timestamp_to_ns("not-a-timestamp") == 0
    assert _parse_7z_timestamp_to_ns("") == 0
