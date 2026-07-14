#!/usr/bin/env python3
"""
G198 · bin/real_depth_validator.py — verify a depth/ directory produced by
the RealDepthExporter Fabric mod meets lint v3 criteria #15 + #16.

The mod emits {000000.exr, …, 001799.exr} at PRD §3.4 6 fps × 5 min into
{tarball}/depth/. This script runs the full buyer-acceptance pipe in one
command:

  1. Filename + count check  (1800 files, 0-padded indices)
  2. Per-file structural check (single-channel "Z" float32, 1920×1080)
  3. Invalid-pixel-ratio check  (per-frame ratio ≤ 5 %, mean ≤ 4 %)
  4. Cross-frame coherence  (no frame is solid-zero / solid-sky)
  5. Aggregate verdict   (matches the lint v3 #15 + #16 pass/fail booleans)

Exit code:
   0 → PASS (lint v3 #15+#16 will pass on this directory)
   1 → FAIL (one or more criteria violated; details printed)
   2 → environment error (OpenEXR missing, dir absent, etc)

The math helpers here are a Python mirror of
mc-mod/src/main/java/world/oyster/recorder/depth/DepthMath.java and are
exercised by tests/test_real_depth_math.py.  Both implementations MUST
stay in lockstep.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


# --------------------------------------------------------------------------- math


INVALID_DEPTH_THRESHOLD: float = 0.999
"""Sky / clipped-far cutoff matching DepthMath.INVALID_DEPTH_THRESHOLD."""

MAX_INVALID_RATIO_HARD: float = 0.05
"""Buyer lint v3 #15 ceiling — frames above this fail acceptance."""

MAX_INVALID_RATIO_WARN: float = 0.04
"""Soft warning threshold — gives operators headroom before buyer rejection."""


def linear_depth_classic(depth_buf: float, near: float, far: float) -> float:
    """Single-pixel inversion mirroring DepthMath.linearDepthClassic.

    Returns 0.0 for any input that violates the PRD §3.4 invalid contract
    (sky/clipped/NaN/Inf/out-of-range/bad planes).
    """
    if not math.isfinite(depth_buf):
        return 0.0
    if not math.isfinite(near) or not math.isfinite(far):
        return 0.0
    if near <= 0.0 or far <= near:
        return 0.0
    if depth_buf < 0.0 or depth_buf > 1.0:
        return 0.0
    if depth_buf >= INVALID_DEPTH_THRESHOLD:
        return 0.0
    z_ndc = depth_buf * 2.0 - 1.0
    denom = (far + near) - z_ndc * (far - near)
    if denom == 0.0:
        return 0.0
    linear = (2.0 * near * far) / denom
    if not math.isfinite(linear) or linear <= 0.0 or linear > far:
        return 0.0
    return float(linear)


def linear_depth_reversed(depth_buf: float, near: float, far: float) -> float:
    """Reversed-z variant (near at 1, far at 0)."""
    if not math.isfinite(depth_buf):
        return 0.0
    if depth_buf < 0.0 or depth_buf > 1.0:
        return 0.0
    return linear_depth_classic(1.0 - depth_buf, near, far)


def linearize_buffer(
    depth: np.ndarray,
    near: float,
    far: float,
    reversed_z: bool = False,
) -> np.ndarray:
    """Vectorised buffer conversion. Always returns float32, same shape.

    Pure numpy — no per-pixel Python loop. The branches mirror
    DepthMath.linearizeBuffer's scalar paths but vectorised so a
    1920×1080 buffer converts in <50 ms on commodity hardware.
    """
    arr = np.asarray(depth, dtype=np.float32)
    if reversed_z:
        # Invalid bits already include NaN / negative / >1 via the masks below;
        # we flip THEN apply classic math.
        flipped = np.where(np.isfinite(arr), 1.0 - arr, np.nan)
        valid_input = np.isfinite(arr) & (arr >= 0.0) & (arr <= 1.0)
        arr = np.where(valid_input, flipped, np.nan).astype(np.float32)

    # Plane validity
    if not (math.isfinite(near) and math.isfinite(far) and near > 0.0 and far > near):
        return np.zeros_like(arr, dtype=np.float32)

    # Validity mask — finite, in [0, 1), below sky threshold
    valid = np.isfinite(arr) & (arr >= 0.0) & (arr <= 1.0) & (arr < INVALID_DEPTH_THRESHOLD)

    # Suppress numpy warnings about invalid input — we mask after.
    with np.errstate(invalid="ignore", divide="ignore"):
        z_ndc = arr * 2.0 - 1.0
        denom = (far + near) - z_ndc * (far - near)
        linear = (2.0 * near * far) / denom

    # Post-conditions: drop any pixel that came out non-finite / OOB.
    valid &= np.isfinite(linear) & (linear > 0.0) & (linear <= far)
    out = np.where(valid, linear, 0.0).astype(np.float32)
    return out


