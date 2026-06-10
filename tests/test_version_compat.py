#!/usr/bin/env python3
"""
Tests for G251 · bin/version_compat_checker.py
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bin import version_compat_checker as vcc  # noqa: E402

# ---------------------------------------------------------------------------
# Sample matrix
# ---------------------------------------------------------------------------


def _matrix() -> dict[str, Any]:
    return {
        "_meta": {"schema_version": 1},
        "entries": {
            "v0.26.0": {
                "min_pipeline": "0.1.0-rc4",
                "lint_version": 33,
                "deprecated": True,
                "deprecation_reason": "pre-rc7 — depth EXR objects not bundled",
                "support_window_end": "2026-04-30",
            },
            "v0.27.0-rc8": {
                "min_pipeline": "0.1.0-rc7",
                "lint_version": 36,
                "deprecated": False,
            },
            "v0.28.0-rc19.0.0": {
                "min_pipeline": "0.1.0-rc8",
                "lint_version": 37,
            },
            "v0.28.0-rc19.x": {
                "min_pipeline": "0.1.0-rc8",
                "lint_version": 38,
            },
        },
    }


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


class TestPipelineAtLeast:
    def test_major_minor_patch_strict(self):
        assert vcc.is_pipeline_at_least("0.2.0", "0.1.0") is True
        assert vcc.is_pipeline_at_least("0.1.0", "0.2.0") is False
        assert vcc.is_pipeline_at_least("0.1.0", "0.1.0") is True

    def test_pre_release_numeric(self):
        assert vcc.is_pipeline_at_least("0.1.0-rc10", "0.1.0-rc8") is True
        assert vcc.is_pipeline_at_least("0.1.0-rc8", "0.1.0-rc8") is True
        assert vcc.is_pipeline_at_least("0.1.0-rc7", "0.1.0-rc8") is False

    def test_no_pre_beats_pre_at_same_semver(self):
        assert vcc.is_pipeline_at_least("0.1.0", "0.1.0-rc8") is True


class TestWildcard:
    def test_x_suffix_matches_family(self):
        assert vcc._matches_wildcard("v0.28.0-rc19.0.1", "v0.28.0-rc19.x") is True
        assert vcc._matches_wildcard("v0.28.0-rc19.10", "v0.28.0-rc19.x") is True

    def test_x_suffix_does_not_match_different_family(self):
        assert vcc._matches_wildcard("v0.28.0-rc20.0", "v0.28.0-rc19.x") is False

    def test_non_wildcard_pattern_skipped(self):
        assert vcc._matches_wildcard("v0.28.0-rc19.0.1", "v0.28.0-rc19.0.1") is False


# ---------------------------------------------------------------------------
# Matrix lookup
# ---------------------------------------------------------------------------


class TestLookupEntry:
    def test_exact_match_wins(self):
        key, entry = vcc._lookup_entry("v0.28.0-rc19.0.0", _matrix())
        assert key == "v0.28.0-rc19.0.0"
        assert entry["lint_version"] == 37

    def test_wildcard_match(self):
        key, entry = vcc._lookup_entry("v0.28.0-rc19.0.5", _matrix())
        assert key == "v0.28.0-rc19.x"
        assert entry["lint_version"] == 38

    def test_no_match(self):
        key, entry = vcc._lookup_entry("v0.99.0", _matrix())
        assert key is None
        assert entry is None


# ---------------------------------------------------------------------------
# check_recorder_compat
# ---------------------------------------------------------------------------


class TestCheckRecorderCompat:
    def test_supported_version(self):
        result = vcc.check_recorder_compat(
            "v0.28.0-rc19.0.0", matrix=_matrix()
        )
        assert result.accepted is True
        assert result.matched_entry == "v0.28.0-rc19.0.0"
        assert result.lint_version == 37

    def test_wildcard_family_supported(self):
        result = vcc.check_recorder_compat(
            "v0.28.0-rc19.0.5", matrix=_matrix()
        )
        assert result.accepted is True
        assert result.matched_entry == "v0.28.0-rc19.x"

    def test_unknown_version_rejected(self):
        result = vcc.check_recorder_compat(
            "v9.9.9-future", matrix=_matrix()
        )
        assert result.accepted is False
        assert result.matched_entry is None
        assert "upgrade" in result.reason.lower()
        assert result.upgrade_url.startswith("https://github.com")

    def test_deprecated_flag_rejected(self):
        # v0.26.0 has deprecated=True and support_window_end=2026-04-30
        result = vcc.check_recorder_compat(
            "v0.26.0",
            matrix=_matrix(),
            today=_dt.date(2026, 5, 1),  # past window
        )
        assert result.accepted is False
        assert result.deprecated is True
        assert "deprecated" in result.reason.lower()

    def test_support_window_alone_triggers_rejection(self):
        m = _matrix()
        m["entries"]["v0.27.0-rc8"]["support_window_end"] = "2026-01-01"
        result = vcc.check_recorder_compat(
            "v0.27.0-rc8",
            matrix=m,
            today=_dt.date(2026, 5, 1),
        )
        assert result.accepted is False
        assert result.deprecated is True

    def test_pipeline_too_old_rejected(self):
        result = vcc.check_recorder_compat(
            "v0.28.0-rc19.0.0",
            matrix=_matrix(),
            pipeline_version="0.1.0-rc7",  # < min 0.1.0-rc8
        )
        assert result.accepted is False
        assert "pipeline" in result.reason.lower()
        assert result.min_pipeline == "0.1.0-rc8"

    def test_pipeline_at_minimum_accepted(self):
        result = vcc.check_recorder_compat(
            "v0.28.0-rc19.0.0",
            matrix=_matrix(),
            pipeline_version="0.1.0-rc8",
        )
        assert result.accepted is True

    def test_pipeline_newer_accepted(self):
        result = vcc.check_recorder_compat(
            "v0.28.0-rc19.0.0",
            matrix=_matrix(),
            pipeline_version="0.2.0",
        )
        assert result.accepted is True

    def test_invalid_recorder_version_rejected(self):
        result = vcc.check_recorder_compat("not-a-version", matrix=_matrix())
        assert result.accepted is False
        assert "recognised" in result.reason or "recognized" in result.reason

    def test_missing_recorder_version_rejected(self):
        result = vcc.check_recorder_compat(None, matrix=_matrix())
        assert result.accepted is False
        assert "missing" in result.reason.lower()

    def test_invalid_pipeline_version_rejected(self):
        result = vcc.check_recorder_compat(
            "v0.28.0-rc19.0.0",
            matrix=_matrix(),
            pipeline_version="not-a-version",
        )
        assert result.accepted is False
        assert "pipeline_version" in result.reason


# ---------------------------------------------------------------------------
# Matrix loading
# ---------------------------------------------------------------------------


class TestLoadMatrix:
    def test_default_matrix_loads(self):
        # The shipped bin/compat_matrix.json must always load cleanly.
        data = vcc.load_matrix()
        assert "entries" in data
        assert isinstance(data["entries"], dict)
        # Ensure at least one current entry
        assert any(
            "v0.28.0-rc19" in k for k in data["entries"]
        ), "shipped matrix missing v0.28.0-rc19 family"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(vcc.MatrixError):
            vcc.load_matrix(tmp_path / "nope.json")

    def test_malformed_json_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json at all", encoding="utf-8")
        with pytest.raises(vcc.MatrixError):
            vcc.load_matrix(p)

    def test_missing_entries_key_raises(self, tmp_path):
        p = tmp_path / "noentries.json"
        p.write_text(json.dumps({"_meta": {}}), encoding="utf-8")
        with pytest.raises(vcc.MatrixError):
            vcc.load_matrix(p)


# ---------------------------------------------------------------------------
# Manifest extraction
# ---------------------------------------------------------------------------


class TestManifestExtraction:
    def test_json_manifest(self):
        text = json.dumps({"recorder_version": "v0.28.0-rc19.0.1"})
        assert vcc.extract_version_from_manifest_text(text) == "v0.28.0-rc19.0.1"

    def test_json_with_camelcase_key(self):
        text = json.dumps({"recorderVersion": "v0.28.0-rc19.0.1"})
        assert vcc.extract_version_from_manifest_text(text) == "v0.28.0-rc19.0.1"

    def test_yaml_style_manifest(self):
        text = "recorder_version: v0.28.0-rc19.0.1\nfoo: bar"
        assert vcc.extract_version_from_manifest_text(text) == "v0.28.0-rc19.0.1"

    def test_yaml_quoted_value(self):
        text = "recorder_version: 'v0.28.0-rc19.0.1'\n"
        assert vcc.extract_version_from_manifest_text(text) == "v0.28.0-rc19.0.1"

    def test_no_version_returns_none(self):
        assert vcc.extract_version_from_manifest_text("{}") is None

    def test_empty_returns_none(self):
        assert vcc.extract_version_from_manifest_text("") is None


class TestTarballExtraction:
    def _make_tarball(self, tmp_path: Path, manifest_text: str, name: str = "MANIFEST.json") -> Path:
        tar_path = tmp_path / "clip.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            info = tarfile.TarInfo(name=f"clip/{name}")
            data = manifest_text.encode("utf-8")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        return tar_path

    def test_extracts_from_manifest_json(self, tmp_path):
        text = json.dumps({"recorder_version": "v0.28.0-rc19.0.1"})
        p = self._make_tarball(tmp_path, text)
        assert vcc.extract_version_from_tarball(p) == "v0.28.0-rc19.0.1"

    def test_extracts_from_yaml(self, tmp_path):
        text = "recorder_version: v0.28.0-rc19.0.0\nlint_version: 37"
        p = self._make_tarball(tmp_path, text, name="manifest.yaml")
        assert vcc.extract_version_from_tarball(p) == "v0.28.0-rc19.0.0"

    def test_missing_tarball_raises(self, tmp_path):
        with pytest.raises(vcc.ManifestError):
            vcc.extract_version_from_tarball(tmp_path / "nope.tar.gz")

    def test_tarball_without_manifest_raises(self, tmp_path):
        # Build a tarball that has no manifest at all.
        tar_path = tmp_path / "empty.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            info = tarfile.TarInfo(name="clip/video.mp4")
            data = b"\x00\x00\x00\x00"
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        with pytest.raises(vcc.ManifestError):
            vcc.extract_version_from_tarball(tar_path)


# ---------------------------------------------------------------------------
# End-to-end: tarball -> compat check
# ---------------------------------------------------------------------------


class TestE2EIntegration:
    def test_tarball_with_supported_version_accepted(self, tmp_path):
        text = json.dumps({"recorder_version": "v0.28.0-rc19.0.0"})
        tar = tmp_path / "clip.tar.gz"
        with tarfile.open(tar, "w:gz") as t:
            info = tarfile.TarInfo("clip/MANIFEST.json")
            data = text.encode("utf-8")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))

        version = vcc.extract_version_from_tarball(tar)
        result = vcc.check_recorder_compat(version, matrix=_matrix())
        assert result.accepted is True

    def test_tarball_with_unknown_version_rejected_with_upgrade_url(self, tmp_path):
        text = json.dumps({"recorder_version": "v99.0.0"})
        tar = tmp_path / "clip.tar.gz"
        with tarfile.open(tar, "w:gz") as t:
            info = tarfile.TarInfo("clip/MANIFEST.json")
            data = text.encode("utf-8")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))

        version = vcc.extract_version_from_tarball(tar)
        result = vcc.check_recorder_compat(version, matrix=_matrix())
        assert result.accepted is False
        assert "v99.0.0" in result.reason
        assert result.upgrade_url


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_cli_version_supported_returns_0(self, capsys, tmp_path):
        rc = vcc.main(
            [
                "--version",
                "v0.28.0-rc19.0.1",
                "--matrix",
                str(_REPO_ROOT / "bin" / "compat_matrix.json"),
            ]
        )
        assert rc == 0

    def test_cli_unknown_version_returns_1(self, capsys, tmp_path):
        rc = vcc.main(
            [
                "--version",
                "v9.9.9",
                "--matrix",
                str(_REPO_ROOT / "bin" / "compat_matrix.json"),
            ]
        )
        assert rc == 1

    def test_cli_missing_matrix_returns_2(self, capsys, tmp_path):
        rc = vcc.main(
            [
                "--version",
                "v0.28.0-rc19.0.1",
                "--matrix",
                str(tmp_path / "nope.json"),
            ]
        )
        assert rc == 2

    def test_cli_json_output(self, capsys, tmp_path):
        rc = vcc.main(
            [
                "--version",
                "v0.28.0-rc19.0.1",
                "--matrix",
                str(_REPO_ROOT / "bin" / "compat_matrix.json"),
                "--json",
            ]
        )
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["accepted"] is True
        assert out["recorder_version"] == "v0.28.0-rc19.0.1"
