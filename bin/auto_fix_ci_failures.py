#!/usr/bin/env python3
"""
G188 · bin/auto_fix_ci_failures.py

Automatically fixes common CI failures by analyzing GitHub Actions run logs.
Reads `gh run view --log-failed` output, applies known fixes, and commits changes.

Known fixes:
- black --check failure -> run black
- ruff failure -> ruff --fix
- missing import -> add it
"""

import argparse
import os
import re
import subprocess
import sys
from typing import Dict, List, Optional, Tuple


def run_command(cmd: List[str], cwd: Optional[str] = None) -> Tuple[int, str, str]:
    """
    Run a command and return (returncode, stdout, stderr).

    Args:
        cmd: Command and arguments as list
        cwd: Working directory (optional)

    Returns:
        Tuple of (returncode, stdout, stderr)
    """
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return 1, "", str(e)


def get_failed_logs() -> str:
    """
    Get failed logs from GitHub CLI.

    Returns:
        Output from `gh run view --log-failed`
    """
    returncode, stdout, stderr = run_command(["gh", "run", "view", "--log-failed"])
    if returncode != 0:
        print(f"Error getting failed logs: {stderr}", file=sys.stderr)
        return ""
    return stdout


def parse_black_failure(logs: str) -> List[str]:
    """
    Parse black formatting failures from logs.

    Args:
        logs: CI failure logs

    Returns:
        List of files that need black formatting
    """
    files = []
    # Look for patterns like:
    # "would reformat /path/to/file.py"
    # "Oh no! 💥 💥 💥 1 file would be reformatted"
    pattern = r"would reformat\s+([^\s]+\.py)"
    for match in re.finditer(pattern, logs):
        file_path = match.group(1)
        if os.path.exists(file_path):
            files.append(file_path)

    # Also check for summary line that might list files
    if "black --check" in logs.lower() and "would reformat" in logs:
        # Try to find files mentioned in context
        lines = logs.split('\n')
        for i, line in enumerate(lines):
            if "would reformat" in line or "reformatted" in line:
                # Check surrounding lines for file paths
                for j in range(max(0, i-3), min(len(lines), i+4)):
                    if '.py:' in lines[j]:
                        # Extract file path before colon
                        file_path = lines[j].split(':')[0]
                        if os.path.exists(file_path) and file_path.endswith('.py'):
                            files.append(file_path)

    return list(set(files))  # Remove duplicates


def parse_ruff_failure(logs: str) -> List[str]:
    """
    Parse ruff linting failures from logs.

    Args:
        logs: CI failure logs

    Returns:
        List of files that need ruff fixes
    """
    files = []
    # Look for patterns like:
    # "Found 3 errors in 1 file"
    # "path/to/file.py:12:5: E501 line too long"
    pattern = r"^([^\s:]+\.py):\d+:\d+:"
    for match in re.finditer(pattern, logs, re.MULTILINE):
        file_path = match.group(1)
        if os.path.exists(file_path):
            files.append(file_path)

    # Also check for ruff-specific patterns
    if "ruff check" in logs.lower() or "ruff format" in logs.lower():
        # Look for file paths in error messages
        lines = logs.split('\n')
        for line in lines:
            if '.py:' in line and ('error' in line.lower() or 'warning' in line.lower()):
                file_path = line.split(':')[0]
                if os.path.exists(file_path) and file_path.endswith('.py'):
                    files.append(file_path)

    return list(set(files))


