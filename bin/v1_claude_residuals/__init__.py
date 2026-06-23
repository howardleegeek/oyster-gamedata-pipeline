"""V₁ Claude residuals package.

Re-exports residual functions and the shared ``ResidualResult`` dataclass
so callers can write::

    from bin.v1_claude_residuals import r16_depth_count, ResidualResult
"""
from __future__ import annotations

from .r13_keycode_replay import r13_keycode_replay
from .r15_fps_consistency import r15_fps_consistency
from .r16_depth_count import r16_depth_count
from .r18_session_manifest import r18_session_manifest
from .r20_drift import (
    DriftResult,
    r20a_quat_norm_distribution,
    r20b_mouse_dx_cumulative,
    r20c_fps_jitter,
    r20d_speed_profile,
    r20e_yaw_turn_rate,
)
from .r21_monotonic_frame import r21_monotonic_frame
from .r22_depth_hash import r22_depth_hash
from .r23_video_codec import r23_video_codec
from .residuals import ResidualResult

__all__ = [
    "ResidualResult",
    "DriftResult",
    "r13_keycode_replay",
    "r15_fps_consistency",
    "r16_depth_count",
    "r18_session_manifest",
    "r20a_quat_norm_distribution",
    "r20b_mouse_dx_cumulative",
    "r20c_fps_jitter",
    "r20d_speed_profile",
    "r20e_yaw_turn_rate",
    "r21_monotonic_frame",
    "r22_depth_hash",
    "r23_video_codec",
]
