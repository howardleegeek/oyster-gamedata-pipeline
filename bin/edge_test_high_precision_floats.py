#!/usr/bin/env python3
"""Edge test: high-precision float round-trip via JSON serialization.

Verifies that extremely small camera position values (e.g. 1e-300) survive
a full JSON encode → decode cycle without loss of precision.  This guards
against silent truncation in camera calibration pipelines.

Usage:
    python3 bin/edge_test_high_precision_floats.py [--tolerance 1e-15]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Tuple


def _build_camera_payload(positions: List[Tuple[float, float, float]]) -> Dict[str, Any]:
    """Construct a minimal camera calibration JSON payload.

    Args:
        positions: Iterable of (x, y, z) camera position tuples.

    Returns:
        Dictionary matching a typical camera-spec JSON schema.
    """
    return {
        "version": "1.0",
        "cameras": [
            {
                "id": idx,
                "position": {"x": x, "y": y, "z": z},
                "orientation": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0},
            }
            for idx, (x, y, z) in enumerate(positions)
        ],
    }


def _roundtrip(payload: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-encode then decode *payload*, returning the result."""
    encoded = json.dumps(payload, ensure_ascii=False)
    return json.loads(encoded)


def _extract_positions(payload: Dict[str, Any]) -> List[Tuple[float, float, float]]:
    """Pull (x, y, z) tuples back out of a camera payload dict."""
    return [
        (cam["position"]["x"], cam["position"]["y"], cam["position"]["z"])
        for cam in payload["cameras"]
    ]


def _check_roundtrip(
    original: List[Tuple[float, float, float]],
    recovered: List[Tuple[float, float, float]],
    tolerance: float,
) -> List[str]:
    """Compare original vs recovered positions; return list of failure messages."""
    errors: List[str] = []
    for idx, (orig, rec) in enumerate(zip(original, recovered)):
        for axis_name, o_val, r_val in zip(("x", "y", "z"), orig, rec):
            if o_val == 0.0 and r_val == 0.0:
                continue
            rel_err = abs(o_val - r_val) / max(abs(o_val), abs(r_val))
            if rel_err > tolerance:
                errors.append(
                    f"camera[{idx}].{axis_name}: {o_val!r} → {r_val!r} "
                    f"(rel_err={rel_err:.2e} > tol={tolerance:.2e})"
                )
    return errors


def main(argv: List[str] | None = None) -> int:
    """CLI entry-point.  Returns 0 on success, 1 on any precision failure."""
    parser = argparse.ArgumentParser(
        description="Verify JSON round-trip preserves tiny float precision."
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1e-15,
        help="Maximum acceptable relative error (default: 1e-15).",
    )
    args = parser.parse_args(argv)

    # --- build test positions covering edge cases ---
    test_positions: List[Tuple[float, float, float]] = [
        (1e-300, 1e-300, 1e-300),  # all tiny
        (1e-300, 0.0, 0.0),  # mixed tiny / zero
        (-1e-300, 1e-300, -1e-300),  # signed tiny
        (1e-308, 1e-200, 1e-100),  # subnormal to normal range
        (3.141592653589793e-300, 2.71828e-300, 1.41421e-300),  # irrational-scaled
    ]

    payload = _build_camera_payload(test_positions)
    recovered = _roundtrip(payload)
    recovered_positions = _extract_positions(recovered)

    errors = _check_roundtrip(test_positions, recovered_positions, args.tolerance)

    if errors:
        print("FAIL — precision loss detected:", file=sys.stderr)
        for msg in errors:
            print(f"  {msg}", file=sys.stderr)
        return 1

    print(
        f"PASS — all {len(test_positions)} camera positions survived JSON round-trip "
        f"within relative tolerance {args.tolerance:.2e}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
