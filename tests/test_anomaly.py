"""Tests for bin/anomaly_detector_clip_quality.py — G194 batch outlier
detection extensions.

The existing single-clip checks (action entropy, camera variance, farming
trajectory dupes) live alongside the new ``batch_outliers()`` and
``run_batch()`` entry points. Tests synthesise a batch of metric snapshots
and verify that >3 σ outliers are flagged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))

import anomaly_detector_clip_quality as ad  # noqa: E402

# ---------------------------------------------------------------------------
# helpers — build synthetic clip metric files
# ---------------------------------------------------------------------------


def make_metric_dir(tmp_path: Path, clips: list[dict]) -> Path:
    """Drop one JSON per clip into a fresh directory."""
    out = tmp_path / "batch"
    out.mkdir()
    for c in clips:
        cid = c["clip_id"]
        (out / f"{cid}.json").write_text(json.dumps(c), encoding="utf-8")
    return out


def baseline_clip(
    clip_id: str,
    *,
    fps: float = 30.0,
    size_mb: float = 800.0,
    depth_invalid: float = 0.02,
    action_entropy: float = 2.5,
) -> dict:
    return {
        "clip_id": clip_id,
        "metrics": {
            "avg_fps": fps,
            "file_size_mb": size_mb,
            "depth_invalid_ratio": depth_invalid,
            "action_entropy": action_entropy,
        },
    }


# ---------------------------------------------------------------------------
# batch outlier core
# ---------------------------------------------------------------------------


def test_batch_outliers_flags_3sigma():
    """Clip with fps far below mean (>3 σ) is flagged."""
    clips = [baseline_clip(f"c{i:03d}", fps=30.0 + (i % 5) * 0.1) for i in range(20)]
    clips.append(baseline_clip("c999", fps=5.0))  # massive outlier
    results = ad.batch_outliers(clips, sigma=3.0)
    flagged_ids = {r["clip_id"] for r in results}
    assert "c999" in flagged_ids
    c999 = next(r for r in results if r["clip_id"] == "c999")
    assert "avg_fps" in c999["outlier_metrics"]
    deviation = c999["outlier_metrics"]["avg_fps"]
    assert abs(deviation["z_score"]) > 3.0


def test_batch_outliers_silent_when_uniform():
    """No outliers when all clips are within ε of each other."""
    clips = [baseline_clip(f"c{i:03d}") for i in range(15)]
    results = ad.batch_outliers(clips, sigma=3.0)
    assert results == []


def test_batch_outliers_multiple_metrics_per_clip():
    """A clip outlying on >1 metric reports all of them."""
    clips = [baseline_clip(f"c{i:03d}") for i in range(20)]
    clips.append(baseline_clip("c999", fps=5.0, size_mb=4000.0))
    results = ad.batch_outliers(clips, sigma=3.0)
    c999 = next(r for r in results if r["clip_id"] == "c999")
    assert "avg_fps" in c999["outlier_metrics"]
    assert "file_size_mb" in c999["outlier_metrics"]


# ---------------------------------------------------------------------------
# CLI / batch runner
# ---------------------------------------------------------------------------


def test_run_batch_writes_json_and_csv(tmp_path):
    clips = [baseline_clip(f"c{i:03d}", fps=30.0 + (i % 3) * 0.2) for i in range(15)]
    clips.append(baseline_clip("bad", fps=5.0, depth_invalid=0.5))
    batch_dir = make_metric_dir(tmp_path, clips)
    out_json = tmp_path / "anomalies.json"
    out_csv = tmp_path / "anomalies.csv"
    rc = ad.run_batch(batch_dir, out_json, out_csv, sigma=3.0)
    assert rc in (0, 1)
    assert out_json.is_file()
    assert out_csv.is_file()
    data = json.loads(out_json.read_text())
    assert data["sigma"] == 3.0
    assert data["clip_count"] == 16
    assert data["outlier_count"] >= 1
    flagged = {o["clip_id"] for o in data["outliers"]}
    assert "bad" in flagged
    csv_text = out_csv.read_text()
    assert "clip_id" in csv_text
    assert "bad" in csv_text


def test_cli_batch_mode(tmp_path):
    clips = [baseline_clip(f"c{i:03d}") for i in range(10)]
    clips.append(baseline_clip("evil", fps=2.0))
    batch_dir = make_metric_dir(tmp_path, clips)
    out_json = tmp_path / "an.json"
    out_csv = tmp_path / "an.csv"
    rc = ad.main(
        [
            "--batch",
            str(batch_dir),
            "--output-json",
            str(out_json),
            "--output-csv",
            str(out_csv),
            "--sigma",
            "3.0",
        ]
    )
    assert out_json.is_file()
    assert out_csv.is_file()
    data = json.loads(out_json.read_text())
    assert "evil" in {o["clip_id"] for o in data["outliers"]}


# ---------------------------------------------------------------------------
# regression: existing per-clip analyse_clip still works
# ---------------------------------------------------------------------------


def test_analyze_clip_still_supported():
    clip = {
        "id": "c001",
        "actions": [{"value": i % 4} for i in range(20)],
        "camera": [{"x": float(i % 3), "y": 0, "z": 0} for i in range(20)],
        "trajectory": [{"x": float(i), "y": 0, "z": 0} for i in range(10)],
    }
    result = ad.analyze_clip(clip, {})
    assert result["clip_id"] == "c001"
    assert "metrics" in result


# ---------------------------------------------------------------------------
# Outlier on metric with zero variance shouldn't NaN
# ---------------------------------------------------------------------------


def test_zero_variance_metric_safe():
    """If all clips have identical depth_invalid_ratio, we shouldn't crash
    (sigma divides by 0)."""
    clips = [baseline_clip(f"c{i:03d}", depth_invalid=0.0) for i in range(10)]
    clips.append(baseline_clip("solo", depth_invalid=0.0))
    results = ad.batch_outliers(clips, sigma=3.0)
    # No metric varies → no outliers, no NaN exceptions.
    assert results == []
