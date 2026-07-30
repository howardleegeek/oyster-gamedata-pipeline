"""Smoke + unit tests for bin/buyer_eval_harness.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# bin/ is on the repo root, not the SDK — load it via importlib so the
# test runs whether or not the user has the SDK installed.
import importlib.util


@pytest.fixture(scope="session")
def harness_module(repo_root: Path):
    script = repo_root / "bin" / "buyer_eval_harness.py"
    if not script.is_file():
        pytest.skip(f"harness not found at {script}")
    spec = importlib.util.spec_from_file_location("buyer_eval_harness", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["buyer_eval_harness"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestMetrics:
    def test_compute_trajectory_empty(self, harness_module):
        m = harness_module.compute_trajectory([])
        assert m.n_frames == 0
        assert m.path_length_m == 0.0

    def test_compute_trajectory_synthetic(self, harness_module, minimal_clip: Path):
        from oyster_gamedata_sdk import Tarball
        tar = Tarball.from_path(minimal_clip)
        m = harness_module.compute_trajectory(tar.action_camera)
        # Synthetic frames step camera_position.x = 0, 1, 2 → path = 2m
        assert m.path_length_m == pytest.approx(2.0)
        assert m.n_frames == 3
        assert m.bbox_span_x == pytest.approx(2.0)

    def test_compute_actions(self, harness_module, minimal_clip: Path):
        from oyster_gamedata_sdk import Tarball
        tar = Tarball.from_path(minimal_clip)
        m = harness_module.compute_actions(tar.action_camera)
        # All frames press key 87
        assert 87 in m.key_code_counts
        assert m.most_common_key == 87
        # All keys identical → entropy 0
        assert m.key_code_entropy_bits == 0.0

    def test_compute_timing(self, harness_module, minimal_clip: Path):
        from oyster_gamedata_sdk import Tarball
        tar = Tarball.from_path(minimal_clip)
        m = harness_module.compute_timing(tar.action_camera)
        assert m.fps_mean == 30.0
        assert m.fps_std == 0.0
        assert m.duration_s == pytest.approx(0.1)  # 3 frames / 30 fps

    def test_route_distribution(self, harness_module, minimal_clip: Path):
        from oyster_gamedata_sdk import Tarball
        tar = Tarball.from_path(minimal_clip)
        d = harness_module.compute_route_distribution(tar.action_camera)
        # Synthetic: 2 frames route_type=1, 1 frame route_type=2
        assert d == {1: 2, 2: 1}


class TestEvaluateClip:
    def test_synthetic(self, harness_module, minimal_clip: Path):
        report = harness_module.evaluate_clip(minimal_clip, run_lint=False)
        assert report.n_frames == 3
        assert report.resolution == (1920, 1080)
        assert report.game == "test.exe"
        assert report.error is None

    def test_corrupt_path(self, harness_module, tmp_path: Path):
        report = harness_module.evaluate_clip(tmp_path / "does-not-exist.tar.gz", run_lint=False)
        assert report.error is not None
        assert report.n_frames == 0


class TestEvaluateBatch:
    def test_two_clips(self, harness_module, minimal_clip: Path, tmp_path: Path):
        # Symlink the same clip twice to make a 2-clip batch (works even on macOS).
        import shutil
        clip_a = tmp_path / "clip-a"
        clip_b = tmp_path / "clip-b"
        shutil.copytree(minimal_clip, clip_a)
        shutil.copytree(minimal_clip, clip_b)
        report = harness_module.evaluate_batch([clip_a, clip_b], run_lint=False)
        assert report.n_clips == 2
        assert report.median_n_frames == 3
        d = report.to_dict()
        assert "summary" in d
        assert d["summary"]["n_clips"] == 2


class TestHtmlReport:
    def test_renders_html_for_empty_batch(self, harness_module, tmp_path: Path):
        empty = harness_module.BatchReport(
            batch_path=tmp_path,
            n_clips=0,
            n_passed=0,
            overall_route_distribution={},
            overall_scene_distribution={},
            overall_operator_distribution={},
            median_path_length_m=0.0,
            median_n_frames=0,
            median_action_entropy_bits=0.0,
            median_stationary_fraction=0.0,
            median_fps_std=0.0,
            clips=[],
            elapsed_seconds=0.0,
            started_at="2026-05-13 00:00:00",
        )
        html = harness_module.render_html_report(empty)
        assert "<html" in html
        assert "Buyer Evaluation Report" in html

    def test_renders_with_real_clip(self, harness_module, minimal_clip: Path):
        report = harness_module.evaluate_batch([minimal_clip], run_lint=False)
        html = harness_module.render_html_report(report)
        assert "<table" in html
        assert "route_type" in html


class TestCli:
    def test_smoke_json_only(self, harness_module, minimal_clip: Path, tmp_path: Path):
        out = tmp_path / "out"
        rc = harness_module.main([
            "--tarball", str(minimal_clip),
            "--output", str(out),
            "--no-lint",
            "--json-only",
        ])
        assert rc == 0
        json_path = out / "eval_report.json"
        assert json_path.is_file()
        payload = json.loads(json_path.read_text())
        assert payload["summary"]["n_clips"] == 1

    def test_smoke_html(self, harness_module, minimal_clip: Path, tmp_path: Path):
        out = tmp_path / "out"
        rc = harness_module.main([
            "--tarball", str(minimal_clip),
            "--output", str(out),
            "--no-lint",
        ])
        assert rc == 0
        assert (out / "eval_report.html").is_file()
        assert (out / "eval_report.json").is_file()

    def test_missing_batch_dir(self, harness_module, tmp_path: Path):
        rc = harness_module.main([
            "--batch-dir", str(tmp_path / "nope"),
            "--output", str(tmp_path / "out"),
        ])
        assert rc == 2
