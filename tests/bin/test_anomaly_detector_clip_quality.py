#!/usr/bin/env python3
"""Tests for bin/anomaly_detector_clip_quality.py — clip-quality anomaly detector.

Covers:
  * _entropy (empty list, single value, uniform distribution, custom bins)
  * _variance (empty list, single value, known values)
  * _norm_trajectory (empty, single point, multi-point translation invariance)
  * _hash_trajectory (precision rounding, identical after translate)
  * analyze_clip (action-entropy threshold, camera-variance threshold,
    trajectory hash presence, anomalies list structure, clip_id fallback)
  * detect_farming (N>=threshold, N<threshold, single group vs multiple)
  * load_clips (.json list, .jsonl, mixed, dir scan, corrupt file warning)
  * parse_args (defaults, custom thresholds, quiet flag)
  * main() (missing input exits 1, complete-clean exits 0, anomalies exit 1,
    JSON file output path)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from bin.anomaly_detector_clip_quality import (  # noqa: E402
    _entropy,
    _hash_trajectory,
    _norm_trajectory,
    _variance,
    analyze_clip,
    detect_farming,
    load_clips,
    main,
    parse_args,
    run_detection,
)

# ---------------------------------------------------------------------------
# _entropy
# ---------------------------------------------------------------------------


class TestEntropy:
    """Shannon entropy of a distribution."""

    def test_empty_list_returns_zero(self):
        assert _entropy([]) == 0.0

    def test_single_value_returns_zero(self):
        """A single value lands in one bin → probability 1.0 → entropy 0."""
        assert _entropy([0.5]) == 0.0

    def test_uniform_distribution_is_positive(self):
        """A spread of values across bins should yield positive entropy."""
        # 10 values spread evenly across the [0, 10) range
        e = _entropy(list(range(10)), bins=10)
        assert e > 0.0
        # log2(10) ≈ 3.32 is the maximum entropy for 10 bins; expect close to it
        assert e == pytest.approx(3.32, abs=0.01)

    def test_single_bin_uses_custom_bins(self):
        """Pass bins=1: only one bin populated → entropy 0."""
        assert _entropy([1.0, 2.0, 3.0], bins=1) == 0.0


# ---------------------------------------------------------------------------
# _variance
# ---------------------------------------------------------------------------


class TestVariance:
    """Variance computation."""

    def test_empty_list_returns_zero(self):
        assert _variance([]) == 0.0

    def test_single_value_returns_zero(self):
        assert _variance([42.0]) == 0.0

    def test_known_values(self):
        """Variance of [1, 2, 3, 4, 5] is 2.0 (population)."""
        assert _variance([1.0, 2.0, 3.0, 4.0, 5.0]) == pytest.approx(2.0)

    def test_identical_values_returns_zero(self):
        assert _variance([7.0, 7.0, 7.0, 7.0]) == 0.0


# ---------------------------------------------------------------------------
# _norm_trajectory
# ---------------------------------------------------------------------------


class TestNormTrajectory:
    """Translation-invariant normalized trajectory."""

    def test_empty_trajectory_returns_empty_tuple(self):
        assert _norm_trajectory([]) == ()

    def test_single_point_returns_zero_normalized(self):
        """One point → no diffs → returns that single (0,0,0)-centered point."""
        result = _norm_trajectory([{"x": 5.0, "y": 3.0, "z": 1.0}])
        # After centering: (0,0,0)
        assert result == ((0.0, 0.0, 0.0),)

    def test_translation_invariance(self):
        """Same shape, different translation → same normalized trajectory."""
        a = [{"x": 0.0, "y": 0.0, "z": 0.0}, {"x": 1.0, "y": 0.0, "z": 0.0}]
        b = [{"x": 100.0, "y": 50.0, "z": -20.0}, {"x": 101.0, "y": 50.0, "z": -20.0}]
        assert _norm_trajectory(a) == _norm_trajectory(b)

    def test_missing_keys_default_to_zero(self):
        """Missing x/y/z keys default to 0."""
        result = _norm_trajectory([{}, {"x": 1.0}])
        # First point is (0,0,0), second is (1,0,0) — after centering the
        # midpoint is (0.5,0,0), so points are (-0.5,0,0) and (0.5,0,0).
        # With 2 points: diff is (1,0,0), normalized is (1,0,0).
        assert result == ((1.0, 0.0, 0.0),)


# ---------------------------------------------------------------------------
# _hash_trajectory
# ---------------------------------------------------------------------------


class TestHashTrajectory:
    """Hashable string representation of a trajectory."""

    def test_identical_after_translation(self):
        """Translated trajectories hash to the same string."""
        a = [{"x": 0.0, "y": 0.0, "z": 0.0}, {"x": 1.0, "y": 0.0, "z": 0.0}]
        b = [{"x": 50.0, "y": 50.0, "z": 50.0}, {"x": 51.0, "y": 50.0, "z": 50.0}]
        assert _hash_trajectory(a) == _hash_trajectory(b)

    def test_returns_string(self):
        result = _hash_trajectory([{"x": 0.0, "y": 0.0, "z": 0.0}, {"x": 1.0, "y": 0.0, "z": 0.0}])
        assert isinstance(result, str)

    def test_precision_rounds_to_2dp_by_default(self):
        """Default precision is 2 decimal places."""
        a = [{"x": 0.0, "y": 0.0, "z": 0.0}, {"x": 1.0, "y": 0.0, "z": 0.0}]
        # Bump the second point by 0.001 — default precision=2 rounds it away
        b = [{"x": 0.0, "y": 0.0, "z": 0.0}, {"x": 1.001, "y": 0.0, "z": 0.0}]
        assert _hash_trajectory(a) == _hash_trajectory(b)

    def test_different_directions_different_hash(self):
        """Distinct turn directions produce distinct hashes."""
        # Two-segment trajectory: turn right vs turn left.
        a = [
            {"x": 0.0, "y": 0.0, "z": 0.0},
            {"x": 1.0, "y": 0.0, "z": 0.0},
            {"x": 1.0, "y": 1.0, "z": 0.0},
        ]
        b = [
            {"x": 0.0, "y": 0.0, "z": 0.0},
            {"x": 1.0, "y": 0.0, "z": 0.0},
            {"x": 1.0, "y": -1.0, "z": 0.0},
        ]
        assert _hash_trajectory(a) != _hash_trajectory(b)


# ---------------------------------------------------------------------------
# analyze_clip
# ---------------------------------------------------------------------------


class TestAnalyzeClip:
    """Single-clip anomaly analysis."""

    def test_clip_id_from_id_key(self):
        result = analyze_clip({"id": "clip-1"}, {})
        assert result["clip_id"] == "clip-1"

    def test_clip_id_from_clip_id_key(self):
        result = analyze_clip({"clip_id": "clip-2"}, {})
        assert result["clip_id"] == "clip-2"

    def test_clip_id_defaults_to_unknown(self):
        result = analyze_clip({}, {})
        assert result["clip_id"] == "unknown"

    def test_low_action_entropy_flagged(self):
        """All same action → entropy 0 → flagged if threshold > 0."""
        clip = {"id": "c1", "actions": [{"value": 1}, {"value": 1}, {"value": 1}]}
        result = analyze_clip(clip, {"action_entropy_threshold": 1.0})
        assert any("low_action_entropy" in a for a in result["anomalies"])
        assert "action_entropy" in result["metrics"]

    def test_high_action_entropy_not_flagged(self):
        """Spread actions → entropy above threshold → not flagged."""
        clip = {"id": "c1", "actions": [{"value": i} for i in range(20)]}
        result = analyze_clip(clip, {"action_entropy_threshold": 2.0})
        assert not any("low_action_entropy" in a for a in result["anomalies"])

    def test_low_camera_variance_flagged(self):
        """Static camera → low variance → flagged if threshold > 0."""
        clip = {"id": "c1", "camera": [{"x": 1, "y": 1, "z": 1}] * 5}
        result = analyze_clip(clip, {"camera_variance_threshold": 0.5})
        assert any("low_camera_variance" in a for a in result["anomalies"])
        assert "camera_variance" in result["metrics"]

    def test_trajectory_hash_present_when_trajectory_given(self):
        clip = {"id": "c1", "trajectory": [{"x": 0, "y": 0, "z": 0}, {"x": 1, "y": 0, "z": 0}]}
        result = analyze_clip(clip, {})
        assert "trajectory_hash" in result["metrics"]
        assert isinstance(result["metrics"]["trajectory_hash"], str)

    def test_no_anomaly_keys_when_empty_clip(self):
        """Empty clip → no anomalies, no metrics."""
        result = analyze_clip({"id": "c1"}, {})
        assert result["anomalies"] == []
        assert result["metrics"] == {}

    def test_action_value_fallback_to_action_key(self):
        """If 'value' missing, falls back to 'action' key."""
        clip = {"id": "c1", "actions": [{"action": 1}, {"action": 1}]}
        result = analyze_clip(clip, {"action_entropy_threshold": 0.5})
        # All same → low entropy
        assert any("low_action_entropy" in a for a in result["anomalies"])


# ---------------------------------------------------------------------------
# detect_farming
# ---------------------------------------------------------------------------


class TestDetectFarming:
    """Group identical trajectories and flag if N >= threshold."""

    def test_no_farming_with_no_trajectories(self):
        results = [{"clip_id": "c1", "metrics": {}}]
        assert detect_farming(results, n_clips=3) == []

    def test_farming_when_count_meets_threshold(self):
        """3 identical trajectory hashes → flagged when n_clips=3."""
        traj = [{"x": 0, "y": 0, "z": 0}, {"x": 1, "y": 0, "z": 0}]
        h = _hash_trajectory(traj)
        results = [
            {"clip_id": f"c{i}", "metrics": {"trajectory_hash": h}}
            for i in range(3)
        ]
        farming = detect_farming(results, n_clips=3)
        assert len(farming) == 1
        assert farming[0]["count"] == 3
        assert farming[0]["anomaly"] == "identical_trajectory_pattern"
        assert farming[0]["clip_ids"] == ["c0", "c1", "c2"]

    def test_no_farming_below_threshold(self):
        """2 identical trajectories with n_clips=3 → not flagged."""
        traj = [{"x": 0, "y": 0, "z": 0}, {"x": 1, "y": 0, "z": 0}]
        h = _hash_trajectory(traj)
        results = [
            {"clip_id": f"c{i}", "metrics": {"trajectory_hash": h}}
            for i in range(2)
        ]
        assert detect_farming(results, n_clips=3) == []

    def test_distinct_trajectories_no_farming(self):
        results = [
            {"clip_id": "c0", "metrics": {"trajectory_hash": "hash_A"}},
            {"clip_id": "c1", "metrics": {"trajectory_hash": "hash_B"}},
            {"clip_id": "c2", "metrics": {"trajectory_hash": "hash_C"}},
        ]
        assert detect_farming(results, n_clips=2) == []


# ---------------------------------------------------------------------------
# load_clips
# ---------------------------------------------------------------------------


class TestLoadClips:
    """File and directory loading."""

    def test_load_json_list(self, tmp_path):
        path = tmp_path / "clips.json"
        path.write_text(json.dumps([{"id": "c1"}, {"id": "c2"}]))
        clips = load_clips(path)
        assert len(clips) == 2
        assert clips[0]["id"] == "c1"

    def test_load_jsonl(self, tmp_path):
        path = tmp_path / "clips.jsonl"
        path.write_text('{"id": "c1"}\n{"id": "c2"}\n')
        clips = load_clips(path)
        assert len(clips) == 2
        assert clips[1]["id"] == "c2"

    def test_load_single_object_json(self, tmp_path):
        """Single dict (not list) is wrapped into a list of one."""
        path = tmp_path / "clip.json"
        path.write_text(json.dumps({"id": "c1"}))
        clips = load_clips(path)
        assert clips == [{"id": "c1"}]

    def test_load_directory_with_mixed_files(self, tmp_path):
        (tmp_path / "a.json").write_text(json.dumps([{"id": "a1"}]))
        (tmp_path / "b.jsonl").write_text('{"id": "b1"}\n{"id": "b2"}\n')
        clips = load_clips(tmp_path)
        assert len(clips) == 3

    def test_corrupt_file_in_directory_warns_to_stderr(self, tmp_path, capsys):
        """A bad file inside a directory triggers a stderr warning and is skipped."""
        (tmp_path / "good.json").write_text(json.dumps([{"id": "g1"}]))
        (tmp_path / "bad.json").write_text("{not valid json")
        clips = load_clips(tmp_path)
        captured = capsys.readouterr()
        # The good file is loaded; the bad one emits a warning.
        assert any(c["id"] == "g1" for c in clips)
        assert "Warning" in captured.err

    def test_directory_with_corrupt_file_still_loads_others(self, tmp_path, capsys):
        """One bad file does not abort directory load."""
        (tmp_path / "good.json").write_text(json.dumps([{"id": "g1"}]))
        (tmp_path / "bad.json").write_text("{not valid")
        clips = load_clips(tmp_path)
        assert len(clips) == 1
        assert clips[0]["id"] == "g1"


# ---------------------------------------------------------------------------
# run_detection
# ---------------------------------------------------------------------------


class TestRunDetection:
    """Full pipeline: load → analyze → farm-detect → emit."""

    def test_empty_input_returns_error_status(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text(json.dumps([]))
        result = run_detection(path, {})
        assert result["status"] == "error"
        assert result["total_clips"] == 0

    def test_clean_clips_complete_with_no_anomalies(self, tmp_path):
        path = tmp_path / "clean.json"
        clip = {
            "id": "c1",
            "actions": [{"value": i} for i in range(20)],
            "camera": [{"x": i, "y": i, "z": i} for i in range(20)],
        }
        path.write_text(json.dumps([clip]))
        result = run_detection(path, {})
        assert result["status"] == "complete"
        assert result["total_clips"] == 1
        assert result["clips_with_anomalies"] == 0
        assert result["farming_patterns_detected"] == 0

    def test_anomalous_clips_counted(self, tmp_path):
        path = tmp_path / "bad.json"
        # All-same action → low entropy
        clip = {"id": "c1", "actions": [{"value": 1}] * 10}
        path.write_text(json.dumps([clip]))
        result = run_detection(path, {"action_entropy_threshold": 0.5})
        assert result["clips_with_anomalies"] == 1

    def test_output_file_written(self, tmp_path):
        path = tmp_path / "in.json"
        out = tmp_path / "out.json"
        path.write_text(json.dumps([{"id": "c1"}]))
        result = run_detection(path, {}, out)
        assert out.exists()
        loaded = json.loads(out.read_text())
        assert loaded["status"] == result["status"]


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    """argparse behavior."""

    def test_defaults(self):
        args = parse_args(["in.json"])
        assert args.action_entropy_threshold == 2.0
        assert args.camera_variance_threshold == 0.5
        assert args.farming_n_clips == 3
        assert args.quiet is False
        assert args.output is None

    def test_custom_thresholds(self):
        args = parse_args([
            "in.json",
            "--action-entropy-threshold", "3.5",
            "--camera-variance-threshold", "1.0",
            "--farming-n-clips", "5",
            "-q",
        ])
        assert args.action_entropy_threshold == 3.5
        assert args.camera_variance_threshold == 1.0
        assert args.farming_n_clips == 5
        assert args.quiet is True

    def test_short_quiet_flag(self):
        args = parse_args(["in.json", "-q"])
        assert args.quiet is True


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMain:
    """CLI entry point."""

    def test_missing_input_file_exits_1(self, capsys):
        rc = main(["/nonexistent/path.json"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "Error" in captured.err or "does not exist" in captured.err

    def test_clean_input_exits_0(self, tmp_path, capsys):
        path = tmp_path / "clean.json"
        clip = {
            "id": "c1",
            "actions": [{"value": i} for i in range(20)],
            "camera": [{"x": i, "y": i, "z": i} for i in range(20)],
        }
        path.write_text(json.dumps([clip]))
        rc = main([str(path)])
        assert rc == 0
        captured = capsys.readouterr()
        assert "Analysis complete" in captured.out

    def test_anomalous_input_exits_1(self, tmp_path, capsys):
        path = tmp_path / "bad.json"
        clip = {"id": "c1", "actions": [{"value": 1}] * 10}
        path.write_text(json.dumps([clip]))
        rc = main([str(path), "--action-entropy-threshold", "0.1"])
        assert rc == 1

    def test_quiet_flag_suppresses_output(self, tmp_path, capsys):
        path = tmp_path / "clean.json"
        clip = {
            "id": "c1",
            "actions": [{"value": i} for i in range(20)],
            "camera": [{"x": i, "y": i, "z": i} for i in range(20)],
        }
        path.write_text(json.dumps([clip]))
        rc = main([str(path), "-q"])
        assert rc == 0
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_output_flag_writes_json(self, tmp_path, capsys):
        path = tmp_path / "in.json"
        out = tmp_path / "out.json"
        path.write_text(json.dumps([{"id": "c1"}]))
        rc = main([str(path), "-o", str(out), "-q"])
        assert rc == 0
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["status"] == "complete"
