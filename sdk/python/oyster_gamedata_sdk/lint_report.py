"""LintReport — re-uses the 24-criterion validator from bin/lint_v3_prd_grounded.py.

The SDK does NOT vendor a fork of the lint logic. Instead it dynamically
imports ``bin/lint_v3_prd_grounded.py`` if it's on the project path, and
falls back to a built-in *minimal* validator (structural-only) if not —
so that ``pip install oyster-gamedata-sdk`` works standalone on a buyer's
machine without cloning the whole repo.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import TarballStructureError


@dataclass
class LintResult:
    """One validator criterion result."""

    criterion_id: int
    name: str
    passed: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.criterion_id,
            "name": self.name,
            "passed": self.passed,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class LintReport:
    """Aggregated lint output. ``passed`` is True only when every criterion passed."""

    data_dir: Path
    results: List[LintResult] = field(default_factory=list)

    def add(self, r: LintResult) -> None:
        self.results.append(r)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_count(self) -> int:
        return self.total - self.passed_count

    @property
    def passed(self) -> bool:
        return self.failed_count == 0 and self.total > 0

    @property
    def pass_rate(self) -> float:
        return self.passed_count / self.total if self.total else 0.0

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"[{status}] {self.passed_count}/{self.total} criteria passed "
            f"({self.pass_rate*100:.1f}%)"
        )

    def failed(self) -> List[LintResult]:
        return [r for r in self.results if not r.passed]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "data_dir": str(self.data_dir),
            "summary": {
                "total": self.total,
                "passed": self.passed_count,
                "failed": self.failed_count,
                "pass_rate": f"{self.pass_rate*100:.1f}%",
                "status": "PASS" if self.passed else "FAIL",
            },
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ---------------------------------------------------------------------------
# Validator dispatch
# ---------------------------------------------------------------------------


def _find_lint_script() -> Optional[Path]:
    """Locate ``bin/lint_v3_prd_grounded.py`` by walking up from this file.

    The SDK installs into the buyer's site-packages so __file__ won't
    point at the repo root. We try, in order:
      1. ``$OYSTER_GAMEDATA_LINT_SCRIPT`` env var
      2. Walk up the SDK's __file__ looking for bin/lint_v3_prd_grounded.py
      3. ``./bin/lint_v3_prd_grounded.py`` relative to CWD
    """
    env_override = os.environ.get("OYSTER_GAMEDATA_LINT_SCRIPT")
    if env_override:
        p = Path(env_override)
        if p.is_file():
            return p

    # Walk up from this module
    here = Path(__file__).resolve()
    for ancestor in [here, *here.parents]:
        candidate = ancestor / "bin" / "lint_v3_prd_grounded.py"
        if candidate.is_file():
            return candidate
        # Also try one extra level up (e.g. sdk/python/oyster_gamedata_sdk -> repo root)
        candidate = ancestor.parent / "bin" / "lint_v3_prd_grounded.py"
        if candidate.is_file():
            return candidate

    # CWD fallback
    cwd = Path.cwd() / "bin" / "lint_v3_prd_grounded.py"
    if cwd.is_file():
        return cwd

    return None


def _load_lint_module(script_path: Path) -> Any:
    """Dynamically import bin/lint_v3_prd_grounded.py as a module."""
    spec = importlib.util.spec_from_file_location("_oyster_lint_v3", str(script_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load lint script: {script_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_oyster_lint_v3", mod)
    spec.loader.exec_module(mod)
    return mod


def _run_external_lint(data_dir: Path) -> LintReport:
    """Run bin/lint_v3_prd_grounded.py against the data dir."""
    script = _find_lint_script()
    if script is None:
        return _run_minimal_lint(data_dir)
    mod = _load_lint_module(script)
    external_rpt = mod.run_all_checks(data_dir)
    report = LintReport(data_dir=data_dir)
    for r in external_rpt.results:
        report.add(
            LintResult(
                criterion_id=r.criterion_id,
                name=r.name,
                passed=r.passed,
                message=r.message,
                details=r.details,
            )
        )
    return report


def _run_minimal_lint(data_dir: Path) -> LintReport:
    """Fallback: structural-only validation, no third-party deps.

    Checks the PRD 5-file layout (criterion 24 of the full validator):
      - video.mp4 present, >0 bytes
      - systeminfo.json present + valid JSON
      - action_camera.json present + valid JSON list
      - gameinfo.xlsx present, >0 bytes
      - depth/ directory with >0 .exr files
    """
    report = LintReport(data_dir=data_dir)

    # 1. video.mp4
    video = data_dir / "video.mp4"
    report.add(
        LintResult(
            criterion_id=1,
            name="video.mp4 present",
            passed=video.is_file() and video.stat().st_size > 0,
            message="OK" if video.is_file() else "missing video.mp4",
        )
    )

    # 2. systeminfo.json
    syscfg = data_dir / "systeminfo.json"
    ok = syscfg.is_file()
    msg = "OK"
    if ok:
        try:
            json.loads(syscfg.read_text())
        except Exception as exc:
            ok = False
            msg = f"invalid JSON: {exc}"
    else:
        msg = "missing systeminfo.json"
    report.add(LintResult(2, "systeminfo.json parses", ok, msg))

    # 3. action_camera.json
    ac = data_dir / "action_camera.json"
    ok = ac.is_file()
    n_frames = 0
    msg = "OK"
    if ok:
        try:
            data = json.loads(ac.read_text())
            if not isinstance(data, list):
                ok = False
                msg = "action_camera.json must be a JSON list"
            else:
                n_frames = len(data)
                msg = f"{n_frames} frames"
        except Exception as exc:
            ok = False
            msg = f"invalid JSON: {exc}"
    else:
        msg = "missing action_camera.json"
    report.add(LintResult(3, "action_camera.json parses", ok, msg, {"frames": n_frames}))

    # 4. gameinfo.xlsx
    gi = data_dir / "gameinfo.xlsx"
    report.add(
        LintResult(
            criterion_id=4,
            name="gameinfo.xlsx present",
            passed=gi.is_file() and gi.stat().st_size > 0,
            message="OK" if gi.is_file() else "missing gameinfo.xlsx",
        )
    )

    # 5. depth/*.exr
    depth_dir = data_dir / "depth"
    exrs: List[Path] = []
    if depth_dir.is_dir():
        exrs = sorted(depth_dir.glob("*.exr"))
    ok = len(exrs) > 0
    report.add(
        LintResult(
            criterion_id=5,
            name="depth/*.exr present",
            passed=ok,
            message=f"{len(exrs)} EXR files" if ok else "no depth/*.exr",
            details={"count": len(exrs)},
        )
    )

    return report


def run_lint(data_dir: Path) -> LintReport:
    """Public entry: run the full 24-criterion validator if available, else minimal."""
    if not data_dir.is_dir():
        raise TarballStructureError(f"Not a directory: {data_dir}")
    return _run_external_lint(data_dir)
