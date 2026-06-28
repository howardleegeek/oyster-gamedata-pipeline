#!/usr/bin/env python3
"""
defense_file_lock.py - fcntl flock wrapper for tarball operations.

Blue team defense for G089: Prevents concurrent corruption when multiple
processes attempt to read/write tarball files simultaneously.
"""

import argparse
import fcntl
import os
import sys
import tempfile
from collections.abc import Generator
from contextlib import contextmanager, suppress
from enum import Enum
from types import TracebackType
from typing import IO


class LockType(Enum):
    """Lock types for file operations."""

    SHARED = fcntl.LOCK_SH
    EXCLUSIVE = fcntl.LOCK_EX


class FileLockError(Exception):
    """Exception raised when file lock operations fail."""

    pass


class FileLock:
    """
    Context manager for acquiring file locks using fcntl.flock.

    Provides safe concurrent access to files for tarball operations.
    """

    def __init__(
        self, file_path: str, lock_type: LockType = LockType.EXCLUSIVE, timeout: float = 0.0
    ) -> None:
        self.file_path = os.path.abspath(file_path)
        self.lock_type = lock_type
        self.timeout = timeout
        self._file_handle: IO | None = None
        self._lock_acquired = False

    def __enter__(self) -> IO:
        mode = "r+b" if self.lock_type == LockType.EXCLUSIVE else "rb"

        if self.lock_type == LockType.EXCLUSIVE:
            parent_dir = os.path.dirname(self.file_path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)
            if not os.path.exists(self.file_path):
                open(self.file_path, "wb").close()

        try:
            self._file_handle = open(self.file_path, mode)
        except (FileNotFoundError, PermissionError) as e:
            raise FileLockError(f"Cannot open {self.file_path}: {e}") from e

        lock_flag = self.lock_type.value
        if self.timeout == 0:
            lock_flag |= fcntl.LOCK_NB

        try:
            fcntl.flock(self._file_handle.fileno(), lock_flag)
            self._lock_acquired = True
        except (BlockingIOError, OSError) as e:
            if self._file_handle:
                self._file_handle.close()
                self._file_handle = None
            raise FileLockError(f"Lock failed on {self.file_path}: {e}") from e

        return self._file_handle

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._file_handle:
            if self._lock_acquired:
                with suppress(OSError):
                    fcntl.flock(self._file_handle.fileno(), fcntl.LOCK_UN)
            self._file_handle.close()
            self._file_handle = None
            self._lock_acquired = False


@contextmanager
def file_lock(
    file_path: str, exclusive: bool = True, timeout: float = 0.0
) -> Generator[IO, None, None]:
    """Context manager for acquiring file locks."""
    lock_type = LockType.EXCLUSIVE if exclusive else LockType.SHARED
    with FileLock(file_path, lock_type, timeout) as f:
        yield f


def acquire_tarball_lock(tarball_path: str, for_write: bool = True) -> tuple[IO, FileLock]:
    """Acquire lock on a tarball file for safe concurrent access."""
    lock_type = LockType.EXCLUSIVE if for_write else LockType.SHARED
    lock_obj = FileLock(tarball_path, lock_type)
    return lock_obj.__enter__(), lock_obj


def main(argv: list | None = None) -> int:
    """CLI entry point for file lock operations."""
    parser = argparse.ArgumentParser(description="File lock utility for tarball operations")
    parser.add_argument("--file", "-f", help="Path to file to lock")
    parser.add_argument(
        "--operation",
        "-o",
        choices=["read", "write"],
        default="read",
        help="Operation: read (shared) or write (exclusive)",
    )
    parser.add_argument(
        "--timeout", "-t", type=float, default=0.0, help="Timeout seconds (0=non-blocking)"
    )
    parser.add_argument("--test", "-T", action="store_true", help="Run self-test")

    args = parser.parse_args(argv)

    if args.test:
        return _run_self_test()

    if not args.file:
        parser.error("--file is required unless --test is specified")

    exclusive = args.operation == "write"
    try:
        with file_lock(args.file, exclusive=exclusive, timeout=args.timeout) as f:
            print(f"Acquired {'exclusive' if exclusive else 'shared'} lock on: {args.file}")
            if not exclusive:
                print(f"File size: {len(f.read())} bytes")
        print("Lock released")
        return 0
    except FileLockError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _run_self_test() -> int:
    """Run self-test for file lock functionality."""
    print("Running self-test...")
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "test.tar")

        print("  Testing exclusive lock...")
        with file_lock(test_file, exclusive=True) as f:
            f.write(b"test data")

        print("  Testing shared lock...")
        with file_lock(test_file, exclusive=False) as f:
            assert f.read() == b"test data"

        print("  Testing FileLock class...")
        with FileLock(test_file, LockType.EXCLUSIVE) as f:
            f.write(b"updated")

    print("All tests passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
