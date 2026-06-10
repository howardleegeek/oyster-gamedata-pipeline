"""Tests for bin/recorder_quality_score.py."""

from __future__ import annotations

import json

import pytest

from bin import recorder_quality_score as qs


@pytest.fixture
def clip_dir(tmp_path):
    """Create a clip dir with action_camera.json containing real data."""
    d = tmp_path / "clip-test"
    d.mkdir()
    payload = {
        "actions": [{"value": i} for i in range(20)],
        "camera": [{"x": i * 0.1, "y": i * 0.1, "z": i * 0.05} for i in range(20)],
    }
    (d / "action_camera.json").write_text(json.dumps(payload))
    return d


class TestLoadActionCamera:
    def test_split_dict_payload(self, clip_dir):
        actions, camera = qs._load_action_camera(clip_dir)
        assert len(actions) == 20
        assert len(camera) == 20

    def test_missing_file_returns_empty(self, tmp_path):
        actions, camera = qs._load_action_camera(tmp_path)
        assert actions == [] and camera == []

    def test_corrupt_json_returns_empty(self, tmp_path):
        (tmp_path / "action_camera.json").write_text("{not json")
        actions, camera = qs._load_action_camera(tmp_path)
        assert actions == [] and camera == []


class TestComputeScore:
    """Score formula must obey documented penalties."""

    def test_perfect_clip(self):
        score, farming = qs.compute_score({"anomalies": [], "metrics": {}})
        assert score == 10.0
        assert farming is False

    def test_critical_failure_forces_zero(self):
        score, farming = qs.compute_score(
            {"anomalies": [], "metrics": {}},
            critical_failures=["missing_scorable_input"],
        )
        assert score == 0.0
        assert farming is False

    def test_low_entropy_only(self):
        score, farming = qs.compute_score(
            {"anomalies": ["low_action_entropy:1.0<2.0"], "metrics": {}}
        )
        assert score == 6.0
        assert farming is False

    def test_low_entropy_and_variance_means_farming(self):
        score, farming = qs.compute_score(
            {
                "anomalies": ["low_action_entropy:0.5<2.0", "low_camera_variance:0.1<0.5"],
                "metrics": {},
            }
        )
        assert score == 3.0
        assert farming is True

    def test_score_clamped_to_zero(self):
        score, _ = qs.compute_score(
            {
                "anomalies": [
                    "low_action_entropy:0.5",
                    "low_camera_variance:0.1",
                    "other1",
                    "other2",
                    "other3",
                    "other4",
                ],
                "metrics": {},
            }
        )
        assert score == 0.0


class TestBuildClipData:
    def test_uses_dir_name_as_clip_id(self, clip_dir):
        data = qs.build_clip_data(clip_dir)
        assert data["clip_id"] == "clip-test"
        assert "actions" in data
        assert "camera" in data


class TestScoreClip:
    def test_writes_payload_with_required_fields(self, clip_dir):
        payload = qs.score_clip(clip_dir)
        for key in (
            "clip_id",
            "score",
            "farming_detected",
            "critical_failures",
            "anomalies",
            "metrics",
            "scored_at",
        ):
            assert key in payload, f"missing key {key}"
        assert 0.0 <= payload["score"] <= 10.0

    def test_empty_clip_scores_zero_not_ten(self, tmp_path):
        clip = tmp_path / "empty-clip"
        clip.mkdir()

        payload = qs.score_clip(clip)

        assert payload["score"] == 0.0
        assert "missing_scorable_input" in payload["critical_failures"]
        assert "critical_failure:missing_scorable_input" in payload["anomalies"]


class TestMain:
    def test_exit_1_on_missing_dir(self, tmp_path):
        missing = tmp_path / "nope"
        assert qs.main(["--clip-dir", str(missing)]) == 1

    def test_writes_qa_score_json(self, clip_dir):
        rc = qs.main(["--clip-dir", str(clip_dir)])
        assert rc in (0, 2)
        out = clip_dir / "qa_score.json"
        assert out.exists()
        loaded = json.loads(out.read_text())
        assert "score" in loaded

    def test_empty_clip_returns_1_and_writes_low_score(self, tmp_path):
        clip = tmp_path / "empty-clip"
        clip.mkdir()

        rc = qs.main(["--clip-dir", str(clip)])

        assert rc == 1
        loaded = json.loads((clip / "qa_score.json").read_text())
        assert loaded["score"] == 0.0
