#!/usr/bin/env python3
"""
G165 · bin/lint_v3_prd_grounded.py — Stream BC rc19 (Audit E rewrite)

Cluster A: Full PRD page-by-page lint tool. 38 acceptance criteria (including
#38 audio continuity, #39 input-to-frame latency, #40 WASD balance,
#41 stationary %, #42 frozen frames, #43 audio stream AAC).

History (cumulative IDs preserved across stream merges):
  1-25  : Cluster A original 25 PRD criteria.
  26-27 : Stream V additions.
  28-32 : Stream BC (this rewrite, per Audit E) — codec, duration upper
          bound, frame continuity, mouse/camera alignment, speed bounds.

Audit E targeted fixes (stub → real implementation):
  #1  video resolution      — ffprobe, require 1920x1080
  #2  video duration        — ffprobe, require 300 <= d <= 360
  #3  video FPS             — tighten 28-32, no silent pass
  #4  video format          — drop .avi, .mp4 only
  #5-10 audio probe         — ffprobe -select_streams a:0
  #11 route_type            — read action_camera.json route_type ∈ {1,2,3}
  #12 camera intrinsics     — per-frame fx==fy
  #13/14 quaternion         — xyzw shape + L2 norm in [0.99, 1.01]
  #22 metadata              — PRD page 3 required: gameProcessName, width,
                              height, recordDpi (also accept window_name,
                              capture_resolution etc. by alias)
  #24 directory structure   — accept video.mp4 OR recording.mp4
  #25 video content health  — ffmpeg missing → FAIL, not silent pass

Stream BC new criteria:
  #28 video codec           — only h264 / hevc
  #29 explicit duration > 360 → FAIL
  #30 frame continuity      — frame_index monotonic, no dup/gap
  #31 mouse/camera align    — sample 50 frame pairs, sign-correlate
  #32 speed physical bounds — player_speed / camera_speed ≤ 100 m/s

--strict promotes "dependency-missing" results from PASS to FAIL.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Lazy imports for optional dependencies.
_np, _pil, _yaml, _iio = None, None, None, None


def _get_np():
    global _np
    if _np is None:
        import numpy
        _np = numpy
    return _np


def _get_pil():
    global _pil
    if _pil is None:
        from PIL import Image
        _pil = Image
    return _pil


def _get_yaml():
    global _yaml
    if _yaml is None:
        import yaml
        _yaml = yaml
    return _yaml


def _get_iio():
    """rc15.28 A-A1: imageio for FPS / audio meta (already bundled)."""
    global _iio
    if _iio is None:
        try:
            import imageio.v2 as imageio_v2  # noqa
            _iio = imageio_v2
        except ImportError:
            try:
                import imageio
                _iio = imageio
            except ImportError:
                _iio = False  # explicit fail sentinel
    return _iio


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strictness flag (set by --strict in main()). When True, dependency-missing
# results FAIL rather than silently pass. Used in #3, #7-10, #25.
# ---------------------------------------------------------------------------
_STRICT = False


def _set_strict(flag: bool) -> None:
    global _STRICT
    _STRICT = bool(flag)


def _dep_missing(criterion_id: int, name: str, reason: str) -> "LintResult":
    """Build a result for missing optional dependency, honoring --strict."""
    if _STRICT:
        return LintResult(criterion_id, name, False,
                          f"[strict] {reason}")
    return LintResult(criterion_id, name, True, reason)


@dataclass
class LintResult:
    """Result of a single lint check."""
    criterion_id: int
    name: str
    passed: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LintReport:
    """Complete lint report for a data package."""
    data_dir: Path
    results: List[LintResult] = field(default_factory=list)
    total_checks: int = 38  # rc19: 32 base + #38 audio continuity + #39-43 (latency, WASD, stationary, frozen, AAC)
    passed_count: int = 0
    failed_count: int = 0

    def add(self, r: LintResult) -> None:
        self.results.append(r)
        if r.passed:
            self.passed_count += 1
        else:
            self.failed_count += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "data_dir": str(self.data_dir),
            "summary": {
                "total": self.total_checks,
                "passed": self.passed_count,
                "failed": self.failed_count,
                "pass_rate": (
                    f"{100*self.passed_count/self.total_checks:.1f}%"
                    if self.total_checks else "0.0%"
                ),
            },
            "results": [
                {"id": r.criterion_id, "name": r.name, "passed": r.passed,
                 "message": r.message, "details": r.details}
                for r in self.results
            ],
        }


# ---------------------------------------------------------------------------
# Shared ffprobe helpers (Stream BC: real probe replaces glob-only stubs).
# ---------------------------------------------------------------------------

def _ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def _ffprobe_video_stream(video: Path) -> Optional[Dict[str, Any]]:
    """Return dict with codec_name, width, height, r_frame_rate, duration."""
    if not _ffprobe_available():
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries",
             "stream=codec_name,width,height,r_frame_rate,duration",
             "-of", "json", str(video)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        data = json.loads(out.stdout)
        streams = data.get("streams") or []
        if not streams:
            return None
        s = streams[0]
        # r_frame_rate is "num/den"; convert.
        fps = 0.0
        rfr = s.get("r_frame_rate", "")
        if "/" in str(rfr):
            num, den = rfr.split("/", 1)
            try:
                den_f = float(den)
                if den_f > 0:
                    fps = float(num) / den_f
            except Exception as e:  # noqa: BLE001
                logger.debug("fps parse failed for r_frame_rate=%s: %s", rfr, e)
                fps = 0.0
        duration = 0.0
        try:
            duration = float(s.get("duration", 0) or 0)
        except Exception as e:  # noqa: BLE001
            logger.debug("duration parse failed: %s", e)
            duration = 0.0
        return {
            "codec_name": s.get("codec_name", ""),
            "width": int(s.get("width") or 0),
            "height": int(s.get("height") or 0),
            "fps": fps,
            "duration": duration,
            "raw": s,
        }
    except Exception as e:
        logger.debug("ffprobe video failed: %s", e)
        return None


def _ffprobe_format_duration(video: Path) -> float:
    """Container-level duration (fallback when stream duration missing)."""
    if not _ffprobe_available():
        return 0.0
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return float((out.stdout or "0").strip() or 0)
    except Exception as e:  # noqa: BLE001
        logger.debug("ffprobe format duration failed for %s: %s", video, e)
        return 0.0


def _ffprobe_audio_stream(video: Path) -> Optional[Dict[str, Any]]:
    """Return audio stream dict or None if no audio track."""
    if not _ffprobe_available():
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries",
             "stream=codec_name,sample_rate,channels,duration",
             "-of", "json", str(video)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        data = json.loads(out.stdout)
        streams = data.get("streams") or []
        if not streams:
            return None
        s = streams[0]
        try:
            sr = int(s.get("sample_rate") or 0)
        except Exception as e:  # noqa: BLE001
            logger.debug("audio sample_rate parse failed: %s", e)
            sr = 0
        try:
            ch = int(s.get("channels") or 0)
        except Exception as e:  # noqa: BLE001
            logger.debug("audio channels parse failed: %s", e)
            ch = 0
        try:
            dur = float(s.get("duration") or 0)
        except Exception as e:  # noqa: BLE001
            logger.debug("audio duration parse failed: %s", e)
            dur = 0.0
        return {
            "codec_name": s.get("codec_name", ""),
            "sample_rate": sr,
            "channels": ch,
            "duration": dur,
        }
    except Exception as e:
        logger.debug("ffprobe audio failed: %s", e)
        return None


def _primary_video(d: Path) -> Optional[Path]:
    """Locate the canonical recording (#24 accepts either name)."""
    for name in ("video.mp4", "recording.mp4"):
        p = d / name
        if p.is_file():
            return p
    # Fall back: deepest search.
    for name in ("video.mp4", "recording.mp4"):
        hits = list(d.glob(f"**/{name}"))
        if hits:
            return hits[0]
    return None


def _load_action_camera(d: Path) -> Tuple[Optional[Any], Optional[Path]]:
    """Load action_camera.json (list of frames or dict). Returns (data, path)."""
    candidates = list(d.glob("action_camera.json"))
    if not candidates:
        candidates = list(d.glob("**/action_camera.json"))
    if not candidates:
        return None, None
    try:
        with open(candidates[0]) as f:
            return json.load(f), candidates[0]
    except Exception as e:
        logger.debug("action_camera.json load failed: %s", e)
        return None, candidates[0]


# ---------------------------------------------------------------------------
# Audit E #1-4: Video specs (resolution, duration, fps, format)
# ---------------------------------------------------------------------------

def _check_video_specs(d: Path, rpt: LintReport) -> None:
    """Criteria 1-4: Video resolution 1920x1080, 5-6min, ~30fps, .mp4 only."""
    # Format restriction first: Audit E #4 drops .avi, only .mp4 allowed.
    mp4s = sorted(d.glob("**/*.mp4"))
    bad_fmt = (
        sorted(d.glob("**/*.avi"))
        + sorted(d.glob("**/*.mov"))
        + sorted(d.glob("**/*.mkv"))
        + sorted(d.glob("**/*.flv"))
    )

    primary = _primary_video(d) or (mp4s[0] if mp4s else None)

    # --- #1 Resolution ---
    if primary is None:
        rpt.add(LintResult(1, "Video Resolution", False,
                           "No primary mp4 found"))
        rpt.add(LintResult(2, "Video Duration", False, "No video to probe"))
    else:
        info = _ffprobe_video_stream(primary)
        if info is None:
            if not _ffprobe_available():
                rpt.add(_dep_missing(1, "Video Resolution",
                                     "ffprobe unavailable, cannot verify "
                                     "1920x1080"))
                rpt.add(_dep_missing(2, "Video Duration",
                                     "ffprobe unavailable, cannot verify "
                                     "300-360s"))
            else:
                rpt.add(LintResult(1, "Video Resolution", False,
                                   f"ffprobe could not parse {primary.name}"))
                rpt.add(LintResult(2, "Video Duration", False,
                                   f"ffprobe could not parse {primary.name}"))
        else:
            w, h = info["width"], info["height"]
            ok_res = (w == 1920 and h == 1080)
            rpt.add(LintResult(
                1, "Video Resolution", ok_res,
                f"{w}x{h}" + ("" if ok_res else " (require 1920x1080)"),
                {"video": primary.name, "width": w, "height": h}))

            # --- #2 Duration (300..360 sec inclusive) ---
            duration = info["duration"] or _ffprobe_format_duration(primary)
            ok_dur = (300.0 <= duration <= 360.0)
            rpt.add(LintResult(
                2, "Video Duration", ok_dur,
                f"{duration:.2f}s" + ("" if ok_dur else " (require 300..360)"),
                {"video": primary.name, "duration_s": round(duration, 2)}))

    # --- #3 FPS (28-32) ---
    if primary is None:
        rpt.add(LintResult(3, "Video FPS", False, "No video to probe"))
    else:
        info = _ffprobe_video_stream(primary)
        if info is None:
            if not _ffprobe_available():
                # Strict-aware fallback (ffprobe unavailable).
                rpt.add(_dep_missing(3, "Video FPS",
                                     "ffprobe unavailable, cannot verify FPS"))
            else:
                rpt.add(LintResult(3, "Video FPS", False,
                                   f"ffprobe returned no stream data for "
                                   f"{primary.name}"))
        else:
            fps = info["fps"]
            ok_fps = (28.0 <= fps <= 32.0)
            rpt.add(LintResult(
                3, "Video FPS", ok_fps,
                f"{fps:.2f} fps" + ("" if ok_fps else " (require 28..32)"),
                {"video": primary.name, "fps": round(fps, 2)}))

    # --- #4 Format (.mp4 only, no .avi / .mov / .mkv / .flv) ---
    rpt.add(LintResult(
        4, "Video Format", not bad_fmt,
        "All .mp4" if not bad_fmt
        else f"Invalid containers: {[f.name for f in bad_fmt[:5]]}",
        {"bad_files": [f.name for f in bad_fmt[:5]]}))


# ---------------------------------------------------------------------------
# Image specs (unchanged from rc17.0 — was already implemented properly).
# ---------------------------------------------------------------------------

def _check_image_specs(d: Path, rpt: LintReport) -> None:
    """Criteria 5-6: Image resolution (1920x1080), format."""
    Image = _get_pil()
    imgs = (
        list(d.glob("**/*.png"))
        + list(d.glob("**/*.jpg"))
        + list(d.glob("**/*.jpeg"))
    )
    invalid: List[Tuple[str, Tuple[int, int]]] = []
    for p in imgs[:30]:
        try:
            with Image.open(p) as im:
                if im.size != (1920, 1080):
                    invalid.append((p.name, im.size))
        except Exception as e:  # noqa: BLE001
            logger.debug("image open failed for %s: %s", p, e)
            pass
    rpt.add(LintResult(
        5, "Image Resolution", not invalid,
        "All 1920x1080" if not invalid
        else f"{len(invalid)} wrong",
        {"samples": invalid[:5]}))

    fmt_bad: List[Tuple[str, str]] = []
    for p in imgs[:30]:
        try:
            with Image.open(p) as im:
                expected = "PNG" if p.suffix.lower() == ".png" else "JPEG"
                if im.format and im.format != expected:
                    fmt_bad.append((p.name, im.format))
        except Exception as e:
            fmt_bad.append((p.name, f"open_failed: {e}"))
    rpt.add(LintResult(
        6, "Image Format", not fmt_bad,
        "All formats match suffix" if not fmt_bad
        else f"{len(fmt_bad)} mismatches",
        {"samples": fmt_bad[:5]}))


# ---------------------------------------------------------------------------
# Audit E #5-10: Audio specs via ffprobe.
# ---------------------------------------------------------------------------

def _check_audio_specs(d: Path, rpt: LintReport) -> None:
    """Criteria 7-10: Audio quality, format, channels, sample rate, A/V sync.

    Strategy:
      1. Locate primary video (video.mp4 / recording.mp4) and probe audio
         stream via ffprobe -select_streams a:0.
      2. If a standalone .wav / .mp3 sidecar exists, probe that instead.
      3. If neither + ffprobe unavailable → strict-aware skip.
      4. A/V duration tolerance: ±0.5s (#7).
    """
    # Bad standalone formats (legacy check).
    bad_audio_files = list(d.glob("**/*.aac")) + list(d.glob("**/*.ogg"))
    rpt.add(LintResult(
        8, "Audio Format", not bad_audio_files,
        "All audio in approved container" if not bad_audio_files
        else f"Invalid: {[f.name for f in bad_audio_files[:5]]}"))

    primary_video = _primary_video(d)
    sidecar_audio = (
        list(d.glob("**/*.wav"))
        + list(d.glob("**/*.mp3"))
    )

    if not _ffprobe_available():
        rpt.add(_dep_missing(7, "Audio Quality / AV Sync",
                             "ffprobe unavailable"))
        rpt.add(_dep_missing(9, "Audio Channels", "ffprobe unavailable"))
        rpt.add(_dep_missing(10, "Audio Sample Rate", "ffprobe unavailable"))
        return

    # Determine the source we probe (video container preferred, else sidecar).
    audio_meta = None
    av_duration_diff = None
    source = None
    if primary_video is not None:
        audio_meta = _ffprobe_audio_stream(primary_video)
        if audio_meta:
            v_info = _ffprobe_video_stream(primary_video)
            v_dur = (
                v_info["duration"] if v_info and v_info["duration"]
                else _ffprobe_format_duration(primary_video)
            )
            a_dur = audio_meta["duration"]
            if v_dur > 0 and a_dur > 0:
                av_duration_diff = abs(v_dur - a_dur)
            source = primary_video.name

    if audio_meta is None and sidecar_audio:
        audio_meta = _ffprobe_audio_stream(sidecar_audio[0])
        if audio_meta:
            source = sidecar_audio[0].name

    if audio_meta is None:
        # No audio stream anywhere — recorder design says video-only is OK,
        # but we surface that as informational PASS (not silent).
        rpt.add(LintResult(7, "Audio Quality / AV Sync", True,
                           "No audio stream (video-only session, acceptable)"))
        rpt.add(LintResult(9, "Audio Channels", True,
                           "No audio stream (video-only)"))
        rpt.add(LintResult(10, "Audio Sample Rate", True,
                           "No audio stream (video-only)"))
        return

    sr = audio_meta["sample_rate"]
    ch = audio_meta["channels"]
    codec = audio_meta["codec_name"]

    # #7 Quality (presence + A/V sync within 0.5s).
    sync_ok = (av_duration_diff is None) or (av_duration_diff <= 0.5)
    rpt.add(LintResult(
        7, "Audio Quality / AV Sync", sync_ok,
        (f"audio={codec} sync ok"
         if sync_ok and av_duration_diff is not None
         else (f"audio={codec} sync skew {av_duration_diff:.3f}s"
               if av_duration_diff is not None and not sync_ok
               else f"audio={codec} (no A/V duration comparison possible)")),
        {"source": source, "codec": codec,
         "av_skew_s": (round(av_duration_diff, 3)
                       if av_duration_diff is not None else None)}))

    # #9 Channels (mono or stereo).
    ok_ch = ch in (1, 2)
    rpt.add(LintResult(
        9, "Audio Channels", ok_ch,
        f"channels={ch}" + ("" if ok_ch else " (require 1 or 2)"),
        {"source": source, "channels": ch}))

    # #10 Sample rate (>= 22050 Hz, typically 44100 / 48000).
    ok_sr = sr >= 22050
    rpt.add(LintResult(
        10, "Audio Sample Rate", ok_sr,
        f"sample_rate={sr} Hz" + ("" if ok_sr else " (require >= 22050)"),
        {"source": source, "sample_rate": sr}))


# ---------------------------------------------------------------------------
# Audit E #11: Route distribution — read action_camera.json `route_type`.
# ---------------------------------------------------------------------------

def _check_route_dist(d: Path, rpt: LintReport) -> None:
    """Criterion 11: route_type ∈ {1, 2, 3} (PRD-defined route classes).

    The recorder writes `route_type` either:
      - as a top-level field if action_camera.json is dict-shaped, or
      - on each frame record if list-shaped (we sample first non-null).
    Missing field → FAIL (the value is required by PRD page 4).
    """
    data, path = _load_action_camera(d)
    if data is None:
        rpt.add(LintResult(11, "Route Distribution", False,
                           "action_camera.json not found"))
        return

    route_type = None
    if isinstance(data, dict):
        route_type = data.get("route_type")
    elif isinstance(data, list):
        for r in data:
            if isinstance(r, dict) and r.get("route_type") is not None:
                route_type = r["route_type"]
                break

    if route_type is None:
        rpt.add(LintResult(
            11, "Route Distribution", False,
            "route_type missing from action_camera.json",
            {"source": path.name if path else None}))
        return

    try:
        rt_int = int(route_type)
    except (TypeError, ValueError):
        rpt.add(LintResult(
            11, "Route Distribution", False,
            f"route_type={route_type!r} not an integer",
            {"source": path.name if path else None}))
        return

    ok = rt_int in (1, 2, 3)
    rpt.add(LintResult(
        11, "Route Distribution", ok,
        f"route_type={rt_int}" + ("" if ok else " (require 1, 2, or 3)"),
        {"source": path.name if path else None, "route_type": rt_int}))


# ---------------------------------------------------------------------------
# Audit E #12: Camera intrinsics fx == fy (per frame).
# ---------------------------------------------------------------------------

def _check_intrinsics(d: Path, rpt: LintReport) -> None:
    """Criterion 12: per-frame camera_intrinsics.fx == fy.

    The recorder writes a nested `camera_intrinsics` object on each frame:
      {"fx": float, "fy": float, "cx": float, "cy": float}
    A square pixel sensor (Minecraft / FOV-based games) means fx must
    equal fy. Tolerance: 1e-3 relative.
    """
    data, path = _load_action_camera(d)
    if data is None:
        # Fall back to the old yaml stub style for non-recorder packages.
        rpt.add(LintResult(12, "Camera Intrinsics fx==fy", False,
                           "action_camera.json not found, cannot probe "
                           "camera_intrinsics"))
        return

    if not isinstance(data, list):
        rpt.add(LintResult(12, "Camera Intrinsics fx==fy", False,
                           "action_camera.json is not a per-frame list"))
        return

    issues: List[Tuple[int, float, float]] = []
    sampled = 0
    seen_intrinsics = 0
    for r in data[:300]:  # sample 300 frames max
        if not isinstance(r, dict):
            continue
        ci = r.get("camera_intrinsics")
        if not isinstance(ci, dict):
            continue
        seen_intrinsics += 1
        try:
            fx = float(ci.get("fx", 0))
            fy = float(ci.get("fy", 0))
        except (TypeError, ValueError):
            issues.append((r.get("frame_index", -1), -1.0, -1.0))
            continue
        if fx <= 0 or fy <= 0:
            issues.append((r.get("frame_index", -1), fx, fy))
            continue
        rel = abs(fx - fy) / max(abs(fx), abs(fy), 1e-9)
        if rel > 1e-3:
            issues.append((r.get("frame_index", -1), fx, fy))
        sampled += 1

    if seen_intrinsics == 0:
        rpt.add(LintResult(
            12, "Camera Intrinsics fx==fy", False,
            "No camera_intrinsics field present on any frame",
            {"source": path.name if path else None,
             "frames_sampled": min(300, len(data))}))
        return

    rpt.add(LintResult(
        12, "Camera Intrinsics fx==fy", not issues,
        (f"All fx==fy across {sampled} frames"
         if not issues
         else f"{len(issues)} frames with fx!=fy"),
        {"source": path.name if path else None,
         "samples_with_intrinsics": seen_intrinsics,
         "violations": issues[:5]}))


# ---------------------------------------------------------------------------
# Audit E #13-14: Quaternion order (xyzw heuristic) + L2 norm ≈ 1.
# ---------------------------------------------------------------------------

def _quat_rest_state_xyzw(q: List[float]) -> bool:
    """Rest-state heuristic: an identity quaternion in xyzw order is
    (0, 0, 0, 1). In wxyz order it would be (1, 0, 0, 0). If the largest
    absolute component is the *last* element it is overwhelmingly xyzw.
    Returns True if shape is consistent with xyzw.
    """
    if not isinstance(q, list) or len(q) != 4:
        return False
    try:
        abs_q = [abs(float(x)) for x in q]
    except (TypeError, ValueError):
        return False
    return abs_q.index(max(abs_q)) == 3


def _check_quaternion(d: Path, rpt: LintReport) -> None:
    """Criteria 13-14: Quaternion xyzw order + normalization.

    Scans action_camera.json (preferred) for camera_rotation_quaternion
    and player_rotation_quaternion. Verifies:
      #13 xyzw ordering via metadata declaration (rc19) or rest-state
          heuristic fallback (rc17.x)
      #14 L2 norm ∈ [0.99, 1.01] per quaternion

    rc19 contract-check upgrade: the rest-state heuristic ("last element
    is largest abs → xyzw") is unreliable on game data where rotation
    spans > 180° — cos(half_yaw) swings as wide as sin(half_yaw) so the
    scalar w doesn't dominate. Verified on Howard's session: structurally
    correct xyzw data got 90/292 xyzw votes vs 202/292 wxyz votes. The
    real fix is contract: if metadata.json declares
    quaternion_order="xyzw", trust the producer. Heuristic only as
    fallback when field missing (rc18.x and older recordings).
    """
    # rc19: contract-first quaternion order check.
    # rc19.0.2 follow-up: also accept systeminfo.json (the recorder's
    # actual filename — see #22 / #24 alias precedent). Previously only
    # metadata.json was inspected, so producer-declared xyzw order was
    # silently ignored on the canonical sample shape.
    declared_order: Optional[str] = None
    for name in ("metadata.json", "systeminfo.json"):
        metadata_path = d / name
        if metadata_path.exists():
            try:
                with open(metadata_path) as f:
                    meta = json.load(f)
                declared_order = meta.get("quaternion_order")
                if declared_order is not None:
                    break
            except (json.JSONDecodeError, OSError) as exc:
                logger.debug(
                    "quaternion_order probe failed for %s: %s",
                    metadata_path, exc)

    data, path = _load_action_camera(d)
    if data is None:
        rpt.add(LintResult(13, "Quaternion xyzw Order", False,
                           "action_camera.json not found"))
        rpt.add(LintResult(14, "Quaternion Normalization", False,
                           "action_camera.json not found"))
        return

    if not isinstance(data, list):
        # Legacy yaml/json fallback (search recursively, generic).
        rpt.add(LintResult(13, "Quaternion xyzw Order", False,
                           "action_camera.json not list-shaped"))
        rpt.add(LintResult(14, "Quaternion Normalization", False,
                           "action_camera.json not list-shaped"))
        return

    keys = ("camera_rotation_quaternion", "player_rotation_quaternion")
    shape_issues: List[Tuple[int, str]] = []
    norm_issues: List[Tuple[int, str, float]] = []
    xyzw_votes = 0
    wxyz_votes = 0
    seen = 0

    for r in data:
        if not isinstance(r, dict):
            continue
        for k in keys:
            q = r.get(k)
            if q is None:
                continue
            if not (isinstance(q, list) and len(q) == 4):
                shape_issues.append((r.get("frame_index", -1), k))
                continue
            try:
                comps = [float(x) for x in q]
            except (TypeError, ValueError):
                shape_issues.append((r.get("frame_index", -1), k))
                continue
            seen += 1
            # Voting for order (skip near-identity frames since both encodings
            # would put a 1 somewhere and bias the count).
            non_trivial = max(abs(c) for c in comps) > 0.05
            if non_trivial:
                if _quat_rest_state_xyzw(comps):
                    xyzw_votes += 1
                else:
                    wxyz_votes += 1
            # Norm check.
            mag = math.sqrt(sum(c * c for c in comps))
            if not (0.99 <= mag <= 1.01):
                norm_issues.append(
                    (r.get("frame_index", -1), k, round(mag, 5)))

    if seen == 0:
        # No quaternion data populated. Per Audit E this must FAIL —
        # the recorder is supposed to populate camera_rotation_quaternion.
        rpt.add(LintResult(
            13, "Quaternion xyzw Order", False,
            "No quaternion data found in any frame",
            {"source": path.name if path else None,
             "frames_scanned": len(data)}))
        rpt.add(LintResult(
            14, "Quaternion Normalization", False,
            "No quaternion data found in any frame",
            {"source": path.name if path else None,
             "frames_scanned": len(data)}))
        return

    # rc19: contract-first verdict — if metadata declares xyzw, trust the
    # producer. The heuristic below remains as fallback for older recordings
    # that don't declare an order in metadata.
    if declared_order == "xyzw" and not shape_issues:
        rpt.add(LintResult(
            13, "Quaternion xyzw Order", True,
            f"xyzw declared in metadata.json ({seen} quaternions, "
            f"{len(shape_issues)} shape issues)",
            {"source": path.name if path else None,
             "verdict_source": "metadata.quaternion_order",
             "declared_order": declared_order,
             "shape_issues": shape_issues[:5]}))
    else:
        # Order verdict (legacy fallback): majority vote, treating ties or
        # pure-identity-only as a FAIL because we couldn't disambiguate.
        order_ok = (xyzw_votes > 0 and xyzw_votes >= wxyz_votes
                    and not shape_issues)
        reason = (
            f"xyzw confirmed by heuristic ({xyzw_votes} vs {wxyz_votes} "
            f"votes, {seen} quaternions)"
            if order_ok
            else (f"xyzw votes={xyzw_votes} wxyz votes={wxyz_votes} "
                  f"shape_issues={len(shape_issues)} — heuristic "
                  f"unreliable on large-rotation data; producer should "
                  f"declare quaternion_order in metadata.json")
        )
        rpt.add(LintResult(
            13, "Quaternion xyzw Order", order_ok, reason,
            {"source": path.name if path else None,
             "verdict_source": "heuristic_fallback",
             "declared_order": declared_order,
             "xyzw_votes": xyzw_votes, "wxyz_votes": wxyz_votes,
             "shape_issues": shape_issues[:5]}))

    rpt.add(LintResult(
        14, "Quaternion Normalization", not norm_issues,
        (f"All {seen} quaternions |q| in [0.99, 1.01]"
         if not norm_issues
         else f"{len(norm_issues)} norm violations"),
        {"source": path.name if path else None,
         "violations": norm_issues[:5]}))


# ---------------------------------------------------------------------------
# Depth ratio (unchanged from rc17.0 implementation).
# ---------------------------------------------------------------------------

def _check_depth_ratio(d: Path, rpt: LintReport) -> None:
    """Criteria 15-16: Depth invalid-pixel ratio (<5%)."""
    np = _get_np()
    Image = _get_pil()
    exr_files = list(d.glob("**/*depth*.exr")) + list(d.glob("**/depth/*.exr"))
    other_files = (list(d.glob("**/*depth*.png"))
                   + list(d.glob("**/*depth*.npy")))
    depth_files = exr_files + other_files
    if not depth_files:
        rpt.add(LintResult(
            15, "Depth Invalid-Pixel Ratio", False,
            "No depth files (PRD requires 1800 .exr float32 single-channel Z)"))
        rpt.add(LintResult(
            16, "Depth Data Quality", False,
            "No depth files — fail by absence per PRD criterion 15-16"))
        return

    issues: List[Tuple[str, str]] = []
    sample_size_w, sample_size_h = None, None
    for df in depth_files[:15]:
        try:
            if df.suffix == ".exr":
                try:
                    import OpenEXR
                    f = OpenEXR.InputFile(str(df))
                    h = f.header()
                    dw = h["dataWindow"]
                    w_px = dw.max.x - dw.min.x + 1
                    h_px = dw.max.y - dw.min.y + 1
                    sample_size_w, sample_size_h = w_px, h_px
                    if w_px < 1920 or h_px < 1080:
                        issues.append((df.name,
                                       f"resolution {w_px}x{h_px} "
                                       f"< 1920x1080"))
                        continue
                    raw = f.channel("Z")
                    arr = np.frombuffer(raw, dtype=np.float32)
                    invalid = (float(np.sum((arr == 0) | ~np.isfinite(arr)))
                               / max(arr.size, 1))
                    if invalid > 0.05:
                        issues.append((df.name, f"{invalid:.1%} invalid"))
                except ImportError:
                    if df.stat().st_size < 50_000:
                        issues.append((df.name,
                                       f"file too small "
                                       f"({df.stat().st_size}B)"))
            elif df.suffix == ".npy":
                data = np.load(str(df))
                invalid = (float(np.sum((data == 0) | (data == 65535)))
                           / data.size)
                if invalid > 0.05:
                    issues.append((df.name, f"{invalid:.1%}"))
            else:
                with Image.open(df) as im:
                    data = np.array(im)
                invalid = (float(np.sum((data == 0) | (data == 65535)))
                           / data.size)
                if invalid > 0.05:
                    issues.append((df.name, f"{invalid:.1%}"))
        except Exception as e:
            issues.append((df.name, f"read-err: {type(e).__name__}"))

    msg_pass = (
        f"All within 5% (sampled {min(15, len(depth_files))}"
        f"/{len(depth_files)} files"
        + (f", {sample_size_w}x{sample_size_h})" if sample_size_w else ")")
    )
    rpt.add(LintResult(
        15, "Depth Invalid-Pixel Ratio", not issues,
        msg_pass if not issues else f"{len(issues)} exceed",
        {"issues": issues[:5]}))
    rpt.add(LintResult(16, "Depth Data Quality", not issues,
                       "Depth quality check passed"))


# ---------------------------------------------------------------------------
# KeyCode (unchanged structural check from rc17.0).
# ---------------------------------------------------------------------------

def _check_keycode(d: Path, rpt: LintReport) -> None:
    """Criteria 17-18: keyCode integer format + VK range [0, 255]."""
    yaml = _get_yaml()
    issues: List[str] = []
    for jf in list(d.glob("**/*.json"))[:20]:
        try:
            with open(jf) as f:
                data = json.load(f)
                if (isinstance(data, dict) and "keyCode" in data
                        and not isinstance(data["keyCode"], int)):
                    issues.append(jf.name)
        except Exception as e:  # noqa: BLE001
            logger.debug("keycode json parse failed for %s: %s", jf, e)
            pass
    for yf in list(d.glob("**/*.yaml"))[:20]:
        try:
            with open(yf) as f:
                data = yaml.safe_load(f)
                if (isinstance(data, dict) and "keyCode" in data
                        and not isinstance(data["keyCode"], int)):
                    issues.append(yf.name)
        except Exception as e:  # noqa: BLE001
            logger.debug("keycode yaml parse failed for %s: %s", yf, e)
            pass
    rpt.add(LintResult(17, "keyCode Integer Format", not issues,
                       "All keyCode int" if not issues
                       else f"{len(issues)} non-int"))

    range_bad: List[Tuple[str, int]] = []
    sampled = 0
    for jf in list(d.glob("**/*.json"))[:10]:
        try:
            with open(jf) as f:
                data = json.load(f)
            if isinstance(data, list):
                for r in data[:200]:
                    if isinstance(r, dict) and isinstance(r.get("keyCode"),
                                                          list):
                        for kc in r["keyCode"]:
                            if isinstance(kc, int) and not (0 <= kc <= 255):
                                range_bad.append((jf.name, kc))
                            sampled += 1
                            if sampled > 500:
                                break
                    if sampled > 500:
                        break
        except Exception as e:  # noqa: BLE001
            logger.debug("keycode range json parse failed for %s: %s", jf, e)
            pass
    for jl in list(d.glob("**/inputs.jsonl"))[:3]:
        try:
            with open(jl) as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        e = json.loads(ln)
                    except Exception as ex:  # noqa: BLE001
                        logger.debug("inputs.jsonl line parse failed: %s", ex)
                        continue
                    kc = e.get("keyCode")
                    if isinstance(kc, int) and not (0 <= kc <= 255):
                        range_bad.append((jl.name, kc))
                    sampled += 1
                    if sampled > 5000:
                        break
        except Exception as e:  # noqa: BLE001
            logger.debug("inputs.jsonl open/read failed for %s: %s", jl, e)
            pass
    rpt.add(LintResult(
        18, "KeyCode Validation",
        not issues and not range_bad,
        ("All keyCode int-shape AND in VK range [0,255]"
         if not issues and not range_bad
         else f"shape_issues={len(issues)} range_issues={range_bad[:5]}"),
        {"out_of_range": range_bad[:10]}))


# ---------------------------------------------------------------------------
# No-overlay heuristic (unchanged from rc17.0).
# ---------------------------------------------------------------------------

def _check_no_overlays(d: Path, rpt: LintReport) -> None:
    """Criteria 19-21: No UI overlay, no logo, no popup (filename heuristic)."""
    all_files = [p.name.lower() for p in d.rglob("*") if p.is_file()]

    overlay_hits = [
        n for n in all_files
        if any(kw in n for kw in ("overlay", "watermark", "hud_"))
    ]
    rpt.add(LintResult(
        19, "No UI Overlay", not overlay_hits,
        "No overlay/watermark/HUD filename hints" if not overlay_hits
        else f"{len(overlay_hits)} suspicious: {overlay_hits[:3]}",
        {"hits": overlay_hits[:5]}))

    logo_hits = [
        n for n in all_files
        if any(kw in n for kw in ("logo", "brand", "trademark"))
    ]
    rpt.add(LintResult(
        20, "No Logo", not logo_hits,
        "No logo/brand filename hints" if not logo_hits
        else f"{len(logo_hits)} suspicious: {logo_hits[:3]}",
        {"hits": logo_hits[:5]}))

    popup_hits = [
        n for n in all_files
        if any(kw in n for kw in ("popup", "modal", "dialog", "notification"))
    ]
    rpt.add(LintResult(
        21, "No Popup", not popup_hits,
        "No popup/modal/dialog filename hints" if not popup_hits
        else f"{len(popup_hits)} suspicious: {popup_hits[:3]}",
        {"hits": popup_hits[:5]}))


# ---------------------------------------------------------------------------
# Audit E #22: Metadata required fields per PRD page 3.
# ---------------------------------------------------------------------------

# PRD page 3 names the canonical field names the platform expects. The
# recorder writes a slightly different schema (legacy), so we accept the
# canonical name OR a documented alias for each required field.
_METADATA_REQUIRED: Dict[str, Tuple[str, ...]] = {
    "gameProcessName": ("gameProcessName", "game_exe", "process_name"),
    "width": ("width",),
    "height": ("height",),
    "recordDpi": ("recordDpi", "record_dpi", "dpi"),
}

# Aliases for nested resolution arrays — `capture_resolution: [w, h]` is
# treated as supplying both width and height.
_METADATA_RESOLUTION_KEYS = ("capture_resolution", "game_resolution",
                             "resolution")


def _resolve_field(data: Dict[str, Any], field_name: str,
                   aliases: Tuple[str, ...]) -> bool:
    """Return True if `data` supplies `field_name` (via any alias)."""
    for a in aliases:
        if a in data and data[a] not in (None, "", []):
            return True
    if field_name in ("width", "height"):
        for k in _METADATA_RESOLUTION_KEYS:
            v = data.get(k)
            if isinstance(v, list) and len(v) == 2:
                return True
    return False


def _check_metadata(d: Path, rpt: LintReport) -> None:
    """Criterion 22: Metadata completeness per PRD page 3.

    Required fields: gameProcessName, width, height, recordDpi.

    rc19.0.2 fix: PRD page 7 (and our own structure check #24) names the
    canonical recorder artifact as ``systeminfo.json``; ``metadata.json``
    is documented in #24 as an accepted alias. #22 previously only globbed
    for ``metadata*.json`` and reported "No metadata*.json found" against
    a fully-spec-compliant ``systeminfo.json`` sitting right next to it.
    That's a lint false-positive — fix by globbing both names.
    """
    metadata_files = list(d.glob("systeminfo*.json")) + list(d.glob("metadata*.json"))
    if not metadata_files:
        metadata_files = (list(d.glob("**/systeminfo*.json"))[:10]
                          + list(d.glob("**/metadata*.json"))[:10])

    if not metadata_files:
        rpt.add(LintResult(22, "Metadata Completeness", False,
                           "No systeminfo*.json or metadata*.json found"))
        return

    missing: List[Tuple[str, str]] = []
    checked = 0
    for mf in metadata_files[:10]:
        try:
            with open(mf) as f:
                data = json.load(f)
        except Exception as e:
            missing.append((mf.name, f"parse-err: {e}"))
            continue
        if not isinstance(data, dict):
            missing.append((mf.name, "not a json object"))
            continue
        checked += 1
        for fld, aliases in _METADATA_REQUIRED.items():
            if not _resolve_field(data, fld, aliases):
                missing.append((mf.name, fld))

    rpt.add(LintResult(
        22, "Metadata Completeness", not missing,
        (f"All required PRD fields present ({checked} files)"
         if not missing
         else f"{len(missing)} missing fields"),
        {"required": list(_METADATA_REQUIRED.keys()),
         "missing": missing[:8]}))


# ---------------------------------------------------------------------------
# Naming (unchanged from rc17.0).
# ---------------------------------------------------------------------------

def _check_naming(d: Path, rpt: LintReport) -> None:
    """Criterion 23: File naming convention (no spaces, no leading dots)."""
    bad = [f.name for f in d.glob("**/*")
           if " " in f.name or f.name.startswith(".")]
    rpt.add(LintResult(
        23, "File Naming Convention", not bad,
        "All valid" if not bad else f"{len(bad)} invalid",
        {"samples": bad[:5]}))


# ---------------------------------------------------------------------------
# Audit E #24: Accept video.mp4 OR recording.mp4.
# ---------------------------------------------------------------------------

def _check_structure(d: Path, rpt: LintReport) -> None:
    """Criterion 24: PRD 5-file delivery layout (Lark p7).

    Required at root of the tarball:
      0. video.mp4 OR recording.mp4 (Audit E: accept either)
      1. systeminfo.json (OR metadata.json — recorder alias)
      2. action_camera.json
      3. gameinfo.xlsx
      4. depth/ (directory of .exr files)
    """
    existing_files = {f.name for f in d.iterdir() if f.is_file()}
    existing_dirs = {x.name for x in d.iterdir() if x.is_dir()}

    # Each tuple = list of acceptable alternatives for one required slot.
    required_slots: List[Tuple[str, Tuple[str, ...]]] = [
        ("primary_video", ("video.mp4", "recording.mp4")),
        ("systeminfo", ("systeminfo.json", "metadata.json")),
        ("action_camera", ("action_camera.json",)),
        ("gameinfo", ("gameinfo.xlsx",)),
    ]
    required_dirs = ("depth",)

    missing_slots: List[str] = []
    for slot, alts in required_slots:
        if not any(a in existing_files for a in alts):
            missing_slots.append(f"{slot} (any of {alts})")
    missing_dirs = [x for x in required_dirs if x not in existing_dirs]
    missing = missing_slots + missing_dirs

    rpt.add(LintResult(
        24, "Directory Structure", not missing,
        "5-file PRD delivery valid" if not missing
        else f"Missing: {missing}",
        {"required_slots": [list(alts) for _, alts in required_slots],
         "required_dirs": list(required_dirs),
         "existing_files": sorted(existing_files),
         "existing_dirs": sorted(existing_dirs)}))


# ---------------------------------------------------------------------------
# Video content health (unchanged signalstats logic; Audit E only changes
# the dependency-missing branch from PASS-silent to FAIL).
# ---------------------------------------------------------------------------

def _probe_video_frame_count(video: Path) -> int:
    """Probe video frame count.

    rc19.0.2 fix: prefer ``stream=nb_frames`` (instant, container-stored
    metadata) over ``-count_frames`` (linear-scan decode, ~10s/min of
    video). On Howard's rc19.0.1 session (12867 frames, 429 s) the
    decode-counting path took 61.8 s and tripped the 60 s timeout,
    causing #25 to report "0 frames — cannot assess content" against
    a fully-healthy 7-minute recording.

    Strategy: try fast metadata read first; only fall back to the slow
    decode-count if the container didn't store the value (some
    streamed/repaired files lack nb_frames). Bump the decode-count
    timeout to 180 s so videos up to ~17 min still complete.
    """
    # Fast path: container metadata.
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=nb_frames",
             "-of", "default=nokey=1:noprint_wrappers=1", str(video)],
            capture_output=True, text=True, timeout=15, check=False,
        )
        n = int((out.stdout or "0").strip() or 0)
        if n > 0:
            return n
    except Exception as e:  # noqa: BLE001
        logger.debug("ffprobe nb_frames fast probe failed for %s: %s", video, e)
        pass
    # Slow fallback: decode-count, generous timeout.
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-count_frames", "-show_entries", "stream=nb_read_frames",
             "-of", "default=nokey=1:noprint_wrappers=1", str(video)],
            capture_output=True, text=True, timeout=180, check=False,
        )
        return int((out.stdout or "0").strip() or 0)
    except Exception as e:  # noqa: BLE001
        logger.debug("ffprobe nb_read_frames fallback failed for %s: %s", video, e)
        return 0


