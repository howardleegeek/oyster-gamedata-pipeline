#!/usr/bin/env python3
"""
secure_subprocess_lib.py — Safe subprocess wrapper rejecting shell=True
with a configurable binary allowlist.

Usage:
    python3 bin/secure_subprocess_lib.py -- ls -la /
    python3 bin/secure_subprocess_lib.py --allowlist ls,cat -- cat /etc/hostname
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set


class SecureSubprocessError(Exception):
    """Base exception for secure subprocess failures."""


class ShellModeError(SecureSubprocessError):
    """Raised when shell=True is attempted."""


class BinaryNotAllowedError(SecureSubprocessError):
    """Raised when a binary is not in the allowlist."""


DEFAULT_ALLOWLIST: Set[str] = frozenset(
    {
        "ls",
        "cat",
        "head",
        "tail",
        "wc",
        "grep",
        "find",
        "sort",
        "uniq",
        "awk",
        "sed",
        "cut",
        "tr",
        "diff",
        "echo",
        "date",
        "whoami",
        "pwd",
        "mkdir",
        "cp",
        "mv",
        "rm",
        "chmod",
        "chown",
        "stat",
        "file",
        "md5sum",
        "sha256sum",
        "tar",
        "gzip",
        "gunzip",
        "zip",
        "unzip",
        "python3",
        "python",
        "node",
        "bash",
        "sh",
        "env",
        "printenv",
        "id",
        "uname",
        "df",
        "du",
        "ps",
        "sleep",
        "touch",
        "ln",
        "readlink",
        "basename",
        "dirname",
        "realpath",
        "which",
        "xargs",
        "tee",
        "jq",
        "curl",
        "wget",
        "git",
        "make",
        "gcc",
        "g++",
        "clang",
        "pip",
        "pip3",
        "npm",
        "rsync",
        "ssh",
        "scp",
        "base64",
        "xxd",
        "hexdump",
    }
)


def validate_binary(binary: str, allowlist: Set[str]) -> str:
    """Validate that *binary* is in *allowlist* or raise BinaryNotAllowedError."""
    name = Path(binary).name
    if name not in allowlist:
        raise BinaryNotAllowedError(
            f"Binary '{binary}' not in allowlist. Allowed: {sorted(allowlist)}"
        )
    return name


def run(
    cmd: Sequence[str],
    *,
    allowlist: Optional[Set[str]] = None,
    timeout: Optional[float] = None,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    capture_output: bool = True,
    check: bool = True,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """Execute *cmd* securely — shell=True is strictly forbidden."""
    if kwargs.pop("shell", False):
        raise ShellModeError("shell=True is forbidden. Use list-form arguments.")
    if not cmd:
        raise SecureSubprocessError("Command list must not be empty.")

    effective = allowlist if allowlist is not None else DEFAULT_ALLOWLIST
    validate_binary(cmd[0], effective)

    run_env: Optional[Dict[str, str]] = {**os.environ, **env} if env else None

    return subprocess.run(
        list(cmd),
        shell=False,
        timeout=timeout,
        cwd=cwd,
        env=run_env,
        capture_output=capture_output,
        check=check,
        **kwargs,
    )


def create_temp_dir(prefix: str = "secure_subprocess_") -> str:
    """Create a secure temporary directory using tempfile.mkdtemp."""
    return tempfile.mkdtemp(prefix=prefix)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for secure subprocess execution."""
    parser = argparse.ArgumentParser(
        description="Secure subprocess runner with binary allowlist.",
    )
    parser.add_argument(
        "--allowlist",
        type=str,
        default=None,
        help="Comma-separated list of allowed binaries.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Maximum seconds to wait for the subprocess.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command and arguments to execute.",
    )

    args = parser.parse_args(argv)

    cmd = list(args.command)
    # Strip leading '--' separator if present
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]

    if not cmd:
        parser.print_help()
        return 1

    allowlist: Optional[Set[str]] = None
    if args.allowlist:
        allowlist = {b.strip() for b in args.allowlist.split(",") if b.strip()}

    try:
        result = run(cmd, allowlist=allowlist, timeout=args.timeout)
        if result.stdout:
            sys.stdout.buffer.write(result.stdout)
        if result.stderr:
            sys.stderr.buffer.write(result.stderr)
        return result.returncode
    except (ShellModeError, BinaryNotAllowedError, SecureSubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print(f"ERROR: Command timed out after {args.timeout}s", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
