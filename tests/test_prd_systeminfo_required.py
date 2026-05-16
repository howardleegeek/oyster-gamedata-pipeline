#!/usr/bin/env python3
"""Tests for bin/prd_test_systeminfo_required.py"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bin" / "prd_test_systeminfo_required.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _write_json(data: dict) -> Path:
    """Write data to a temp JSON file and return the path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return Path(f.name)


def _write_yaml(data: dict) -> Path:
    """Write data to a temp YAML file and return the path."""
    import yaml
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(data, f)
    f.close()
    return Path(f.name)


# ---------------------------------------------------------------------------
# Unit tests via import
# ---------------------------------------------------------------------------

class TestLoadSysteminfo:
    """Tests for load_systeminfo function."""

    def _load(self, path: Path):
        import importlib.util
        spec = importlib.util.spec_from_file_location("systeminfo", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.load_systeminfo(path)

    def test_load_json(self):
        """Load systeminfo from JSON file."""
        data = {"gpu": "RTX 3080", "cpu": "AMD 5900X", "ram_gb": 32, "os": "Windows", "build": "10.0"}
        path = _write_json(data)
        try:
            loaded = self._load(path)
            assert loaded["gpu"] == "RTX 3080"
            assert loaded["cpu"] == "AMD 5900X"
        finally:
            path.unlink()

    def test_load_yaml(self):
        """Load systeminfo from YAML file."""
        data = {"gpu": "RTX 3080", "cpu": "AMD 5900X", "ram_gb": 32, "os": "Windows", "build": "10.0"}
        path = _write_yaml(data)
        try:
            loaded = self._load(path)
            assert loaded["gpu"] == "RTX 3080"
        finally:
            path.unlink()

    def test_unsupported_extension_raises(self):
        """Unsupported file extension should raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported file extension"):
            self._load(Path("/tmp/test.txt"))


class TestValidateRequiredKeys:
    """Tests for validate_required_keys function."""

    def _validate(self, data: dict):
        import importlib.util
        spec = importlib.util.spec_from_file_location("systeminfo", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.validate_required_keys(data)

    def test_all_keys_present(self):
        """No missing keys when all required keys are present."""
        data = {"gpu": "RTX 3080", "cpu": "AMD 5900X", "ram_gb": 32, "os": "Windows", "build": "10.0"}
        missing = self._validate(data)
        assert missing == []

    def test_single_missing_key(self):
        """Single missing key is detected."""
        data = {"gpu": "RTX 3080", "cpu": "AMD 5900X", "ram_gb": 32, "os": "Windows"}
        missing = self._validate(data)
        assert "build" in missing

    def test_multiple_missing_keys(self):
        """Multiple missing keys are detected."""
        data = {"gpu": "RTX 3080"}
        missing = self._validate(data)
        assert len(missing) == 4
        assert "cpu" in missing
        assert "ram_gb" in missing
        assert "os" in missing
        assert "build" in missing

    def test_empty_data(self):
        """Empty data has all keys missing."""
        missing = self._validate({})
        assert len(missing) == 5


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestSysteminfoRequiredCLI:
    """Tests for the CLI interface."""

    def test_help(self):
        """Script shows help with --help."""
        result = _run(["--help"])
        assert result.returncode == 0
        assert "system-info" in result.stdout.lower()

    def test_valid_systeminfo_passes(self):
        """Valid systeminfo with all keys passes."""
        data = {"gpu": "RTX 3080", "cpu": "AMD 5900X", "ram_gb": 32, "os": "Windows", "build": "10.0"}
        path = _write_json(data)
        try:
            result = _run([str(path)])
            assert result.returncode == 0, f"Expected 0, got {result.returncode}: {result.stderr}"
        finally:
            path.unlink()

    def test_missing_key_fails(self):
        """Missing key causes non-zero exit."""
        data = {"gpu": "RTX 3080", "cpu": "AMD 5900X"}  # missing ram_gb, os, build
        path = _write_json(data)
        try:
            result = _run([str(path)])
            assert result.returncode == 1, f"Expected 1, got {result.returncode}"
        finally:
            path.unlink()

    def test_missing_file_errors(self):
        """Non-existent file causes error (non-zero exit)."""
        result = _run(["/nonexistent/path/systeminfo.json"])
        assert result.returncode != 0
        assert "not found" in result.stderr.lower() or "error" in result.stderr.lower()

    def test_invalid_json_errors(self):
        """Invalid JSON causes error (non-zero exit)."""
        path = Path(tempfile.mktemp(suffix=".json"))
        path.write_text("{ invalid json }")
        try:
            result = _run([str(path)])
            assert result.returncode != 0
        finally:
            path.unlink()

    def test_yaml_support(self):
        """YAML files are supported."""
        data = {"gpu": "RTX 3080", "cpu": "AMD 5900X", "ram_gb": 32, "os": "Windows", "build": "10.0"}
        path = _write_yaml(data)
        try:
            result = _run([str(path)])
            assert result.returncode == 0, f"Expected 0, got {result.returncode}: {result.stderr}"
        finally:
            path.unlink()
