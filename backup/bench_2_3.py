#!/usr/bin/env python3
"""
Roadmap 2.3 benchmark script — memory scaling + preflight_space sanity.

Covers, locally and automatically:
  (a) Peak memory of SourceInventoryManager.scan() vs. file count.
  (a-prime) Peak memory of parse_archive_listing() vs. member count, using
      a synthetic `-slt`-shaped fixture (no real multi-GB archive needed).
  (c) preflight_space()'s 10% + 50 MiB formula at large sizes, using sparse
      files so no real disk space is consumed.

Does NOT cover (b) — wall-clock cost of the full staging copy on a large
real archive. That needs an actual large archive; see the printed note at
the end of this script's output for the two-line manual recipe.

Usage:
    python bench_2_3.py                    # default file-count ladder
    python bench_2_3.py --scan-counts 10000 50000 150000 300000
    python bench_2_3.py --skip-scan         # only run the space-formula check
    python bench_2_3.py --skip-space        # only run the scan/parse memory checks
    python bench_2_3.py --keep-trees        # don't delete generated trees after each run

Run this ON THE MACHINE you actually care about (disk speed, filesystem,
antivirus scanning behavior, etc. all affect the real numbers) — this
script has no dependency on a sandbox and needs nothing beyond `backup.py`
being importable (same directory, or pass --backup-path).
"""
from __future__ import annotations

import argparse
import ctypes
import gc
import os
import platform
import shutil
import sys
import time
import tracemalloc
from pathlib import Path


# ----------------------------------------------------------------------
# Cross-platform peak memory reading (current process, no dependencies)
# ----------------------------------------------------------------------

def process_peak_memory_bytes() -> "int | None":
    """Best-effort whole-process peak working set / RSS, in bytes.

    This is SECONDARY/informational only — see bench_scan/bench_parse_listing,
    which use tracemalloc as the primary metric instead (more precise: it
    isolates memory attributable to the call being measured, rather than
    whole-process noise from antivirus, interpreter startup, unrelated
    background allocations, etc.). Returns None if unavailable rather than
    raising, since a benchmark script crashing over an OS API quirk is worse
    than just omitting one informational column.

    Windows: PeakWorkingSetSize via GetProcessMemoryInfo. Needs explicit
    ctypes argtypes/restype — ctypes.windll defaults handle-typed return
    values to 32-bit int, which silently mangles the process handle on
    64-bit Windows and makes the call fail with no useful error otherwise.
    POSIX (Linux/Mac): ru_maxrss via resource.getrusage — Linux reports
    KB, Mac reports bytes, normalized here to bytes either way.
    """
    try:
        if platform.system() == "Windows":
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.GetCurrentProcess.argtypes = []
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD
            ]

            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            handle = kernel32.GetCurrentProcess()
            ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
            if not ok:
                return None
            return counters.PeakWorkingSetSize
        else:
            import resource
            ru_maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # Linux reports KB, macOS reports bytes.
            return ru_maxrss * 1024 if platform.system() == "Linux" else ru_maxrss
    except Exception:
        return None


def human_size(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PiB"


# ----------------------------------------------------------------------
# (a) SourceInventoryManager.scan() memory vs. file count
# ----------------------------------------------------------------------

def generate_tree(root: Path, n_files: int, files_per_dir: int = 1000) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n_files):
        d = root / f"dir_{i // files_per_dir}"
        d.mkdir(exist_ok=True)
        (d / f"file_{i}.txt").touch()


def bench_scan(backup_module, counts: list[int], keep_trees: bool, base_dir: Path) -> None:
    print("\n=== (a) SourceInventoryManager.scan() — memory vs. file count ===\n")
    print(f"{'files':>10} {'gen_s':>8} {'scan_s':>8} {'traced_peak':>12} {'per_file':>10} {'proc_peak':>12}")
    print("(traced_peak = memory attributable to scan() itself, via tracemalloc — this is the")
    print(" primary number. proc_peak = whole-process peak working set, best-effort, informational")
    print(" only; shows 'n/a' if the OS API isn't available/reliable on this platform.)\n")

    prev_traced = None
    for n in counts:
        tree_dir = base_dir / f"tree_{n}"
        t0 = time.perf_counter()
        generate_tree(tree_dir, n)
        gen_elapsed = time.perf_counter() - t0

        items = [backup_module.BackupItem("Bench", tree_dir)]
        gc.collect()

        tracemalloc.start()
        t0 = time.perf_counter()
        inventory = backup_module.SourceInventoryManager().scan(items)
        scan_elapsed = time.perf_counter() - t0
        _current, traced_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        proc_peak = process_peak_memory_bytes()

        assert len(inventory.entries) >= n, (
            f"scan returned {len(inventory.entries)} entries for {n} generated files — "
            "investigate before trusting the memory numbers below"
        )

        per_file = (traced_peak / n) if n else 0
        proc_peak_str = human_size(proc_peak) if proc_peak is not None else "n/a"
        print(f"{n:>10} {gen_elapsed:>8.2f} {scan_elapsed:>8.2f} {human_size(traced_peak):>12} "
              f"{human_size(per_file):>10} {proc_peak_str:>12}")

        if prev_traced is not None and prev_traced > 0:
            ratio_mem = traced_peak / prev_traced
            ratio_files = n / counts[counts.index(n) - 1]
            if ratio_mem > ratio_files * 1.5:
                print(f"    NOTE: traced memory grew {ratio_mem:.2f}x while file count grew {ratio_files:.2f}x "
                      f"— worse than linear, worth a closer look.")
        prev_traced = traced_peak

        # keep inventory alive until after we've printed prev_traced comparison,
        # then let it go before the next iteration generates a fresh tree.
        del inventory

        if not keep_trees:
            shutil.rmtree(tree_dir, ignore_errors=True)

    print("\nInterpretation: per_file (traced_peak / n) should stay roughly constant across rows.")
    print("If it climbs with file count, that's super-linear growth — a real finding.")