def parse_missing_imports(logs: str) -> Dict[str, List[str]]:
    """
    Parse missing import errors from logs.

    Args:
        logs: CI failure logs

    Returns:
        Dict mapping file paths to list of missing imports
    """
    imports_by_file: Dict[str, List[str]] = {}

    # Look for ImportError patterns
    # "ModuleNotFoundError: No module named 'module_name'"
    # "ImportError: cannot import name 'name' from 'module'"
    import_error_pattern = r"ModuleNotFoundError: No module named ['\"]([^'\"]+)['\"]"
    for match in re.finditer(import_error_pattern, logs):
        module_name = match.group(1)
        # Try to find which file caused this error
        lines = logs.split('\n')
        for i, line in enumerate(lines):
            if match.group(0) in line:
                # Look backward for file path
                for j in range(max(0, i-5), i):
                    if '.py:' in lines[j]:
                        file_path = lines[j].split(':')[0]
                        if os.path.exists(file_path):
                            if file_path not in imports_by_file:
                                imports_by_file[file_path] = []
                            imports_by_file[file_path].append(module_name)

    # Look for specific import errors
    # Regex to match: ImportError: cannot import name 'X' from 'Y'
    import_name_pattern = (
        r"ImportError: cannot import name ['\"]([^'\"]+)['\"] from ['\"]([^'\"]+)['\"]"
    )
    for match in re.finditer(import_name_pattern, logs):
        import_name = match.group(1)
        module_name = match.group(2)
        # Try to find which file caused this error
        lines = logs.split('\n')
        for i, line in enumerate(lines):
            if match.group(0) in line:
                # Look backward for file path
                for j in range(max(0, i-5), i):
                    if '.py:' in lines[j]:
                        file_path = lines[j].split(':')[0]
                        if os.path.exists(file_path):
                            if file_path not in imports_by_file:
                                imports_by_file[file_path] = []
                            imports_by_file[file_path].append(f"{import_name} from {module_name}")

    return imports_by_file


def apply_black_fix(files: List[str]) -> bool:
    """
    Apply black formatting to files.

    Args:
        files: List of files to format

    Returns:
        True if successful, False otherwise
    """
    if not files:
        return True

    print(f"Running black on {len(files)} file(s)...")
    returncode, stdout, stderr = run_command(["black"] + files)

    if returncode != 0:
        print(f"Error running black: {stderr}", file=sys.stderr)
        return False

    if stdout:
        print(stdout)

    print("Black formatting completed.")
    return True


def apply_ruff_fix(files: List[str]) -> bool:
    """
    Apply ruff fixes to files.

    Args:
        files: List of files to fix

    Returns:
        True if successful, False otherwise
    """
    if not files:
        return True

    print(f"Running ruff --fix on {len(files)} file(s)...")

    # First check if ruff is available
    returncode, _, _ = run_command(["ruff", "--version"])
    if returncode != 0:
        print("ruff not found, trying ruff check instead...")
        # Try alternative command
        returncode, stdout, stderr = run_command(["ruff", "check", "--fix"] + files)
    else:
        returncode, stdout, stderr = run_command(["ruff", "--fix"] + files)

    if returncode != 0:
        print(f"Error running ruff: {stderr}", file=sys.stderr)
        return False

    if stdout:
        print(stdout)

    print("Ruff fixes completed.")
    return True


def add_missing_imports(imports_by_file: Dict[str, List[str]]) -> bool:
    """
    Add missing imports to files.

    Args:
        imports_by_file: Dict mapping files to list of imports to add

    Returns:
        True if successful, False otherwise
    """
    if not imports_by_file:
        return True

    print(f"Adding missing imports to {len(imports_by_file)} file(s)...")

    for file_path, imports in imports_by_file.items():
        try:
            with open(file_path, 'r') as f:
                content = f.read()

            # Find the best place to add imports (after any existing imports)
            lines = content.split('\n')
            import_end = 0

            for i, line in enumerate(lines):
                line_stripped = line.strip()
                if line_stripped.startswith('import ') or line_stripped.startswith('from '):
                    import_end = i + 1
                elif line_stripped and not line_stripped.startswith('#') and i > import_end:
                    break

            # Add new imports
            new_imports = []
            for imp in imports:
                if ' from ' in imp:
                    # Format: "name from module"
                    parts = imp.split(' from ')
                    if len(parts) == 2:
                        new_imports.append(f"from {parts[1]} import {parts[0]}")
                else:
                    new_imports.append(f"import {imp}")

            # Insert new imports
            if import_end == 0:
                # No existing imports, add at top (after any shebang or encoding)
                if lines and lines[0].startswith('#!'):
                    # Shebang line present
                    lines.insert(1, '')
                    lines.insert(2, '\n'.join(new_imports))
                    import_end = 2 + len(new_imports)
                else:
                    lines.insert(0, '\n'.join(new_imports))
                    import_end = len(new_imports)
            else:
                # Add after existing imports
                lines.insert(import_end, '')
                lines.insert(import_end + 1, '\n'.join(new_imports))

            # Write back
            with open(file_path, 'w') as f:
                f.write('\n'.join(lines))

            print(f"  Added imports to {file_path}: {', '.join(imports)}")

        except Exception as e:
            print(f"Error adding imports to {file_path}: {e}", file=sys.stderr)
            return False

    print("Missing imports added.")
    return True