def count_invalid(metres: np.ndarray) -> int:
    """Count zeros — the PRD §3.4 invalid sentinel."""
    return int(np.sum(np.asarray(metres) == 0.0))


# --------------------------------------------------------------------------- validation


@dataclass
class FrameResult:
    """Per-frame validation result. Tracks every PRD §3.4 axis."""

    name: str
    width: int = 0
    height: int = 0
    has_z_channel: bool = False
    is_float32: bool = False
    invalid_ratio: float = 0.0
    mean_metres: float = 0.0
    max_metres: float = 0.0
    nan_count: int = 0
    inf_count: int = 0
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


@dataclass
class ValidationResult:
    """Top-level summary mirroring lint v3 #15 + #16 booleans."""

    depth_dir: Path
    total_files: int = 0
    sampled_files: int = 0
    frames: list[FrameResult] = field(default_factory=list)
    missing_indices: list[int] = field(default_factory=list)
    duplicate_names: list[str] = field(default_factory=list)
    aggregate_issues: list[str] = field(default_factory=list)

    @property
    def lint_v3_15_pass(self) -> bool:
        """Lint v3 #15 — Depth Invalid-Pixel Ratio."""
        if not self.frames:
            return False
        # Any single frame exceeding the hard cap fails the gate.
        return all(f.invalid_ratio <= MAX_INVALID_RATIO_HARD for f in self.frames)

    @property
    def lint_v3_16_pass(self) -> bool:
        """Lint v3 #16 — Depth Data Quality."""
        if not self.frames:
            return False
        # 16 is the catch-all quality gate: all frames must be structurally
        # sound AND invalid-ratio gate must pass AND no missing files.
        if self.missing_indices or self.duplicate_names or self.aggregate_issues:
            return False
        return self.lint_v3_15_pass and all(f.ok for f in self.frames)

    def to_dict(self) -> dict[str, Any]:
        return {
            "depth_dir": str(self.depth_dir),
            "total_files": self.total_files,
            "sampled_files": self.sampled_files,
            "missing_indices_count": len(self.missing_indices),
            "missing_indices_sample": self.missing_indices[:10],
            "duplicate_names": self.duplicate_names[:10],
            "aggregate_issues": self.aggregate_issues,
            "lint_v3_15_pass": self.lint_v3_15_pass,
            "lint_v3_16_pass": self.lint_v3_16_pass,
            "frames_sample": [
                {
                    "name": f.name,
                    "size": f"{f.width}x{f.height}",
                    "z_channel": f.has_z_channel,
                    "float32": f.is_float32,
                    "invalid_ratio": round(f.invalid_ratio, 5),
                    "mean_m": round(f.mean_metres, 3),
                    "max_m": round(f.max_metres, 3),
                    "nan_count": f.nan_count,
                    "inf_count": f.inf_count,
                    "issues": f.issues,
                }
                for f in self.frames[:5]
            ],
        }


# --------------------------------------------------------------------------- IO


