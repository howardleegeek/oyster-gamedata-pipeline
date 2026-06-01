#!/usr/bin/env python3
"""
G070 · PRD p5 #2: route_type field distribution check.

Validates that across a FLEET of clips (PRD: 240/batch), the clip-level
``route_type`` field spans at least 5 distinct types.

This is a FLEET-LEVEL test: its unit is the CLIP SUMMARY (one ``route_type``
per clip), NOT the per-frame ``action_camera.json`` rows. A single recording
session's ``action_camera.json`` contains thousands of per-frame records (one
per video frame), each carrying that frame's instantaneous ``route_type``.
Counting those frame-rows as "clips" makes one session look like thousands of
clips and lets a single session mis-PASS a fleet gate. The loader therefore
EXCLUDES per-frame artifacts and only aggregates genuine clip-summary units.
On a single session (or any input with fewer than the minimum clips), the test
SKIPS (exit 2) — it never counts frames as clips and never mis-passes.

Usage:
    python bin/prd_test_route_type_distribution.py --data-dir data/clips
    python bin/prd_test_route_type_distribution.py --clips-file data/clips.json

Exit codes:
    0 - route_type distribution meets requirements
    1 - route_type distribution does not meet requirements (validation failure)
    2 - Skip: missing data, corrupt files, or too few clips (e.g. single session)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Minimum fraction of expected clips required to run validation.
# Below this threshold, the test is skipped (exit 2).
MIN_DATA_FRACTION = 0.5

# Filenames that are per-frame / session artifacts, never clip-summary units.
# action_camera.json is the canonical per-frame stream (one row per video frame).
_PER_FRAME_FILENAMES = frozenset({"action_camera.json"})

# Field names that mark a dict as a PER-FRAME action_camera record rather than a
# clip-summary unit. Per-frame records describe one video frame's camera pose;
# clip summaries describe a whole clip. Presence of these ⇒ not a clip.
_PER_FRAME_RECORD_KEYS = frozenset({
    "frame", "camera_position", "camera_intrinsics",
    "camera_rotation_quaternion", "mouse_dx", "keyCode",
})


def _is_per_frame_record(obj: Any) -> bool:
    """True if ``obj`` looks like a per-frame action_camera record (not a clip).

    A per-frame record carries frame-level camera fields. Requiring at least two
    such keys avoids misclassifying a legitimately rich clip summary that merely
    happens to mention one of them.
    """
    if not isinstance(obj, dict):
        return False
    return len(_PER_FRAME_RECORD_KEYS & set(obj.keys())) >= 2


def _is_clip_unit(obj: Any) -> bool:
    """True if ``obj`` is a genuine clip-summary unit for fleet aggregation.

    A clip unit is a dict carrying a SCALAR ``route_type`` (int/str) and is not a
    per-frame record. A ``route_type`` that is itself a dict (e.g. the descriptive
    provenance block ``{"distinct_route_types": ...}`` emitted into
    clip_summary.json) is NOT a clip-level value and is rejected.
    """
    if not isinstance(obj, dict) or _is_per_frame_record(obj):
        return False
    rt = obj.get("route_type")
    if rt is None or rt == "":
        return False
    return isinstance(rt, (str, int, float, bool))


def _clips_from_json(data: Any) -> list[dict[str, Any]]:
    """Extract clip-summary units from one parsed JSON document.

    Rules:
      * A list of per-frame records (an action_camera.json payload) yields NO
        clips — it is at most one session's frames, not a list of clips.
      * A list otherwise contributes each element that is a clip unit.
      * A single dict contributes itself iff it is a clip unit (a clip_summary.json
        whose top-level ``route_type`` is a provenance dict is excluded).
    """
    if isinstance(data, list):
        # If the list is per-frame rows, it's one session's frames → not clips.
        if any(_is_per_frame_record(el) for el in data[:32]):
            return []
        return [el for el in data if _is_clip_unit(el)]
    if isinstance(data, dict):
        return [data] if _is_clip_unit(data) else []
    return []


def load_clips(data_dir: Path | None, clips_file: Path | None) -> list[dict[str, Any]]:
    """Load clip-summary units from a directory or single file.

    Per-frame artifacts (``action_camera.json`` and any list of per-frame
    records) are excluded so one session cannot masquerade as a fleet of clips.
    """
    clips: list[dict[str, Any]] = []

    if data_dir:
        if not data_dir.is_dir():
            raise FileNotFoundError(f"Data directory not found: {data_dir}")
        for json_file in sorted(data_dir.glob("*.json")):
            if json_file.name in _PER_FRAME_FILENAMES:
                continue  # per-frame stream, never clip units
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)
            clips.extend(_clips_from_json(data))
    elif clips_file:
        with open(clips_file, encoding="utf-8") as f:
            data = json.load(f)
        clips = _clips_from_json(data)

    return clips


def extract_route_types(clips: list[dict[str, Any]]) -> list[str]:
    """Extract clip-level route_type values, skipping missing/non-scalar fields.

    Only SCALAR route_type values (int/str/float/bool) count: a route_type that
    is a dict or list is provenance/aggregate data, not a clip-level label, and
    is skipped so it can never be mis-counted as a clip's type.
    """
    out: list[str] = []
    for c in clips:
        if not isinstance(c, dict):
            continue
        rt = c.get("route_type")
        if rt is None or rt == "":
            continue
        if not isinstance(rt, (str, int, float, bool)):
            continue
        out.append(str(rt))
    return out


def validate_distribution(
    route_types: list[str],
    min_distinct: int = 5,
    expected_total: int = 240,
) -> tuple[bool, dict[str, Any]]:
    """Validate route_type distribution meets requirements."""
    distinct = set(route_types)
    dist = dict(Counter(route_types))

    details = {
        "total_clips": len(route_types),
        "expected_clips": expected_total,
        "distinct_types": len(distinct),
        "min_required": min_distinct,
        "distribution": dist,
        "type_list": sorted(distinct),
    }

    success = len(distinct) >= min_distinct and len(route_types) >= expected_total * MIN_DATA_FRACTION
    return success, details


def report(details: dict[str, Any], verbose: bool = False) -> None:
    """Print validation results."""
    print(f"Total clips: {details['total_clips']}")
    print(f"Expected clips: {details['expected_clips']}")
    print(f"Distinct route_type values: {details['distinct_types']}")
    print(f"Minimum required: {details['min_required']}")
    print(f"Type list: {', '.join(details['type_list'])}")

    if verbose:
        print("\nDistribution:")
        for rt, cnt in sorted(details["distribution"].items()):
            print(f"  {rt}: {cnt}")


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Validate route_type field distribution across clips."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Directory containing clip JSON files (mutually exclusive with --clips-file)",
    )
    parser.add_argument(
        "--clips-file",
        type=Path,
        help="Single JSON file containing clips (mutually exclusive with --data-dir)",
    )
    parser.add_argument(
        "--min-distinct",
        type=int,
        default=5,
        help="Minimum distinct route_type values required (default: 5)",
    )
    parser.add_argument(
        "--expected-total",
        type=int,
        default=240,
        help="Expected total number of clips (default: 240)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show detailed distribution"
    )
    args = parser.parse_args(argv)

    if args.data_dir is None and args.clips_file is None:
        parser.error("Either --data-dir or --clips-file required")

    try:
        clips = load_clips(args.data_dir, args.clips_file)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    # Check if any clip-summary units were loaded. Per-frame artifacts
    # (action_camera.json, per-frame record lists) are excluded by load_clips,
    # so a single session legitimately yields zero clips here → skip.
    if not clips:
        if args.data_dir:
            print(
                f"SKIP: No clips found in directory (per-frame artifacts "
                f"excluded; not a clip fleet): {args.data_dir}",
                file=sys.stderr,
            )
        else:
            print(f"SKIP: No clips found in file: {args.clips_file}", file=sys.stderr)
        return 2

    route_types = extract_route_types(clips)

    # If clips exist but none have a scalar route_type field, this is a data
    # availability issue (skip-worthy), not a validation failure.
    if not route_types:
        print(
            "SKIP: No clips found with a route_type field — data not available",
            file=sys.stderr,
        )
        return 2

    # Check if we have enough clips to run validation. Below MIN_DATA_FRACTION of
    # the expected total (e.g. a single session) the fleet gate cannot run → skip.
    min_required_clips = int(args.expected_total * MIN_DATA_FRACTION)
    if len(route_types) < min_required_clips:
        print(
            f"SKIP: Only {len(route_types)} clips, need at least "
            f"{min_required_clips} to validate — fleet data not available",
            file=sys.stderr,
        )
        return 2

    success, details = validate_distribution(
        route_types, args.min_distinct, args.expected_total
    )

    report(details, args.verbose)

    if success:
        print("\n✓ PASS: route_type distribution meets requirements")
        return 0
    else:
        print("\n✗ FAIL: route_type distribution does not meet requirements", file=sys.stderr)
        if details["distinct_types"] < args.min_distinct:
            print(f"  - Only {details['distinct_types']} types, need {args.min_distinct}", file=sys.stderr)
        if details["total_clips"] < args.expected_total * MIN_DATA_FRACTION:
            print(f"  - Only {details['total_clips']} clips, need {int(args.expected_total * MIN_DATA_FRACTION)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
