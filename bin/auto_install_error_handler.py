#!/usr/bin/env python3
"""
G237 · bin/auto_install_error_handler.py

One-line bootstrap: import this at any process startup to install G234 global
Python error handling hooks. Works via PYTHONSTARTUP, sitecustomize.py, or
explicit import. Zero-config for any runtime.

Usage:
    # In any Python script:
    import auto_install_error_handler

    # Or via environment:
    export PYTHONSTARTUP=/path/to/auto_install_error_handler.py

    # CLI mode:
    python auto_install_error_handler.py --check
    python auto_install_error_handler.py --install
"""

from __future__ import annotations

import argparse
import atexit
import logging
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Optional, Type

# Module-level constants
_MODULE_NAME = "auto_install_error_handler"
_LOGGER_NAME = "g234.error_handler"

# Configure module logger
_logger = logging.getLogger(_LOGGER_NAME)


def _get_temp_dir() -> Path:
    """Get a temporary directory for error logs (no hardcoded /tmp)."""
    return Path(tempfile.mkdtemp(prefix="g234_errors_"))


def _format_exception(exc_type: Type[BaseException], exc_value: BaseException,
                      exc_tb: Optional[Any]) -> str:
    """Format an exception with full traceback and context."""
    lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    return "".join(lines)


def _g234_excepthook(exc_type: Type[BaseException], exc_value: BaseException,
                     exc_tb: Optional[Any]) -> None:
    """
    Global exception hook for G234 error handling.

    Logs uncaught exceptions with full context and optionally writes to
    a temp file for post-mortem analysis.
    """
    formatted = _format_exception(exc_type, exc_value, exc_tb)

    # Log to stderr
    sys.stderr.write(formatted)
    sys.stderr.flush()

    # Log via logging module
    _logger.critical("Uncaught exception: %s: %s", exc_type.__name__, exc_value)
    _logger.debug("Full traceback:\n%s", formatted)

    # Write to temp file for post-mortem
    try:
        temp_dir = _get_temp_dir()
        error_file = temp_dir / "last_error.txt"
        error_file.write_text(formatted, encoding="utf-8")
        _logger.info("Error details written to: %s", error_file)
    except OSError as e:
        _logger.warning("Could not write error file: %s", e)


def _g234_sys_exit_hook(code: int) -> None:
    """Hook for sys.exit calls to log exit codes."""
    _logger.info("Process exiting with code: %d", code)


def _cleanup_temp_resources() -> None:
    """Cleanup handler registered with atexit."""
    _logger.debug("Running G234 cleanup handlers")


def _install_hooks() -> bool:
    """
    Install G234 global Python error handling hooks.

    Returns:
        True if hooks were installed, False if already installed.
    """
    # Check if already installed
    if getattr(sys, "_g234_error_hooks_installed", False):
        _logger.debug("G234 hooks already installed, skipping")
        return False

    # Install exception hook
    original_excepthook = sys.excepthook
    sys.excepthook = _g234_excepthook

    # Store original for potential restoration
    sys._g234_original_excepthook = original_excepthook

    # Register cleanup
    atexit.register(_cleanup_temp_resources)

    # Mark as installed
    sys._g234_error_hooks_installed = True

    _logger.info("G234 error handling hooks installed successfully")
    return True


def _uninstall_hooks() -> bool:
    """
    Uninstall G234 hooks and restore original handlers.

    Returns:
        True if hooks were uninstalled, False if not installed.
    """
    if not getattr(sys, "_g234_error_hooks_installed", False):
        return False

    # Restore original excepthook
    original = getattr(sys, "_g234_original_excepthook", sys.__excepthook__)
    sys.excepthook = original

    # Clear flags
    if hasattr(sys, "_g234_error_hooks_installed"):
        delattr(sys, "_g234_error_hooks_installed")
    if hasattr(sys, "_g234_original_excepthook"):
        delattr(sys, "_g234_original_excepthook")

    _logger.info("G234 error handling hooks uninstalled")
    return True


def is_installed() -> bool:
    """Check if G234 hooks are currently installed."""
    return getattr(sys, "_g234_error_hooks_installed", False)


def main(argv: Optional[list[str]] = None) -> int:
    """
    CLI entry point for auto_install_error_handler.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success, non-zero for errors).
    """
    parser = argparse.ArgumentParser(
        prog=_MODULE_NAME,
        description="Install or manage G234 global Python error handling hooks."
    )
    parser.add_argument(
        "--install", "-i",
        action="store_true",
        help="Install G234 error handling hooks"
    )
    parser.add_argument(
        "--uninstall", "-u",
        action="store_true",
        help="Uninstall G234 error handling hooks"
    )
    parser.add_argument(
        "--check", "-c",
        action="store_true",
        help="Check if G234 hooks are installed"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )

    args = parser.parse_args(argv)

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    if args.check:
        installed = is_installed()
        print(f"G234 hooks installed: {installed}")
        return 0 if installed else 1

    if args.install:
        success = _install_hooks()
        print(f"G234 hooks {'installed' if success else 'already installed'}")
        return 0

    if args.uninstall:
        success = _uninstall_hooks()
        print(f"G234 hooks {'uninstalled' if success else 'were not installed'}")
        return 0

    # Default: install hooks
    _install_hooks()
    print("G234 error handling hooks installed (default mode)")
    return 0


# Auto-install on import for zero-config bootstrap
_install_hooks()

if __name__ == "__main__":
    sys.exit(main())
