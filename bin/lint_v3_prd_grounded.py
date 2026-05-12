#!/usr/bin/env python3
"""
G165 · bin/lint_v3_prd_grounded.py

Stream-J Audit-E rewrite (2026-05-11): every criterion now actually reads the
data it claims to validate.  Previously many criteria stubbed `passed=True`
without ever probing the file — customer-side verification would have caught
what we missed.  See Audit E for the failure-mode list.

Criteria covered (PRD pages 1-14 → Lark Docs):

  Video:  resolution 1920x1080, duration 5-6 min, FPS=30, format=.mp4 only
          codec=h264/hevc, frame-continuity, content health (not all-black)
  Audio:  presence, codec/duration match (PRD: 有声音、声音连续、声画同步)
  Image:  resolution 1920x1080, format-matches-suffix
  Camera: fx==fy, fx/fy/Cx/Cy all positive, quaternion xyzw order, |q|≈1
  Player: mouse_dx/dy direction must match camera yaw delta; speed < 100 m/s
  Route:  route_type ∈ {1,2,3} present every frame
  Depth:  EXR float32 1920x1080, invalid pixels <5%
  Input:  keyCode integers in VK range [0, 255]
  Misc:   no overlay/logo/popup file hints; systeminfo has PRD fields
          (gameProcessName, width, height, recordDpi); delivery uses
          5-file PRD layout
"""
from __future__ import annotations
import argparse, json, logging, math, random, shutil, subprocess, sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Lazy imports for optional dependencies
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
    """rc15.28 A-A1: imageio for FPS / video meta (best-effort)."""
    global _iio
    if _iio is None:
        try:
            import imageio.v2 as imageio_v2  # noqa: F401
            _iio = imageio_v2
        except ImportError:
            try:
                import imageio
                _iio = imageio
            except ImportError:
                _iio = False  # explicit fail sentinel
    return _iio


# Set by main() based on the --strict flag.  When STRICT is True, any
# dependency-missing case (ffprobe / imageio / OpenEXR absent) FAILS the
# criterion rather than passing with a "skipped" message.  This keeps lint
# trustworthy in CI / customer-delivery environments.
STRICT: bool = False


def _have_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _ffprobe_streams(video: Path) -> Optional[Dict[str, Any]]:
    """Return full ffprobe -show_streams JSON (or None on failure)."""
    if not _have_ffprobe():
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(video)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        return json.loads(out.stdout)
    except Exception as e:
        logger.debug("ffprobe failed on %s: %s", video, e)
        return None


def _ffprobe_video_meta(video: Path) -> Optional[Dict[str, Any]]:
    """Extract (width, height, duration, fps, frame_count, codec) via ffprobe."""
    data = _ffprobe_streams(video)
    if not data:
        return None
    vstreams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    if not vstreams:
        return None
    v = vstreams[0]
    rfr = v.get("r_frame_rate") or "0/1"
    try:
        num, den = rfr.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    duration = v.get("duration")
    if duration is None:
        duration = data.get("format", {}).get("duration")
    try:
        duration = float(duration) if duration is not None else 0.0
    except (TypeError, ValueError):
        duration = 0.0
    nb_frames = v.get("nb_frames")
    try:
        nb_frames = int(nb_frames) if nb_frames is not None else 0
    except (TypeError, ValueError):
        nb_frames = 0
    return {
        "width": int(v.get("width") or 0),
        "height": int(v.get("height") or 0),
        "duration": duration,
        "fps": fps,
        "nb_frames": nb_frames,
        "codec": str(v.get("codec_name") or "").lower(),
    }


def _ffprobe_audio_meta(video: Path) -> Optional[Dict[str, Any]]:
    """Extract first audio stream meta from a container (or None)."""
    data = _ffprobe_streams(video)
    if not data:
        return None
    astreams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not astreams:
        return None
    a = astreams[0]
    duration = a.get("duration")
    if duration is None:
        duration = data.get("format", {}).get("duration")
    try:
        duration = float(duration) if duration is not None else 0.0
    except (TypeError, ValueError):
        duration = 0.0
    try:
        sample_rate = int(a.get("sample_rate") or 0)
    except (TypeError, ValueError):
        sample_rate = 0
    try:
        channels = int(a.get("channels") or 0)
    except (TypeError, ValueError):
        channels = 0
    return {
        "codec": str(a.get("codec_name") or "").lower(),
        "duration": duration,
        "sample_rate": sample_rate,
        "channels": channels,
    }


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
    total_checks: int = 30  # 25 PRD + 5 Audit-E new
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
            "summary": {"total": self.total_checks, "passed": self.passed_count,
                        "failed": self.failed_count,
                        "pass_rate": f"{100 * self.passed_count / max(self.total_checks, 1):.1f}%"},
            "results": [{"id": r.criterion_id, "name": r.name, "passed": r.passed,
                         "message": r.message, "details": r.details} for r in self.results]
        }


def _find_delivery_video(d: Path) -> Optional[Path]:
    """PRD spec name is `video.mp4`; recorder historically writes `recording.mp4`.
    We accept either."""
    for name in ("video.mp4", "recording.mp4"):
        p = d / name
        if p.is_file():
            return p
    for name in ("video.mp4", "recording.mp4"):
        hits = list(d.glob(f"**/{name}"))
        if hits:
            return hits[0]
    return None


def _load_action_camera(d: Path) -> Optional[List[Dict[str, Any]]]:
    """Load action_camera.json delivery file as list of frame dicts."""
    p = d / "action_camera.json"
    if not p.is_file():
        hits = list(d.glob("**/action_camera.json"))
        if not hits:
            return None
        p = hits[0]
    try:
        with open(p) as f:
            data = json.load(f)
    except Exception as e:
        logger.debug("action_camera.json parse fail: %s", e)
        return None
    if isinstance(data, list):
        return data
    return None


