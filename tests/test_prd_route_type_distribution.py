#!/usr/bin/env python3
"""Tests for bin/prd_test_route_type_distribution.py"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bin" / "prd_test_route_type_distribution.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _write_json(data) -> Path:
    """Write data to a temp JSON file and return the path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return Path(f.name)


def _write_clips_dir(clips: list[dict]) -> Path:
    """Write clips to a temp directory as individual JSON files."""
    import tempfile as tf
    d = Path(tf.mkdtemp(prefix="route_clips_"))
    for i, clip in enumerate(clips):
        (d / f"clip_{i:04d}.json").write_text(json.dumps(clip))
    return d


# ---------------------------------------------------------------------------
# Unit tests via import
# ---------------------------------------------------------------------------

class TestLoadClips:
    """Tests for load_clips function."""

    def _load_clips(self, data_dir=None, clips_file=None):
        import importlib.util
        spec = importlib.util.spec_from_file_location("route", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.load_clips(data_dir, clips_file)

    def test_load_from_file(self):
        """Load clips from a single JSON file."""
        data = [{"route_type": "highway"}, {"route_type": "city"}]
        path = _write_json(data)
        try:
            clips = self._load_clips(clips_file=path)
            assert len(clips) == 2
            assert clips[0]["route_type"] == "highway"
        finally:
            path.unlink()

    def test_load_from_dir(self):
        """Load clips from a directory of JSON files."""
        clips = [{"route_type": "highway"}, {"route_type": "city"}]
        d = _write_clips_dir(clips)
        try:
            loaded = self._load_clips(data_dir=d)
            assert len(loaded) == 2
        finally:
            import shutil
            shutil.rmtree(d)

    def test_load_single_object(self):
        """Single JSON object (not list) should be wrapped."""
        path = _write_json({"route_type": "highway"})
        try:
            clips = self._load_clips(clips_file=path)
            assert len(clips) == 1
        finally:
            path.unlink()


class TestExtractRouteTypes:
    """Tests for extract_route_types function."""

    def _extract(self, clips):
        import importlib.util
        spec = importlib.util.spec_from_file_location("route", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.extract_route_types(clips)

    def test_basic_extraction(self):
        """Extract route_type from clips."""
        clips = [{"route_type": "highway"}, {"route_type": "city"}]
        types = self._extract(clips)
        assert types == ["highway", "city"]

    def test_skips_missing(self):
        """Clips without route_type should be skipped."""
        clips = [{"route_type": "highway"}, {"other": "field"}]
        types = self._extract(clips)
        assert types == ["highway"]

    def test_skips_empty(self):
        """Empty route_type should be skipped."""
        clips = [{"route_type": ""}, {"route_type": "city"}]
        types = self._extract(clips)
        assert types == ["city"]


class TestValidateDistribution:
    """Tests for validate_distribution function."""

    def _validate(self, route_types, min_distinct=5, expected_total=240):
        import importlib.util
        spec = importlib.util.spec_from_file_location("route", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.validate_distribution(route_types, min_distinct, expected_total)

    def test_passes_with_enough_types_and_clips(self):
        """5+ distinct types and 90%+ clips should pass."""
        types = ["highway", "city", "rural", "mountain", "coastal"] * 50  # 250 clips
        success, details = self._validate(types)
        assert success is True
        assert details["distinct_types"] == 5

    def test_fails_too_few_types(self):
        """Fewer than 5 distinct types should fail."""
        types = ["highway", "city", "rural"] * 100  # 300 clips, only 3 types
        success, details = self._validate(types)
        assert success is False
        assert details["distinct_types"] == 3

    def test_fails_too_few_clips(self):
        """Fewer than 90% of expected clips should fail."""
        types = ["a", "b", "c", "d", "e"] * 10  # 50 clips, need 216
        success, details = self._validate(types)
        assert success is False
        assert details["total_clips"] == 50

    def test_custom_min_distinct(self):
        """Custom min_distinct should be respected."""
        types = ["highway", "city"] * 200
        success, details = self._validate(types, min_distinct=2)
        assert success is True

    def test_distribution_counts(self):
        """Distribution should count correctly."""
        types = ["highway"] * 100 + ["city"] * 50
        success, details = self._validate(types, min_distinct=2, expected_total=100)
        assert success is True
        assert details["distribution"]["highway"] == 100
        assert details["distribution"]["city"] == 50


class TestRouteTypeDistributionCLI:
    """End-to-end CLI tests."""

    def test_passes_with_valid_data(self):
        """Valid clips file should pass."""
        types = ["highway", "city", "rural", "mountain", "coastal"] * 50
        data = [{"route_type": t} for t in types]
        path = _write_json(data)
        try:
            result = _run(["--clips-file", str(path)])
            assert result.returncode == 0
            assert "PASS" in result.stdout
        finally:
            path.unlink()

    def test_fails_with_few_types(self):
        """Too few route types should fail."""
        data = [{"route_type": "highway"}] * 300
        path = _write_json(data)
        try:
            result = _run(["--clips-file", str(path)])
            assert result.returncode == 1
            assert "FAIL" in result.stdout or "FAIL" in result.stderr
        finally:
            path.unlink()

    def test_skips_with_too_few_clips(self):
        """Too few clips (below MIN_DATA_FRACTION) should be skipped (exit 2)."""
        data = [{"route_type": t} for t in ["a", "b", "c", "d"]]
        path = _write_json(data)
        try:
            result = _run(["--clips-file", str(path)])
            assert result.returncode == 2
            assert "SKIP" in result.stderr
        finally:
            path.unlink()

    def test_verbose_output(self):
        """--verbose should show distribution."""
        data = [{"route_type": t} for t in ["highway", "city", "rural", "mountain", "coastal"] * 50]
        path = _write_json(data)
        try:
            result = _run(["--clips-file", str(path), "--verbose"])
            assert result.returncode == 0
            assert "Distribution:" in result.stdout
        finally:
            path.unlink()

    def test_custom_expected_total(self):
        """--expected-total should be respected."""
        data = [{"route_type": t} for t in ["a", "b", "c", "d", "e"] * 20]  # 100 clips
        path = _write_json(data)
        try:
            result = _run(["--clips-file", str(path), "--expected-total", "100"])
            assert result.returncode == 0
        finally:
            path.unlink()

    def test_data_dir(self):
        """--data-dir should work with a directory of clip files."""
        clips = [{"route_type": t} for t in ["highway", "city", "rural", "mountain", "coastal"] * 50]
        d = _write_clips_dir(clips)
        try:
            result = _run(["--data-dir", str(d)])
            assert result.returncode == 0
        finally:
            import shutil
            shutil.rmtree(d)
