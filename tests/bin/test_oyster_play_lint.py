"""Tests for bin/oyster_play.py::_run_lint_step (Audit H P1).

Lock the rc-vs-report contract introduced to fix the bug where the wrapper
always returned 0 regardless of how many lint criteria failed.

Contract under test:
    - rc=0    when every criterion has ``passed: true``
    - rc=N    when N criteria have ``passed: false`` (capped at 255)
    - rc=2    when the report is missing, unparseable, or has no criteria

The lint module is stubbed before importing ``oyster_play`` so the tests run
deterministically on macOS / Linux without any of the heavy lint
dependencies (ffmpeg, OpenCV, etc.).
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _stub_lint_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Replace ``lint_v3_prd_grounded`` with a per-test stub.

    Tests inject the ``main`` callable they want by assigning to
    ``stub.main`` — the stub is yielded so each test can rewrite it.
    """
    stub = types.ModuleType("lint_v3_prd_grounded")
    stub.main = lambda argv: 0  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lint_v3_prd_grounded", stub)
    return stub


@pytest.fixture
def oyster_play(_stub_lint_module: types.ModuleType):
    """Import ``oyster_play`` lazily so the lint stub is already in place."""
    bin_dir = Path(__file__).resolve().parents[2] / "bin"
    sys.path.insert(0, str(bin_dir))
    # Force reimport so we always get the freshly-stubbed lint module.
    sys.modules.pop("oyster_play", None)
    import oyster_play as op  # noqa: PLC0415
    return op


def _write_report(argv: list[str], payload: dict) -> None:
    """Helper: read ``--output`` from argv and write the JSON payload there."""
    out_path = Path(argv[argv.index("--output") + 1])
    out_path.write_text(json.dumps(payload), encoding="utf-8")


def test_all_criteria_pass_returns_zero(_stub_lint_module, oyster_play):
    def main(argv):
        _write_report(argv, {"results": [
            {"criterion_id": 1, "passed": True},
            {"criterion_id": 2, "passed": True},
        ]})
        return 0
    _stub_lint_module.main = main

    with tempfile.TemporaryDirectory() as d:
        assert oyster_play._run_lint_step(Path(d)) == 0


def test_partial_failures_returns_failed_count(_stub_lint_module, oyster_play):
    def main(argv):
        _write_report(argv, {"results": [
            {"passed": True}, {"passed": False}, {"passed": False},
            {"passed": False}, {"passed": True},
        ]})
        return 1
    _stub_lint_module.main = main

    with tempfile.TemporaryDirectory() as d:
        assert oyster_play._run_lint_step(Path(d)) == 3


def test_failure_count_capped_at_255(_stub_lint_module, oyster_play):
    def main(argv):
        _write_report(argv, {
            "results": [{"passed": False} for _ in range(300)],
        })
        return 1
    _stub_lint_module.main = main

    with tempfile.TemporaryDirectory() as d:
        assert oyster_play._run_lint_step(Path(d)) == 255


def test_missing_report_returns_two(_stub_lint_module, oyster_play):
    _stub_lint_module.main = lambda argv: 2  # never writes the file

    with tempfile.TemporaryDirectory() as d:
        assert oyster_play._run_lint_step(Path(d)) == 2


def test_unparseable_report_returns_two(_stub_lint_module, oyster_play):
    def main(argv):
        Path(argv[argv.index("--output") + 1]).write_text(
            "not json{{", encoding="utf-8",
        )
        return 0
    _stub_lint_module.main = main

    with tempfile.TemporaryDirectory() as d:
        assert oyster_play._run_lint_step(Path(d)) == 2


def test_empty_results_returns_two(_stub_lint_module, oyster_play):
    def main(argv):
        _write_report(argv, {"results": []})
        return 0
    _stub_lint_module.main = main

    with tempfile.TemporaryDirectory() as d:
        assert oyster_play._run_lint_step(Path(d)) == 2


def test_lint_main_raises_but_report_exists(_stub_lint_module, oyster_play):
    def main(argv):
        _write_report(argv, {"results": [
            {"passed": False}, {"passed": True},
        ]})
        raise RuntimeError("lint crashed after partial write")
    _stub_lint_module.main = main

    with tempfile.TemporaryDirectory() as d:
        # One criterion failed; partial write is still authoritative.
        assert oyster_play._run_lint_step(Path(d)) == 1
