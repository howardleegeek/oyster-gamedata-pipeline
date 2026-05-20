#!/usr/bin/env python3
"""Generate tester onboarding kit ZIP from documentation files."""

import argparse
import sys
import zipfile
from pathlib import Path

VERSION = "1.0.0"

DOCS = [
    "docs/TESTER_ONBOARDING.md",
    "docs/TESTER_FAQ.md",
    "docs/TESTER_TROUBLESHOOTING.md",
]


def get_project_root() -> Path:
    """Find project root (where docs/ and scripts/ live)."""
    script_dir = Path(__file__).resolve().parent
    return script_dir.parent


def create_kit(output_path: str, version: str = VERSION) -> Path:
    """Create a ZIP file containing all tester documentation."""
    root = get_project_root()
    out = Path(output_path)

    missing = [d for d in DOCS if not (root / d).exists()]
    if missing:
        print(f"ERROR: Missing files: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc in DOCS:
            src = root / doc
            arcname = Path(doc).name
            zf.write(src, arcname)

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate tester onboarding kit ZIP")
    parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="Output ZIP file path",
    )
    parser.add_argument(
        "--version",
        "-v",
        default=VERSION,
        help=f"Kit version (default: {VERSION})",
    )
    args = parser.parse_args()

    result = create_kit(args.output, args.version)
    size_kb = result.stat().st_size / 1024
    print(f"✅ Kit generated: {result} ({size_kb:.1f} KB)")
    print(f"   Version: {args.version}")
    print(f"   Contents: {', '.join(Path(d).name for d in DOCS)}")


if __name__ == "__main__":
    main()
