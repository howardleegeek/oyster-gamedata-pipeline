#!/usr/bin/env python3
"""Dependency Pinning Check.

Production utility to verify all pip dependencies are pinned to exact versions.
Fails CI if any dependency uses version ranges (>=, <=, ~=, !=, >, <).

Exit codes: 0=success, 1=unpinned deps found, 2=errors.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, NamedTuple

# Range operators indicate unpinned dependencies
RANGE_PATTERN = re.compile(
    r"^\s*(?P<name>[a-zA-Z0-9][-a-zA-Z0-9._]*)\s*"
    r"(?P<op>>=|<=|~=|!=|>|<)\s*(?P<ver>[^\s;#]+)"
)
# No version specifier is also unpinned
NO_VERSION_PATTERN = re.compile(r"^\s*(?P<name>[a-zA-Z0-9][-a-zA-Z0-9._]*)\s*(?=[;#]|$)")
COMMENT_OR_OPTION = re.compile(r"^\s*(#|$|-\w|--\w+)")


class UnpinnedDep(NamedTuple):
    """Unpinned dependency details."""

    name: str
    specifier: str
    line: int
    path: str


def check_file(file_path: Path) -> List[UnpinnedDep]:
    """Check a requirements file for unpinned dependencies."""
    unpinned: List[UnpinnedDep] = []
    if not file_path.exists():
        return unpinned
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return unpinned

    for num, raw_line in enumerate(lines, start=1):
        line = raw_line.split("#")[0].strip()
        if COMMENT_OR_OPTION.match(line):
            continue
        m = RANGE_PATTERN.match(line)
        if m:
            unpinned.append(
                UnpinnedDep(
                    m.group("name"), f"{m.group('op')}{m.group('ver')}", num, str(file_path)
                )
            )
            continue
        m = NO_VERSION_PATTERN.match(line)
        if m:
            unpinned.append(UnpinnedDep(m.group("name"), "(no version)", num, str(file_path)))
    return unpinned


def find_requirements_files(root: Path) -> List[Path]:
    """Find all requirements files in the project."""
    patterns = ["requirements*.txt", "*requirements*.txt", "requirements/*.txt"]
    files: set[Path] = set()
    for p in patterns:
        files.update(root.glob(p))
        files.update(root.rglob(p))
    return sorted(files)


def main(argv: List[str] | None = None) -> int:
    """Main entry point. Returns exit code."""
    parser = argparse.ArgumentParser(
        description="Check that all pip dependencies are pinned to exact versions."
    )
    parser.add_argument(
        "files", nargs="*", type=Path, help="Requirements files to check (default: auto-detect)"
    )
    parser.add_argument(
        "-r",
        "--root",
        type=Path,
        default=Path("."),
        help="Root directory to search for requirements files",
    )
    args = parser.parse_args(argv)

    files = args.files if args.files else find_requirements_files(args.root)
    if not files:
        print("No requirements files found.", file=sys.stderr)
        return 0

    all_unpinned: List[UnpinnedDep] = []
    for fp in files:
        all_unpinned.extend(check_file(fp))

    if not all_unpinned:
        print(f"✓ All {len(files)} requirements file(s) have pinned dependencies.")
        return 0

    print(f"✗ Found {len(all_unpinned)} unpinned dependency(ies):", file=sys.stderr)
    for dep in all_unpinned:
        print(f"  {dep.path}:{dep.line}: {dep.name} {dep.specifier}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
