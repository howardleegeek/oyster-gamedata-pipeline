"""V₁ R20 — distribution-level drift detector (closes Bucket C 2/5 → 5/5).

Single-frame R01–R16 are blind to sub-threshold drift: each frame passes
its threshold but the aggregate population has drifted. R20 runs **once
per dataset** over all records.

Spec: ``docs/SPEC_R20_STATISTICAL_DRIFT.md`` §§ 3, 5, 7.

Per IL11 (Distribution Honesty): every entry point declares ``min_frames``,
ABSTAINs on degenerate inputs (empty / wrong-shape / NaN / non-monotone time)
rather than crashing, and reports ``sample_size`` + ``n_outliers`` for
human auditability.

Stdlib only — ``statistics.fmean`` and ``statistics.stdev`` keep this callable in
headless harnesses without numpy.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DriftResult:
    """Per-dataset verdict (sibling of ResidualResult, returned once per dataset)."""

    name: str
    passed: bool
    sample_stat: float
    threshold: float
    n_outliers: int
    sample_size: int
    detail: str = ""


def _abstain(name: str, reason: str, threshold: float = 0.0) -> DriftResult:
    return DriftResult(name, False, math.nan, threshold, 0, 0, f"ABSTAIN:{reason}")


def _parse_time(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f")


def r20a_quat_norm_distribution(
    records: list[dict],
    max_offset: float = 1e-5,
    max_std: float = 1e-4,
    min_frames: int = 10,
) -> DriftResult:
    """``|μ_‖q‖ − 1.0| ≤ max_offset`` AND ``σ_‖q‖ ≤ max_std``."""
    if not records:
        return _abstain("R20a", "empty_records")
    if len(records) < min_frames:
        return _abstain("R20a", f"insufficient_sample({len(records)}<{min_frames})")
    norms = []
    for r in records:
        q = r.get("camera_rotation_quaternion")
        if not q or len(q) != 4:
            return _abstain("R20a", "missing_field(camera_rotation_quaternion)")
        n = math.sqrt(sum(float(c) * float(c) for c in q))
        if math.isnan(n):
            return _abstain("R20a", "nan_in_stat")
        norms.append(n)
    mu = statistics.fmean(norms)
    sigma = statistics.stdev(norms) if len(norms) > 1 else 0.0
    offset = abs(mu - 1.0)
    n_out = sum(1 for n in norms if abs(n - mu) > 3.0 * sigma) if sigma > 0 else 0
    passed = offset <= max_offset and sigma <= max_std
    return DriftResult(
        "R20a",
        passed,
        offset,
        max_offset,
        n_out,
        len(norms),
        f"mu={mu:.3e} sigma={sigma:.3e}",
    )


def r20b_mouse_dx_cumulative(
    records: list[dict],
    tolerance: float = 1e-3,
    min_frames: int = 10,
) -> DriftResult:
    """``|Σ mouse_dx − (mouse_x[N-1] − mouse_x[0])| ≤ tolerance``."""
    if not records:
        return _abstain("R20b", "empty_records", tolerance)
    if len(records) < min_frames:
        return _abstain("R20b", f"insufficient_sample({len(records)}<{min_frames})", tolerance)
    s = 0.0
    for r in records:
        dx = r.get("mouse_dx")
        if not isinstance(dx, list) or not dx:
            return _abstain("R20b", "malformed_field(mouse_dx)", tolerance)
        s += float(dx[0])
    x0 = records[0].get("mouse_x")
    xN = records[-1].get("mouse_x")
    if not isinstance(x0, list) or not x0 or not isinstance(xN, list) or not xN:
        return _abstain("R20b", "malformed_field(mouse_x)", tolerance)
    delta_x = float(xN[0]) - float(x0[0])
    drift = abs(s - delta_x)
    if math.isnan(drift):
        return _abstain("R20b", "nan_in_stat", tolerance)
    return DriftResult(
        "R20b",
        drift <= tolerance,
        drift,
        tolerance,
        0,
        len(records),
        f"sum={s:.3e} delta_x={delta_x:.3e}",
    )


def r20c_fps_jitter(
    records: list[dict],
    max_offset_ms: float = 0.1,
    max_std_ms: float = 5.0,
    min_frames: int = 10,
) -> DriftResult:
    """``|μ_dt − 1/fps| ≤ max_offset_ms`` AND ``σ_dt ≤ max_std_ms``."""
    if not records:
        return _abstain("R20c", "empty_records", max_offset_ms)
    if len(records) < min_frames:
        return _abstain(
            "R20c",
            f"insufficient_sample({len(records)}<{min_frames})",
            max_offset_ms,
        )
    declared_fps = float(records[0].get("fps", 30.0))
    target_dt_ms = 1000.0 / declared_fps if declared_fps > 0 else 1000.0 / 30.0
    dts_ms: list[float] = []
    try:
        for i in range(len(records) - 1):
            dt = (
                _parse_time(records[i + 1]["time"]) - _parse_time(records[i]["time"])
            ).total_seconds() * 1000.0
            if dt < 0:
                return _abstain("R20c", "non_monotone_time", max_offset_ms)
            dts_ms.append(dt)
    except (KeyError, ValueError, TypeError):
        return _abstain("R20c", "malformed_field(time)", max_offset_ms)
    mu = statistics.fmean(dts_ms)
    sigma = statistics.stdev(dts_ms) if len(dts_ms) > 1 else 0.0
    offset = abs(mu - target_dt_ms)
    n_out = sum(1 for dt in dts_ms if abs(dt - mu) > 3.0 * sigma) if sigma > 0 else 0
    passed = offset <= max_offset_ms and sigma <= max_std_ms
    return DriftResult(
        "R20c",
        passed,
        offset,
        max_offset_ms,
        n_out,
        len(dts_ms),
        f"mu_dt={mu:.3f}ms target={target_dt_ms:.3f}ms sigma={sigma:.3f}ms",
    )


def r20d_speed_profile(
    records: list[dict],
    max_outlier_pct: float = 0.10,
    max_mean_speed: float = 15.0,
    high_speed_threshold: float = 30.0,
    min_frames: int = 10,
) -> DriftResult:
    """``ratio(‖speed‖ > 30) ≤ 10%`` AND ``μ_‖speed‖ ≤ 15 m/s``."""
    if not records:
        return _abstain("R20d", "empty_records", max_outlier_pct)
    if len(records) < min_frames:
        return _abstain(
            "R20d",
            f"insufficient_sample({len(records)}<{min_frames})",
            max_outlier_pct,
        )
    mags: list[float] = []
    for r in records:
        s = r.get("camera_speed")
        if not isinstance(s, list) or len(s) != 3:
            return _abstain("R20d", "malformed_field(camera_speed)", max_outlier_pct)
        mag = math.sqrt(sum(float(c) * float(c) for c in s))
        if math.isnan(mag):
            return _abstain("R20d", "nan_in_stat", max_outlier_pct)
        mags.append(mag)
    n_high = sum(1 for m in mags if m > high_speed_threshold)
    ratio = n_high / len(mags)
    mu = statistics.fmean(mags)
    passed = ratio <= max_outlier_pct and mu <= max_mean_speed
    return DriftResult(
        "R20d",
        passed,
        ratio,
        max_outlier_pct,
        n_high,
        len(mags),
        f"mu_speed={mu:.3f}m/s ratio_high={ratio:.3f}",
    )


def r20e_yaw_turn_rate(
    records: list[dict],
    max_rate_deg_per_sec: float = 720.0,
    max_outlier_pct: float = 0.05,
    min_frames: int = 10,
) -> DriftResult:
    """``ratio(|Δyaw|/dt > 720°/s) ≤ 5%`` over all adjacent pairs."""
    if not records:
        return _abstain("R20e", "empty_records", max_outlier_pct)
    if len(records) < min_frames:
        return _abstain(
            "R20e",
            f"insufficient_sample({len(records)}<{min_frames})",
            max_outlier_pct,
        )
    rates: list[float] = []
    try:
        for i in range(len(records) - 1):
            oula_n = records[i].get("camera_rotation_oula")
            oula_n1 = records[i + 1].get("camera_rotation_oula")
            if not oula_n or not oula_n1 or len(oula_n) < 2 or len(oula_n1) < 2:
                return _abstain("R20e", "malformed_field(camera_rotation_oula)", max_outlier_pct)
            d_yaw = float(oula_n1[1]) - float(oula_n[1])
            # 360° wrap-around: take shortest signed delta.
            d_yaw = (d_yaw + 180.0) % 360.0 - 180.0
            dt = (
                _parse_time(records[i + 1]["time"]) - _parse_time(records[i]["time"])
            ).total_seconds()
            if dt <= 0:
                return _abstain("R20e", "non_monotone_time", max_outlier_pct)
            rates.append(abs(d_yaw) / dt)
    except (KeyError, ValueError, TypeError):
        return _abstain("R20e", "malformed_field(time)", max_outlier_pct)
    n_extreme = sum(1 for r in rates if r > max_rate_deg_per_sec)
    ratio = n_extreme / len(rates) if rates else 0.0
    passed = ratio <= max_outlier_pct
    return DriftResult(
        "R20e",
        passed,
        ratio,
        max_outlier_pct,
        n_extreme,
        len(rates),
        f"ratio_extreme={ratio:.3f} max_rate={max(rates) if rates else 0.0:.1f}deg/s",
    )
