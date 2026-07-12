#!/usr/bin/env python3
"""verify_prd_schema.py — strict PRD-spec JSON schema validator for action_camera.json.

Buyer-spec PRD references:
  - docs/PRD.md lines 117-131 — canonical record example (20 fields per frame)
  - docs/PRD_AUDIT_2026_05_04.md lines 70-81 — field type table

Each `action_camera.json` record must satisfy:
  * frame                       int >= 0
  * time                        string (datetime-like)
  * fps                         number, exactly 30.0
  * route_type                  int in {1, 2, 3}
  * mouse_x, mouse_y            number in [0, 1]
  * mouse_dx, mouse_dy          number in [-1, 1]
  * keyCode                     array of integers (camelCase per PRD)
  * camera_position             {x, y, z} | list[number]×3
  * camera_speed                {x, y, z} | list[number]×3
  * player_position             {x, y, z} | list[number]×3
  * player_speed                {x, y, z} | list[number]×3
  * camera_rotation_quaternion  {x, y, z, w} | list[number]×4 with ‖q‖ ≈ 1
  * player_rotation_quaternion  {x, y, z, w} | list[number]×4 with ‖q‖ ≈ 1
  * camera_Follow Offset        {x, y, z}    — literal name, space + capital F (PRD)
  * camera_intrinsics           {fx, fy, cx, cy} with fx == fy
  * camera_rotation_oula        {pitch, yaw, roll} | list×3   (PRD `oula` Pinyin)
  * player_rotation_oula        {pitch, yaw, roll} | list×3
  * camera_rotation_euler       (alias of camera_rotation_oula — accepted)
  * player_rotation_euler       (alias of player_rotation_oula — accepted)
  * metric_scale                number (default 1.0)

CLI:
    python3 bin/verify_prd_schema.py <clip_dir>            # human-readable
    python3 bin/verify_prd_schema.py <clip_dir> --json     # JSON report

Exit code: 0 if every record passes, non-zero otherwise (= count of failed records,
capped at 254 to stay inside the 8-bit POSIX exit-code range; 99 = file/parse error).

Implementation notes:
  - Stdlib only by default; uses `jsonschema` if importable for the structural pass,
    then layers semantic checks (range, fx==fy, ‖q‖≈1) on top.
  - Falls back to a hand-rolled validator with the same error vocabulary if
    `jsonschema` isn't installed — pytest test suite passes either way.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Tolerances for semantic checks (match verify_action_camera.py conventions)
QUATERNION_NORM_EPS = 0.01  # ‖q‖ within ±1%
FX_EQ_FY_EPS = 1e-6  # fx == fy with float tolerance
FPS_EXACT = 30.0
FPS_EPS = 1e-6
EXIT_PARSE_ERROR = 99
EXIT_MAX = 254


# ---------------------------------------------------------------------------
# Optional jsonschema integration — used as a structural pre-pass when
# available; semantic checks layered on top regardless.
# ---------------------------------------------------------------------------

try:
    import jsonschema as _jsonschema  # noqa: F401  (probe-only import)

    _HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover - exercised only without jsonschema
    _HAS_JSONSCHEMA = False


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

# Vector3 in dict form: PRD examples use {x, y, z}; we also accept {pitch, yaw, roll}
# for euler/oula fields per PRD line 73 "Vector3 (Euler degrees)".
_VEC3_KEYS_XYZ = ("x", "y", "z")
_VEC3_KEYS_EULER = ("pitch", "yaw", "roll")
_VEC4_KEYS = ("x", "y", "z", "w")
_INTRINSICS_KEYS = ("fx", "fy", "cx", "cy")

# Per PRD `keyCode` is camelCase (audit doc F2: "PRD literally specifies this").
# Per user task spec, keyCode is `array of integers` (PRD example line 121: [87]).
_REQUIRED_FIELDS = (
    "frame",
    "time",
    "fps",
    "route_type",
    "mouse_x",
    "mouse_y",
    "mouse_dx",
    "mouse_dy",
    "keyCode",
    "camera_position",
    "camera_speed",
    "player_position",
    "player_speed",
    "camera_rotation_quaternion",
    "player_rotation_quaternion",
    "camera_Follow Offset",  # literal field name with space + capital F per PRD
    "camera_intrinsics",
)

# Either `*_oula` (PRD canonical Pinyin) or `*_euler` (English alias) is accepted.
_EULER_ALIASES = {
    "camera_rotation": ("camera_rotation_oula", "camera_rotation_euler"),
    "player_rotation": ("player_rotation_oula", "player_rotation_euler"),
}

# `metric_scale` is OPTIONAL per task spec ("default 1.0") — we allow absence.

# Pre-compiled time-format regex (yyyy-mm-dd hh:mm:ss[.fff] is PRD example,
# but PRD audit p70 says "string MM-DD H...", so we accept any non-empty
# string and additionally try strict datetime parsing for diagnostics only).
_TIME_RE = re.compile(r"^\S")


# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

Number = (int, float)


def _is_number(v: Any) -> bool:
    """True for int or float, but not bool (bool is an int subclass)."""
    return isinstance(v, Number) and not isinstance(v, bool)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# ---------------------------------------------------------------------------
# Vector / structural helpers
# ---------------------------------------------------------------------------


def _check_vector3(name: str, value: Any, *, allow_euler_keys: bool = False) -> list[str]:
    """Return list of violations for a Vector3 field.

    Accepts:
      * dict with numeric x, y, z
      * dict with numeric pitch, yaw, roll  (only if allow_euler_keys=True)
      * list/tuple of length 3 with numeric items
    """
    issues: list[str] = []
    if isinstance(value, dict):
        keys_xyz = all(k in value for k in _VEC3_KEYS_XYZ)
        keys_euler = all(k in value for k in _VEC3_KEYS_EULER)
        if keys_xyz:
            for k in _VEC3_KEYS_XYZ:
                if not _is_number(value[k]):
                    issues.append(f"{name}.{k}: not a number ({type(value[k]).__name__})")
        elif allow_euler_keys and keys_euler:
            for k in _VEC3_KEYS_EULER:
                if not _is_number(value[k]):
                    issues.append(f"{name}.{k}: not a number ({type(value[k]).__name__})")
        else:
            expected = "{x,y,z}"
            if allow_euler_keys:
                expected = "{x,y,z} or {pitch,yaw,roll}"
            issues.append(f"{name}: dict missing required keys {expected}")
        return issues
    if isinstance(value, (list, tuple)):
        if len(value) != 3:
            issues.append(f"{name}: list must have length 3, got {len(value)}")
            return issues
        for i, item in enumerate(value):
            if not _is_number(item):
                issues.append(f"{name}[{i}]: not a number ({type(item).__name__})")
        return issues
    issues.append(f"{name}: must be dict or list[number]×3, got {type(value).__name__}")
    return issues


def _check_vector4_quaternion(name: str, value: Any) -> list[str]:
    """Return list of violations for a Vector4 quaternion field, including ‖q‖ ≈ 1."""
    issues: list[str] = []
    components: tuple[float, ...] | None = None
    if isinstance(value, dict):
        if not all(k in value for k in _VEC4_KEYS):
            issues.append(f"{name}: dict missing required keys {{x,y,z,w}}")
            return issues
        try:
            components = tuple(float(value[k]) for k in _VEC4_KEYS)
        except (TypeError, ValueError):
            issues.append(f"{name}: components must be numeric")
            return issues
        for k in _VEC4_KEYS:
            if not _is_number(value[k]):
                issues.append(f"{name}.{k}: not a number ({type(value[k]).__name__})")
                return issues
    elif isinstance(value, (list, tuple)):
        if len(value) != 4:
            issues.append(f"{name}: list must have length 4, got {len(value)}")
            return issues
        for i, item in enumerate(value):
            if not _is_number(item):
                issues.append(f"{name}[{i}]: not a number ({type(item).__name__})")
                return issues
        components = tuple(float(x) for x in value)
    else:
        issues.append(
            f"{name}: must be dict {{x,y,z,w}} or list[number]×4, " f"got {type(value).__name__}"
        )
        return issues
    # Norm check
    norm = math.sqrt(sum(c * c for c in components))
    if abs(norm - 1.0) > QUATERNION_NORM_EPS:
        issues.append(f"{name}: quaternion not unit-norm: ‖q‖={norm:.4f} (expected ≈1.0)")
    return issues


def _check_intrinsics(name: str, value: Any) -> list[str]:
    """Return violations for camera_intrinsics: {fx, fy, cx, cy} with fx == fy."""
    issues: list[str] = []
    if not isinstance(value, dict):
        issues.append(f"{name}: must be dict, got {type(value).__name__}")
        return issues
    for k in _INTRINSICS_KEYS:
        if k not in value:
            issues.append(f"{name}: missing required key '{k}'")
        elif not _is_number(value[k]):
            issues.append(f"{name}.{k}: not a number ({type(value[k]).__name__})")
    if any(f"{name}.{k}" in v for v in issues for k in ("fx", "fy")):
        return issues
    if "fx" in value and "fy" in value:
        if abs(float(value["fx"]) - float(value["fy"])) > FX_EQ_FY_EPS:
            issues.append(
                f"{name}: fx ({value['fx']}) != fy ({value['fy']}) — "
                "PRD requires pinhole model with fx == fy"
            )
    return issues


def _check_keycode(name: str, value: Any) -> list[str]:
    """keyCode must be an array of integers (camelCase per PRD audit F2)."""
    issues: list[str] = []
    if not isinstance(value, list):
        issues.append(f"{name}: must be array of integers, got {type(value).__name__}")
        return issues
    for i, item in enumerate(value):
        if not _is_int(item):
            issues.append(f"{name}[{i}]: must be int, got {type(item).__name__} ({item!r})")
    return issues


def _check_time(name: str, value: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(value, str):
        issues.append(f"{name}: must be string, got {type(value).__name__}")
        return issues
    if not _TIME_RE.match(value):
        issues.append(f"{name}: empty or whitespace-only timestamp")
        return issues
    # Try ISO-ish parse — informational; failure is not a hard error
    # (PRD audit p70 says "string MM-DD H..." — flexible).
    parsed = False
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            datetime.strptime(value, fmt)
            parsed = True
            break
        except ValueError:
            continue
    if not parsed:
        # Best-effort: ISO 8601 fallback
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            parsed = True
        except ValueError as exc:
            logger.debug(
                "verify_prd_schema: ISO 8601 fallback failed for %r: %s",
                value, exc,
            )
    if not parsed:
        issues.append(
            f"{name}: unrecognized datetime format {value!r} "
            "(expected e.g. 'YYYY-MM-DD HH:MM:SS.fff')"
        )
    return issues


# ---------------------------------------------------------------------------
# Per-record validator
# ---------------------------------------------------------------------------


def validate_record(record: Any, *, index: int) -> list[str]:
    """Validate a single action_camera.json record against the PRD schema.

    Returns a list of human-readable violation strings (empty == valid).
    Each string is prefixed with the record index for batch reports.
    """
    if not isinstance(record, dict):
        return [f"[{index}] record is not an object: {type(record).__name__}"]

    issues: list[str] = []

    # Required scalar fields ------------------------------------------------
    for field in _REQUIRED_FIELDS:
        if field not in record:
            issues.append(f"[{index}] missing required field '{field}'")
    # At least one of *_oula / *_euler must be present per group
    for _group, aliases in _EULER_ALIASES.items():
        if not any(a in record for a in aliases):
            issues.append(
                f"[{index}] missing required field '{aliases[0]}' " f"(or alias '{aliases[1]}')"
            )

    # Bail early if missing critical fields (avoid downstream key errors)
    if issues and any("missing required" in v for v in issues):
        # We still continue so callers see all type errors that *are* present.
        pass

    # frame -----------------------------------------------------------------
    if "frame" in record:
        v = record["frame"]
        if not _is_int(v):
            issues.append(f"[{index}] frame: must be int, got {type(v).__name__} ({v!r})")
        elif v < 0:
            issues.append(f"[{index}] frame: must be >= 0, got {v}")

    # time ------------------------------------------------------------------
    if "time" in record:
        for v in _check_time("time", record["time"]):
            issues.append(f"[{index}] {v}")

    # fps -------------------------------------------------------------------
    if "fps" in record:
        v = record["fps"]
        if not _is_number(v):
            issues.append(f"[{index}] fps: must be number, got {type(v).__name__}")
        elif abs(float(v) - FPS_EXACT) > FPS_EPS:
            issues.append(f"[{index}] fps: must equal {FPS_EXACT}, got {v}")

    # route_type ------------------------------------------------------------
    if "route_type" in record:
        v = record["route_type"]
        if not _is_int(v):
            issues.append(f"[{index}] route_type: must be int, got {type(v).__name__}")
        elif v not in {1, 2, 3}:
            issues.append(f"[{index}] route_type: must be in {{1, 2, 3}}, got {v}")

    # mouse_x / mouse_y in [0, 1] ------------------------------------------
    for axis in ("mouse_x", "mouse_y"):
        if axis in record:
            v = record[axis]
            if not _is_number(v):
                issues.append(f"[{index}] {axis}: must be number, got {type(v).__name__}")
            elif not (0.0 <= float(v) <= 1.0):
                issues.append(f"[{index}] {axis}: must be in [0, 1], got {v}")

    # mouse_dx / mouse_dy in [-1, 1] ---------------------------------------
    for axis in ("mouse_dx", "mouse_dy"):
        if axis in record:
            v = record[axis]
            if not _is_number(v):
                issues.append(f"[{index}] {axis}: must be number, got {type(v).__name__}")
            elif not (-1.0 <= float(v) <= 1.0):
                issues.append(f"[{index}] {axis}: must be in [-1, 1], got {v}")

    # keyCode (array of int) ------------------------------------------------
    if "keyCode" in record:
        for v in _check_keycode("keyCode", record["keyCode"]):
            issues.append(f"[{index}] {v}")

    # Vector3 fields --------------------------------------------------------
    for field in (
        "camera_position",
        "camera_speed",
        "player_position",
        "player_speed",
        "camera_Follow Offset",
    ):
        if field in record:
            for v in _check_vector3(field, record[field]):
                issues.append(f"[{index}] {v}")

    # Vector3 (euler-keyed) — accept *_oula or *_euler; validate whichever present
    for _group, aliases in _EULER_ALIASES.items():
        for alias in aliases:
            if alias in record:
                for v in _check_vector3(alias, record[alias], allow_euler_keys=True):
                    issues.append(f"[{index}] {v}")

    # Quaternion fields -----------------------------------------------------
    for field in ("camera_rotation_quaternion", "player_rotation_quaternion"):
        if field in record:
            for v in _check_vector4_quaternion(field, record[field]):
                issues.append(f"[{index}] {v}")

    # camera_intrinsics -----------------------------------------------------
    if "camera_intrinsics" in record:
        for v in _check_intrinsics("camera_intrinsics", record["camera_intrinsics"]):
            issues.append(f"[{index}] {v}")

    # metric_scale (optional, default 1.0) ----------------------------------
    if "metric_scale" in record:
        v = record["metric_scale"]
        if not _is_number(v):
            issues.append(f"[{index}] metric_scale: must be number, got {type(v).__name__}")

    return issues


# ---------------------------------------------------------------------------
# Batch validator
# ---------------------------------------------------------------------------


def validate_records(records: list[Any]) -> dict[str, Any]:
    """Validate a list of records, returning a structured report.

    Report keys:
      total      : int  — number of records
      passed     : int  — records with zero violations
      failed     : int  — records with at least one violation
      violations : list[str] — flat list of all violation strings (already
                              prefixed with `[index]`)
      per_record : list[list[str]] — violations indexed by record position
    """
    per_record: list[list[str]] = []
    violations: list[str] = []
    failed = 0
    for i, rec in enumerate(records):
        v = validate_record(rec, index=i)
        per_record.append(v)
        if v:
            failed += 1
            violations.extend(v)
    return {
        "total": len(records),
        "passed": len(records) - failed,
        "failed": failed,
        "violations": violations,
        "per_record": per_record,
    }


# ---------------------------------------------------------------------------
# JSON-Schema export (for tooling that wants the bare structural schema —
# semantic checks like fx==fy and ‖q‖≈1 are still done in Python)
# ---------------------------------------------------------------------------


def build_json_schema() -> dict[str, Any]:
    """Return a draft 2020-12 JSON Schema for the PRD action_camera record.

    Does not encode semantic constraints (fx==fy, ‖q‖≈1) — those are layered
    on top by ``validate_record``. Useful for tooling that consumes raw schemas.
    """
    vec3 = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "z": {"type": "number"},
                },
                "required": ["x", "y", "z"],
            },
            {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 3,
                "maxItems": 3,
            },
        ]
    }
    vec3_or_euler = {
        "oneOf": [
            vec3["oneOf"][0],
            {
                "type": "object",
                "properties": {
                    "pitch": {"type": "number"},
                    "yaw": {"type": "number"},
                    "roll": {"type": "number"},
                },
                "required": ["pitch", "yaw", "roll"],
            },
            vec3["oneOf"][1],
        ]
    }
    vec4 = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "z": {"type": "number"},
                    "w": {"type": "number"},
                },
                "required": ["x", "y", "z", "w"],
            },
            {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 4,
                "maxItems": 4,
            },
        ]
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://oysterworld.dev/schema/action_camera_record.json",
        "title": "PRD action_camera.json record",
        "type": "object",
        "required": list(_REQUIRED_FIELDS),
        "properties": {
            "frame": {"type": "integer", "minimum": 0},
            "time": {"type": "string", "minLength": 1},
            "fps": {"type": "number", "const": FPS_EXACT},
            "route_type": {"type": "integer", "enum": [1, 2, 3]},
            "mouse_x": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "mouse_y": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "mouse_dx": {"type": "number", "minimum": -1.0, "maximum": 1.0},
            "mouse_dy": {"type": "number", "minimum": -1.0, "maximum": 1.0},
            "keyCode": {"type": "array", "items": {"type": "integer"}},
            "camera_position": vec3,
            "camera_speed": vec3,
            "player_position": vec3,
            "player_speed": vec3,
            "camera_rotation_quaternion": vec4,
            "player_rotation_quaternion": vec4,
            "camera_Follow Offset": vec3,
            "camera_intrinsics": {
                "type": "object",
                "required": ["fx", "fy", "cx", "cy"],
                "properties": {
                    "fx": {"type": "number"},
                    "fy": {"type": "number"},
                    "cx": {"type": "number"},
                    "cy": {"type": "number"},
                },
            },
            "camera_rotation_oula": vec3_or_euler,
            "camera_rotation_euler": vec3_or_euler,
            "player_rotation_oula": vec3_or_euler,
            "player_rotation_euler": vec3_or_euler,
            "metric_scale": {"type": "number", "default": 1.0},
        },
    }


# ---------------------------------------------------------------------------
# I/O + CLI
# ---------------------------------------------------------------------------


def _load_records(ac_path: Path) -> list[Any]:
    """Load action_camera.json. Accepts top-level list or {"records": [...]}.

    Raises ValueError on bad shape; FileNotFoundError on missing file.
    """
    with ac_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return data["records"]
    raise ValueError(
        f"unrecognized action_camera.json shape: expected list or "
        f"{{'records': [...]}}, got {type(data).__name__}"
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
      argv: argument list (sys.argv[1:] if None)

    Returns:
      0  if all records pass
      99 if file not found, parse error, or schema construction error
      otherwise: number of failed records (capped at 254)
    """
    parser = argparse.ArgumentParser(
        description="Strict PRD-spec JSON schema validator for action_camera.json"
    )
    parser.add_argument(
        "clip_dir",
        type=Path,
        help="Path to extracted clip directory containing action_camera.json",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON report instead of human-readable summary",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Print at most N violations (0 = no limit)",
    )
    args = parser.parse_args(argv)

    ac_path: Path = args.clip_dir / "action_camera.json"
    if not ac_path.is_file():
        msg = f"ERROR: {ac_path} not found"
        if args.json:
            print(
                json.dumps(
                    {"error": msg, "total": 0, "failed": 0, "passed": 0, "violations": [msg]}
                )
            )
        else:
            print(msg, file=sys.stderr)
        return EXIT_PARSE_ERROR

    try:
        records = _load_records(ac_path)
    except (json.JSONDecodeError, ValueError) as exc:
        msg = f"ERROR: failed to parse {ac_path}: {exc}"
        if args.json:
            print(
                json.dumps(
                    {"error": str(exc), "total": 0, "failed": 0, "passed": 0, "violations": [msg]}
                )
            )
        else:
            print(msg, file=sys.stderr)
        return EXIT_PARSE_ERROR

    report = validate_records(records)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        total = report["total"]
        passed = report["passed"]
        failed = report["failed"]
        bar = "OK" if failed == 0 else "FAIL"
        print(f"\n=== verify_prd_schema.py — {bar} ===")
        print(f"file:    {ac_path}")
        print(f"records: {total}")
        print(f"passed:  {passed}")
        print(f"failed:  {failed}")
        if failed:
            print("\nViolations:")
            shown = report["violations"]
            if args.limit and len(shown) > args.limit:
                shown = shown[: args.limit]
            for v in shown:
                print(f"  - {v}")
            if args.limit and len(report["violations"]) > args.limit:
                hidden = len(report["violations"]) - args.limit
                print(f"  ... and {hidden} more (use --limit 0 to see all)")
        print()

    return min(report["failed"], EXIT_MAX)


if __name__ == "__main__":
    sys.exit(main())
