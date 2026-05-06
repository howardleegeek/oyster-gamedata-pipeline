"""Tests for bin/recorder_post_pipeline.py."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

from bin import recorder_post_pipeline as pipeline


@pytest.fixture
def clip_dir(tmp_path):
    """Create a minimal clip directory with a stub video.mp4."""
    d = tmp_path / "clip-20260506-000000"
    d.mkdir()
    (d / "video.mp4").write_bytes(b"\x00" * 16)
    return d


class TestRunPipelineSkipping:
    """Pipeline must skip phases gracefully when siblings missing."""

    def test_all_phases_skip_when_no_siblings(self, clip_dir):
        # _try_import returns None → every phase should land in 'skipped'.
        with mock.patch.object(pipeline, "_try_import", return_value=None):
            report = pipeline.run_pipeline(clip_dir)
        statuses = {p.name: p.status for p in report.phases}
        assert statuses == {
            "audio": "skipped",
            "depth": "skipped",
            "manifest": "skipped",
        }
        assert not report.hard_failed()

    def test_explicit_skip_flags(self, clip_dir):
        with mock.patch.object(pipeline, "_try_import", return_value=None):
            report = pipeline.run_pipeline(clip_dir,
                                           skip=["audio", "depth"])
        details = {p.name: p.detail for p in report.phases}
        assert "--skip-audio" in details["audio"]
        assert "--skip-depth" in details["depth"]


class TestRunPipelinePhases:
    """Each phase should pick the right sibling when available."""

    def test_audio_phase_uses_primary(self, clip_dir):
        primary = types.SimpleNamespace(
            process_clip=mock.Mock(return_value=clip_dir / "audio.flac"))

        def fake_import(name):
            if name == "recorder_audio_postprocess":
                return primary
            return None

        with mock.patch.object(pipeline, "_try_import",
                                side_effect=fake_import):
            result = pipeline.run_audio_phase(clip_dir)
        assert result.status == "ok"
        primary.process_clip.assert_called_once_with(clip_dir)

    def test_audio_phase_uses_fallback(self, clip_dir):
        fallback = types.SimpleNamespace(
            extract_and_validate=mock.Mock(return_value="ok"))

        def fake_import(name):
            if name == "audio_track_extractor":
                return fallback
            return None

        with mock.patch.object(pipeline, "_try_import",
                                side_effect=fake_import):
            result = pipeline.run_audio_phase(clip_dir)
        assert result.status == "ok"
        fallback.extract_and_validate.assert_called_once()

    def test_phase_failure_recorded(self, clip_dir):
        boom = types.SimpleNamespace(
            process_clip=mock.Mock(side_effect=RuntimeError("kaboom")))

        def fake_import(name):
            if name == "recorder_audio_postprocess":
                return boom
            return None

        with mock.patch.object(pipeline, "_try_import",
                                side_effect=fake_import):
            result = pipeline.run_audio_phase(clip_dir)
        assert result.status == "failed"
        assert "kaboom" in result.detail


class TestPipelineReport:
    """Report serialisation must round-trip through JSON cleanly."""

    def test_to_dict_serialises(self, clip_dir):
        with mock.patch.object(pipeline, "_try_import", return_value=None):
            report = pipeline.run_pipeline(clip_dir)
        as_dict = report.to_dict()
        # Must be JSON-serialisable.
        json.dumps(as_dict)
        assert as_dict["clip_dir"] == str(clip_dir)
        assert len(as_dict["phases"]) == 3


class TestMain:
    """End-to-end CLI driver."""

    def test_exit_1_on_missing_dir(self, tmp_path):
        missing = tmp_path / "nope"
        assert pipeline.main(["--clip-dir", str(missing)]) == 1

    def test_exit_0_when_all_skipped(self, clip_dir):
        with mock.patch.object(pipeline, "_try_import", return_value=None):
            assert pipeline.main(["--clip-dir", str(clip_dir)]) == 0

    def test_report_json_written(self, clip_dir, tmp_path):
        out = tmp_path / "report.json"
        with mock.patch.object(pipeline, "_try_import", return_value=None):
            pipeline.main(["--clip-dir", str(clip_dir),
                           "--report-json", str(out)])
        loaded = json.loads(out.read_text())
        assert "phases" in loaded and len(loaded["phases"]) == 3