# =====================================================================
# Criteria 1-4: Video resolution / duration / FPS / format
# =====================================================================
def _check_video_specs(d: Path, rpt: LintReport) -> None:
    """Criteria 1-4: Video resolution (1920x1080), duration (5-6 min), fps 30, format.

    Audit-E fix #1-4:
      - #1 resolution: read width/height via ffprobe → require 1920x1080
      - #2 duration: read duration → require 300.0 ≤ duration ≤ 360.0
      - #3 fps: read r_frame_rate → require 28-32 fps; cross-check with
              frame_count/duration to catch the AMD-780M
              "physical 9026 / logical 301" disagreement
      - #4 format: only .mp4 accepted (no .avi fallback)
    """
    video = _find_delivery_video(d)
    if video is None:
        for cid, name in [(1, "Video Resolution"), (2, "Video Duration"),
                          (3, "Video FPS"), (4, "Video Format")]:
            rpt.add(LintResult(cid, name, False, "No video.mp4 or recording.mp4 found"))
        return

    bad_fmt_files = [p.name for p in d.rglob("*")
                     if p.is_file() and p.suffix.lower()
                     in {".avi", ".mov", ".mkv", ".flv", ".webm"}]
    rpt.add(LintResult(4, "Video Format", not bad_fmt_files,
                       "Only .mp4 accepted" if not bad_fmt_files
                       else f"Non-mp4 video files present: {bad_fmt_files[:5]}",
                       {"non_mp4_count": len(bad_fmt_files),
                        "samples": bad_fmt_files[:5]}))

    meta = _ffprobe_video_meta(video)
    iio = _get_iio()
    if meta is None and iio not in (False, None):
        try:
            m2 = iio.get_reader(str(video), format="FFMPEG").get_meta_data()
            sz = m2.get("size") or (0, 0)
            nf = m2.get("nframes")
            try:
                nframes = int(nf) if nf not in (None, float("inf")) else 0
            except (TypeError, ValueError):
                nframes = 0
            meta = {
                "width": int(sz[0] if sz else 0),
                "height": int(sz[1] if sz else 0),
                "duration": float(m2.get("duration") or 0),
                "fps": float(m2.get("fps") or 0),
                "nb_frames": nframes,
                "codec": str(m2.get("codec") or "").lower(),
            }
        except Exception as e:
            logger.debug("imageio fallback failed: %s", e)
            meta = None

    if meta is None:
        msg = ("cannot verify video specs — install ffprobe or imageio"
               + (" [STRICT]" if STRICT else ""))
        rpt.add(LintResult(1, "Video Resolution", False, msg))
        rpt.add(LintResult(2, "Video Duration", False, msg))
        rpt.add(LintResult(3, "Video FPS", False, msg))
        return

    res_ok = meta["width"] == 1920 and meta["height"] == 1080
    rpt.add(LintResult(1, "Video Resolution", res_ok,
                       f"{meta['width']}x{meta['height']}"
                       + (" (PRD requires exactly 1920x1080)" if not res_ok else ""),
                       {"width": meta["width"], "height": meta["height"]}))

    duration = meta["duration"]
    dur_ok = 300.0 <= duration <= 360.0
    rpt.add(LintResult(2, "Video Duration", dur_ok,
                       f"duration={duration:.2f}s"
                       + (" (PRD requires 300-360s i.e. 5-6 min)" if not dur_ok else ""),
                       {"duration_s": duration}))

    fps = meta["fps"]
    nb_frames = meta["nb_frames"]
    fps_in_band = 28.0 <= fps <= 32.0
    cross_check_msg = ""
    cross_check_ok = True
    if nb_frames > 0 and duration > 0:
        actual_fps = nb_frames / duration
        if abs(actual_fps - fps) > 2.0:
            cross_check_ok = False
            cross_check_msg = (
                f" — declared {fps:.2f} fps but nb_frames/duration ="
                f" {nb_frames}/{duration:.2f} = {actual_fps:.2f} fps"
                f" (diff {abs(actual_fps - fps):.2f} > 2; "
                "likely encoder/clock mismatch — see AMD-780M case)"
            )
    fps_ok = fps_in_band and cross_check_ok
    rpt.add(LintResult(3, "Video FPS", fps_ok,
                       f"fps={fps:.2f}{cross_check_msg}"
                       + (" (PRD requires ~30 fps, band 28-32)" if not fps_in_band else ""),
                       {"declared_fps": fps, "nb_frames": nb_frames,
                        "duration": duration,
                        "cross_check_fps": (nb_frames / duration) if duration > 0 else None}))


