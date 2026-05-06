"""Tests for IL10 artifact-honesty lint (`bin/audit_artifact_honesty.py`).

Four cases:
  1. Honest residuals (current codebase R13/R15/R16/R22/R23) → 0 violations.
  2. Fake residual: takes ``video_path`` and silent-PASSes → 1 violation.
  3. Fake residual: returns ``residual=NaN`` (no string literal) → 0 violations.
  4. Empty residual file (no functions) → 0 violations.
"""
from __future__ import annotations

from pathlib import Path

from bin.audit_artifact_honesty import audit


def test_honest_residuals_zero_violations() -> None:
    """Real residuals (R13/R15/R16/R22/R23) all carry IL10 ABSTAIN gates."""
    violations = audit()  # full real-codebase scan
    assert violations == [], "\n".join(v.format() for v in violations)


def test_fake_silent_pass_is_caught(tmp_path: Path) -> None:
    """A residual taking ``video_path`` with no ABSTAIN must trigger a violation."""
    bad = tmp_path / "fake_residual.py"
    bad.write_text(
        "def r99_fake(rec, video_path=None):\n"
        "    if video_path is None:\n"
        "        return {'passed': True}  # SILENT PASS — IL10 violation\n"
        "    return {'passed': True}\n",
        encoding="utf-8",
    )
    violations = audit([bad])
    assert len(violations) == 1
    v = violations[0]
    assert v.function == "r99_fake"
    assert v.artifact_param == "video_path"


def test_nan_residual_is_accepted(tmp_path: Path) -> None:
    """A residual that returns NaN counts as honest even without 'ABSTAIN' string."""
    ok = tmp_path / "nan_residual.py"
    ok.write_text(
        "import math\n"
        "def r99_nan(rec, manifest_path=None):\n"
        "    if manifest_path is None:\n"
        "        return {'passed': False, 'residual': math.nan}\n"
        "    return {'passed': True, 'residual': 0.0}\n",
        encoding="utf-8",
    )
    assert audit([ok]) == []


def test_empty_file_no_violations(tmp_path: Path) -> None:
    """File with no FunctionDef nodes triggers nothing."""
    empty = tmp_path / "empty_residual.py"
    empty.write_text("# placeholder, no functions yet\n", encoding="utf-8")
    assert audit([empty]) == []


def test_float_nan_string_form_accepted(tmp_path: Path) -> None:
    """Bonus: ``float('nan')`` literal also counts as an honest abstain encoding."""
    ok = tmp_path / "float_nan.py"
    ok.write_text(
        "def r99(rec, inputs_path=None):\n"
        "    if inputs_path is None:\n"
        "        return {'passed': False, 'residual': float('nan')}\n"
        "    return {'passed': True}\n",
        encoding="utf-8",
    )
    assert audit([ok]) == []