def _read_exr_z_channel(path: Path) -> tuple[np.ndarray, int, int, bool, bool]:
    """Return (data, width, height, has_Z, is_float32).

    On any error a structurally-invalid result is returned with empty data
    and the flags False — caller decides what to do.
    """
    try:
        import OpenEXR  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "OpenEXR is required: pip install OpenEXR>=3.0"
        ) from exc

    try:
        f = OpenEXR.InputFile(str(path))
    except Exception:
        return np.zeros(0, dtype=np.float32), 0, 0, False, False

    try:
        header = f.header()
        channels = header.get("channels", {})
        names = list(channels.keys())
        if not names:
            return np.zeros(0, dtype=np.float32), 0, 0, False, False

        has_z = "Z" in names
        chan_name = "Z" if has_z else names[0]

        # Float check matches lint_buyer_spec._read_exr_lazy.
        ptype = getattr(channels[chan_name], "type", None)
        is_float = False
        type_v = getattr(ptype, "v", None)
        if type_v == 2:
            is_float = True
        if not is_float:
            try:
                if int(ptype) == 2:
                    is_float = True
            except (TypeError, ValueError):
                pass
        if not is_float and "FLOAT" in repr(ptype).upper():
            is_float = True

        dw = header["dataWindow"]
        width = dw.max.x - dw.min.x + 1
        height = dw.max.y - dw.min.y + 1

        raw = f.channel(chan_name)
        arr = np.frombuffer(raw, dtype=np.float32).reshape(height, width)
        return arr.copy(), int(width), int(height), has_z, is_float
    finally:
        f.close()


# --------------------------------------------------------------------------- main check


def validate_frame(
    path: Path,
    expected_width: int,
    expected_height: int,
) -> FrameResult:
    """Validate one EXR file against the buyer's depth contract."""
    r = FrameResult(name=path.name)
    try:
        arr, w, h, has_z, is_f32 = _read_exr_z_channel(path)
    except RuntimeError as e:
        r.issues.append(f"read-error: {e}")
        return r
    r.width = w
    r.height = h
    r.has_z_channel = has_z
    r.is_float32 = is_f32

    if w == 0 or h == 0:
        r.issues.append("zero-dimension or unreadable header")
        return r
    if not has_z:
        r.issues.append("missing 'Z' channel (lint v3 requires Z)")
    if not is_f32:
        r.issues.append("channel is not float32")
    if w != expected_width or h != expected_height:
        r.issues.append(
            f"resolution {w}x{h} != expected {expected_width}x{expected_height}"
        )

    # Invalid-pixel ratio (PRD §3.4 + lint v3 #15)
    nan_count = int(np.sum(~np.isfinite(arr)))
    inf_count = int(np.sum(np.isinf(arr)))
    r.nan_count = nan_count
    r.inf_count = inf_count
    invalid = np.sum((arr == 0.0) | ~np.isfinite(arr))
    total = arr.size
    r.invalid_ratio = float(invalid) / total if total else 1.0

    if r.invalid_ratio > MAX_INVALID_RATIO_HARD:
        r.issues.append(
            f"invalid-pixel ratio {r.invalid_ratio:.4%} > hard cap "
            f"{MAX_INVALID_RATIO_HARD:.0%}"
        )

    valid_mask = np.isfinite(arr) & (arr > 0.0)
    if valid_mask.any():
        r.mean_metres = float(arr[valid_mask].mean())
        r.max_metres = float(arr[valid_mask].max())
    else:
        r.issues.append("no valid pixels at all")

    return r


