#!/usr/bin/env python3
"""edge_test_unicode_filenames.py — Boundary test for unicode filenames in tarballs.

Verifies tarball creation/extraction handles UTF-8 filenames: Chinese, emoji, RTL.
Usage: python3 bin/edge_test_unicode_filenames.py [--tarball PATH] [--verbose]
"""

import argparse
import io
import os
import sys
import tarfile
import tempfile
from typing import List, Tuple

UNICODE_NAMES: List[str] = [
    "中文文件.txt", "日本語テスト.pdf", "한국어_데이터.csv",
    "🎉🚀emoji_file.dat", "📁📊🔥report.xlsx",
    "ملف_عربي.txt", "קובץ_עברי.log",
    "αβγδ_ελληνικά.md", "файл_данных.json", "café_résumé.txt",
]


def _create_unicode_tarball(tar_path: str) -> List[Tuple[str, bytes]]:
    """Create a tarball with unicode-named entries. Returns (name, content) list."""
    entries: List[Tuple[str, bytes]] = []
    with tarfile.open(tar_path, "w:gz") as tf:
        for name in UNICODE_NAMES:
            content = f"content-for-{name}".encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
            entries.append((name, content))
    return entries


def _verify_tarball(tar_path: str, expected: List[Tuple[str, bytes]]) -> bool:
    """Verify all entries in the tarball match expected data."""
    with tarfile.open(tar_path, "r:gz") as tf:
        members = tf.getnames()
        if len(members) != len(expected):
            print(f"  FAIL: expected {len(expected)} entries, got {len(members)}")
            return False
        for fname, content in expected:
            if fname not in members:
                print(f"  FAIL: missing entry '{fname}'")
                return False
            extracted = tf.extractfile(fname)
            if extracted is None or extracted.read() != content:
                print(f"  FAIL: content mismatch for '{fname}'")
                return False
    return True


def _verify_extraction(tar_path: str, expected: List[Tuple[str, bytes]]) -> bool:
    """Extract tarball to temp dir and verify files on disk."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with tarfile.open(tar_path, "r:gz") as tf:
            tf.extractall(path=tmpdir)
        for fname, content in expected:
            fpath = os.path.join(tmpdir, fname)
            if not os.path.isfile(fpath):
                print(f"  FAIL: extracted file missing: '{fname}'")
                return False
            with open(fpath, "rb") as fh:
                if fh.read() != content:
                    print(f"  FAIL: disk content mismatch for '{fname}'")
                    return False
    return True


def main(argv: List[str] | None = None) -> int:
    """CLI entry point. Returns 0 on success, 1 on failure."""
    parser = argparse.ArgumentParser(
        description="Boundary test: unicode filenames in tarball entries."
    )
    parser.add_argument("--tarball", default=None,
                        help="Path to existing tarball to verify (skips creation).")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print detailed results.")
    args = parser.parse_args(argv)
    all_pass = True

    if args.tarball:
        print(f"Verifying existing tarball: {args.tarball}")
        with tarfile.open(args.tarball, "r:gz") as tf:
            names = tf.getnames()
            if args.verbose:
                for n in names:
                    print(f"  entry: {n}")
            print(f"  Total entries: {len(names)}")
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = os.path.join(tmpdir, "unicode_test.tar.gz")
            print(f"Creating tarball with {len(UNICODE_NAMES)} unicode entries...")
            entries = _create_unicode_tarball(tar_path)

            print("Verifying tarball manifest...")
            if not _verify_tarball(tar_path, entries):
                all_pass = False
            else:
                print("  PASS: manifest verification")

            print("Verifying extraction to disk...")
            if not _verify_extraction(tar_path, entries):
                all_pass = False
            else:
                print("  PASS: extraction verification")

            if args.verbose:
                print(f"  Tarball size: {os.path.getsize(tar_path)} bytes")

    if all_pass:
        print("All unicode filename tests PASSED.")
        return 0
    else:
        print("Some unicode filename tests FAILED.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