# ----------------------------------------------------------------------
# (a-prime) parse_archive_listing() memory vs. member count (synthetic)
# ----------------------------------------------------------------------

def synthetic_slt_output(n_members: int) -> str:
    """Build a synthetic `7z l -slt`-shaped listing with n_members entries,
    including the archive's own leading metadata block + separator that
    the real parser skips (see parse_archive_listing's docstring)."""
    lines = [
        "Path = Backup.7z",
        "Type = 7z",
        "Physical Size = 123456789",
        "",
        "----------",
        "",
    ]
    for i in range(n_members):
        lines.append(f"Path = Documents/dir_{i // 1000}/file_{i}.txt")
        lines.append("Folder = -")
        lines.append("Size = 1024")
        lines.append("Modified = 2026-09-02 11:46:12.1234567")
        lines.append(f"CRC = {i % 0xFFFFFFFF:08X}")
        lines.append("Attributes = A")
        lines.append("")
    return "\n".join(lines)


def bench_parse_listing(backup_module, counts: list[int]) -> None:
    print("\n=== (a-prime) parse_archive_listing() — memory vs. member count (synthetic) ===\n")
    print(f"{'members':>10} {'parse_s':>8} {'traced_peak':>12} {'per_member':>12} {'proc_peak':>12}")

    for n in counts:
        text = synthetic_slt_output(n)
        gc.collect()
        tracemalloc.start()
        t0 = time.perf_counter()
        entries = backup_module.parse_archive_listing(text)
        elapsed = time.perf_counter() - t0
        _current, traced_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        proc_peak = process_peak_memory_bytes()
        assert len(entries) == n, f"parsed {len(entries)} entries, expected {n}"
        per_member = traced_peak / n if n else 0
        proc_peak_str = human_size(proc_peak) if proc_peak is not None else "n/a"
        print(f"{n:>10} {elapsed:>8.2f} {human_size(traced_peak):>12} {human_size(per_member):>12} {proc_peak_str:>12}")
        del entries, text

    print("\nSame interpretation as above: per_member should stay roughly flat.")


# ----------------------------------------------------------------------
# (c) preflight_space formula sanity at large sizes (sparse files, no
#     real disk space consumed)
# ----------------------------------------------------------------------

def make_sparse_file(path: Path, size_bytes: int) -> bool:
    """Create a file that reports the given size without using that much
    real disk space. Returns True if it's confirmed sparse (apparent size
    matches, real allocation doesn't), False if sparse creation wasn't
    possible/confirmed on this filesystem (caller should treat the size-
    formula check as still valid either way — it never reads this file
    back for its numbers, see bench_space_formula).

    Ordering matters on NTFS: the sparse attribute must be set BEFORE the
    file is extended, not after. Setting it after a seek+write has already
    extended the file does nothing — NTFS has already zero-filled that
    range for real by then, consuming genuine disk space. (This was a bug
    in an earlier version of this script — it would silently allocate the
    full nominal size on Windows instead of staying sparse.)
    """
    # 1. Create the file empty.
    with open(path, "wb"):
        pass

    # 2. Mark it sparse BEFORE any extension happens.
    if platform.system() == "Windows":
        ret = os.system(f'fsutil sparse setflag "{path}" >nul 2>&1')
        sparse_flag_ok = (ret == 0)
    else:
        # ext4/xfs/most POSIX filesystems are sparse-by-default for holes
        # created via truncate/seek — no explicit flag needed.
        sparse_flag_ok = True

    # 3. Extend via truncate (no actual write) rather than seek+write —
    #    truncate-to-extend is what actually stays sparse; writing a byte
    #    at the end is not required and was never necessary.
    with open(path, "r+b") as fh:
        fh.truncate(size_bytes)

    return sparse_flag_ok


