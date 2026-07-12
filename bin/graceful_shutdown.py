#!/usr/bin/env python3
"""G130 · Graceful Shutdown Handler — SIGTERM flushes in-flight writes + closes tarballs."""

import argparse
import logging
import os
import signal
import sys
import tarfile
import tempfile
import time
from typing import IO, List, Optional, Set

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """Manage SIGTERM handling: flush registered file handles and close tarballs."""

    def __init__(self) -> None:
        self._files: Set[IO[bytes]] = set()
        self._tarballs: Set[tarfile.TarFile] = set()
        self._shutting_down: bool = False
        self._original_handler = None

    def register_file(self, fh: IO[bytes]) -> None:
        """Register an open file handle to be flushed on SIGTERM."""
        self._files.add(fh)

    def register_tarball(self, tf: tarfile.TarFile) -> None:
        """Register an open tarball to be closed on SIGTERM."""
        self._tarballs.add(tf)

    def _handler(self, signum: int, frame) -> None:  # noqa: ANN001
        """Internal SIGTERM handler — flushes, closes, then re-raises."""
        logger.info(
            "SIGTERM: flushing %d file(s), closing %d tarball(s)",
            len(self._files), len(self._tarballs),
        )
        self._shutting_down = True
        for fh in self._files:
            try:
                fh.flush()
                os.fsync(fh.fileno())
            except (OSError, ValueError):
                logger.debug("Could not flush %r", fh, exc_info=True)
        for tf in self._tarballs:
            try:
                tf.close()
            except Exception as e:
                logger.debug("Could not close tarball %r: %s", tf, e, exc_info=True)
        if self._original_handler is not None:
            signal.signal(signal.SIGTERM, self._original_handler)
        os.kill(os.getpid(), signal.SIGTERM)

    def install(self) -> None:
        """Install the SIGTERM handler (idempotent)."""
        if self._original_handler is None:
            self._original_handler = signal.signal(signal.SIGTERM, self._handler)

    @property
    def shutting_down(self) -> bool:
        """Return True if a shutdown sequence has been triggered."""
        return self._shutting_down


_manager = GracefulShutdown()


def install_handler() -> None:
    """Install the global SIGTERM graceful-shutdown handler."""
    _manager.install()


def register_file(fh: IO[bytes]) -> None:
    """Register *fh* for automatic flush on SIGTERM."""
    _manager.register_file(fh)


def register_tarball(tf: tarfile.TarFile) -> None:
    """Register *tf* for automatic close on SIGTERM."""
    _manager.register_tarball(tf)


def shutdown_requested() -> bool:
    """Check whether a SIGTERM has been received."""
    return _manager.shutting_down


def _run_test() -> int:
    """Smoke-test: create a temp tarball, register it, self-SIGTERM."""
    tmpdir = tempfile.mkdtemp(prefix="g130_shutdown_")
    tar_path = os.path.join(tmpdir, "test.tar")
    with tarfile.open(tar_path, "w") as tf:
        register_tarball(tf)
        data = b"graceful-shutdown-test-payload\n"
        info = tarfile.TarInfo(name="test.txt")
        info.size = len(data)
        tf.addfile(info, fileobj=__import__("io").BytesIO(data))
        logger.info(
            "Test tarball at %s — sending SIGTERM to PID %d", tar_path, os.getpid()
        )
        os.kill(os.getpid(), signal.SIGTERM)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point — parse args, install handler, optionally run test."""
    parser = argparse.ArgumentParser(
        description="G130: Graceful shutdown handler for production services.",
    )
    parser.add_argument("--test", action="store_true", help="Run self-SIGTERM smoke test.")
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )
    install_handler()
    if args.test:
        return _run_test()
    logger.info("Handler installed (PID %d). Send SIGTERM to trigger shutdown.", os.getpid())
    try:
        while not shutdown_requested():
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