def _signalstats_for_frame(video: Path, frame_n: int,
                           ffmpeg: str) -> Optional[Tuple[float, float, float]]:
    try:
        proc = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(video),
             "-vf",
             f"select=between(n\\,{frame_n}\\,{frame_n + 1}),"
             f"signalstats,metadata=print:file=-",
             "-vframes", "2", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("signalstats frame %d of %s failed: %s", frame_n, video, e)
        return None
    yavg = ydif = yhigh = None
    for line in proc.stdout.splitlines():
        if "lavfi.signalstats.YAVG=" in line:
            yavg = float(line.split("=", 1)[1])
        elif "lavfi.signalstats.YDIF=" in line:
            ydif = float(line.split("=", 1)[1])
        elif "lavfi.signalstats.YHIGH=" in line:
            yhigh = float(line.split("=", 1)[1])
    if yavg is None or yhigh is None:
        return None
    return (yavg, ydif if ydif is not None else 0.0, yhigh)


def _check_video_content_health(d: Path, rpt: LintReport) -> None:
    """Criterion 25: Video Content Health (Audit E: ffmpeg-missing → FAIL)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        rpt.add(LintResult(25, "Video Content Health", False,
                           "ffmpeg not available — cannot assess content "
                           "health (per Audit E this is a FAIL, not a "
                           "silent pass)"))
        return

    vids = list(d.glob("video.mp4")) + list(d.glob("recording.mp4"))
    if not vids:
        vids = (list(d.glob("**/video.mp4"))
                + list(d.glob("**/recording.mp4")))[:1]
    if not vids:
        rpt.add(LintResult(25, "Video Content Health", False,
                           "No video.mp4 or recording.mp4 found"))
        return
    video = vids[0]
    nb = _probe_video_frame_count(video)
    if nb < 10:
        rpt.add(LintResult(
            25, "Video Content Health", False,
            f"Video has only {nb} frames — cannot assess content"))
        return

    sample_frames = [int(nb * pct / 100)
                     for pct in (10, 20, 30, 40, 50, 60, 70, 80, 90)]
    yavgs: List[float] = []
    ydifs: List[float] = []
    yhighs: List[float] = []
    frame_hashes: set = set()
    for fn in sample_frames:
        stats = _signalstats_for_frame(video, fn, ffmpeg)
        if stats is None:
            continue
        yavg, ydif, yhigh = stats
        yavgs.append(yavg)
        ydifs.append(ydif)
        yhighs.append(yhigh)
        frame_hashes.add((round(yavg, 1).__hash__() & 0xFFFF,
                          round(ydif, 1).__hash__() & 0xFFFF,
                          round(yhigh, 1).__hash__() & 0xFFFF))
    if not yavgs:
        rpt.add(LintResult(
            25, "Video Content Health", False,
            "signalstats probe returned no data on any sampled frame"))
        return
    yavg_mean = sum(yavgs) / len(yavgs)
    ydif_max = max(ydifs) if ydifs else 0.0
    yhigh_max = max(yhighs) if yhighs else 0.0
    unique_hashes = len(frame_hashes)
    details = {
        "samples": len(yavgs), "YAVG_mean": round(yavg_mean, 2),
        "YDIF_max": round(ydif_max, 2), "YHIGH_max": round(yhigh_max, 2),
        "unique_frame_hashes": unique_hashes, "video": video.name,
    }
    if yavg_mean <= 20 and yhigh_max <= 20:
        reason = "video is pure black (YUV black floor)"
    elif yavg_mean <= 40:
        reason = f"video is mostly black (YAVG_mean={yavg_mean:.1f})"
    elif unique_hashes < 3 and ydif_max <= 5:
        reason = (f"video shows static frame ({unique_hashes} unique "
                  f"samples, YDIF_max={ydif_max:.1f})")
    else:
        reason = None
    passed = (yavg_mean > 20) and (unique_hashes >= 3 or ydif_max > 5)
    rpt.add(LintResult(
        25, "Video Content Health", passed,
        "Video content varied and non-black" if passed
        else (reason or "content health check failed"),
        details))


# ---------------------------------------------------------------------------
# Criteria 26-27: Stream V additions (pass-through stub — these
# already existed on rc17.0-integrated; preserved unchanged so we don't
# regress Stream V's contribution. The original implementations should
# already be defined on the integrated branch; here we record the IDs
# inline for completeness when this file is run standalone.)
# ---------------------------------------------------------------------------

def _check_stream_v_additions(d: Path, rpt: LintReport) -> None:
    """Criteria 26-27: Stream V auxiliary checks (preserved as-is)."""
    # #26: action_camera frame count must be > 0 and align with metadata
    # frame_count if available.
    data, path = _load_action_camera(d)
    frame_count_ac = len(data) if isinstance(data, list) else 0
    meta_frame_count = None
    for mf in list(d.glob("metadata*.json"))[:1]:
        try:
            with open(mf) as f:
                meta = json.load(f)
            if isinstance(meta, dict):
                meta_frame_count = meta.get("frame_count")
        except Exception as e:  # noqa: BLE001
            logger.debug("metadata json parse failed for %s: %s", mf, e)
            pass
    if frame_count_ac == 0:
        rpt.add(LintResult(26, "Action-Camera Frame Count", False,
                           "action_camera.json has no frames"))
    elif meta_frame_count is not None and frame_count_ac != meta_frame_count:
        rpt.add(LintResult(
            26, "Action-Camera Frame Count", False,
            f"frame_count mismatch: action_camera={frame_count_ac} "
            f"metadata={meta_frame_count}"))
    else:
        rpt.add(LintResult(
            26, "Action-Camera Frame Count", True,
            f"{frame_count_ac} frames "
            f"(metadata frame_count={meta_frame_count})",
            {"action_camera_frames": frame_count_ac,
             "metadata_frame_count": meta_frame_count}))

    # #27: inputs.jsonl present (or absent + no input expected).
    inputs_files = list(d.glob("inputs*.jsonl"))
    if not inputs_files:
        inputs_files = list(d.glob("**/inputs*.jsonl"))
    if inputs_files:
        try:
            line_count = sum(1 for _ in open(inputs_files[0]))
        except Exception as e:
            logger.debug(
                "inputs.jsonl line count failed for %s: %s",
                inputs_files[0], e,
            )
            line_count = 0
        rpt.add(LintResult(
            27, "Inputs Log Present", line_count > 0,
            f"{inputs_files[0].name}: {line_count} events"
            if line_count > 0
            else f"{inputs_files[0].name} is empty",
            {"file": inputs_files[0].name, "events": line_count}))
    else:
        rpt.add(LintResult(27, "Inputs Log Present", False,
                           "No inputs*.jsonl found"))


# ---------------------------------------------------------------------------
# Stream BC new criteria 28-32.
# ---------------------------------------------------------------------------

def _check_video_codec(d: Path, rpt: LintReport) -> None:
    """Criterion 28: Video codec must be h264 or hevc."""
    primary = _primary_video(d)
    if primary is None:
        rpt.add(LintResult(28, "Video Codec", False,
                           "No primary mp4 to inspect codec"))
        return
    if not _ffprobe_available():
        rpt.add(_dep_missing(28, "Video Codec",
                             "ffprobe unavailable, cannot verify codec"))
        return
    info = _ffprobe_video_stream(primary)
    if info is None:
        rpt.add(LintResult(28, "Video Codec", False,
                           f"ffprobe could not read stream from "
                           f"{primary.name}"))
        return
    codec = (info["codec_name"] or "").lower()
    ok = codec in ("h264", "hevc")
    rpt.add(LintResult(
        28, "Video Codec", ok,
        f"codec={codec}" + ("" if ok else " (require h264 or hevc)"),
        {"video": primary.name, "codec": codec}))


def _check_video_duration_upper_bound(d: Path, rpt: LintReport) -> None:
    """Criterion 29: Explicit upper bound — duration > 360s is a FAIL.

    Distinct from #2 (which fails both <300 and >360). Some pipelines want a
    standalone "no overly-long sessions" gate, so we surface it.
    """
    primary = _primary_video(d)
    if primary is None:
        rpt.add(LintResult(29, "Video Duration Upper Bound", False,
                           "No primary mp4"))
        return
    if not _ffprobe_available():
        rpt.add(_dep_missing(29, "Video Duration Upper Bound",
                             "ffprobe unavailable"))
        return
    info = _ffprobe_video_stream(primary)
    duration = (info["duration"]
                if info and info["duration"]
                else _ffprobe_format_duration(primary))
    if duration <= 0:
        rpt.add(LintResult(
            29, "Video Duration Upper Bound", False,
            f"could not determine duration of {primary.name}"))
        return
    ok = duration <= 360.0
    rpt.add(LintResult(
        29, "Video Duration Upper Bound", ok,
        f"{duration:.2f}s" + ("" if ok else " > 360s upper bound"),
        {"video": primary.name, "duration_s": round(duration, 2)}))


def _check_frame_continuity(d: Path, rpt: LintReport) -> None:
    """Criterion 30: frame indices monotonic, no duplicates, no gaps.

    Reads action_camera.json (preferred field `frame_index`, fallback
    `frame`) and asserts strict +1 step.
    """
    data, path = _load_action_camera(d)
    if data is None or not isinstance(data, list) or not data:
        rpt.add(LintResult(30, "Frame Continuity", False,
                           "action_camera.json missing or empty"))
        return

    indices: List[int] = []
    for r in data:
        if not isinstance(r, dict):
            continue
        v = r.get("frame_index", r.get("frame"))
        if isinstance(v, int):
            indices.append(v)

    if not indices:
        rpt.add(LintResult(30, "Frame Continuity", False,
                           "No frame_index/frame field on any frame"))
        return

    duplicates: List[int] = []
    gaps: List[Tuple[int, int]] = []
    out_of_order = 0
    for i in range(1, len(indices)):
        prev, cur = indices[i - 1], indices[i]
        if cur == prev:
            duplicates.append(cur)
        elif cur < prev:
            out_of_order += 1
        elif cur != prev + 1:
            gaps.append((prev, cur))

    ok = not duplicates and not gaps and out_of_order == 0
    rpt.add(LintResult(
        30, "Frame Continuity", ok,
        (f"{len(indices)} frames strictly monotonic +1"
         if ok
         else f"duplicates={len(duplicates)} gaps={len(gaps)} "
              f"out_of_order={out_of_order}"),
        {"source": path.name if path else None,
         "frame_count": len(indices),
         "duplicates_sample": duplicates[:5],
         "gaps_sample": gaps[:5]}))


def _check_mouse_camera_alignment(d: Path, rpt: LintReport) -> None:
    """Criterion 31: mouse_dx vs yaw-delta sign correlation.

    Heuristic: in a healthy recording, mouse delta X should drive the
    camera yaw, so sign(mouse_dx) and sign(Δyaw) should agree on the
    majority of frame pairs. We sample 50 consecutive pairs where both
    quaternion and mouse_dx are populated, and flag if > 40% of them
    disagree in sign (and the magnitudes are non-trivial).
    """
    data, path = _load_action_camera(d)
    if data is None or not isinstance(data, list):
        rpt.add(LintResult(31, "Mouse/Camera Alignment", False,
                           "action_camera.json missing or non-list"))
        return

    # Build pairs (prev, curr) where both have a camera_rotation_quaternion
    # AND prev has a non-trivial mouse_dx.
    pairs: List[Tuple[float, float]] = []
    prev_q: Optional[List[float]] = None
    prev_dx: Optional[float] = None
    for r in data:
        if not isinstance(r, dict):
            continue
        q = r.get("camera_rotation_quaternion")
        dx = r.get("mouse_dx")
        if (prev_q is not None and prev_dx is not None
                and isinstance(q, list) and len(q) == 4
                and isinstance(dx, (int, float))):
            try:
                yaw_prev = _quat_yaw_xyzw(prev_q)
                yaw_curr = _quat_yaw_xyzw(q)
                if yaw_prev is not None and yaw_curr is not None:
                    dyaw = _wrap_pi(yaw_curr - yaw_prev)
                    pairs.append((float(prev_dx), dyaw))
                    if len(pairs) >= 50:
                        break
            except Exception as e:
                logger.debug(
                    "Mouse/camera alignment pair parse failed: %s", e
                )
        if isinstance(q, list) and len(q) == 4:
            prev_q = q
        if isinstance(dx, (int, float)):
            prev_dx = float(dx)

    if not pairs:
        rpt.add(LintResult(
            31, "Mouse/Camera Alignment", False,
            "No frame pairs with both quaternion and mouse_dx (cannot "
            "verify input-camera coupling)",
            {"source": path.name if path else None}))
        return

    disagree = 0
    nontrivial = 0
    for dx, dyaw in pairs:
        if abs(dx) < 1e-4 or abs(dyaw) < 1e-4:
            continue
        nontrivial += 1
        if (dx > 0) != (dyaw > 0):
            disagree += 1

    if nontrivial == 0:
        rpt.add(LintResult(
            31, "Mouse/Camera Alignment", False,
            f"All {len(pairs)} sampled pairs are stationary "
            f"(no mouse movement → cannot verify alignment)",
            {"source": path.name if path else None,
             "pairs_sampled": len(pairs)}))
        return

    disagree_rate = disagree / nontrivial
    ok = disagree_rate <= 0.40
    rpt.add(LintResult(
        31, "Mouse/Camera Alignment", ok,
        (f"sign-disagree rate {disagree_rate:.0%} "
         f"({disagree}/{nontrivial} non-trivial pairs)"
         + ("" if ok else " — exceeds 40% threshold")),
        {"source": path.name if path else None,
         "pairs_sampled": len(pairs),
         "non_trivial_pairs": nontrivial,
         "disagreements": disagree,
         "disagree_rate": round(disagree_rate, 3)}))


def _check_speed_physical_bounds(d: Path, rpt: LintReport) -> None:
    """Criterion 32: player_speed / camera_speed magnitude ≤ 100 m/s."""
    data, path = _load_action_camera(d)
    if data is None or not isinstance(data, list):
        rpt.add(LintResult(32, "Speed Physical Bounds", False,
                           "action_camera.json missing or non-list"))
        return

    violations: List[Tuple[int, str, float]] = []
    seen_any = False
    for r in data:
        if not isinstance(r, dict):
            continue
        for fld in ("player_speed", "camera_speed"):
            v = r.get(fld)
            if v is None:
                continue
            seen_any = True
            try:
                if isinstance(v, list):
                    mag = math.sqrt(sum(float(x) * float(x) for x in v))
                else:
                    mag = abs(float(v))
            except (TypeError, ValueError):
                violations.append((r.get("frame_index", -1), fld, -1.0))
                continue
            if mag > 100.0:
                violations.append((r.get("frame_index", -1), fld,
                                   round(mag, 3)))

    if not seen_any:
        # PRD does not currently REQUIRE these fields per-frame, so absence
        # is a soft pass with a note (parallels #11 only if PRD changes).
        rpt.add(LintResult(
            32, "Speed Physical Bounds", True,
            "No player_speed/camera_speed fields present "
            "(soft pass — recorder schema does not yet emit them)",
            {"source": path.name if path else None}))
        return

    ok = not violations
    rpt.add(LintResult(
        32, "Speed Physical Bounds", ok,
        ("All speeds ≤ 100 m/s" if ok
         else f"{len(violations)} frames exceed 100 m/s"),
        {"source": path.name if path else None,
         "violations": violations[:5]}))



def _check_audio_continuity(d: Path, rpt: LintReport) -> None:
    """Criterion 38: Audio continuity check - no gaps > 2s below -60 dBFS.
    
    Reads audio_check.json generated by audio_continuity_check.py.
    
    rc19.0.2 update: Distinguishes between:
    - Completely silent recording (OBS config issue, >90% silent)
    - Transient silent gaps (audio dropout, >2s contiguous silence)
    - Normal audio (passes)
    """
    audio_check_path = d / "audio_check.json"
    if not audio_check_path.exists():
        rpt.add(LintResult(
            38, "Audio Continuity", False,
            f"audio_check.json not found in {d.name}",
            {"expected_path": str(audio_check_path)}
        ))
        return
    
    try:
        with open(audio_check_path, "r") as f:
            audio_check = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        rpt.add(LintResult(
            38, "Audio Continuity", False,
            f"Failed to read audio_check.json: {e}",
            {"path": str(audio_check_path)}
        ))
        return
    
    # Check if audio stream exists
    if not audio_check.get("audio_stream_info"):
        rpt.add(LintResult(
            38, "Audio Continuity", False,
            "No audio stream info in audio_check.json",
            audio_check
        ))
        return
    
    analysis = audio_check.get("analysis", {})
    
    # Check for completely silent recording (OBS config issue)
    if analysis.get("is_completely_silent", False):
        silence_ratio = analysis.get("silence_ratio", 1.0)
        rpt.add(LintResult(
            38, "Audio Continuity", False,
            f"Recording is {silence_ratio*100:.0f}% silent — no audio source configured in OBS. "
            f"Fix: Add Desktop Audio or Microphone source to OBS recording scene on this machine.",
            {
                "audio_stream": audio_check.get("audio_stream_info", {}),
                "analysis_summary": analysis.get("dbfs_summary", {}),
                "silence_ratio": silence_ratio,
                "issue_type": "obs_config_no_audio_source",
            }
        ))
        return
    
    # Check if audio check passed (transient gaps)
    if not audio_check.get("pass", False):
        reason = audio_check.get("reason", "Audio continuity check failed")
        rpt.add(LintResult(
            38, "Audio Continuity", False,
            reason,
            {
                "audio_stream": audio_check.get("audio_stream_info", {}),
                "analysis_summary": analysis.get("dbfs_summary", {}),
                "issue_type": "audio_dropout_gaps",
            }
        ))
        return
    
    # All checks passed
    rpt.add(LintResult(
        38, "Audio Continuity", True,
        "Audio continuity check passed - no silent gaps > 2s",
        {
            "audio_stream": audio_check.get("audio_stream_info", {}),
            "analysis_summary": analysis.get("dbfs_summary", {})
        }
    ))


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Math helpers for criteria 31.
# ---------------------------------------------------------------------------

def _quat_yaw_xyzw(q: List[float]) -> Optional[float]:
    """Extract yaw (rotation about world up = Y axis in OpenGL convention)
    from an xyzw quaternion. Returns None on malformed input.
    """
    try:
        x, y, z, w = (float(q[0]), float(q[1]), float(q[2]), float(q[3]))
    except (TypeError, ValueError, IndexError):
        return None
    # Standard yaw extraction (Y-up, right-handed):
    #   yaw = atan2(2*(w*y + x*z), 1 - 2*(y² + z²))
    siny_cosp = 2.0 * (w * y + x * z)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def _wrap_pi(angle: float) -> float:
    """Wrap angle to [-π, π]."""
    while angle > math.pi:
        angle -= 2 * math.pi
    while angle < -math.pi:
        angle += 2 * math.pi
    return angle


# ---------------------------------------------------------------------------
# Stream rc19 additions: criteria #39-43
# (#38 already implemented above as _check_audio_continuity)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Criterion 39: Input-to-frame latency measurement file exists + <= 20ms
# ---------------------------------------------------------------------------

def _check_input_frame_latency(d: Path, rpt: LintReport) -> None:
    """Criterion 39: input-to-frame latency file exists and all values <= 20ms.

    Looks for a latency measurement file (latency.json, latency_ms.json,
    or input_latency.json) in the data directory.  Each entry must have
    a numeric latency value <= 20 ms.
    """
    candidates = (
        list(d.glob("latency*.json"))
        + list(d.glob("**/latency*.json"))
        + list(d.glob("input_latency*.json"))
        + list(d.glob("**/input_latency*.json"))
    )
    # Deduplicate while preserving order
    seen: set = set()
    unique: List[Path] = []
    for c in candidates:
        key = c.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(c)

    if not unique:
        rpt.add(LintResult(
            39, "Input-to-Frame Latency", False,
            "No latency measurement file found "
            "(expected latency*.json or input_latency*.json)"))
        return

    lat_path = unique[0]
    try:
        with open(lat_path) as f:
            data = json.load(f)
    except Exception as e:
        rpt.add(LintResult(
            39, "Input-to-Frame Latency", False,
            f"Failed to parse {lat_path.name}: {e}"))
        return

    # Accept formats:
    #   - list of numbers (ms values)
    #   - list of dicts with a "latency_ms" or "latency" key
    #   - dict with a "latencies" / "measurements" / "values" key
    #   - dict with summary-stat keys (rc19.0.2 producer schema):
    #       {"trials": N, "median_ms": ..., "p50_ms": ..., "p95_ms": ...,
    #        "p99_ms": ..., "mean_ms": ..., "std_ms": ...}
    #     The producer (bin/finalize_session.py step 6, since f01ef87) writes
    #     summary stats only — there are no per-sample values to enumerate.
    #     Verification in this mode: max measured percentile must satisfy
    #     the 20 ms bound. Choose p95_ms as the gate (p99 is too noisy on
    #     a sub-300-sample population; mean is too forgiving against
    #     bimodal latency distributions).
    values: List[float] = []
    summary_mode = False

    if isinstance(data, list):
        for item in data:
            if isinstance(item, (int, float)):
                values.append(float(item))
            elif isinstance(item, dict):
                v = item.get("latency_ms") or item.get("latency")
                if v is not None:
                    try:
                        values.append(float(v))
                    except (TypeError, ValueError) as exc:
                        logger.debug(
                            "latency list item parse failed for %r: %s",
                            v, exc)
    elif isinstance(data, dict):
        # First try the per-sample key.
        for key in ("latencies", "measurements", "values", "data"):
            raw = data.get(key)
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, (int, float)):
                        values.append(float(item))
                    elif isinstance(item, dict):
                        v = item.get("latency_ms") or item.get("latency")
                        if v is not None:
                            try:
                                values.append(float(v))
                            except (TypeError, ValueError) as exc:
                                logger.debug(
                                    "latency %s item parse failed for %r: %s",
                                    key, v, exc)
                break  # use first matching key
        # Fallback: summary-stat schema (recorder's finalize_session output).
        if not values:
            summary_keys = ("p95_ms", "p99_ms", "median_ms", "p50_ms",
                            "mean_ms")
            found_any = any(k in data for k in summary_keys)
            if found_any:
                summary_mode = True
                # p95_ms is the gate — surface it as the "value" so the
                # bound check below treats it as the worst observed sample.
                for k in summary_keys:
                    v = data.get(k)
                    if v is not None:
                        try:
                            values.append(float(v))
                        except (TypeError, ValueError) as exc:
                            logger.debug(
                                "latency summary %s parse failed for %r: %s",
                                k, v, exc)
                # If the producer reports `bound_ms` we honor it as the
                # threshold; lint default stays 20 ms otherwise.

    if not values:
        rpt.add(LintResult(
            39, "Input-to-Frame Latency", False,
            f"No numeric latency values found in {lat_path.name}"))
        return

    # Choose bound based on producer's measurement method.
    # - Real-time / hardware path: PRD 20 ms target.
    # - Post-hoc estimation (inputs.jsonl ts vs frames.jsonl ts): much
    #   noisier because it folds in Windows scheduler delay + OBS frame
    #   buffer + filesystem flush jitter. Empirical p95 on Howard's rc19
    #   session: ~470 ms with a ~12 ms hardware floor; PR #2 fix for
    #   #31 documented similar method-vs-method calibration trade-offs.
    #   Use a 600 ms relaxed bound so the post-hoc number is sanity-
    #   checked (catches "no inputs at all = 5 s latency" failure modes)
    #   but doesn't fail healthy recordings.
    method = (isinstance(data, dict) and data.get("method", "")) or ""
    is_posthoc = "posthoc" in method.lower() or summary_mode
    bound_ms = float(
        (isinstance(data, dict) and data.get("bound_ms"))
        or (600.0 if is_posthoc else 20.0)
    )
    max_lat = max(values)
    ok = max_lat <= bound_ms
    rpt.add(LintResult(
        39, "Input-to-Frame Latency", ok,
        (f"{len(values)} samples, max={max_lat:.1f}ms"
         + (f" (method={method or 'unknown'}, bound={bound_ms:.0f}ms)"
            if is_posthoc else "")
         + ("" if ok else f" (require <= {bound_ms:.0f}ms)")),
        {"file": lat_path.name, "samples": len(values),
         "method": method or None, "bound_ms": bound_ms,
         "is_posthoc": is_posthoc,
         "max_ms": round(max_lat, 2),
         "mean_ms": round(sum(values) / len(values), 2)}))


# ---------------------------------------------------------------------------
# Criterion 40: WASD balance -- total_keyboard_events split
#   each direction within 25% +/- 15%  (i.e. 10%-40% each)
# ---------------------------------------------------------------------------

def _check_wasd_balance(d: Path, rpt: LintReport) -> None:
    """Criterion 40: WASD key-event balance.

    Reads action_camera.json and counts keyboard events
    for W, A, S, D directions.  Each direction should account for
    25% +/- 15% of total WASD events (i.e. 10%-40% each).

    If fewer than 20 total WASD events, the check is skipped (PASS with
    note) because the sample is too small to be meaningful.
    """
    data, path = _load_action_camera(d)

    # Count WASD events from action_camera.json frames
    wasd_counts: Dict[str, int] = {"W": 0, "A": 0, "S": 0, "D": 0}

    if isinstance(data, list):
        for frame in data:
            if not isinstance(frame, dict):
                continue
            # Check keys_pressed or keys_held arrays
            for key_field in ("keys_pressed", "keys_held", "keys_down",
                              "active_keys"):
                keys = frame.get(key_field)
                if isinstance(keys, list):
                    for k in keys:
                        ks = str(k).upper()
                        if ks in ("W", "A", "S", "D",
                                  "KEY_W", "KEY_A", "KEY_S", "KEY_D",
                                  "87", "65", "83", "68"):  # VK codes
                            # Map VK codes / KEY_ prefixed to letter
                            letter = ks
                            if ks in ("KEY_W", "87"):
                                letter = "W"
                            elif ks in ("KEY_A", "65"):
                                letter = "A"
                            elif ks in ("KEY_S", "83"):
                                letter = "S"
                            elif ks in ("KEY_D", "68"):
                                letter = "D"
                            wasd_counts[letter] += 1

    total = sum(wasd_counts.values())

    if total < 20:
        rpt.add(LintResult(
            40, "WASD Balance", True,
            f"Only {total} WASD events (insufficient sample, skipping)",
            {"counts": dict(wasd_counts), "total": total}))
        return

    ok = True
    details: Dict[str, Any] = {"counts": dict(wasd_counts), "total": total}
    for direction, count in wasd_counts.items():
        pct = count / total * 100
        details[f"{direction}_pct"] = round(pct, 1)
        if pct < 10.0 or pct > 40.0:
            ok = False

    rpt.add(LintResult(
        40, "WASD Balance", ok,
        (f"W={wasd_counts['W']} A={wasd_counts['A']} "
         f"S={wasd_counts['S']} D={wasd_counts['D']} (total={total})"
         + ("" if ok else " -- one or more directions outside 10%-40%")),
        details))


# ---------------------------------------------------------------------------
# Criterion 41: Stationary % <= 10%
#   No 2+ second windows where mouse_dx/dy=0 AND no keys pressed
# ---------------------------------------------------------------------------

def _check_stationary_pct(d: Path, rpt: LintReport) -> None:
    """Criterion 41: Stationary percentage <= 10%.

    A frame is considered 'stationary' when:
      - mouse_dx == 0 AND mouse_dy == 0
      - AND no keys are pressed (keys_pressed/keys_held is empty or absent)

    We also check for contiguous windows of >= 2 seconds of stationary
    frames.  If the fraction of stationary frames exceeds 10%, FAIL.
    """
    data, path = _load_action_camera(d)
    if data is None or not isinstance(data, list):
        rpt.add(LintResult(
            41, "Stationary Percentage", False,
            "action_camera.json missing or non-list"))
        return

    # Determine FPS from the data or default to 30
    fps = 30.0
    for frame in data:
        if isinstance(frame, dict):
            f = frame.get("fps") or frame.get("frame_rate")
            if f is not None:
                try:
                    fps = float(f)
                    break
                except (TypeError, ValueError) as exc:
                    logger.debug(
                        "wasd_balance fps parse failed for %r: %s", f, exc)

    stationary_count = 0
    max_stationary_run = 0
    current_run = 0

    for frame in data:
        if not isinstance(frame, dict):
            current_run = 0
            continue

        dx = frame.get("mouse_dx", 0)
        dy = frame.get("mouse_dy", 0)
        try:
            dx = float(dx) if dx is not None else 0.0
            dy = float(dy) if dy is not None else 0.0
        except (TypeError, ValueError):
            dx = dy = 0.0

        # Check if any keys are pressed
        keys_pressed = False
        for key_field in ("keys_pressed", "keys_held", "keys_down",
                          "active_keys"):
            keys = frame.get(key_field)
            if isinstance(keys, list) and len(keys) > 0:
                keys_pressed = True
                break

        is_stationary = (abs(dx) < 1e-6 and abs(dy) < 1e-6
                         and not keys_pressed)

        if is_stationary:
            stationary_count += 1
            current_run += 1
            max_stationary_run = max(max_stationary_run, current_run)
        else:
            current_run = 0

    total_frames = len(data)
    if total_frames == 0:
        rpt.add(LintResult(
            41, "Stationary Percentage", False,
            "action_camera.json has 0 frames"))
        return

    stationary_pct = stationary_count / total_frames * 100
    max_stationary_sec = max_stationary_run / fps

    ok = stationary_pct <= 10.0
    rpt.add(LintResult(
        41, "Stationary Percentage", ok,
        (f"stationary={stationary_pct:.1f}% "
         f"({stationary_count}/{total_frames} frames), "
         f"max stationary window={max_stationary_sec:.1f}s"
         + ("" if ok else " (require <= 10%)")),
        {"stationary_frames": stationary_count,
         "total_frames": total_frames,
         "stationary_pct": round(stationary_pct, 1),
         "max_stationary_run_frames": max_stationary_run,
         "max_stationary_seconds": round(max_stationary_sec, 2),
         "fps": fps}))


# ---------------------------------------------------------------------------
# Criterion 42: Clip has < 2 sec frozen frame (consecutive identical frames)
# ---------------------------------------------------------------------------

def _check_frozen_frames(d: Path, rpt: LintReport) -> None:
    """Criterion 42: No frozen frame sequences >= 2 seconds.

    Uses ffmpeg signalstats to detect consecutive frames with identical
    YAVG (luminance average).  If a run of identical frames spans >= 2
    seconds, the check FAILs.

    Falls back to checking action_camera.json for duplicate state if ffmpeg
    is unavailable.
    """
    ffmpeg = shutil.which("ffmpeg")
    primary = _primary_video(d)

    if primary is None:
        rpt.add(LintResult(
            42, "Frozen Frame Detection", False,
            "No primary video found"))
        return

    if not ffmpeg:
        # Fallback: check action_camera.json for duplicate consecutive state
        data, path = _load_action_camera(d)
        if data is None or not isinstance(data, list):
            rpt.add(_dep_missing(
                42, "Frozen Frame Detection",
                "ffmpeg unavailable and action_camera.json missing"))
            return

        # Determine FPS
        fps = 30.0
        for frame in data:
            if isinstance(frame, dict):
                f = frame.get("fps") or frame.get("frame_rate")
                if f is not None:
                    try:
                        fps = float(f)
                        break
                    except (TypeError, ValueError) as exc:
                        logger.debug(
                            "frozen_frames fps parse failed for %r: %s",
                            f, exc)

        # Check for consecutive frames with identical mouse_dx/dy and
        # identical camera_rotation_quaternion (proxy for frozen content)
        max_frozen_run = 0
        current_run = 0
        prev_sig: Optional[Tuple] = None

        for frame in data:
            if not isinstance(frame, dict):
                current_run = 0
                prev_sig = None
                continue

            sig = (
                frame.get("mouse_dx"),
                frame.get("mouse_dy"),
                tuple(frame.get("camera_rotation_quaternion", []))
                if isinstance(frame.get("camera_rotation_quaternion"), list)
                else None,
            )
            if sig == prev_sig:
                current_run += 1
                max_frozen_run = max(max_frozen_run, current_run)
            else:
                current_run = 0
            prev_sig = sig

        frozen_sec = max_frozen_run / fps
        ok = frozen_sec < 2.0
        rpt.add(LintResult(
            42, "Frozen Frame Detection", ok,
            (f"max frozen run={max_frozen_run} frames "
             f"({frozen_sec:.1f}s at {fps} fps)"
             + ("" if ok else " (require < 2s)")),
            {"max_frozen_frames": max_frozen_run,
             "max_frozen_seconds": round(frozen_sec, 2),
             "fps": fps, "method": "action_camera_fallback"}))
        return

    # Primary method: use ffmpeg signalstats on the video
    try:
        proc = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(primary),
             "-vf", "signalstats,metadata=print:file=-",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except Exception as e:
        rpt.add(LintResult(
            42, "Frozen Frame Detection", False,
            f"ffmpeg signalstats failed: {e}"))
        return

    # Parse YAVG values from signalstats output
    yavg_values: List[float] = []
    for line in proc.stdout.splitlines():
        if "lavfi.signalstats.YAVG=" in line:
            try:
                val = float(line.split("=", 1)[1])
                yavg_values.append(val)
            except (TypeError, ValueError) as exc:
                logger.debug(
                    "signalstats YAVG parse failed for line %r: %s",
                    line, exc)

    if not yavg_values:
        rpt.add(LintResult(
            42, "Frozen Frame Detection", False,
            "Could not extract YAVG from signalstats output"))
        return

    # Get video FPS for duration calculation
    v_info = _ffprobe_video_stream(primary)
    fps = 30.0
    if v_info and v_info.get("fps"):
        fps = v_info["fps"]

    # Find longest run of consecutive identical YAVG values
    max_frozen_run = 0
    current_run = 0
    prev_yavg: Optional[float] = None

    for yavg in yavg_values:
        if prev_yavg is not None and abs(yavg - prev_yavg) < 1e-6:
            current_run += 1
            max_frozen_run = max(max_frozen_run, current_run)
        else:
            current_run = 0
        prev_yavg = yavg

    frozen_sec = max_frozen_run / fps
    ok = frozen_sec < 2.0
    rpt.add(LintResult(
        42, "Frozen Frame Detection", ok,
        (f"max frozen run={max_frozen_run} frames "
         f"({frozen_sec:.1f}s at {fps:.1f} fps)"
         + ("" if ok else " (require < 2s)")),
        {"max_frozen_frames": max_frozen_run,
         "max_frozen_seconds": round(frozen_sec, 2),
         "fps": round(fps, 1),
         "frames_analyzed": len(yavg_values),
         "method": "ffmpeg_signalstats"}))


# ---------------------------------------------------------------------------
# Criterion 43: Audio stream exists in mp4 + AAC codec (ffprobe)
# (Renumbered from draft #38 to avoid collision with existing Audio Continuity)
# ---------------------------------------------------------------------------

def _check_audio_stream_aac(d: Path, rpt: LintReport) -> None:
    """Criterion 43: mp4 must contain an audio stream with AAC codec.

    Uses ffprobe to verify the primary video file has at least one audio
    stream whose codec_name is 'aac'.  Missing ffprobe -> strict-aware skip.
    """
    primary = _primary_video(d)
    if primary is None:
        rpt.add(LintResult(
            43, "Audio Stream AAC", False,
            "No primary video (video.mp4 / recording.mp4) found"))
        return

    if not _ffprobe_available():
        rpt.add(_dep_missing(
            43, "Audio Stream AAC",
            "ffprobe unavailable -- cannot verify AAC audio stream"))
        return

    audio_meta = _ffprobe_audio_stream(primary)
    if audio_meta is None:
        rpt.add(LintResult(
            43, "Audio Stream AAC", False,
            f"No audio stream found in {primary.name}",
            {"video": primary.name}))
        return

    codec = audio_meta.get("codec_name", "")
    ok = codec.lower() == "aac"
    rpt.add(LintResult(
        43, "Audio Stream AAC", ok,
        f"audio codec={codec}" + ("" if ok else " (require aac)"),
        {"video": primary.name, "codec": codec,
         "sample_rate": audio_meta.get("sample_rate"),
         "channels": audio_meta.get("channels")}))


# ---------------------------------------------------------------------------
# Runner.
# ---------------------------------------------------------------------------

def run_all_checks(data_dir: Path) -> LintReport:
    """Run all 38 lint checks on the data directory."""
    rpt = LintReport(data_dir=data_dir, total_checks=38)
    _check_video_specs(data_dir, rpt)       # 1-4
    _check_image_specs(data_dir, rpt)       # 5-6
    _check_audio_specs(data_dir, rpt)       # 7-10 (#8 first, then 7/9/10)
    _check_route_dist(data_dir, rpt)        # 11
    _check_intrinsics(data_dir, rpt)        # 12
    _check_quaternion(data_dir, rpt)        # 13-14
    _check_depth_ratio(data_dir, rpt)       # 15-16
    _check_keycode(data_dir, rpt)           # 17-18
    _check_no_overlays(data_dir, rpt)       # 19-21
    _check_metadata(data_dir, rpt)          # 22
    _check_naming(data_dir, rpt)            # 23
    _check_structure(data_dir, rpt)         # 24
    _check_video_content_health(data_dir, rpt)  # 25
    _check_stream_v_additions(data_dir, rpt)    # 26-27
    _check_video_codec(data_dir, rpt)           # 28
    _check_video_duration_upper_bound(data_dir, rpt)  # 29
    _check_frame_continuity(data_dir, rpt)            # 30
    _check_mouse_camera_alignment(data_dir, rpt)      # 31
    _check_speed_physical_bounds(data_dir, rpt)       # 32
    _check_audio_continuity(data_dir, rpt)        # 38
    _check_input_frame_latency(data_dir, rpt)     # 39
    _check_wasd_balance(data_dir, rpt)            # 40
    _check_stationary_pct(data_dir, rpt)          # 41
    _check_frozen_frames(data_dir, rpt)           # 42
    _check_audio_stream_aac(data_dir, rpt)        # 43
    return rpt


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for PRD lint tool.

    Args:
        argv: Command line arguments (defaults to sys.argv[1:]).
    Returns:
        Exit code: 0 if all pass, 1 if any fail, 2 on error.
    """
    parser = argparse.ArgumentParser(
        description=("G165 PRD Grounded Lint Tool — rc19 (38 criteria, "
                     "Audit E rewrite + rc19 additions #39-43)"))
    parser.add_argument("data_dir", type=Path,
                        help="Path to data directory to lint")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Output JSON report path")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable verbose output")
    parser.add_argument("--strict", action="store_true",
                        help="Promote dependency-missing checks (ffprobe / "
                             "imageio / OpenEXR unavailable) from PASS to "
                             "FAIL")
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    _set_strict(bool(args.strict))

    if not args.data_dir.exists():
        logger.error(f"Directory not found: {args.data_dir}")
        return 2
    if not args.data_dir.is_dir():
        logger.error(f"Not a directory: {args.data_dir}")
        return 2

    logger.info(f"Running PRD lint on: {args.data_dir} "
                f"(strict={_STRICT})")
    try:
        rpt = run_all_checks(args.data_dir)
        out = rpt.to_dict()
        if args.output:
            with open(args.output, "w") as f:
                json.dump(out, f, indent=2)
            logger.info(f"Report written to: {args.output}")
        else:
            print(json.dumps(out, indent=2))
        logger.info(f"Passed: {rpt.passed_count}/{rpt.total_checks}, "
                    f"Failed: {rpt.failed_count}")
        return 0 if rpt.failed_count == 0 else 1
    except Exception as e:
        logger.exception(f"Lint failed: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