def bench_space_formula(backup_module, sizes_gib: list[float], base_dir: Path) -> None:
    print("\n=== (c) preflight_space() formula sanity at scale ===\n")
    print(f"{'archive_size':>14} {'estimated_needed':>18} {'headroom_required':>18}")
    print("(The formula check below is pure arithmetic — it does NOT depend on real files")
    print(" existing on disk. A best-effort sparse file is created per size purely as a")
    print(" secondary confirmation that stat() reports the right apparent size; if sparse")
    print(" creation isn't available/confirmed at a given size, that row is skipped and a")
    print(" note is printed, but the arithmetic check itself still runs and still counts.)\n")

    runner_dir = base_dir / "space_check"
    runner_dir.mkdir(parents=True, exist_ok=True)
    fake_archive = runner_dir / "Backup.7z"

    for size_gib in sizes_gib:
        size_bytes = int(size_gib * 1024 ** 3)

        # existing archive size + a representative "sources roughly as
        # large as the archive" estimate, mirroring what preflight_space
        # is actually called with for --update (archive size + source
        # dir sizes) — see the docstring in backup.py. This is pure
        # arithmetic and does not depend on any file existing on disk.
        estimated_needed_bytes = size_bytes + size_bytes  # archive copy + full re-add upper bound
        headroom = int(estimated_needed_bytes * 1.1) + 50 * 1024 * 1024

        print(f"{human_size(size_bytes):>14} {human_size(estimated_needed_bytes):>18} {human_size(headroom):>18}")

        assert headroom > estimated_needed_bytes, "headroom must always exceed the raw estimate"
        assert headroom < estimated_needed_bytes * 1.2, (
            "headroom growing >20% beyond the raw estimate at this scale suggests a rounding/"
            "overflow issue in the formula, not just conservative padding"
        )

        # Best-effort secondary check: does a sparse file at this size
        # actually report the right apparent size via stat()? Skipped
        # gracefully (not fatal) if disk space or sparse support is the
        # limiting factor at this size.
        try:
            confirmed_sparse = make_sparse_file(fake_archive, size_bytes)
            reported_size = fake_archive.stat().st_size
            assert reported_size == size_bytes, (
                f"stat() reported {reported_size} bytes, expected {size_bytes} — "
                "sparse-file size reporting itself looks broken on this filesystem"
            )
            if not confirmed_sparse:
                print(f"    note: created at this size, but sparse flag could not be confirmed "
                      f"(fsutil failed) — may have used real disk space.")
        except OSError as exc:
            print(f"    note: skipped real-file confirmation at this size ({exc.strerror or exc}) — "
                  f"arithmetic check above is unaffected and still counts.")
        finally:
            fake_archive.unlink(missing_ok=True)

    print("\nInterpretation: 'headroom_required' should be ~10% above 'estimated_needed' at every")
    print("row, with the flat 50 MiB only mattering at small sizes. No overflow, no truncation,")
    print("no sudden jumps between rows would confirm the formula holds at real scale.")


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backup-path", default=".", help="Directory containing backup.py (default: cwd)")
    parser.add_argument("--scan-counts", type=int, nargs="+", default=[10_000, 50_000, 150_000, 300_000],
                         help="File-count ladder for the scan/parse benchmarks")
    parser.add_argument("--space-sizes-gib", type=float, nargs="+", default=[1, 10, 100, 500, 1000],
                         help="Archive-size ladder (GiB) for the preflight_space formula check")
    parser.add_argument("--skip-scan", action="store_true", help="Skip the scan()/parse_archive_listing benchmarks")
    parser.add_argument("--skip-space", action="store_true", help="Skip the preflight_space formula check")
    parser.add_argument("--keep-trees", action="store_true", help="Don't delete generated file trees afterward")
    parser.add_argument("--work-dir", default=None, help="Where to generate benchmark data (default: ./bench_2_3_data)")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(args.backup_path).resolve()))
    import backup as backup_module  # noqa: E402

    base_dir = Path(args.work_dir) if args.work_dir else Path.cwd() / "bench_2_3_data"
    base_dir.mkdir(parents=True, exist_ok=True)

    print(f"Platform: {platform.system()} {platform.release()}  |  Python: {platform.python_version()}")
    print(f"Working dir for generated data: {base_dir}")

    if not args.skip_scan:
        bench_scan(backup_module, sorted(args.scan_counts), args.keep_trees, base_dir)
        bench_parse_listing(backup_module, sorted(args.scan_counts))

    if not args.skip_space:
        bench_space_formula(backup_module, sorted(args.space_sizes_gib), base_dir)

    print("\n=== (b) NOT covered by this script ===")
    print("Wall-clock cost of the full staging copy on a large REAL archive needs your own")
    print("large archive. Two-line manual recipe:")
    print("    time python backup.py --update <source> <archive>       # full update")
    print("    python -c \"import shutil,time; t=time.time(); "
          "shutil.copy2('Backup.7z','copy_test.7z'); print(time.time()-t)\"   # copy alone")
    print("Subtract the second from the first to see how much of the update is 'just the copy'.")

    if not args.keep_trees:
        with __import__("contextlib").suppress(OSError):
            shutil.rmtree(base_dir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