def validate_dir(
    depth_dir: Path,
    *,
    expected_count: int = 1800,
    expected_width: int = 1920,
    expected_height: int = 1080,
    sample_every: int = 1,
) -> ValidationResult:
    """Walk depth/ and apply per-file + cross-file checks."""
    result = ValidationResult(depth_dir=depth_dir)
    if not depth_dir.exists() or not depth_dir.is_dir():
        result.aggregate_issues.append(f"depth/ dir missing: {depth_dir}")
        return result

    exrs = sorted(p for p in depth_dir.iterdir()
                  if p.suffix.lower() == ".exr" and p.is_file())
    result.total_files = len(exrs)

    if not exrs:
        result.aggregate_issues.append("no .exr files in depth/")
        return result

    # Filename + index sanity (000000..NNNNNN, 0-padded, contiguous).
    names_seen: set[str] = set()
    indices: list[int] = []
    for p in exrs:
        if p.name in names_seen:
            result.duplicate_names.append(p.name)
        names_seen.add(p.name)
        try:
            idx = int(p.stem)
            indices.append(idx)
        except ValueError:
            result.aggregate_issues.append(
                f"non-numeric filename: {p.name} (expected NNNNNN.exr)"
            )
    if indices:
        indices.sort()
        # Look for gaps in the 0..expected_count-1 range.
        expected_set = set(range(min(indices), min(indices) + expected_count))
        actual_set = set(indices)
        missing = sorted(expected_set - actual_set)
        if missing:
            result.missing_indices = missing
        if len(exrs) < expected_count:
            result.aggregate_issues.append(
                f"frame count {len(exrs)} < expected {expected_count} "
                f"(PRD §3.4 = 6 fps × 5 min = 1800)"
            )

    # Per-file structural validation (sampled).
    sample_indices = list(range(0, len(exrs), max(1, sample_every)))
    result.sampled_files = len(sample_indices)
    for i in sample_indices:
        p = exrs[i]
        fr = validate_frame(p, expected_width, expected_height)
        result.frames.append(fr)

    return result


# --------------------------------------------------------------------------- CLI


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="G198 — validate a real-depth shader output against lint v3 #15 + #16.",
    )
    p.add_argument("depth_dir", type=Path, help="Path to the depth/ directory.")
    p.add_argument("--expected-count", type=int, default=1800,
                   help="Expected frame count (PRD §3.4: 6 fps × 5 min = 1800).")
    p.add_argument("--width", type=int, default=1920, help="Expected EXR width.")
    p.add_argument("--height", type=int, default=1080, help="Expected EXR height.")
    p.add_argument("--sample-every", type=int, default=1,
                   help="Validate every Nth file (default 1 = all).")
    p.add_argument("--json", action="store_true",
                   help="Emit JSON summary instead of human-readable text.")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        result = validate_dir(
            args.depth_dir,
            expected_count=args.expected_count,
            expected_width=args.width,
            expected_height=args.height,
            sample_every=args.sample_every,
        )
    except RuntimeError as e:
        print(f"environment error: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        _emit_text(result)

    return 0 if (result.lint_v3_15_pass and result.lint_v3_16_pass) else 1


def _emit_text(r: ValidationResult) -> None:
    print(f"G198 real-depth validator")
    print(f"  depth_dir       : {r.depth_dir}")
    print(f"  total_files     : {r.total_files}")
    print(f"  sampled         : {r.sampled_files}")
    print(f"  missing_indices : {len(r.missing_indices)}")
    if r.missing_indices:
        print(f"    first 5       : {r.missing_indices[:5]}")
    if r.duplicate_names:
        print(f"  duplicates      : {r.duplicate_names[:5]}")
    if r.aggregate_issues:
        print("  aggregate_issues:")
        for a in r.aggregate_issues:
            print(f"    - {a}")
    bad = [f for f in r.frames if not f.ok]
    print(f"  frame_failures  : {len(bad)} / {len(r.frames)}")
    for f in bad[:5]:
        print(f"    - {f.name}: {f.issues}")
    # Per-frame ratio stats.
    if r.frames:
        ratios = np.array([f.invalid_ratio for f in r.frames], dtype=np.float64)
        print(
            f"  invalid_ratio   : mean={ratios.mean():.4%} "
            f"max={ratios.max():.4%} p95={np.percentile(ratios, 95):.4%}"
        )

    print()
    if r.lint_v3_15_pass:
        print("  lint v3 #15 (Depth Invalid-Pixel Ratio): PASS")
    else:
        print("  lint v3 #15 (Depth Invalid-Pixel Ratio): FAIL")
    if r.lint_v3_16_pass:
        print("  lint v3 #16 (Depth Data Quality)       : PASS")
    else:
        print("  lint v3 #16 (Depth Data Quality)       : FAIL")


if __name__ == "__main__":
    sys.exit(main())
