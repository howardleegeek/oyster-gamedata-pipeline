"""
defense_finite_check.py — G100 Blue-Team Finite-Value Guard for Vector3 Writes
===============================================================================

Purpose
-------
Provides ``assert_finite`` — a defensive helper that validates every component
of a Vector3 (or any 3-element numeric sequence) before it is written to
downstream state.  Raises ``ValueError`` on NaN / ±Inf so that bad data is
caught at the boundary rather than silently corrupting geometry pipelines.

Usage
-----
    from defense_finite_check import assert_finite
    assert_finite((1.0, 2.0, 3.0))          # OK
    assert_finite([float('nan'), 0, 1])     # raises ValueError

CLI
---
    python -m defense_finite_check --check 1.0 2.0 3.0   # exit 0
    python -m defense_finite_check --check nan 0 1       # exit 1
"""

from __future__ import annotations

import argparse
import math
import sys
from typing import Iterable, Sequence, Union

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

Vector3 = Union[Sequence[float], Iterable[float]]


def assert_finite(vec: Vector3, label: str = "Vector3") -> None:
    """Assert that every component of *vec* is a finite float.

    Parameters
    ----------
    vec :
        A 3-element sequence of numeric values (list, tuple, numpy array, …).
    label :
        Human-readable identifier used in the error message.

    Raises
    ------
    ValueError
        If *vec* does not contain exactly 3 elements, or if any element is
        ``NaN`` or ``±Inf``.
    TypeError
        If an element cannot be converted to ``float``.
    """
    values: list[float] = [float(v) for v in vec]
    if len(values) != 3:
        raise ValueError(
            f"{label}: expected 3 components, got {len(values)}"
        )
    for idx, v in enumerate(values):
        if not math.isfinite(v):
            raise ValueError(
                f"{label}[{idx}] = {v!r} is not finite "
                f"(NaN/Inf detected before write)"
            )


def check_vector3(values: Sequence[str]) -> tuple[bool, str]:
    """Validate a list of 3 string tokens as a finite Vector3.

    Returns ``(True, "")`` on success or ``(False, error_message)`` on failure.
    """
    try:
        assert_finite(values, label="CLI-Vector3")
        return True, ""
    except (ValueError, TypeError) as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and run a finite check on the supplied Vector3.

    Returns 0 if all components are finite, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        prog="defense_finite_check",
        description="Blue-team guard: assert Vector3 components are finite.",
    )
    parser.add_argument(
        "--check",
        nargs=3,
        metavar="X",
        required=True,
        help="Three numeric values to validate (e.g. 1.0 2.0 3.0).",
    )
    parser.add_argument(
        "--label",
        default="Vector3",
        help="Optional label for error messages (default: 'Vector3').",
    )
    args = parser.parse_args(argv)

    ok, msg = check_vector3(args.check)
    if ok:
        print(f"[OK] {args.label} = {args.check} is finite.")
        return 0
    else:
        print(f"[FAIL] {msg}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
