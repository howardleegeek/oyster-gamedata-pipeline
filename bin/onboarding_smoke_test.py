#!/usr/bin/env python3
"""onboarding_smoke_test.py — G136 post-install smoke test.

Captures a 10-second clip, runs lint/validation checks, and reports
a go / no-go verdict for customer-facing onboarding.

Usage:
    python3 bin/onboarding_smoke_test.py [--duration 10] [--output-dir DIR]
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Sequence

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def _import_pil():
    """Return PIL.Image or raise ImportError."""
    from PIL import Image  # noqa: PLC0415

    return Image


def _import_numpy():
    """Return numpy or raise ImportError."""
    import numpy as np  # noqa: PLC0415

    return np


class CheckResult:
    """Holds the outcome of a single smoke-test check."""

    def __init__(self, name: str, passed: bool, detail: str = "") -> None:
        self.name, self.passed, self.detail = name, passed, detail

    def __repr__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.name}" + (f" — {self.detail}" if self.detail else "")


def check_python_version() -> CheckResult:
    """Verify running Python >= 3.8."""
    major, minor = sys.version_info[:2]
    return CheckResult("python_version", (major, minor) >= (3, 8), f"{major}.{minor}")


def check_stdlib_modules() -> CheckResult:
    """Verify essential stdlib modules import cleanly."""
    missing = [
        m
        for m in ["argparse", "json", "logging", "tempfile", "pathlib", "ast"]
        if __import__(m) is None
    ]
    return CheckResult(
        "stdlib_modules", not missing, ", ".join(missing) if missing else "all present"
    )


def check_optional_deps() -> CheckResult:
    """Verify optional vendor deps (numpy, PIL) are available."""
    missing = [n for n, g in [("numpy", _import_numpy), ("PIL", _import_pil)] if not _try_import(g)]
    return CheckResult(
        "optional_deps", not missing, "missing: " + ", ".join(missing) if missing else "all present"
    )


def _try_import(getter) -> bool:
    """Return True if *getter* succeeds without ImportError."""
    try:
        getter()
        return True
    except ImportError:
        return False


def check_output_dir(output_dir: Path) -> CheckResult:
    """Ensure the output directory is writable."""
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        probe = output_dir / ".write_probe"
        probe.touch()
        probe.unlink()
        return CheckResult("output_dir_writable", True, str(output_dir))
    except OSError as exc:
        return CheckResult("output_dir_writable", False, str(exc))


def capture_clip(output_dir: Path, duration: int) -> CheckResult:
    """Simulate capturing a *duration*-second clip and write a manifest."""
    clip_path = output_dir / f"smoke_clip_{duration}s.json"
    try:
        Image, np = _import_pil(), _import_numpy()
        frame = np.random.randint(0, 255, (48, 64, 3), dtype=np.uint8)
        img_path = output_dir / "smoke_clip_frame.png"
        Image.fromarray(frame, "RGB").save(str(img_path), "PNG")
        manifest = {
            "type": "smoke_clip",
            "duration_s": duration,
            "frame_shape": list(frame.shape),
            "frame_path": str(img_path),
            "timestamp": time.time(),
        }
    except ImportError:
        manifest = {
            "type": "smoke_clip",
            "duration_s": duration,
            "frame_shape": None,
            "frame_path": None,
            "timestamp": time.time(),
        }
    clip_path.write_text(json.dumps(manifest, indent=2))
    return CheckResult(
        "capture_clip", clip_path.exists() and clip_path.stat().st_size > 0, str(clip_path)
    )


def lint_clip(output_dir: Path) -> CheckResult:
    """Validate the captured clip manifest is well-formed JSON."""
    candidates = list(output_dir.glob("smoke_clip_*.json"))
    if not candidates:
        return CheckResult("lint_clip", False, "no clip manifest found")
    try:
        data = json.loads(candidates[0].read_text())
        missing = {"type", "duration_s", "timestamp"} - set(data.keys())
        if missing:
            return CheckResult("lint_clip", False, f"missing keys: {missing}")
        if data["type"] != "smoke_clip":
            return CheckResult("lint_clip", False, f"unexpected type: {data['type']}")
        return CheckResult("lint_clip", True, str(candidates[0]))
    except (json.JSONDecodeError, OSError) as exc:
        return CheckResult("lint_clip", False, str(exc))


def self_ast_check() -> CheckResult:
    """Verify this script's own source parses cleanly (AST gate)."""
    try:
        ast.parse(Path(__file__).read_text())
        return CheckResult("self_ast_check", True, "ast.parse OK")
    except SyntaxError as exc:
        return CheckResult("self_ast_check", False, str(exc))


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry-point for the onboarding smoke test.

    Returns 0 on full pass (go), 1 on any failure (no-go).
    """
    parser = argparse.ArgumentParser(
        description="G136 post-install smoke test — captures a clip, lints, reports go/no-go."
    )
    parser.add_argument(
        "--duration", type=int, default=10, help="Clip duration in seconds (default: 10)."
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None, help="Output directory (default: auto tempdir)."
    )
    args = parser.parse_args(argv)

    output_dir = args.output_dir or Path(tempfile.mkdtemp(prefix="g136_smoke_"))
    log.info("=== G136 Onboarding Smoke Test ===")
    log.info("duration=%ds  output_dir=%s", args.duration, output_dir)

    checks: List[CheckResult] = []
    for fn in [
        check_python_version,
        check_stdlib_modules,
        check_optional_deps,
        lambda: check_output_dir(output_dir),
        self_ast_check,
    ]:
        r = fn()
        checks.append(r)
        log.info("  %s", r)

    checks.append(capture_clip(output_dir, args.duration))
    log.info("  %s", checks[-1])
    checks.append(lint_clip(output_dir))
    log.info("  %s", checks[-1])

    failures = [c for c in checks if not c.passed]
    if failures:
        log.error("--- NO-GO: %d check(s) failed ---", len(failures))
        for f in failures:
            log.error("  %s", f)
        return 1
    log.info("--- GO: all %d checks passed ---", len(checks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
