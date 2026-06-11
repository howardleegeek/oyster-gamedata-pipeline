"""
G198 — Integration tests for bin/real_depth_validator.py.

Builds a small synthetic depth/ directory mimicking the RealDepthExporter
mod's output and asserts the validator's pass/fail booleans match what
lint v3 #15 + #16 would report on the same data.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# OpenEXR is a hard requirement for this skill — skip if absent so the
# Windows-minipc CI without OpenEXR doesn't error during collection.
pytest.importorskip("OpenEXR")

# Make bin/ importable; mirrors existing depth tests in tests/bin/.
sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from real_depth_validator import (  # noqa: E402
    INVALID_DEPTH_THRESHOLD,
    MAX_INVALID_RATIO_HARD,
    linearize_buffer,
    validate_dir,
    validate_frame,
)


def _write_synth_exr(path: Path, depth_m: np.ndarray) -> None:
    """Mirror what RealDepthExporter writes — single-channel 'Z' float32."""
    import OpenEXR  # noqa: PLC0415

    h, w = depth_m.shape

    # Use the OpenEXR 3.x v1-compat API: Header() + Channel() + OutputFile.
    try:
        import Imath  # noqa: PLC0415

        header = OpenEXR.Header(w, h)
        header["channels"] = {"Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))}
        out = OpenEXR.OutputFile(str(path), header)
        out.writePixels({"Z": depth_m.astype(np.float32).tobytes()})
        out.close()
    except ImportError:
        # OpenEXR 3.x without Imath: use the new File() API directly.
        from OpenEXR import File  # noqa: PLC0415

        header = {"compression": OpenEXR.NO_COMPRESSION, "type": OpenEXR.scanlineimage}
        channels = {"Z": depth_m.astype(np.float32)}
        f = File(header, channels)
        f.write(str(path))


def _make_scene(width: int, height: int, sky_ratio: float, seed: int) -> np.ndarray:
    """Build a synthetic depth scene with a controllable invalid ratio."""
    rng = np.random.default_rng(seed)
    # A simple linear gradient + Gaussian object — values in metres.
    x = np.linspace(0.5, 30.0, width, dtype=np.float32)
    y = np.linspace(0.5, 30.0, height, dtype=np.float32)
    X, Y = np.meshgrid(x, y)
    depth = (X + Y) * 0.5
    depth = depth.astype(np.float32)
    # Sky pixels (zeros).
    if sky_ratio > 0.0:
        n = int(depth.size * sky_ratio)
        idx = rng.choice(depth.size, size=n, replace=False)
        flat = depth.reshape(-1)
        flat[idx] = 0.0
    return depth


def _populate_dir(out_dir: Path, count: int, width: int, height: int, sky_ratio: float) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        d = _make_scene(width, height, sky_ratio, seed=i)
        _write_synth_exr(out_dir / f"{i:06d}.exr", d)


# --------------------------------------------------------------- single-frame


class TestValidateFrame:
    def test_clean_frame_passes(self, tmp_path: Path) -> None:
        scene = _make_scene(64, 32, sky_ratio=0.01, seed=1)
        path = tmp_path / "000000.exr"
        _write_synth_exr(path, scene)
        r = validate_frame(path, expected_width=64, expected_height=32)
        assert r.ok, r.issues
        assert r.has_z_channel
        assert r.is_float32
        assert r.invalid_ratio <= MAX_INVALID_RATIO_HARD

    def test_sky_heavy_frame_fails_ratio_gate(self, tmp_path: Path) -> None:
        scene = _make_scene(64, 32, sky_ratio=0.30, seed=2)
        path = tmp_path / "000000.exr"
        _write_synth_exr(path, scene)
        r = validate_frame(path, expected_width=64, expected_height=32)
        assert not r.ok
        assert any("invalid-pixel ratio" in i for i in r.issues)

    def test_resolution_mismatch_flagged(self, tmp_path: Path) -> None:
        scene = _make_scene(32, 32, sky_ratio=0.0, seed=3)
        path = tmp_path / "000000.exr"
        _write_synth_exr(path, scene)
        r = validate_frame(path, expected_width=64, expected_height=64)
        assert not r.ok
        assert any("resolution" in i for i in r.issues)


# --------------------------------------------------------------- directory


class TestValidateDir:
    def test_small_clean_set_passes(self, tmp_path: Path) -> None:
        depth_dir = tmp_path / "depth"
        _populate_dir(depth_dir, count=30, width=32, height=32, sky_ratio=0.01)
        result = validate_dir(
            depth_dir,
            expected_count=30,
            expected_width=32,
            expected_height=32,
            sample_every=1,
        )
        assert result.lint_v3_15_pass, result.aggregate_issues
        assert result.lint_v3_16_pass, result.aggregate_issues

    def test_missing_dir_fails(self, tmp_path: Path) -> None:
        result = validate_dir(tmp_path / "nope", expected_count=1)
        assert not result.lint_v3_15_pass
        assert not result.lint_v3_16_pass
        assert any("missing" in a for a in result.aggregate_issues)

    def test_sky_heavy_set_fails(self, tmp_path: Path) -> None:
        depth_dir = tmp_path / "depth"
        _populate_dir(depth_dir, count=10, width=32, height=32, sky_ratio=0.20)
        result = validate_dir(
            depth_dir,
            expected_count=10,
            expected_width=32,
            expected_height=32,
            sample_every=1,
        )
        assert not result.lint_v3_15_pass
        assert not result.lint_v3_16_pass

    def test_missing_files_caught(self, tmp_path: Path) -> None:
        depth_dir = tmp_path / "depth"
        _populate_dir(depth_dir, count=5, width=32, height=32, sky_ratio=0.01)
        # Delete the middle file.
        (depth_dir / "000002.exr").unlink()
        result = validate_dir(
            depth_dir,
            expected_count=5,
            expected_width=32,
            expected_height=32,
        )
        # lint_v3_16 should fail because of missing/missing aggregate issue.
        assert not result.lint_v3_16_pass


# --------------------------------------------------------------- buffer math


class TestLinearizeBufferEdgeCases:
    """Vectorised buffer match scalar fn for every PRD invalid path."""

    def test_uniform_sky_buffer_all_zero_output(self) -> None:
        depth = np.full(100, 1.0, dtype=np.float32)
        out = linearize_buffer(depth, near=0.5, far=30.0)
        assert np.all(out == 0.0)

    def test_invalid_planes_yield_all_zeros(self) -> None:
        depth = np.linspace(0.0, 0.9, 100, dtype=np.float32)
        out = linearize_buffer(depth, near=0.0, far=30.0)
        assert np.all(out == 0.0)

    def test_finite_output_for_valid_input(self) -> None:
        depth = np.linspace(0.0, 0.5, 100, dtype=np.float32)
        out = linearize_buffer(depth, near=0.5, far=30.0)
        assert np.all(np.isfinite(out))
        assert np.all(out > 0.0)

    def test_threshold_matches_constant(self) -> None:
        depth = np.array(
            [
                INVALID_DEPTH_THRESHOLD - 0.0001,
                INVALID_DEPTH_THRESHOLD,
                INVALID_DEPTH_THRESHOLD + 0.0001,
            ],
            dtype=np.float32,
        )
        out = linearize_buffer(depth, near=0.5, far=30.0)
        assert out[0] > 0.0
        assert out[1] == 0.0
        assert out[2] == 0.0