def commit_changes() -> bool:
    """
    Commit changes with auto-fix-ci tag.

    Returns:
        True if successful, False otherwise
    """
    print("Committing changes...")

    # Check if there are any changes to commit
    returncode, stdout, stderr = run_command(["git", "status", "--porcelain"])
    if returncode != 0:
        print(f"Error checking git status: {stderr}", file=sys.stderr)
        return False

    if not stdout.strip():
        print("No changes to commit.")
        return True

    # Add all changes
    returncode, stdout, stderr = run_command(["git", "add", "."])
    if returncode != 0:
        print(f"Error adding changes: {stderr}", file=sys.stderr)
        return False

    # Commit with auto-fix-ci tag
    commit_message = "ci: auto-fix-ci - apply automatic fixes for CI failures"
    returncode, stdout, stderr = run_command(["git", "commit", "-m", commit_message])
    if returncode != 0:
        print(f"Error committing changes: {stderr}", file=sys.stderr)
        return False

    print(f"Committed changes: {commit_message}")

    # Add tag
    returncode, stdout, stderr = run_command(["git", "tag", "auto-fix-ci"])
    if returncode != 0:
        print(f"Warning: Could not add tag: {stderr}", file=sys.stderr)
        # Continue even if tag fails
    else:
        print("Added tag: auto-fix-ci")

    return True


def main(argv: List[str]) -> int:
    """
    Main entry point for the script.

    Args:
        argv: Command line arguments

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    parser = argparse.ArgumentParser(
        description="Automatically fix common CI failures by analyzing GitHub Actions run logs.",
        epilog="Example: python bin/auto_fix_ci_failures.py"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be fixed without making changes"
    )
    parser.add_argument(
        "--log-file",
        type=str,
        help="Path to log file instead of running 'gh run view --log-failed'"
    )

    args = parser.parse_args(argv)

    # Get logs
    if args.log_file:
        try:
            with open(args.log_file, 'r') as f:
                logs = f.read()
        except Exception as e:
            print(f"Error reading log file {args.log_file}: {e}", file=sys.stderr)
            return 1
    else:
        logs = get_failed_logs()
        if not logs:
            print("No logs found or error retrieving logs.", file=sys.stderr)
            return 1

    if not logs.strip():
        print("No failed logs found.", file=sys.stderr)
        return 0

    # Parse failures
    print("Analyzing CI failures...")

    black_files = parse_black_failure(logs)
    ruff_files = parse_ruff_failure(logs)
    missing_imports = parse_missing_imports(logs)

    # Report findings
    if black_files:
        print(f"Found {len(black_files)} file(s) needing black formatting:")
        for f in black_files:
            print(f"  - {f}")

    if ruff_files:
        print(f"Found {len(ruff_files)} file(s) needing ruff fixes:")
        for f in ruff_files:
            print(f"  - {f}")

    if missing_imports:
        print(f"Found {len(missing_imports)} file(s) with missing imports:")
        for f, imports in missing_imports.items():
            print(f"  - {f}: {', '.join(imports)}")

    if not black_files and not ruff_files and not missing_imports:
        print("No known fixable issues found in logs.")
        print("Logs may contain other types of failures not handled by this script.")
        return 0

    if args.dry_run:
        print("\nDry run complete. No changes made.")
        return 0

    # Apply fixes
    success = True

    if black_files:
        if not apply_black_fix(black_files):
            success = False

    if ruff_files:
        if not apply_ruff_fix(ruff_files):
            success = False

    if missing_imports:
        if not add_missing_imports(missing_imports):
            success = False

    if not success:
        print("Some fixes failed to apply.", file=sys.stderr)
        return 1

    # Commit changes
    if not commit_changes():
        print("Failed to commit changes.", file=sys.stderr)
        return 1

    print("\nAll fixes applied and committed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

