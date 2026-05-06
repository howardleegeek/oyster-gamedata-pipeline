"""V₁ Claude residuals package.

Re-exports residual functions and the shared ``ResidualResult`` dataclass
so callers can write::

    from bin.v1_claude_residuals import r16_depth_count, ResidualResult
"""
from __future__ import annotations

from .residuals import ResidualResult
from .r15_fps_consistency import r15_fps_consistency
from .r16_depth_count import r16_depth_count

__all__ = ["ResidualResult", "r15_fps_consistency", "r16_depth_count"]
