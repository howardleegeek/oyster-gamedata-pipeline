"""Tests for bin/multi_clip_stitcher.py — G197 scene continuity + duplicate
detection extensions.

The new entry points are:

* ``analyze_scene_continuity(clip_metas)`` — given a list of clip
  metadata dicts from the same operator + scene, totals their duration
  and flags violation of PRD §3.1 "≤ 30 min per scene".
* ``detect_duplicate_clips(clip_metas)`` — flags clips that share the
  same content hash AND the same start_time (potential fraud).
* ``run_continuity_report(...)`` — wraps the above and writes
  ``scene_continuity_report.json``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))

import multi_clip_stitcher as stitcher  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def clip(clip_id: str, *, operator="op-A", scene="scene-1",
        duration_s=300.0, sha="aaa", start=0.0) -> dict:
    return {
        "clip_id": clip_id,
        "operator_id": operator,
        "scene_id": scene,
        "duration_s": duration_s,
        "content_hash": sha,
        "start_time": start,
    }


# ---------------------------------------------------------------------------
# scene continuity (≤ 30 min)
# ---------------------------------------------------------------------------

def test_continuity_within_limit():
    """6 × 5 min clips = 30 min — at the edge, allowed."""
    metas = [clip(f"c{i:03d}", duration_s=300.0, start=i * 300.0) for i in range(6)]
    rep = stitcher.analyze_scene_continuity(metas)
    assert rep["total_duration_s"] == pytest.approx(1800.0)
    assert rep["scene_minute_cap_violation"] is False


def test_continuity_above_limit():
    """7 × 5 min clips = 35 min — should violate the ≤ 30 min rule."""
    metas = [clip(f"c{i:03d}", duration_s=300.0, start=i * 300.0) for i in range(7)]
    rep = stitcher.analyze_scene_continuity(metas)
    assert rep["total_duration_s"] == pytest.approx(2100.0)
    assert rep["scene_minute_cap_violation"] is True


def test_continuity_groups_by_operator_scene():
    metas = [
        clip("a1", operator="op-A", scene="s1", duration_s=300.0),
        clip("a2", operator="op-A", scene="s1", duration_s=300.0),
        clip("b1", operator="op-B", scene="s1", duration_s=300.0),
        clip("a3", operator="op-A", scene="s2", duration_s=300.0),
    ]
    rep = stitcher.analyze_scene_continuity(metas)
    groups = rep["groups"]
    keys = {g["operator_id"] + "|" + g["scene_id"] for g in groups}
    assert keys == {"op-A|s1", "op-A|s2", "op-B|s1"}


# ---------------------------------------------------------------------------
# duplicate / fraud detection
# ---------------------------------------------------------------------------

def test_no_duplicates_unique_hashes():
    metas = [clip(f"c{i}", sha=f"hash-{i}", start=float(i)) for i in range(5)]
    dups = stitcher.detect_duplicate_clips(metas)
    assert dups == []


def test_duplicate_detected_same_hash_and_start():
    metas = [
        clip("a", sha="HASH-X", start=10.0),
        clip("b", sha="HASH-X", start=10.0),
        clip("c", sha="HASH-X", start=99.0),   # same hash, different start
    ]
    dups = stitcher.detect_duplicate_clips(metas)
    # 'a' and 'b' form a duplicate pair; 'c' is content-similar but not
    # flagged as a fraud duplicate because the start_time differs.
    assert len(dups) == 1
    assert {"a", "b"} == set(dups[0]["clip_ids"])
    assert dups[0]["content_hash"] == "HASH-X"


def test_duplicate_tolerates_missing_hash_or_start():
    metas = [
        clip("a", sha=None, start=10.0),
        clip("b", sha=None, start=10.0),
    ]
    # Without hashes we can't claim duplicates — must not crash.
    dups = stitcher.detect_duplicate_clips(metas)
    assert dups == []


# ---------------------------------------------------------------------------
# end-to-end report
# ---------------------------------------------------------------------------

def test_run_continuity_report_writes_json(tmp_path):
    metas = [
        clip(f"a{i}", operator="op-A", scene="s1",
             duration_s=400.0, start=i * 400.0)
        for i in range(5)  # 2000 s = 33 min — over limit
    ]
    out = tmp_path / "scene_continuity_report.json"
    rc = stitcher.run_continuity_report(metas, out)
    assert out.is_file()
    payload = json.loads(out.read_text())
    assert payload["scene_minute_cap_violation"] is True
    assert payload["duplicates"] == []
    # Returns non-zero when violations exist.
    assert rc == 1


def test_run_continuity_report_clean_run(tmp_path):
    metas = [clip(f"c{i}", duration_s=200.0, start=i * 200.0, sha=f"h{i}")
             for i in range(3)]
    out = tmp_path / "scene_continuity_report.json"
    rc = stitcher.run_continuity_report(metas, out)
    assert rc == 0
    payload = json.loads(out.read_text())
    assert payload["scene_minute_cap_violation"] is False


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------

def test_cli_continuity(tmp_path):
    metas_path = tmp_path / "metas.json"
    metas = [clip(f"c{i}", duration_s=200.0, start=i * 200.0, sha=f"h{i}")
             for i in range(3)]
    metas_path.write_text(json.dumps(metas))
    out = tmp_path / "scene_continuity_report.json"
    rc = stitcher.main(["--continuity",
                        "--metadata", str(metas_path),
                        "--output", str(out)])
    assert rc in (0, 1)
    assert out.is_file()


# ---------------------------------------------------------------------------
# Regression: existing stitch_clips still works on 2 simple dirs
# ---------------------------------------------------------------------------

def test_legacy_stitch_clips_smoke(tmp_path):
    """The legacy frame-stitching path should keep working."""
    # Build two tiny clip directories.
    for i in range(2):
        d = tmp_path / f"clip_{i}"
        (d / "frames").mkdir(parents=True)
        # Drop two PNGs (just empty files — stitcher just copies them).
        for fname in ("0001.png", "0002.png"):
            (d / "frames" / fname).write_bytes(b"\x89PNG\r\n\x1a\n")
        meta = {
            "scene_id": "test", "fps": 30, "resolution": "1920x1080",
            "timestamps": [float(i * 2), float(i * 2 + 1)],
            "frame_ids": [i * 2, i * 2 + 1],
        }
        (d / "metadata.json").write_text(json.dumps(meta))

    out = tmp_path / "stitched"
    manifest = stitcher.stitch_clips(
        [tmp_path / "clip_0", tmp_path / "clip_1"],
        out, copy_frames=True,
    )
    assert manifest["num_clips"] == 2
    assert manifest["total_frames"] == 4
    assert (out / "stitch_manifest.json").is_file()
    assert (out / "metadata.json").is_file()