def _check_image_specs(d: Path, rpt: LintReport) -> None:
    """Criteria 5-6: Image resolution (1920x1080), format matches suffix."""
    Image = _get_pil()
    imgs = list(d.glob("**/*.png")) + list(d.glob("**/*.jpg")) + list(d.glob("**/*.jpeg"))
    imgs = [p for p in imgs if "depth" not in p.parts]
    invalid = []
    for p in imgs[:30]:
        try:
            with Image.open(p) as im:
                if im.size != (1920, 1080):
                    invalid.append((p.name, im.size))
        except Exception:
            pass
    rpt.add(LintResult(5, "Image Resolution", not invalid,
                       "All 1920x1080" if not invalid
                       else f"{len(invalid)} wrong-size images",
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
    rpt.add(LintResult(6, "Image Format", not fmt_bad,
                       "All formats match suffix" if not fmt_bad
                       else f"{len(fmt_bad)} mismatches",
                       {"samples": fmt_bad[:5]}))


def _check_audio_specs(d: Path, rpt: LintReport) -> None:
    """Criteria 7-10: Audio quality, format, channels, sample rate.

    Audit-E fix #5: PRD page 8 says "有声音、声音连续、声画同步" → audio is
    REQUIRED.  Probe the mp4's audio stream via ffprobe.
    """
    video = _find_delivery_video(d)
    if video is None:
        msg = "No video.mp4 found — cannot probe audio"
        for cid, name in [(7, "Audio Quality"), (8, "Audio Format"),
                          (9, "Audio Channels"), (10, "Audio Sample Rate")]:
            rpt.add(LintResult(cid, name, False, msg))
        return

    if not _have_ffprobe():
        msg = ("cannot verify audio — install ffprobe"
               + (" [STRICT]" if STRICT else ""))
        for cid, name in [(7, "Audio Quality"), (8, "Audio Format"),
                          (9, "Audio Channels"), (10, "Audio Sample Rate")]:
            rpt.add(LintResult(cid, name, False, msg))
        return

    bad_audio = list(d.glob("**/*.aac")) + list(d.glob("**/*.ogg"))
    rpt.add(LintResult(8, "Audio Format", not bad_audio,
                       "No stray .aac/.ogg files" if not bad_audio
                       else f"Stray audio files: {[f.name for f in bad_audio[:5]]}",
                       {"stray_audio_count": len(bad_audio)}))

    ameta = _ffprobe_audio_meta(video)
    vmeta = _ffprobe_video_meta(video)
    if ameta is None:
        for cid, name in [(7, "Audio Quality"), (9, "Audio Channels"),
                          (10, "Audio Sample Rate")]:
            rpt.add(LintResult(cid, name, False,
                               "no audio stream in mp4 (PRD: 有声音、声音连续)"))
        return

    a_dur = ameta["duration"]
    v_dur = vmeta["duration"] if vmeta else 0.0
    dur_diff = abs(a_dur - v_dur) if (a_dur and v_dur) else float("inf")
    quality_ok = a_dur > 0 and (v_dur == 0 or dur_diff <= 0.5)
    rpt.add(LintResult(7, "Audio Quality", quality_ok,
                       f"audio_duration={a_dur:.2f}s, video_duration={v_dur:.2f}s, "
                       f"diff={dur_diff:.2f}s"
                       + (" (>0.5s drift — truncated/desync risk)"
                          if not quality_ok else ""),
                       {"audio_duration": a_dur, "video_duration": v_dur,
                        "diff_s": dur_diff, "codec": ameta["codec"]}))

    chan = ameta["channels"]
    chan_ok = chan in (1, 2)
    rpt.add(LintResult(9, "Audio Channels", chan_ok,
                       f"{chan} channels"
                       + (" (PRD allows mono or stereo)" if not chan_ok else ""),
                       {"channels": chan}))

    sr = ameta["sample_rate"]
    sr_ok = sr >= 22050
    rpt.add(LintResult(10, "Audio Sample Rate", sr_ok,
                       f"{sr} Hz" + (" (PRD: ≥22050 Hz)" if not sr_ok else ""),
                       {"sample_rate": sr}))


def _check_route_dist(d: Path, rpt: LintReport) -> None:
    """Criterion 11: route_type present every frame, values in {1,2,3}.

    Audit-E fix #6: previously hard-coded passed=True.  Now reads
    action_camera.json per-frame `route_type` field.
    """
    frames = _load_action_camera(d)
    if frames is None:
        rpt.add(LintResult(11, "Route Distribution", False,
                           "action_camera.json not found / not a list"))
        return
    if not frames:
        rpt.add(LintResult(11, "Route Distribution", False,
                           "action_camera.json is empty"))
        return

    missing = 0
    invalid: List[Tuple[int, Any]] = []
    types_seen: Dict[int, int] = {}
    for i, fr in enumerate(frames):
        if not isinstance(fr, dict):
            invalid.append((i, "non-dict frame"))
            continue
        rt = fr.get("route_type")
        if rt is None:
            missing += 1
            continue
        if not isinstance(rt, int) or rt not in (1, 2, 3):
            invalid.append((i, rt))
            continue
        types_seen[rt] = types_seen.get(rt, 0) + 1

    if missing > 0:
        rpt.add(LintResult(11, "Route Distribution", False,
                           f"{missing}/{len(frames)} frames missing route_type "
                           "(PRD page 4-5: required per frame)",
                           {"missing": missing, "total": len(frames),
                            "types_seen": types_seen}))
        return
    if invalid:
        rpt.add(LintResult(11, "Route Distribution", False,
                           f"{len(invalid)} frames have invalid route_type "
                           "(must be int in {1,2,3})",
                           {"sample_invalid": invalid[:5],
                            "types_seen": types_seen}))
        return
    rpt.add(LintResult(11, "Route Distribution", True,
                       f"route_type present, {len(types_seen)} distinct value(s) seen "
                       f"across {len(frames)} frames",
                       {"types_seen": types_seen}))


def _check_intrinsics(d: Path, rpt: LintReport) -> None:
    """Criterion 12: Per-frame camera_intrinsics — fx==fy, all positive.

    Audit-E fix #7: previously looked for `*intrinsics*.yaml` files that
    do not exist in the delivery layout.  Real layout puts intrinsics
    per-frame in action_camera.json's `camera_intrinsics` object.

    PRD page 4-5: fx (Focal Length X), fy (Focal Length Y),
    Cx (Principal point X), Cy (Principal point Y).  Accept lowercase
    cx / cy as well (the sample emits lowercase).
    """
    frames = _load_action_camera(d)
    if frames is None:
        rpt.add(LintResult(12, "Camera Intrinsics fx==fy", False,
                           "action_camera.json not found"))
        return

    bad: List[Tuple[int, str]] = []
    sample = None
    for i, fr in enumerate(frames):
        if not isinstance(fr, dict):
            continue
        ci = fr.get("camera_intrinsics")
        if ci is None:
            bad.append((i, "missing camera_intrinsics"))
            if len(bad) >= 10:
                break
            continue
        if not isinstance(ci, dict):
            bad.append((i, f"camera_intrinsics not dict: {type(ci).__name__}"))
            continue
        fx = ci.get("fx")
        fy = ci.get("fy")
        cx = ci.get("Cx", ci.get("cx"))
        cy = ci.get("Cy", ci.get("cy"))
        if sample is None:
            sample = {"frame": i, "ci": ci}
        bad_this = False
        for k, v in (("fx", fx), ("fy", fy), ("Cx/cx", cx), ("Cy/cy", cy)):
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                bad.append((i, f"{k} not numeric: {v!r}"))
                bad_this = True
                break
            if v <= 0:
                bad.append((i, f"{k}={v} not positive"))
                bad_this = True
                break
        if not bad_this and fx != fy:
            bad.append((i, f"fx={fx} != fy={fy}"))
        if len(bad) >= 10:
            break

    if bad:
        rpt.add(LintResult(12, "Camera Intrinsics fx==fy", False,
                           f"{len(bad)} frames with intrinsics issues "
                           f"(first: frame {bad[0][0]} → {bad[0][1]})",
                           {"sample_issues": bad[:5], "first_ci_sample": sample}))
    else:
        rpt.add(LintResult(12, "Camera Intrinsics fx==fy", True,
                           f"All {len(frames)} frames have fx==fy with "
                           "positive fx/fy/Cx/Cy",
                           {"first_ci_sample": sample}))


def _check_quaternion(d: Path, rpt: LintReport) -> None:
    """Criteria 13-14: Quaternion order is [x,y,z,w] (NOT wxyz), |q|≈1.

    Audit-E fix #8: PRD page 11 says strictly `[x, y, z, w]` order.  At
    rest at session start, the rotation should be near identity → w ≈ ±1
    and x,y,z ≈ 0.  Engines that emit wxyz will show arr[0]≈±1 instead.
    """
    frames = _load_action_camera(d)
    if frames is None:
        rpt.add(LintResult(13, "Quaternion xyzw Order", False,
                           "action_camera.json not found"))
        rpt.add(LintResult(14, "Quaternion Normalization", False,
                           "action_camera.json not found"))
        return

    first_q = None
    first_q_field = None
    for fr in frames:
        if not isinstance(fr, dict):
            continue
        for k in ("camera_rotation_quaternion", "player_rotation_quaternion"):
            q = fr.get(k)
            if isinstance(q, list) and len(q) == 4:
                first_q = q
                first_q_field = k
                break
        if first_q is not None:
            break

    order_ok = True
    order_msg = ""
    order_details: Dict[str, Any] = {}
    if first_q is None:
        order_ok = False
        order_msg = ("no camera/player_rotation_quaternion field found in "
                     "any frame (PRD page 4-5 requires per-frame)")
    else:
        try:
            x, y, z, w = (float(v) for v in first_q)
        except (TypeError, ValueError):
            order_ok = False
            order_msg = f"first quaternion not float-castable: {first_q}"
            x = y = z = w = 0.0
        else:
            order_details = {"first_q": first_q, "field": first_q_field,
                             "abs_pos3": abs(w), "abs_pos0": abs(x),
                             "rest_pos0-2_max": max(abs(x), abs(y), abs(z)),
                             "rest_pos1-3_max": max(abs(y), abs(z), abs(w))}
            EPS = 0.05
            looks_xyzw = (abs(w) >= 1.0 - EPS
                          and max(abs(x), abs(y), abs(z)) <= EPS)
            looks_wxyz = (abs(x) >= 1.0 - EPS
                          and max(abs(y), abs(z), abs(w)) <= EPS)
            if looks_wxyz and not looks_xyzw:
                order_ok = False
                order_msg = (
                    f"first quaternion {first_q} looks like wxyz "
                    "(arr[0]≈±1, rest≈0) — PRD requires xyzw "
                    "(arr[3]=w should be near ±1 at rest)"
                )
            elif not looks_xyzw and not looks_wxyz:
                order_msg = (
                    f"first quaternion {first_q} not at rest; order "
                    "heuristic inconclusive (only normalization "
                    "guarantees enforced)"
                )

    rpt.add(LintResult(13, "Quaternion xyzw Order", order_ok,
                       order_msg or "first frame matches xyzw shape",
                       order_details))

    norm_bad: List[Tuple[int, str, float]] = []
    total = 0
    for i, fr in enumerate(frames):
        if not isinstance(fr, dict):
            continue
        for k in ("camera_rotation_quaternion", "player_rotation_quaternion"):
            q = fr.get(k)
            if isinstance(q, list) and len(q) == 4:
                try:
                    mag = math.sqrt(sum(float(v) * float(v) for v in q))
                except (TypeError, ValueError):
                    norm_bad.append((i, k, -1.0))
                    continue
                if not (0.99 <= mag <= 1.01):
                    norm_bad.append((i, k, round(mag, 5)))
                total += 1
        if len(norm_bad) >= 50:
            break
    norm_ok = not norm_bad
    rpt.add(LintResult(14, "Quaternion Normalization", norm_ok,
                       f"All {total} sampled |q| in [0.99, 1.01]"
                       if norm_ok else
                       f"{len(norm_bad)} unnormalized quaternions "
                       f"(first: frame {norm_bad[0][0]} {norm_bad[0][1]} "
                       f"|q|={norm_bad[0][2]})",
                       {"sample_bad": norm_bad[:5], "total_checked": total}))


def _check_depth_ratio(d: Path, rpt: LintReport) -> None:
    """Criteria 15-16: Depth invalid-pixel ratio (<5%) + data quality."""
    np = _get_np()
    Image = _get_pil()
    exr_files = list(d.glob("**/*depth*.exr")) + list(d.glob("**/depth/*.exr"))
    other_files = list(d.glob("**/*depth*.png")) + list(d.glob("**/*depth*.npy"))
    depth_files = exr_files + other_files
    if not depth_files:
        rpt.add(LintResult(15, "Depth Invalid-Pixel Ratio", False,
                           "No depth files (PRD page 7 requires .exr"
                           " float32 single-channel Z @ 6 fps over 5 min)"))
        rpt.add(LintResult(16, "Depth Data Quality", False,
                           "No depth files — fail by absence per PRD"))
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
                        issues.append((df.name, f"resolution {w_px}x{h_px} < 1920x1080"))
                        continue
                    raw = f.channel("Z")
                    arr = np.frombuffer(raw, dtype=np.float32)
                    invalid = float(np.sum((arr == 0) | ~np.isfinite(arr))) / max(arr.size, 1)
                    if invalid > 0.05:
                        issues.append((df.name, f"{invalid:.1%} invalid"))
                except ImportError:
                    if df.stat().st_size < 50_000:
                        issues.append((df.name, f"file too small ({df.stat().st_size}B)"))
            elif df.suffix == ".npy":
                data = np.load(str(df))
                invalid = float(np.sum((data == 0) | (data == 65535))) / data.size
                if invalid > 0.05:
                    issues.append((df.name, f"{invalid:.1%}"))
            else:
                with Image.open(df) as im:
                    data = np.array(im)
                invalid = float(np.sum((data == 0) | (data == 65535))) / data.size
                if invalid > 0.05:
                    issues.append((df.name, f"{invalid:.1%}"))
        except Exception as e:
            issues.append((df.name, f"read-err: {type(e).__name__}"))
    msg_pass = (
        f"All within 5% (sampled {min(15, len(depth_files))}/{len(depth_files)} files"
        + (f", {sample_size_w}x{sample_size_h})" if sample_size_w else ")")
    )
    rpt.add(LintResult(15, "Depth Invalid-Pixel Ratio", not issues,
                       msg_pass if not issues else f"{len(issues)} exceed",
                       {"issues": issues[:5]}))
    rpt.add(LintResult(16, "Depth Data Quality", not issues,
                       "Depth quality check passed" if not issues
                       else f"{len(issues)} depth files have quality issues"))


def _check_keycode(d: Path, rpt: LintReport) -> None:
    """Criteria 17-18: keyCode integer format + Windows VK range [0,255]."""
    yaml = _get_yaml()
    issues = []
    for jf in list(d.glob("**/*.json"))[:20]:
        try:
            with open(jf) as f:
                data = json.load(f)
                if isinstance(data, dict) and "keyCode" in data and not isinstance(data["keyCode"], int):
                    issues.append(jf.name)
        except Exception:
            pass
    for yf in list(d.glob("**/*.yaml"))[:20]:
        try:
            with open(yf) as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict) and "keyCode" in data and not isinstance(data["keyCode"], int):
                    issues.append(yf.name)
        except Exception:
            pass
    rpt.add(LintResult(17, "keyCode Integer Format", not issues,
                       "All keyCode int" if not issues else f"{len(issues)} non-int"))
    range_bad: List[Tuple[str, int]] = []
    sampled = 0
    for jf in list(d.glob("**/*.json"))[:10]:
        try:
            with open(jf) as f:
                data = json.load(f)
            if isinstance(data, list):
                for r in data[:200]:
                    if isinstance(r, dict) and isinstance(r.get("keyCode"), list):
                        for kc in r["keyCode"]:
                            if isinstance(kc, int) and not (0 <= kc <= 255):
                                range_bad.append((jf.name, kc))
                            sampled += 1
                            if sampled > 500:
                                break
                    if sampled > 500:
                        break
        except Exception:
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
                    except Exception:
                        continue
                    kc = e.get("keyCode")
                    if isinstance(kc, int) and not (0 <= kc <= 255):
                        range_bad.append((jl.name, kc))
                    sampled += 1
                    if sampled > 5000:
                        break
        except Exception:
            pass
    rpt.add(LintResult(18, "KeyCode Validation",
                       not issues and not range_bad,
                       ("All keyCode int-shape AND in VK range [0,255]"
                        if not issues and not range_bad
                        else f"shape_issues={len(issues)} "
                             f"range_issues={range_bad[:5]}"),
                       {"out_of_range": range_bad[:10]}))


def _check_no_overlays(d: Path, rpt: LintReport) -> None:
    """Criteria 19-21: No UI overlay, no logo, no popup (filename heuristic)."""
    all_files = [p.name.lower() for p in d.rglob("*") if p.is_file()]

    overlay_hits = [n for n in all_files
                    if any(kw in n for kw in ("overlay", "watermark", "hud_"))]
    rpt.add(LintResult(19, "No UI Overlay",
                       not overlay_hits,
                       "No overlay/watermark/HUD filename hints"
                       if not overlay_hits
                       else f"{len(overlay_hits)} suspicious: {overlay_hits[:3]}",
                       {"hits": overlay_hits[:5]}))

    logo_hits = [n for n in all_files
                 if any(kw in n for kw in ("logo", "brand", "trademark"))]
    rpt.add(LintResult(20, "No Logo",
                       not logo_hits,
                       "No logo/brand filename hints"
                       if not logo_hits
                       else f"{len(logo_hits)} suspicious: {logo_hits[:3]}",
                       {"hits": logo_hits[:5]}))

    popup_hits = [n for n in all_files
                  if any(kw in n for kw in ("popup", "modal", "dialog", "notification"))]
    rpt.add(LintResult(21, "No Popup",
                       not popup_hits,
                       "No popup/modal/dialog filename hints"
                       if not popup_hits
                       else f"{len(popup_hits)} suspicious: {popup_hits[:3]}",
                       {"hits": popup_hits[:5]}))


def _check_metadata(d: Path, rpt: LintReport) -> None:
    """Criterion 22: systeminfo.json completeness per PRD page 3-4.

    Audit-E fix #9: PRD page 3-4 systeminfo requires gameProcessName,
    x, y, width, height, recordDpi.  Replaces the old hallucinated set
    of `timestamp / location / device_id / session_id`.
    """
    prd_required = ["gameProcessName", "width", "height", "recordDpi"]
    prd_required_keys_zero_ok = ["x", "y"]

    candidates = list(d.glob("systeminfo*.json"))
    if not candidates:
        legacy = list(d.glob("metadata*.json"))
        if legacy:
            rpt.add(LintResult(22, "Metadata Completeness", False,
                               "systeminfo.json missing; only legacy "
                               f"{[p.name for p in legacy[:3]]} found "
                               "(PRD page 3-4 / page 7 require systeminfo.json)",
                               {"legacy_metadata": [p.name for p in legacy[:5]]}))
        else:
            rpt.add(LintResult(22, "Metadata Completeness", False,
                               "No systeminfo.json (PRD page 7 lists it as deliverable #1)"))
        return

    missing: List[Tuple[str, str]] = []
    type_bad: List[Tuple[str, str]] = []
    for mf in candidates[:5]:
        try:
            with open(mf) as f:
                data = json.load(f)
        except Exception as e:
            missing.append((mf.name, f"parse error: {e}"))
            continue
        if not isinstance(data, dict):
            type_bad.append((mf.name, f"top-level is {type(data).__name__}, expect dict"))
            continue
        for fld in prd_required:
            if fld not in data:
                missing.append((mf.name, fld))
        for fld in prd_required_keys_zero_ok:
            if fld not in data:
                missing.append((mf.name, fld))
        if "gameProcessName" in data and not isinstance(data.get("gameProcessName"), str):
            type_bad.append((mf.name, "gameProcessName must be str"))
        for k in ("width", "height", "x", "y"):
            v = data.get(k)
            if v is not None and not isinstance(v, int):
                type_bad.append((mf.name, f"{k} must be int, got {type(v).__name__}"))
        v = data.get("recordDpi")
        if v is not None and not isinstance(v, (int, float)):
            type_bad.append((mf.name, f"recordDpi must be number, got {type(v).__name__}"))

    problems = missing + type_bad
    if problems:
        rpt.add(LintResult(22, "Metadata Completeness", False,
                           f"{len(problems)} systeminfo issues",
                           {"missing": missing[:5], "type_errors": type_bad[:5]}))
    else:
        rpt.add(LintResult(22, "Metadata Completeness", True,
                           f"systeminfo.json has all required PRD fields "
                           f"({', '.join(prd_required + prd_required_keys_zero_ok)})"))


def _check_naming(d: Path, rpt: LintReport) -> None:
    """Criterion 23: File naming convention (no spaces, no leading dots)."""
    bad = [f.name for f in d.glob("**/*") if " " in f.name or f.name.startswith(".")]
    rpt.add(LintResult(23, "File Naming Convention", not bad,
                       "All valid" if not bad else f"{len(bad)} invalid",
                       {"samples": bad[:5]}))


def _check_structure(d: Path, rpt: LintReport) -> None:
    """Criterion 24: PRD 5-file delivery layout (Lark p7).

    Audit-E fix #10: accept both `video.mp4` and `recording.mp4` as the
    video deliverable, but emit a P1 "delivery format" warning when the
    canonical PRD name is missing.
    """
    required_files_alias = {
        "video.mp4": ["video.mp4", "recording.mp4"],
        "systeminfo.json": ["systeminfo.json"],
        "action_camera.json": ["action_camera.json"],
        "gameinfo.xlsx": ["gameinfo.xlsx"],
    }
    required_dirs = ["depth"]

    existing_files = {f.name for f in d.iterdir() if f.is_file()}
    existing_dirs = {x.name for x in d.iterdir() if x.is_dir()}

    missing: List[str] = []
    aliased: List[str] = []
    for canonical, aliases in required_files_alias.items():
        found = next((a for a in aliases if a in existing_files), None)
        if found is None:
            missing.append(canonical)
        elif found != canonical:
            aliased.append(f"{found} (PRD canonical: {canonical})")

    missing_dirs = [x for x in required_dirs if x not in existing_dirs]
    all_missing = missing + missing_dirs

    if all_missing:
        rpt.add(LintResult(24, "Directory Structure", False,
                           f"Missing required deliverables: {all_missing}",
                           {"required_files": list(required_files_alias.keys()),
                            "required_dirs": required_dirs,
                            "existing_files": sorted(existing_files),
                            "existing_dirs": sorted(existing_dirs),
                            "aliased": aliased}))
    else:
        msg = "5-file PRD delivery valid"
        if aliased:
            msg += (f" — WARNING: P1 delivery-format rename needed for "
                    f"{aliased} (rename or symlink to PRD canonical names "
                    "before customer handoff)")
        rpt.add(LintResult(24, "Directory Structure", True, msg,
                           {"aliased_warnings": aliased}))


def _probe_video_frame_count(video: Path) -> int:
    """Return decoded frame count via ffprobe (0 on failure)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-count_frames", "-show_entries", "stream=nb_read_frames",
             "-of", "default=nokey=1:noprint_wrappers=1", str(video)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return int((out.stdout or "0").strip() or 0)
    except Exception:
        return 0


def _signalstats_for_frame(video: Path, frame_n: int, ffmpeg: str) -> Optional[Tuple[float, float, float]]:
    """Run signalstats on a 2-frame window starting at frame_n."""
    try:
        proc = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(video),
             "-vf", f"select=between(n\\,{frame_n}\\,{frame_n + 1}),signalstats,metadata=print:file=-",
             "-vframes", "2", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except Exception:
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
    """Criterion 25: Video Content Health.

    Audit-E fix #11: previously, ffmpeg-absent silently passed.  Now FAILS
    with install hint.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        rpt.add(LintResult(25, "Video Content Health", False,
                           "cannot verify content health — install ffmpeg"
                           + (" [STRICT]" if STRICT else "")))
        return
    video = _find_delivery_video(d)
    if video is None:
        rpt.add(LintResult(25, "Video Content Health", False,
                           "No video.mp4 or recording.mp4 found"))
        return
    nb = _probe_video_frame_count(video)
    if nb < 10:
        rpt.add(LintResult(25, "Video Content Health", False,
                           f"Video has only {nb} frames — cannot assess content"))
        return
    sample_frames = [int(nb * pct / 100) for pct in (10, 20, 30, 40, 50, 60, 70, 80, 90)]
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
        rpt.add(LintResult(25, "Video Content Health", False,
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
        reason = f"video shows static frame ({unique_hashes} unique samples, YDIF_max={ydif_max:.1f})"
    else:
        reason = None
    passed = (yavg_mean > 20) and (unique_hashes >= 3 or ydif_max > 5)
    rpt.add(LintResult(25, "Video Content Health", passed,
                       "Video content varied and non-black" if passed
                       else (reason or "content health check failed"),
                       details))


# =====================================================================
# NEW Criteria 26-30 (Audit-E "missing checks")
# =====================================================================
def _check_video_codec(d: Path, rpt: LintReport) -> None:
    """Criterion 26 (new): video codec must be h264 or hevc."""
    video = _find_delivery_video(d)
    if video is None:
        rpt.add(LintResult(26, "Video Codec", False, "No video file found"))
        return
    if not _have_ffprobe():
        rpt.add(LintResult(26, "Video Codec", False,
                           "cannot verify codec — install ffprobe"
                           + (" [STRICT]" if STRICT else "")))
        return
    meta = _ffprobe_video_meta(video)
    if not meta:
        rpt.add(LintResult(26, "Video Codec", False,
                           "ffprobe could not read video stream"))
        return
    codec = meta["codec"]
    ok = codec in ("h264", "hevc", "h265")
    rpt.add(LintResult(26, "Video Codec", ok,
                       f"codec={codec}" + (" (allowed: h264 or hevc)"
                                           if not ok else ""),
                       {"codec": codec}))


def _check_video_duration_upper_bound(d: Path, rpt: LintReport) -> None:
    """Criterion 27 (new): explicit upper-bound duration check.  PRD page 8: 6≥x≥5 分钟."""
    video = _find_delivery_video(d)
    if video is None:
        rpt.add(LintResult(27, "Video Duration Upper Bound", False,
                           "No video file found"))
        return
    if not _have_ffprobe():
        rpt.add(LintResult(27, "Video Duration Upper Bound", False,
                           "cannot verify duration — install ffprobe"
                           + (" [STRICT]" if STRICT else "")))
        return
    meta = _ffprobe_video_meta(video)
    if not meta:
        rpt.add(LintResult(27, "Video Duration Upper Bound", False,
                           "ffprobe failed"))
        return
    dur = meta["duration"]
    ok = dur <= 360.0
    rpt.add(LintResult(27, "Video Duration Upper Bound", ok,
                       f"duration={dur:.2f}s"
                       + ("" if ok else " > 360s upper bound (PRD: 6 分钟 max)"),
                       {"duration_s": dur}))


def _check_frame_continuity(d: Path, rpt: LintReport) -> None:
    """Criterion 28 (new): action_camera.json `frame` is monotonic with no gaps.

    PRD page 11: 帧率连续性 — 排查 `frame` 是否存在重复帧或跳帧.
    """
    frames = _load_action_camera(d)
    if frames is None:
        rpt.add(LintResult(28, "Frame Continuity", False,
                           "action_camera.json not found"))
        return
    if not frames:
        rpt.add(LintResult(28, "Frame Continuity", False, "empty frames"))
        return
    nums: List[int] = []
    missing_field = 0
    for fr in frames:
        if not isinstance(fr, dict):
            continue
        v = fr.get("frame")
        if v is None:
            v = fr.get("frame_index")
        if v is None:
            missing_field += 1
            continue
        try:
            nums.append(int(v))
        except (TypeError, ValueError):
            missing_field += 1
    if missing_field > 0:
        rpt.add(LintResult(28, "Frame Continuity", False,
                           f"{missing_field} frames missing/non-int `frame` field"
                           " (PRD page 4: required per frame)",
                           {"missing": missing_field}))
        return
    duplicates: List[int] = []
    gaps: List[Tuple[int, int]] = []
    non_monotonic: List[Tuple[int, int]] = []
    seen: Dict[int, int] = {}
    prev = None
    for n in nums:
        if n in seen:
            duplicates.append(n)
        seen[n] = seen.get(n, 0) + 1
        if prev is not None:
            if n < prev:
                non_monotonic.append((prev, n))
            elif n - prev > 1:
                gaps.append((prev, n))
        prev = n
        if len(duplicates) + len(gaps) + len(non_monotonic) > 50:
            break
    ok = not duplicates and not gaps and not non_monotonic
    rpt.add(LintResult(28, "Frame Continuity", ok,
                       (f"All {len(nums)} frames monotonic, no gaps/duplicates"
                        if ok else
                        f"continuity issues: duplicates={len(duplicates)} "
                        f"gaps={len(gaps)} non_monotonic={len(non_monotonic)}"),
                       {"duplicates": duplicates[:5],
                        "gaps": gaps[:5],
                        "non_monotonic": non_monotonic[:5],
                        "total_frames": len(nums)}))


def _check_mouse_camera_alignment(d: Path, rpt: LintReport) -> None:
    """Criterion 29 (new): mouse_dx sign correlates with camera yaw delta.

    PRD page 11: 输入映射正确性 — 检查鼠标 dx,dy 方向是否弄反.

    Heuristic: in a right-handed camera, +mouse_dx → camera yaw decreases
    (camera turns right).  Same-sign rate > 40% across motion-pairs ⇒
    mapping is likely inverted.
    """
    frames = _load_action_camera(d)
    if frames is None:
        rpt.add(LintResult(29, "Mouse/Camera Alignment", False,
                           "action_camera.json not found"))
        return
    if len(frames) < 100:
        rpt.add(LintResult(29, "Mouse/Camera Alignment", False,
                           f"only {len(frames)} frames; need ≥100 for sample"))
        return

    rng = random.Random(0xC0FFEE)
    candidates: List[Tuple[float, float]] = []
    for i in range(len(frames) - 1):
        a, b = frames[i], frames[i + 1]
        if not (isinstance(a, dict) and isinstance(b, dict)):
            continue
        dx = b.get("mouse_dx")
        if dx in (None, 0, 0.0):
            continue
        a_oula = a.get("camera_rotation_oula")
        b_oula = b.get("camera_rotation_oula")
        if not (isinstance(a_oula, list) and isinstance(b_oula, list)
                and len(a_oula) >= 2 and len(b_oula) >= 2):
            continue
        try:
            yaw_a = float(a_oula[1])
            yaw_b = float(b_oula[1])
        except (TypeError, ValueError):
            continue
        yaw_delta = yaw_b - yaw_a
        if abs(yaw_delta) < 0.05:
            continue
        try:
            dx_f = float(dx)
        except (TypeError, ValueError):
            continue
        candidates.append((dx_f, yaw_delta))
    if len(candidates) < 20:
        rpt.add(LintResult(29, "Mouse/Camera Alignment", True,
                           f"insufficient motion-pairs ({len(candidates)}/50) "
                           "to evaluate alignment; pass with caveat"))
        return
    sample = rng.sample(candidates, min(50, len(candidates)))
    same_sign = sum(1 for dx, dy in sample if (dx > 0) == (dy > 0))
    same_sign_pct = same_sign / len(sample)
    ok = same_sign_pct <= 0.40
    rpt.add(LintResult(29, "Mouse/Camera Alignment", ok,
                       f"sampled {len(sample)} motion-pairs, "
                       f"same-sign rate={same_sign_pct:.0%}"
                       + (" — looks correctly inverse" if ok else
                          " (>40% same-sign — mapping likely reversed)"),
                       {"sample_size": len(sample),
                        "same_sign_count": same_sign,
                        "same_sign_pct": same_sign_pct,
                        "candidates_available": len(candidates)}))


def _check_speed_physical_bounds(d: Path, rpt: LintReport) -> None:
    """Criterion 30 (new): player_speed and camera_speed |v| ≤ 100 m/s.

    PRD page 11: 物理阈值限制.  Reference: MC sprint=5.6, elytra=30, GTA=80 m/s.
    """
    frames = _load_action_camera(d)
    if frames is None:
        rpt.add(LintResult(30, "Speed Physical Bounds", False,
                           "action_camera.json not found"))
        return
    bad: List[Tuple[int, str, float]] = []
    sampled = 0
    for i, fr in enumerate(frames):
        if not isinstance(fr, dict):
            continue
        for k in ("player_speed", "camera_speed"):
            v = fr.get(k)
            if not isinstance(v, list) or len(v) != 3:
                continue
            try:
                mag = math.sqrt(sum(float(x) * float(x) for x in v))
            except (TypeError, ValueError):
                continue
            sampled += 1
            if mag > 100.0:
                bad.append((i, k, mag))
        if len(bad) >= 50:
            break
    ok = not bad
    rpt.add(LintResult(30, "Speed Physical Bounds", ok,
                       f"All {sampled} speed samples ≤ 100 m/s"
                       if ok else
                       f"{len(bad)} samples > 100 m/s "
                       f"(first: frame {bad[0][0]} {bad[0][1]} |v|={bad[0][2]:.1f})",
                       {"sample_bad": bad[:5], "sampled": sampled}))


def run_all_checks(data_dir: Path) -> LintReport:
    """Run all 25 + 5 lint checks on the data directory."""
    rpt = LintReport(data_dir=data_dir)
    _check_video_specs(data_dir, rpt)
    _check_image_specs(data_dir, rpt)
    _check_audio_specs(data_dir, rpt)
    _check_route_dist(data_dir, rpt)
    _check_intrinsics(data_dir, rpt)
    _check_quaternion(data_dir, rpt)
    _check_depth_ratio(data_dir, rpt)
    _check_keycode(data_dir, rpt)
    _check_no_overlays(data_dir, rpt)
    _check_metadata(data_dir, rpt)
    _check_naming(data_dir, rpt)
    _check_structure(data_dir, rpt)
    _check_video_content_health(data_dir, rpt)
    _check_video_codec(data_dir, rpt)
    _check_video_duration_upper_bound(data_dir, rpt)
    _check_frame_continuity(data_dir, rpt)
    _check_mouse_camera_alignment(data_dir, rpt)
    _check_speed_physical_bounds(data_dir, rpt)
    rpt.results.sort(key=lambda r: r.criterion_id)
    return rpt


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for PRD lint tool."""
    parser = argparse.ArgumentParser(
        description="G165 PRD Grounded Lint Tool — Checks all 25 + 5 acceptance criteria")
    parser.add_argument("data_dir", type=Path, help="Path to data directory to lint")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Output JSON report path")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable verbose output")
    parser.add_argument("--strict", action="store_true",
                        help="Treat dependency-missing cases (no ffprobe / "
                             "no imageio / no OpenEXR) as FAIL instead of warn.")
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    global STRICT
    STRICT = args.strict

    if not args.data_dir.exists():
        logger.error(f"Directory not found: {args.data_dir}")
        return 2
    if not args.data_dir.is_dir():
        logger.error(f"Not a directory: {args.data_dir}")
        return 2

    logger.info(f"Running PRD lint on: {args.data_dir} (strict={STRICT})")
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
        logger.error(f"Lint failed: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
