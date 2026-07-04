#!/usr/bin/env python3
"""Regression test for bin/verify_prd_schema.py silent error swallow fix.

The ISO 8601 fallback path inside _check_time() catches `ValueError` from
`datetime.fromisoformat()` and previously had a bare `except ValueError: pass`.
This swallowed the exception silently — making datetime parse failures
invisible during PRD schema validation.

The fix binds the exception as `exc` and emits a DEBUG log line via the
module logger, while keeping control flow unchanged (parsed stays False and
the issue still gets appended to the violations list). This test asserts:
  1. The handler now binds the exception and calls logger.debug (no bare pass).
  2. An unparseable time string still produces a violation (control flow preserved).
  3. A parseable ISO 8601 string with Z-suffix still validates (no regression).
"""
from __future__ import annotations

import logging
import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

from bin.verify_prd_schema import _check_time, validate_record  # noqa: E402


def _good_record(frame: int = 0) -> dict:
    return {
        "frame": frame,
        "time": "2026-05-02 15:30:45.000",
        "fps": 30.0,
        "route_type": 1,
        "mouse_x": 0.5,
        "mouse_y": 0.5,
        "mouse_dx": 0.01,
        "mouse_dy": -0.02,
        "keyCode": [87],
        "camera_position": {"x": 100.0, "y": 64.0, "z": 200.0},
        "camera_rotation_oula": {"pitch": 0.0, "yaw": 90.0, "roll": 0.0},
        "camera_rotation_quaternion": {"x": 0.0, "y": 0.7071068, "z": 0.0, "w": 0.7071068},
        "camera_Follow Offset": {"x": 0.0, "y": 1.6, "z": 0.0},
        "camera_intrinsics": {"fx": 960.0, "fy": 960.0, "cx": 960.0, "cy": 540.0},
        "camera_speed": {"x": 0.0, "y": 0.0, "z": 4.317},
        "player_position": {"x": 100.0, "y": 64.0, "z": 200.0},
        "player_rotation_oula": {"pitch": 0.0, "yaw": 90.0, "roll": 0.0},
        "player_rotation_quaternion": {"x": 0.0, "y": 0.7071068, "z": 0.0, "w": 0.7071068},
        "player_speed": {"x": 0.0, "y": 0.0, "z": 4.317},
        "metric_scale": 1.0,
    }


# ---- Case 1: bare `except ValueError: pass` is gone ----------------------


def test_iso_fallback_handler_no_longer_bare_pass() -> None:
    """The ISO 8601 fallback except-block must bind the exception and log.

    Source-level guard: if the handler is reverted to bare `pass`, this
    test fails and the regression is caught.
    """
    import inspect
    src = inspect.getsource(_check_time)
    # The unparseable fallback should bind exc and call logger.debug
    assert "except ValueError as exc" in src, (
        "ISO 8601 fallback must bind ValueError as `exc` "
        "(regression of silent-error-swallow fix)"
    )
    assert "logger.debug" in src, (
        "ISO 8601 fallback must call logger.debug (regression of "
        "silent-error-swallow fix)"
    )
    # The exact `except ValueError:\n    pass` anti-pattern must be gone
    assert "except ValueError:\n                pass" not in src, (
        "Bare `except ValueError: pass` anti-pattern regressed"
    )


# ---- Case 2: unparseable time still produces a violation ------------------


def test_unparseable_time_still_flags_violation() -> None:
    """Control flow preserved: bad ISO string must still be reported."""
    # '2026/05/02' is not parseable by datetime.fromisoformat in 3.11
    # and doesn't match any of the strptime formats.
    issues = _check_time("time", "2026/05/02")
    assert any("unrecognized datetime format" in s for s in issues), (
        f"unparseable time should be reported, got: {issues}"
    )


# ---- Case 3: parseable ISO 8601 with Z still validates --------------------


def test_iso8601_z_suffix_validates_via_fallback() -> None:
    """Z-suffixed ISO 8601 must still parse via the fromisoformat fallback."""
    # 3.11+ parses this directly
    issues = _check_time("time", "2026-05-02T15:30:45Z")
    assert issues == [], f"valid ISO 8601 Z should pass, got: {issues}"


# ---- Case 4: full record with ISO Z time passes ----------------------------


def test_full_record_with_iso_z_time_passes() -> None:
    """A canonical record whose time is ISO 8601 Z should pass validate_record."""
    rec = _good_record(0)
    rec["time"] = "2026-05-02T15:30:45Z"
    violations = validate_record(rec, index=0)
    assert violations == [], f"good record with ISO Z time produced violations: {violations}"


# ---- Case 5: logger.debug is called when fallback fails -------------------


def test_logger_debug_emitted_on_fallback_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the ISO 8601 fallback raises, the module logger must record it."""
    logger = logging.getLogger("bin.verify_prd_schema")
    caplog.set_level(logging.DEBUG, logger=logger.name)
    issues = _check_time("time", "2026/05/02 15:30:45")
    # The fallback should have failed; verify the debug line was emitted
    debug_records = [
        r for r in caplog.records
        if r.name == logger.name and r.levelno == logging.DEBUG
    ]
    assert len(debug_records) >= 1, (
        f"expected at least one DEBUG record from bin.verify_prd_schema, "
        f"got: {[r.getMessage() for r in caplog.records]}"
    )
    assert any("ISO 8601 fallback failed" in r.getMessage() for r in debug_records), (
        f"expected 'ISO 8601 fallback failed' in debug log, got: "
        f"{[r.getMessage() for r in debug_records]}"
    )
    # And control flow is preserved
    assert any("unrecognized datetime format" in s for s in issues), issues
