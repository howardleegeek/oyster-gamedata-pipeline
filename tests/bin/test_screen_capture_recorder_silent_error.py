"""Regression test for the round-271 silent-error fix in
`bin/screen_capture_recorder.py`.

The `capture_worker` closure has an outer `except Exception` that used to
swallow daemon-thread deaths without recording them. The main thread reads
`capture_errors` and raises, so the error reaches the caller, but the
worker thread itself died silently. The fix adds a `logger.exception(...)`
call so the traceback is recorded.

Checks:
  1. Static guard: the outer `except Exception as e:` body in capture_worker
     must include a `logger.exception` call.
  2. No `pass`-only swallow remains in capture_worker.
  3. (Optional, requires `mss`) `logger` is importable from the module
     with the correct name.

The static checks work without `mss` installed (Linux CI runs them as a
pure-source guard), and the import check is skipped if `mss` is missing
to mirror the existing `tests/test_screen_capture_recorder.py` skip
behaviour.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))

SRC = (BIN_DIR / "screen_capture_recorder.py").read_text(encoding="utf-8")


def _capture_worker_body() -> str:
    """Return the source body of the inner `capture_worker` function."""
    match = re.search(
        r"def capture_worker\(.*?(?=\n    capture_thread = threading)",
        SRC,
        re.M | re.S,
    )
    assert match, "capture_worker function not found in source"
    return match.group(0)


def test_outer_except_logs_via_logger_exception() -> None:
    """The outer `except Exception as e:` in capture_worker must call
    `logger.exception` so the daemon worker's death is recorded."""
    body = _capture_worker_body()
    # The outer except sits at indent level 8 and is the LAST except in
    # the function body. Find ALL matches and take the last one.
    all_matches = list(re.finditer(
        r"        except Exception as e:\n(?P<body>(?:^[ \t]+.*\n)+)",
        body,
        re.M,
    ))
    assert all_matches, "outer except Exception in capture_worker not found"
    # Take the LAST match (the outer one, after the while loop)
    outer_except = all_matches[-1]
    outer_body = outer_except.group("body")
    assert "logger.exception" in outer_body, (
        f"outer except body must call logger.exception; got:\n{outer_body}"
    )


def test_no_pass_only_swallow_in_capture_worker() -> None:
    """No `except (...):\\n    pass` style swallow may remain in capture_worker."""
    body = _capture_worker_body()
    bare_pass = re.search(r"except[^\n]*:\s*\n\s+pass\b", body)
    assert not bare_pass, (
        f"silent-pass swallow still present in capture_worker: "
        f"{bare_pass.group(0) if bare_pass else ''}"
    )


def test_module_logger_importable_when_mss_present() -> None:
    """The module-level `logger` must still be importable (sanity).

    Skipped if `mss` isn't installed (matches the skip behaviour of
    `tests/test_screen_capture_recorder.py`).
    """
    pytest = __import__("pytest")  # local import keeps the test discoverable
    pytest.importorskip("mss", reason="mss not installable on headless CI runners")
    import logging  # noqa: PLC0415

    import screen_capture_recorder as scr  # noqa: PLC0415

    assert isinstance(scr.logger, logging.Logger)
    assert scr.logger.name == "screen_capture_recorder"
