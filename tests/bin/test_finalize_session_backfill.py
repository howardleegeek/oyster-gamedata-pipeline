#!/usr/bin/env python3
"""
Tests for bin/finalize_session.py backfill_action_camera()

Regression coverage for rc19.0.2.1 fix:
- Frame key names changed in rc19 (`frame_index`/`timestamp_ns`) vs older
  (`idx`/`t_ns`). Backfill must accept both.
- Timestamps must be aligned correctly across units:
    action_camera.timestamp_ns  — nanoseconds since recording start (monotonic)
    game_state.timestamp_ms     — Unix-epoch wall-clock milliseconds
  Naively comparing them yields zero matches (the symptom this test guards).
- `scene_name` must be backfilled from game_state.dimension per PRD §3.2.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

# Import the finalize_session module from the sibling bin/ directory.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))
from finalize_session import (  # noqa: E402
    _get_frame_index,
    _get_frame_t_ns,
    backfill_action_camera,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_rc19_session(
    session_dir: Path, n_frames: int = 400, n_ticks: int = 8000, wall_clock_ms: int = 1778729962028
) -> None:
    """Write an rc19-style session: timestamp_ns + frame_index in action_camera."""
    # action_camera frames spaced ~33 ms apart (≈30 fps), starting at t_ns=10133900
    # to mirror the real rc19.0.2 session we have on disk.
    ac = []
    for i in range(n_frames):
        t_ns = 10133900 + i * 33_333_333  # 30 fps in ns
        ac.append(
            {
                "frame_index": i,
                "frame_number": i,
                "timestamp": t_ns / 1_000_000_000.0,
                "timestamp_ns": t_ns,
                "camera_position": None,
                "rotation_quaternion": None,
                "camera_rotation_quaternion": None,
                "player_position": None,
                "player_rotation_quaternion": None,
                "Follow_Offset": None,
                "rotation_oula": None,
                "speed": 0.0,
                "player_speed": 0.0,
                "camera_speed": None,
                "metric_scale": 1.0,
            }
        )
    (session_dir / "action_camera.json").write_text(json.dumps(ac))

    # game_state ticks at 20 Hz (mc-mod default) covering the same span.
    # First tick anchors recording start; abs_ms_per_tick = 50.
    with (session_dir / "game_state.jsonl").open("w") as f:
        for tick in range(n_ticks):
            obj = {
                "tick": tick,
                "timestamp_ms": wall_clock_ms + tick * 50,
                "x": 100.0 + tick * 0.01,  # drifts so backfill values are distinct
                "y": 70.0,
                "z": 200.0,
                "yaw_deg": -164.55 + tick * 0.001,
                "pitch_deg": 11.40,
                "velocity_x": 0.5,
                "velocity_y": 0.0,
                "velocity_z": 0.5,
                "dimension": "minecraft:overworld",
                "game_mode": "SURVIVAL",
            }
            f.write(json.dumps(obj) + "\n")


def _write_rc17_session(session_dir: Path, n_frames: int = 20) -> None:
    """Write an older-style session: idx + t_ns in action_camera."""
    ac = []
    for i in range(n_frames):
        t_ns = i * 33_333_333
        ac.append(
            {
                "idx": i,
                "t_ns": t_ns,
                "camera_position": None,
                "rotation_quaternion": None,
            }
        )
    (session_dir / "action_camera.json").write_text(json.dumps(ac))

    with (session_dir / "game_state.jsonl").open("w") as f:
        for tick in range(n_frames * 2):
            obj = {
                "tick": tick,
                "timestamp_ms": 1700000000000 + tick * 50,
                "x": 1.0,
                "y": 64.0,
                "z": 2.0,
                "yaw_deg": 0.0,
                "pitch_deg": 0.0,
                "velocity_x": 0.0,
                "velocity_y": 0.0,
                "velocity_z": 0.0,
                "dimension": "minecraft:the_nether",
                "game_mode": "SURVIVAL",
            }
            f.write(json.dumps(obj) + "\n")


# ---------------------------------------------------------------------------
# Helper-function tests
# ---------------------------------------------------------------------------


class TestFrameKeyHelpers:
    def test_rc19_timestamp_ns_key(self):
        assert _get_frame_t_ns({"timestamp_ns": 123, "frame_index": 0}) == 123

    def test_rc17_t_ns_key(self):
        assert _get_frame_t_ns({"t_ns": 456, "idx": 0}) == 456

    def test_timestamp_seconds_fallback(self):
        # If only floating-seconds `timestamp` is present, convert to ns.
        assert _get_frame_t_ns({"timestamp": 1.5}) == 1_500_000_000

    def test_no_timestamp_returns_none(self):
        assert _get_frame_t_ns({"frame_index": 0}) is None

    def test_rc19_frame_index_key(self):
        assert _get_frame_index({"frame_index": 7, "frame_number": 7}) == 7

    def test_rc17_idx_key(self):
        assert _get_frame_index({"idx": 9}) == 9

    def test_frame_number_fallback(self):
        # rc19 also writes frame_number; if frame_index is missing it should
        # still resolve.
        assert _get_frame_index({"frame_number": 11}) == 11


# ---------------------------------------------------------------------------
# Backfill integration tests
# ---------------------------------------------------------------------------


class TestBackfillActionCamera:
    """Drive backfill_action_camera against synthetic sessions."""

    def test_rc19_session_backfills_all_frames(self, tmp_path: Path):
        """Regression: rc19 keys (`frame_index`, `timestamp_ns`) must yield
        n_frames patched, not 0 (the original bug)."""
        _write_rc19_session(tmp_path, n_frames=400)

        patched = backfill_action_camera(tmp_path)

        assert patched == 400, (
            f"rc19 session: expected 400 frames backfilled, got {patched}. "
            "If you see 0, the function probably looked for old key names "
            "(`idx`/`t_ns`) instead of rc19's `frame_index`/`timestamp_ns`."
        )

    def test_rc19_session_populates_quaternion_and_position(self, tmp_path: Path):
        """Every backfilled frame must have a non-None quaternion + position."""
        _write_rc19_session(tmp_path, n_frames=50)

        backfill_action_camera(tmp_path)

        with (tmp_path / "action_camera.json").open() as f:
            ac = json.load(f)

        for i, frame in enumerate(ac):
            assert frame["camera_rotation_quaternion"] is not None, f"frame {i} quat is None"
            assert len(frame["camera_rotation_quaternion"]) == 4, f"frame {i} quat not xyzw"
            # Unit norm
            qx, qy, qz, qw = frame["camera_rotation_quaternion"]
            n = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
            assert abs(n - 1.0) < 1e-6, f"frame {i} quat not normalized: {n}"

            assert frame["camera_position"] is not None, f"frame {i} position is None"
            assert len(frame["camera_position"]) == 3, f"frame {i} position not xyz"

            assert frame["player_position"] is not None
            assert frame["player_rotation_quaternion"] is not None
            assert frame["Follow_Offset"] == [0.0, 0.0, 0.0]

    def test_rc19_session_populates_scene_name(self, tmp_path: Path):
        """PRD §3.2: scene_name must be backfilled from game_state.dimension."""
        _write_rc19_session(tmp_path, n_frames=10)

        backfill_action_camera(tmp_path)

        ac = json.loads((tmp_path / "action_camera.json").read_text())
        for frame in ac:
            assert frame.get("scene_name") == "minecraft:overworld"

    def test_rc19_session_populates_velocity_speed(self, tmp_path: Path):
        """camera_speed/player_speed must come from velocity_{x,y,z}.

        rc19.0.3: velocity is now unit-converted blocks/tick → m/s (×20,
        MC runs at 20 ticks/sec). Fixture writes 0.5 blocks/tick on x and
        z → 10.0 m/s each after conversion.
        """
        _write_rc19_session(tmp_path, n_frames=10)

        backfill_action_camera(tmp_path)

        ac = json.loads((tmp_path / "action_camera.json").read_text())
        for frame in ac:
            # 0.5 blocks/tick × 20 ticks/sec = 10.0 m/s
            assert frame["camera_speed"] == [10.0, 0.0, 10.0]
            assert frame["player_speed"] == [10.0, 0.0, 10.0]
            # scalar speed = sqrt(100 + 0 + 100) = sqrt(200)
            assert abs(frame["speed"] - math.sqrt(200.0)) < 1e-9

    def test_rc19_nearest_neighbor_matching(self, tmp_path: Path):
        """The nearest game_state tick should be selected per frame.

        Frame 0 (t_ns=10133900 → +10 ms after anchor) should match tick 0
        (anchor + 0 ms), since |0-10|=10 < |50-10|=40.
        Frame 1 (t_ns=10133900 + 33_333_333 → ~+43 ms) should match tick 1
        (anchor + 50 ms), since |50-43|=7 < |0-43|=43.
        """
        _write_rc19_session(tmp_path, n_frames=5)
        backfill_action_camera(tmp_path)

        ac = json.loads((tmp_path / "action_camera.json").read_text())
        # tick 0 has x=100.0; tick 1 has x=100.01
        assert abs(ac[0]["camera_position"][0] - 100.0) < 1e-9
        assert abs(ac[1]["camera_position"][0] - 100.01) < 1e-9

    def test_rc17_session_still_works(self, tmp_path: Path):
        """Backwards compat: old `idx`/`t_ns` recordings must still backfill."""
        _write_rc17_session(tmp_path, n_frames=20)

        patched = backfill_action_camera(tmp_path)

        assert patched == 20, f"rc17 backwards-compat: expected 20, got {patched}"
        ac = json.loads((tmp_path / "action_camera.json").read_text())
        for frame in ac:
            assert frame["camera_rotation_quaternion"] is not None
            assert frame["scene_name"] == "minecraft:the_nether"

    def test_missing_game_state_returns_zero(self, tmp_path: Path):
        (tmp_path / "action_camera.json").write_text(
            json.dumps([{"frame_index": 0, "timestamp_ns": 0}])
        )

        assert backfill_action_camera(tmp_path) == 0

    def test_missing_action_camera_returns_zero(self, tmp_path: Path):
        (tmp_path / "game_state.jsonl").write_text(
            json.dumps({"timestamp_ms": 100, "yaw_deg": 0, "pitch_deg": 0}) + "\n"
        )

        assert backfill_action_camera(tmp_path) == 0

    def test_empty_frames_returns_zero(self, tmp_path: Path):
        (tmp_path / "action_camera.json").write_text("[]")
        (tmp_path / "game_state.jsonl").write_text(
            json.dumps({"timestamp_ms": 100, "yaw_deg": 0, "pitch_deg": 0}) + "\n"
        )

        assert backfill_action_camera(tmp_path) == 0

    def test_dict_wrapped_frames_supported(self, tmp_path: Path):
        """Some older builds wrapped frames in {"frames": [...]}; preserve."""
        wrapped = {
            "frames": [
                {"frame_index": 0, "timestamp_ns": 0, "camera_position": None},
                {"frame_index": 1, "timestamp_ns": 33_333_333, "camera_position": None},
            ]
        }
        (tmp_path / "action_camera.json").write_text(json.dumps(wrapped))
        with (tmp_path / "game_state.jsonl").open("w") as f:
            for tick in range(5):
                f.write(
                    json.dumps(
                        {
                            "timestamp_ms": 1700000000000 + tick * 50,
                            "x": tick,
                            "y": 64.0,
                            "z": 0.0,
                            "yaw_deg": 0.0,
                            "pitch_deg": 0.0,
                            "velocity_x": 0.0,
                            "velocity_y": 0.0,
                            "velocity_z": 0.0,
                            "dimension": "minecraft:overworld",
                        }
                    )
                    + "\n"
                )

        patched = backfill_action_camera(tmp_path)

        assert patched == 2
        ac = json.loads((tmp_path / "action_camera.json").read_text())
        assert isinstance(ac, dict)
        assert "frames" in ac

    def test_corrupt_game_state_line_skipped(self, tmp_path: Path):
        """A half-written final JSONL line should not abort backfill."""
        (tmp_path / "action_camera.json").write_text(
            json.dumps([{"frame_index": 0, "timestamp_ns": 0, "camera_position": None}])
        )
        with (tmp_path / "game_state.jsonl").open("w") as f:
            f.write(
                json.dumps(
                    {
                        "timestamp_ms": 1700000000000,
                        "x": 1.0,
                        "y": 2.0,
                        "z": 3.0,
                        "yaw_deg": 45.0,
                        "pitch_deg": 0.0,
                        "velocity_x": 0.0,
                        "velocity_y": 0.0,
                        "velocity_z": 0.0,
                        "dimension": "minecraft:overworld",
                    }
                )
                + "\n"
            )
            f.write('{"tick": 1, "timestamp_ms":')  # truncated, no newline

        patched = backfill_action_camera(tmp_path)

        assert patched == 1
        ac = json.loads((tmp_path / "action_camera.json").read_text())
        assert ac[0]["camera_position"] == [1.0, 2.0, 3.0]


# ---------------------------------------------------------------------------
# Real-session integration test (the original Howard rc19.0.2 session)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBackfillRealSession:
    """Run backfill against the actual rc19.0.2 session on disk if present."""

    SESSION_PATH = Path("/tmp/rc19.0.2-session/session_20260513_203931_70db9d7b")

    @pytest.fixture
    def session_copy(self, tmp_path: Path) -> Path:
        import shutil

        if not self.SESSION_PATH.exists():
            pytest.skip(f"Real session dir {self.SESSION_PATH} not present")

        # Copy just the two files backfill needs (avoid the 479 MB mp4).
        ac_src = self.SESSION_PATH / "action_camera.json"
        gs_src = self.SESSION_PATH / "game_state.jsonl"
        if not ac_src.exists() or not gs_src.exists():
            pytest.skip("Real session missing action_camera.json or game_state.jsonl")

        dst = tmp_path / "session"
        dst.mkdir()
        shutil.copy2(ac_src, dst / "action_camera.json")
        shutil.copy2(gs_src, dst / "game_state.jsonl")
        return dst

    def test_real_rc19_session_backfills_400_plus_frames(self, session_copy: Path):
        patched = backfill_action_camera(session_copy)

        assert patched >= 400, f"Real rc19.0.2 session should yield ≥400 frames; got {patched}"

        ac = json.loads((session_copy / "action_camera.json").read_text())
        assert ac[0]["camera_position"] is not None
        assert ac[0]["camera_rotation_quaternion"] is not None
        assert ac[0]["scene_name"]  # non-empty
