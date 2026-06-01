#!/usr/bin/env python3
"""Tests for bin/prd_test_240_clip_cap.py"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bin" / "prd_test_240_clip_cap.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        timeout=10,
    )


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

def test_script_exists():
    assert SCRIPT.exists(), f"Script not found: {SCRIPT}"


def test_help():
    result = _run(["--help"])
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower() or "Usage:" in result.stdout


def test_all_cases_pass():
    result = _run(["-t", "all"])
    assert result.returncode == 0


def test_at_limit():
    result = _run(["-t", "at_limit"])
    assert result.returncode == 0


def test_below_limit():
    result = _run(["-t", "below_limit"])
    assert result.returncode == 0


def test_over_limit():
    result = _run(["-t", "over_limit"])
    assert result.returncode == 0


def test_way_over():
    result = _run(["-t", "way_over"])
    assert result.returncode == 0


def test_verbose_output():
    result = _run(["-t", "all", "-v"])
    assert result.returncode == 0
    assert "PASS" in result.stdout


def test_output_json():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        output_path = Path(f.name)
    try:
        result = _run(["-t", "all", "-o", str(output_path)])
        assert result.returncode == 0
        assert output_path.exists()
        data = json.loads(output_path.read_text())
        assert "results" in data
        assert "all_passed" in data
        assert data["all_passed"] is True
        assert "at_limit" in data["results"]
        assert "over_limit" in data["results"]
    finally:
        output_path.unlink(missing_ok=True)


def test_invalid_test_case():
    result = _run(["-t", "nonexistent"])
    assert result.returncode == 2  # argparse error


# ---------------------------------------------------------------------------
# Unit tests via import
# ---------------------------------------------------------------------------

class TestCreateMockScene:
    def _create(self, n: int):
        import importlib.util
        spec = importlib.util.spec_from_file_location("clip_cap", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.create_mock_scene(n)

    def test_zero_clips(self):
        scene = self._create(0)
        assert len(scene["clips"]) == 0

    def test_one_clip(self):
        scene = self._create(1)
        assert len(scene["clips"]) == 1
        assert scene["clips"][0]["id"] == "clip_0000"

    def test_240_clips(self):
        scene = self._create(240)
        assert len(scene["clips"]) == 240
        assert scene["clips"][239]["id"] == "clip_0239"

    def test_clip_ids_sequential(self):
        scene = self._create(5)
        ids = [c["id"] for c in scene["clips"]]
        assert ids == ["clip_0000", "clip_0001", "clip_0002", "clip_0003", "clip_0004"]

    def test_has_scene_id(self):
        scene = self._create(10)
        assert scene["scene_id"] == "test_scene_001"


class TestValidateClipCap:
    def _validate(self, scene, max_clips=240):
        import importlib.util
        spec = importlib.util.spec_from_file_location("clip_cap", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.validate_clip_cap(scene, max_clips)

    def test_empty_scene_valid(self):
        result = self._validate({"clips": []})
        assert result["valid"] is True
        assert result["clip_count"] == 0

    def test_at_limit(self):
        scene = {"clips": [{"id": f"c_{i}"} for i in range(240)]}
        result = self._validate(scene)
        assert result["valid"] is True
        assert result["clip_count"] == 240
        assert result["exceeded_by"] == 0
        assert result["stopped_at"] is None

    def test_below_limit(self):
        scene = {"clips": [{"id": f"c_{i}"} for i in range(100)]}
        result = self._validate(scene)
        assert result["valid"] is True
        assert result["exceeded_by"] == 0

    def test_over_by_one(self):
        scene = {"clips": [{"id": f"c_{i}"} for i in range(241)]}
        result = self._validate(scene)
        assert result["valid"] is False
        assert result["exceeded_by"] == 1
        assert result["stopped_at"] == 241

    def test_over_by_ten(self):
        scene = {"clips": [{"id": f"c_{i}"} for i in range(250)]}
        result = self._validate(scene)
        assert result["valid"] is False
        assert result["exceeded_by"] == 10
        assert result["stopped_at"] == 241

    def test_custom_max_clips(self):
        scene = {"clips": [{"id": f"c_{i}"} for i in range(50)]}
        result = self._validate(scene, max_clips=40)
        assert result["valid"] is False
        assert result["exceeded_by"] == 10
        assert result["max_allowed"] == 40

    def test_no_clips_key(self):
        result = self._validate({})
        assert result["valid"] is True
        assert result["clip_count"] == 0

    def test_message_format_valid(self):
        scene = {"clips": [{"id": "c_0"} for _ in range(100)]}
        result = self._validate(scene)
        assert "100/240" in result["message"]
        assert "exceeded" not in result["message"]

    def test_message_format_exceeded(self):
        scene = {"clips": [{"id": "c_0"} for _ in range(250)]}
        result = self._validate(scene)
        assert "250/240" in result["message"]
        assert "exceeded by 10" in result["message"]


class TestRunTest:
    def _run_test(self, test_case: str, verbose: bool = False):
        import importlib.util
        spec = importlib.util.spec_from_file_location("clip_cap", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.run_test(test_case, verbose)

    def test_at_limit_returns_true(self):
        assert self._run_test("at_limit") is True

    def test_below_limit_returns_true(self):
        assert self._run_test("below_limit") is True

    def test_over_limit_returns_true(self):
        # The test case expects over_limit to be invalid, so run_test returns True
        # because the validation result matches the expected outcome
        assert self._run_test("over_limit") is True

    def test_way_over_returns_true(self):
        assert self._run_test("way_over") is True

    def test_unknown_test_case(self):
        assert self._run_test("nonexistent") is False

    def test_verbose_prints_output(self, capsys):
        self._run_test("at_limit", verbose=True)
        captured = capsys.readouterr()
        assert "at_limit" in captured.out
        assert "240" in captured.out
