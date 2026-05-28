#!/usr/bin/env python3
"""Tests for H8 PASS_STRICT tier in prd_compliance_audit.

Three tiers for kind=engine_zbuffer:
  - PASS_STRICT: gap_miss_ratio < 0.01 (≥99% engine truth)
  - PASS:        0.01 <= gap_miss_ratio <= 0.10
  - PASS_DEGRADED: gap_miss_ratio > 0.10 (still status="PASS" with degraded evidence)
"""

import json
import sys
from pathlib import Path

# Ensure bin/ is on the import path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

from prd_compliance_audit import _evaluate_h8, _find_video, audit_group_a


def _make_valid_exr(path: Path) -> None:
    """Write a minimal valid OpenEXR file so the EXR-readability check passes."""
    try:
        import Imath
        import OpenEXR

        header = OpenEXR.Header(1, 1)
        header["channels"] = {"Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))}
        exr = OpenEXR.OutputFile(str(path), header)
        exr.writePixels({"Z": b"\x00\x00\x00\x00"})
        exr.close()
    except ImportError:
        # If OpenEXR isn't available, just write a dummy file;
        # the audit will fall back to os.access(R_OK).
        path.write_bytes(b"\x00" * 64)


def _make_session(tmp_path: Path, gap_miss_ratio: float) -> Path:
    """Create a synthetic session dir with depth/.source and a dummy EXR."""
    depth_dir = tmp_path / "depth"
    depth_dir.mkdir()
    source = depth_dir / ".source"
    source.write_text(
        json.dumps(
            {
                "kind": "engine_zbuffer",
                "frame_count": 100,
                "gap_miss_ratio": gap_miss_ratio,
            }
        )
    )
    _make_valid_exr(depth_dir / "frame_0001.exr")
    return tmp_path


class TestH8PassStrictTier:
    """H8 three-tier evaluation for engine_zbuffer."""

    def test_gap_0_005_returns_pass_strict(self, tmp_path: Path):
        """gap_miss_ratio=0.005 (<1%) → PASS_STRICT."""
        session = _make_session(tmp_path, gap_miss_ratio=0.005)
        result = _evaluate_h8(session)
        assert result["id"] == "H8"
        assert result["status"] == "PASS_STRICT"
        assert "strict tier" in result["evidence"].lower()
        assert "≥99%" in result["evidence"] or "99%" in result["evidence"]

    def test_gap_0_05_returns_pass(self, tmp_path: Path):
        """gap_miss_ratio=0.05 (5%, between 1% and 10%) → PASS."""
        session = _make_session(tmp_path, gap_miss_ratio=0.05)
        result = _evaluate_h8(session)
        assert result["id"] == "H8"
        assert result["status"] == "PASS"
        assert "PASS_DEGRADED" not in result["evidence"]

    def test_gap_0_15_returns_pass_degraded(self, tmp_path: Path):
        """gap_miss_ratio=0.15 (15%, >10%) → PASS with PASS_DEGRADED evidence."""
        session = _make_session(tmp_path, gap_miss_ratio=0.15)
        result = _evaluate_h8(session)
        assert result["id"] == "H8"
        assert result["status"] == "PASS"
        assert "PASS_DEGRADED" in result["evidence"]


def test_find_video_accepts_lite_video_name(tmp_path: Path):
    (tmp_path / "video.mp4").write_bytes(b"fake-video")

    assert _find_video(tmp_path) == tmp_path / "video.mp4"


def test_audit_group_a_accepts_lite_video_name(tmp_path: Path):
    (tmp_path / "video.mp4").write_bytes(b"fake-video")
    (tmp_path / "action_camera.json").write_text("[]")
    (tmp_path / "gameinfo.xlsx").write_bytes(b"xlsx")
    (tmp_path / "metadata.json").write_text("{}")

    a1 = next(item for item in audit_group_a(tmp_path) if item["id"] == "A1")

    assert a1["status"] == "PASS"
    assert "video.mp4" in a1["evidence"]
