#!/usr/bin/env python3
"""
Red Team Path Traversal Test Module.

This module tests that tarball extractors properly reject path traversal
attacks where entry names contain sequences like ../../etc.

Purpose: Verify security of extraction logic against directory traversal.
"""

import argparse
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import List, Tuple


def is_path_traversal(name: str, base_dir: Path) -> bool:
    """
    Check if a tar entry path attempts directory traversal.

    Args:
        name: The path name from the tar archive entry.
        base_dir: The intended extraction base directory.

    Returns:
        True if the path escapes base_dir, False otherwise.
    """
    # Resolve the full path and check it stays within base_dir
    try:
        # Normalize the path: remove leading slashes, handle ..
        abs_path = (base_dir / name).resolve()
        # Check if the resolved path is within the base directory
        return not str(abs_path).startswith(str(base_dir.resolve()))
    except (ValueError, OSError):
        # Any resolution error should be treated as suspicious
        return True


def extract_tarball_safely(
    tar_path: Path, dest_dir: Path
) -> Tuple[bool, List[str]]:
    """
    Extract a tarball with path traversal protection.

    Args:
        tar_path: Path to the tar archive.
        dest_dir: Destination directory for extraction.

    Returns:
        Tuple of (success: bool, list of extracted files or error messages).
    """
    extracted: List[str] = []
    errors: List[str] = []

    try:
        with tarfile.open(tar_path, "r:*") as tar:
            for member in tar.getmembers():
                # Check for path traversal attempt
                if is_path_traversal(member.name, dest_dir):
                    errors.append(
                        f"BLOCKED: Path traversal detected in '{member.name}'"
                    )
                    continue

                # Safe to extract
                try:
                    tar.extract(member, dest_dir)
                    extracted.append(member.name)
                except Exception as e:
                    errors.append(f"Error extracting '{member.name}': {e}")

    except Exception as e:
        errors.append(f"Failed to open tarball: {e}")
        return False, errors

    return len(errors) == 0, extracted + errors


def create_malicious_tarball(dest_dir: Path) -> Path:
    """
    Create a tarball with path traversal entries for testing.

    This simulates a red team attack where malicious entries like
    ../../etc/passwd are embedded in the archive.

    Args:
        dest_dir: Directory to create the tarball in.

    Returns:
        Path to the created malicious tarball.
    """
    tar_path = dest_dir / "malicious.tar"

    # Create a safe file first
    safe_content = b"This is a safe file.\n"

    with tarfile.open(tar_path, "w") as tar:
        # Add a safe entry
        safe_info = tarfile.TarInfo(name="safe_file.txt")
        safe_info.size = len(safe_content)
        tar.addfile(safe_info, None)

        # Add malicious path traversal entries
        malicious_entries = [
            "../../etc/passwd",
            "../../etc/shadow",
            "../../../../../../etc/hosts",
            "..\\..\\..\\windows\\system32\\config\\sam",
            "foo/../../../bar/etc",
        ]

        for entry_name in malicious_entries:
            info = tarfile.TarInfo(name=entry_name)
            info.size = 0
            tar.addfile(info, None)

    return tar_path


def main(argv: List[str]) -> int:
    """
    Main entry point for the red team path traversal test.

    Args:
        argv: Command line arguments (excluding script name).

    Returns:
        Exit code: 0 for success, 1 for failure.
    """
    parser = argparse.ArgumentParser(
        description="Red team test: verify path traversal rejection"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )
    parser.add_argument(
        "--create-only",
        action="store_true",
        help="Only create the malicious tarball, don't test extraction",
    )

    args = parser.parse_args(argv)

    # Use tempfile for safe temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        tarball_path = create_malicious_tarball(tmp_path)

        if args.create_only:
            print(f"Created malicious tarball: {tarball_path}")
            return 0

        # Test extraction with safety checks
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()

        success, messages = extract_tarball_safely(tarball_path, extract_dir)

        if args.verbose:
            for msg in messages:
                print(msg)

        # The test expects that path traversal is BLOCKED
        # So success=False (blocked) is actually the desired outcome
        blocked_count = sum(1 for m in messages if "BLOCKED" in m)

        if blocked_count > 0:
            print(
                f"SUCCESS: Path traversal attack blocked ({blocked_count} "
                f"entries rejected)"
            )
            return 0
        else:
            print("FAILURE: Path traversal attack was NOT blocked!")
            return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
