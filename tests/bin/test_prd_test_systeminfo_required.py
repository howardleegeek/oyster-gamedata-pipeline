#!/usr/bin/env python3
"""Tests for bin/prd_test_systeminfo_required.py (PRD p7 #1).

PRD p7 #1 — System info must contain required keys: gpu, cpu, ram_gb, os, build.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))
import prd_test_systeminfo_required as systeminfo_required


class TestLoadSysteminfo:
    """Tests for load_systeminfo()."""

    def test_loads_json_file(self):
        """Test loading a valid JSON systeminfo file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(
                {
                    "gpu": "NVIDIA RTX 3080",
                    "cpu": "AMD Ryzen 9 5900X",
                    "ram_gb": 32,
                    "os": "Windows 11",
                    "build": "22000.1",
                },
                f,
            )
            f.flush()
            path = Path(f.name)

        try:
            data = systeminfo_required.load_systeminfo(path)
            assert data["gpu"] == "NVIDIA RTX 3080"
            assert data["cpu"] == "AMD Ryzen 9 5900X"
            assert data["ram_gb"] == 32
            assert data["os"] == "Windows 11"
            assert data["build"] == "22000.1"
        finally:
            path.unlink()

    def test_loads_yaml_file(self):
        """Test loading a valid YAML systeminfo file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(
                """gpu: NVIDIA RTX 3080
cpu: AMD Ryzen 9 5900X
ram_gb: 32
os: Windows 11
build: "22000.1"
"""
            )
            f.flush()
            path = Path(f.name)

        try:
            data = systeminfo_required.load_systeminfo(path)
            assert data["gpu"] == "NVIDIA RTX 3080"
            assert data["ram_gb"] == 32
        finally:
            path.unlink()

    def test_loads_yml_file(self):
        """Test loading a .yml file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False
        ) as f:
            f.write(
                """gpu: Intel Iris Xe
cpu: Apple M2
ram_gb: 16
os: macOS 14.0
build: "23A344"
"""
            )
            f.flush()
            path = Path(f.name)

        try:
            data = systeminfo_required.load_systeminfo(path)
            assert data["gpu"] == "Intel Iris Xe"
            assert data["os"] == "macOS 14.0"
        finally:
            path.unlink()

    def test_raises_on_unsupported_extension(self):
        """Test that unsupported file extensions raise ValueError."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            f.write("gpu: test")
            f.flush()
            path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="Unsupported file extension"):
                systeminfo_required.load_systeminfo(path)
        finally:
            path.unlink()


class TestValidateRequiredKeys:
    """Tests for validate_required_keys()."""

    def test_returns_empty_when_all_keys_present(self):
        """Test that no keys are reported missing when all are present."""
        data = {
            "gpu": "NVIDIA RTX 3080",
            "cpu": "AMD Ryzen 9 5900X",
            "ram_gb": 32,
            "os": "Windows 11",
            "build": "22000.1",
        }
        missing = systeminfo_required.validate_required_keys(data)
        assert missing == []

    def test_returns_missing_keys(self):
        """Test that missing keys are reported."""
        data = {
            "gpu": "NVIDIA RTX 3080",
            "cpu": "AMD Ryzen 9 5900X",
            # missing ram_gb, os, build
        }
        missing = systeminfo_required.validate_required_keys(data)
        assert "ram_gb" in missing
        assert "os" in missing
        assert "build" in missing
        assert "gpu" not in missing
        assert "cpu" not in missing

    def test_returns_all_missing_when_empty_data(self):
        """Test that all required keys are reported missing for empty dict."""
        data = {}
        missing = systeminfo_required.validate_required_keys(data)
        assert set(missing) == {"gpu", "cpu", "ram_gb", "os", "build"}

    def test_returns_all_missing_when_none(self):
        """Test that all required keys are reported missing for None."""
        missing = systeminfo_required.validate_required_keys(None)
        assert set(missing) == {"gpu", "cpu", "ram_gb", "os", "build"}
