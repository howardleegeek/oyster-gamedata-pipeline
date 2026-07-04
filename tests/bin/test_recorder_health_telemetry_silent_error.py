"""Tests for `bin/recorder_health_telemetry.py` KeyboardInterrupt fix.

Regression checks for the `except KeyboardInterrupt: pass` swallow in
``main()`` that was previously silent and is now logged at INFO with the
exception bound:

  1. Static guard: no `except KeyboardInterrupt:\\n    pass` may remain in
     bin/recorder_health_telemetry.py.
  2. KeyboardInterrupt is logged at INFO with the new message.
  3. main() still returns 0 on KeyboardInterrupt (clean shutdown preserved).

Self-review: scope = one file (bin/recorder_health_telemetry.py), one
logical change (replace bare ``except KeyboardInterrupt: pass`` with a
logger.info call that binds the operator's intent), no control-flow
change (still returns 0).
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import pytest

# Add bin to path
BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))

import recorder_health_telemetry as rht  # noqa: E402


SRC = (BIN_DIR / "recorder_health_telemetry.py").read_text(encoding="utf-8")


def test_no_bare_pass_on_keyboard_interrupt() -> None:
    """`except KeyboardInterrupt:\\n    pass` must not remain."""
    bare_pass = re.search(
        r"except\s+KeyboardInterrupt\s*:\s*\n\s+pass\b",
        SRC,
    )
    assert not bare_pass, (
        "Silent-pass on KeyboardInterrupt still present at offset "
        f"{bare_pass.start() if bare_pass else '?'}: "
        f"{bare_pass.group(0) if bare_pass else ''}"
    )


def test_keyboard_interrupt_is_logged_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """KeyboardInterrupt in main() emits an INFO log and returns 0."""
    # Patch _run_loop to raise KeyboardInterrupt instead of looping forever.
    rht._run_loop = lambda **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt())  # type: ignore[assignment]
    with caplog.at_level(logging.INFO, logger="recorder_health_telemetry"):
        rc = rht.main(
            [
                "--endpoint",
                "http://localhost:8080/v1/health",
                "--interval",
                "1",
            ]
        )
    assert rc == 0, f"expected clean exit code 0; got {rc}"
    assert any(
        "KeyboardInterrupt" in rec.getMessage() for rec in caplog.records
    ), (
        "expected INFO log mentioning KeyboardInterrupt; got "
        f"{[r.getMessage() for r in caplog.records]}"
    )
    assert any(
        rec.levelno == logging.INFO for rec in caplog.records
    ), (
        "expected at least one INFO log record on KeyboardInterrupt; got "
        f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )
