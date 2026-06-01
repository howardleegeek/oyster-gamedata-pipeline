#!/usr/bin/env python3
"""
score_session.py — Phase 0 "local truth loop" scoring orchestrator.

Turns a raw recorded session directory into a PRD-scored, buyer-canonical
session by running the EXISTING stage scripts in the correct, load-bearing
order. This file is pure orchestration / wiring — it does NOT reimplement any
stage, and it never fabricates a score.

Pipeline (THIS exact order — ordering is load-bearing):

  0. fps-rederive   ffprobe the session video for the TRUE fps + frame_count and
                    overwrite metadata.json's fps_effective/average_fps/frame_count.
                    The recorder's reported fps is NEVER trusted. A transparent
                    `_fps_correction` provenance block preserves the recorder's
                    original claim. (Real session: recorder claimed 30fps/9614,
                    the muxed video was actually 24fps/7680.)
                    Must run before anything frame-indexed.

  0b. resolution-rederive   ffprobe the SAME video for the TRUE width x height and
                    overwrite metadata.json's capture_resolution (+ game_resolution
                    if present). EXACT parallel to fps-rederive: the recorder's
                    reported resolution is NEVER trusted, and a transparent
                    `_resolution_correction` provenance block preserves the
                    recorder's original claim. NEVER upscales / fabricates a higher
                    resolution — reports the real (possibly lower) one. (Real
                    session: recorder claimed 1920x1080, the muxed video was
                    actually 960x544.) Must run before any resolution-dependent
                    stage.

  0c. systeminfo-synth   ensure <session_dir>/systeminfo.json carries the 5
                    PRD-required hardware keys (gpu, cpu, ram_gb, os, build).
                    The hardware-provenance twin of fps/resolution rederive, and
                    truthful-only: if the recorder already emitted a complete
                    systeminfo.json (v2.6.14+), it is left UNTOUCHED; if the
                    recorder's metadata/systeminfo.json subdir copy is complete,
                    that is used; otherwise the keys are derived strictly from
                    metadata.json's `hardware_specs` (gpu←gpus[0].name,
                    cpu←cpu.brand, ram_gb←round(system.total_memory_gb,1),
                    os←"{os_name} {os_version}", build←recorder_version). A field
                    with no real source in metadata is written null and logged —
                    hardware that was not recorded is NEVER fabricated. Additive
                    (preserves the recorder's window-geometry keys) and
                    idempotent. Closes prd_test_systeminfo_required, which
                    otherwise FAILs on sessions whose recorder wrote only the
                    window-geometry systeminfo.json.

  1. build_action_camera   bin/build_action_camera.py <session_dir>
                    Writes the 20-field action_camera.json (per-frame
                    route_type via classify_route_type). MUST run before
                    finalize — recorder v2.6.14 no longer emits this file and
                    finalize only backfills an *existing* one.

  2. finalize_session   bin/finalize_session.py <session_dir> --repo-root <repo>
                    Internally: game_state sync + action_camera backfill +
                    gameinfo.xlsx + DA-V2 depth EXR + audio + input latency.
                    `--skip-depth` is passed through (maps to finalize's
                    --no-depth) for fast runs.

  2b. build_action_camera (re-assert)   bin/build_action_camera.py <session_dir>
                    Re-run AFTER finalize. finalize's action_camera steps
                    (backfill + resample_action_camera_to_frames) overwrite the
                    PRD-tuned fields build_action_camera produced — they revert
                    camera_position to ABSOLUTE block coords (fails
                    metric_units_meters' 10 m world-cube check) and resample the
                    real per-action `timestamp` onto the frame grid (breaks
                    action_per_second cadence). Re-running build_action_camera
                    re-asserts the buyer-canonical 20-field file as the final
                    artifact. Deterministic from raw inputs → idempotent and
                    cheap (~0.3 s). This is ORDERING only; no stage is modified.
                    (Empirically: without this, the validated session scores
                    78.6%; with it, ~93%.)

  3. prd_acceptance   bin/prd_acceptance.py <session_dir>
                    Runs the 15 PRD tests + lint, writes
                    PRD-ACCEPTANCE-REPORT.md, returns PASS/FAIL.

  4. clip-summary   writes <session_dir>/clip_summary.json — the per-session
                    aggregation unit the future >=120-clip fleet gate consumes.

INTEGRITY (hard rule): the ONLY path that writes a PASS / a numeric score is a
real prd_acceptance run inside this same invocation, parsed from the report it
just produced and cross-checked against its exit code. Nothing here ever
hardcodes or fabricates a score.

Idempotency: re-running on an already-processed directory is safe. fps-rederive
and resolution-rederive each detect a prior correction (via the `_fps_correction`
/ `_resolution_correction` block) and will not double-correct or lose the
recorder's original claim; systeminfo-synth no-ops once the file is complete;
build_action_camera and finalize are themselves idempotent (deterministic
rebuild / sentinels); PRD is read-only on the session.

Usage:
    score_session.py <session_dir> [--skip-depth] [--report-json PATH]
                     [--repo-root PATH] [--prd-timeout SECONDS] [--verbose]

Exit codes:
    0 - pipeline completed AND the session PASSED PRD acceptance
    1 - pipeline completed but the session did NOT pass (some stage failed,
        or PRD acceptance returned a non-PASS) — session marked NOT-PASSED
    2 - fatal precondition (session dir missing / no video / stage scripts absent)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VIDEO_CANDIDATES = ("video.mp4", "recording.mp4", "game.mp4")
DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent  # oyster-agent-runner/

# fps mismatch beyond this (|recorder - real|) is flagged as an anomaly — this is
# the 30->24 class of bug we want visible, not silent.
FPS_ANOMALY_THRESHOLD = 1.0

# Any resolution mismatch at all (width or height differs) is an anomaly — this is
# the 1920x1080->960x544 class of over-claim we want visible, not silent.
RESOLUTION_ANOMALY_THRESHOLD = 0

# metadata.json resolution fields the recorder may over-claim. capture_resolution
# is always present; game_resolution is corrected only when it already exists
# (we never fabricate a field the recorder didn't emit).
RESOLUTION_FIELDS = ("capture_resolution", "game_resolution")

# prd_acceptance default per-test timeout. The lint step is the slowest; the
# observed real session needed lint headroom (it timed out at 18s under a low
# cap), so we default generously. Overridable via --prd-timeout.
DEFAULT_PRD_TIMEOUT = 120

# systeminfo.json required keys (must mirror prd_test_systeminfo_required.py's
# REQUIRED_KEYS exactly — that PRD test fails closed on any missing key). We do
# NOT import the test module to read these (the test is invoked as a subprocess
# by prd_acceptance and must stay an independent oracle); the list is duplicated
# here intentionally and kept in lock-step by the systeminfo-synth stage's own
# self-test.
SYSTEMINFO_REQUIRED_KEYS = ("gpu", "cpu", "ram_gb", "os", "build")

# Stage names (stable identifiers for the report / logs).
STAGE_FPS = "fps_rederive"
STAGE_RESOLUTION = "resolution_rederive"
STAGE_SYSTEMINFO = "systeminfo_synth"
STAGE_ACTION_CAM = "build_action_camera"
STAGE_FINALIZE = "finalize_session"
STAGE_ACTION_CAM_REASSERT = "build_action_camera_reassert"
STAGE_PRD = "prd_acceptance"
STAGE_CLIP = "clip_summary"

logger = logging.getLogger("score_session")


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class StageResult:
    """Structured outcome of a single orchestrated stage."""
    name: str
    status: str = "pending"        # ok | skipped | failed | error
    exit_code: Optional[int] = None
    duration_seconds: float = 0.0
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    detail: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "skipped")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_seconds": round(self.duration_seconds, 3),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "detail": self.detail,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_video(session_dir: Path) -> Optional[Path]:
    """Return the session's primary video file (first existing candidate)."""
    for name in VIDEO_CANDIDATES:
        p = session_dir / name
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def _parse_fps_fraction(value: Optional[str]) -> Optional[float]:
    """Parse an ffprobe rate string like '24/1' or '30000/1001' to a float."""
    if not value or value in ("0/0", "N/A"):
        return None
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            if den_f == 0:
                return None
            return float(num) / den_f
        return float(value)
    except (ValueError, ZeroDivisionError):
        return None


# Stream line emitted by `ffmpeg -i`, e.g.
#   Stream #0:0[0x1](und): Video: h264 (Main) ..., 960x544, 1161 kb/s, ..., 24 fps, ...
# We pull WxH from the first "<w>x<h>" token and the displayed fps from "<n> fps".
_FFMPEG_RES_RE = re.compile(r"\b(\d{2,5})x(\d{2,5})\b")
_FFMPEG_FPS_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*fps\b")
# Final ffmpeg progress/summary line, e.g.
#   frame= 7825 fps=4655 q=-0.0 Lsize=N/A time=00:05:26.04 bitrate=N/A ...
_FFMPEG_FRAME_RE = re.compile(r"frame=\s*(\d+)")
_FFMPEG_TIME_RE = re.compile(r"\btime=\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def _hmss_to_seconds(h: str, m: str, s: str) -> Optional[float]:
    """Convert an HH:MM:SS(.ss) ffmpeg timestamp into total seconds."""
    try:
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (TypeError, ValueError):
        return None


def _hmss_to_seconds_safe(match: "re.Match[str]") -> Optional[float]:
    """Convert a matched HH:MM:SS(.ss) ffmpeg time group into seconds."""
    return _hmss_to_seconds(match.group(1), match.group(2), match.group(3))


def _ffmpeg_probe_video_line(video: Path) -> dict[str, Any]:
    """Parse `ffmpeg -i <video>` stderr for the video Stream line.

    VFR-robust fallback for resolution + displayed fps when the structured
    ffprobe stream probe returns empty fields. Returns whatever it could read:
      {width, height, fps}  (any value may be None)

    NEVER fabricates: width/height come verbatim from the first "WxH" token on
    the Video stream line, fps from the "<n> fps" token ffmpeg prints — these are
    read off the real stream, never rounded up or padded.
    """
    out: dict[str, Any] = {"width": None, "height": None, "fps": None}
    if not shutil.which("ffmpeg"):
        return out
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video)],
        capture_output=True, text=True, timeout=120,
    )
    # `ffmpeg -i` with no output file exits non-zero ("At least one output
    # file must be specified") but still prints full stream info to stderr.
    stderr = proc.stderr or ""
    video_line = None
    for line in stderr.splitlines():
        if "Video:" in line:
            video_line = line
            break
    if video_line is None:
        return out

    m_res = _FFMPEG_RES_RE.search(video_line)
    if m_res:
        w, h = int(m_res.group(1)), int(m_res.group(2))
        if w > 0 and h > 0:
            out["width"], out["height"] = w, h
    m_fps = _FFMPEG_FPS_RE.search(video_line)
    if m_fps:
        try:
            fps = float(m_fps.group(1))
            if fps > 0:
                out["fps"] = fps
        except ValueError:
            pass
    return out


def _ffprobe_resolution_csv(video: Path) -> tuple[Optional[int], Optional[int]]:
    """Secondary resolution fallback: `ffprobe ... -of csv` for width,height."""
    if not shutil.which("ffprobe"):
        return None, None
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x", str(video),
        ],
        capture_output=True, text=True, timeout=120,
    )
    token = (proc.stdout or "").strip().splitlines()[0:1]
    if not token:
        return None, None
    m = _FFMPEG_RES_RE.search(token[0])
    if not m:
        return None, None
    w, h = int(m.group(1)), int(m.group(2))
    return (w if w > 0 else None), (h if h > 0 else None)


def _ffmpeg_decode_count(video: Path) -> dict[str, Any]:
    """Decode the whole stream with ffmpeg and read the TRUE frame count + time.

    VFR-robust fallback when ffprobe's container nb_frames / -count_frames give
    nothing. Runs `ffmpeg -nostats -i <video> -map 0:v:0 -f null -` and parses
    the final `frame=N` and `time=HH:MM:SS.ss` from stderr.

    Returns {frame_count, duration_s} (either may be None on parse failure).
    fps is intentionally derived by the caller as frame_count / duration_s so a
    single source of truth (the actual decode) drives both — no padding, no
    duplicating frames to fake a higher rate.
    """
    out: dict[str, Any] = {"frame_count": None, "duration_s": None}
    if not shutil.which("ffmpeg"):
        return out
    proc = subprocess.run(
        [
            "ffmpeg", "-nostats", "-hide_banner",
            "-i", str(video), "-map", "0:v:0", "-f", "null", "-",
        ],
        capture_output=True, text=True, timeout=1800,
    )
    stderr = proc.stderr or ""
    # The summary line is at/near the end. Scan from the bottom for the last
    # line carrying a frame= count (the final tally).
    frame_count = None
    duration_s = None
    for line in reversed(stderr.splitlines()):
        if frame_count is None:
            mf = _FFMPEG_FRAME_RE.search(line)
            if mf:
                frame_count = int(mf.group(1))
        if duration_s is None:
            mt = _FFMPEG_TIME_RE.search(line)
            if mt:
                duration_s = _hmss_to_seconds_safe(mt)
        if frame_count is not None and duration_s is not None:
            break
    out["frame_count"] = frame_count
    out["duration_s"] = duration_s
    return out


def ffprobe_truth(video: Path, *, count_frames: bool = True) -> dict[str, Any]:
    """ffprobe the video stream for ground-truth fps + frame_count.

    Returns a dict with:
      fps           - authoritative float fps (avg_frame_rate preferred)
      frame_count   - authoritative int (decoded count if available, else
                      container nb_frames, else fps*duration estimate)
      duration_s    - container/stream duration in seconds (float | None)
      width/height  - video resolution (int | None)
      raw           - the raw ffprobe-derived fields (for provenance/debug)

    NEVER trusts metadata.json — this is the truth source. Robust against VFR
    recorder mp4s: when the structured ffprobe stream probe returns empty fps /
    resolution / nb_frames (common on these variable-frame-rate files), it falls
    back to `ffmpeg -i` stderr parsing and a full `ffmpeg` decode-count. Every
    fallback reports the value as-read off the real stream — fps/resolution are
    NEVER upscaled and frames are NEVER padded or duplicated to fake a rate.
    """
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found on PATH — cannot derive ground-truth fps")

    # 1) Cheap stream probe (resolution, rates, container nb_frames, duration).
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames,duration",
            "-show_entries", "format=duration",
            "-of", "json", str(video),
        ],
        capture_output=True, text=True, timeout=120,
    )
    if probe.returncode != 0:
        raise RuntimeError(
            f"ffprobe stream probe failed (exit {probe.returncode}): "
            f"{probe.stderr.strip()[:200]}"
        )

    data = json.loads(probe.stdout or "{}")
    streams = data.get("streams") or [{}]
    s = streams[0] if streams else {}
    fmt = data.get("format") or {}

    avg = _parse_fps_fraction(s.get("avg_frame_rate"))
    rrate = _parse_fps_fraction(s.get("r_frame_rate"))
    fps = avg or rrate
    fps_method = "ffprobe_avg_or_r_frame_rate" if fps is not None else None

    duration_s: Optional[float] = None
    for cand in (s.get("duration"), fmt.get("duration")):
        try:
            if cand not in (None, "N/A"):
                duration_s = float(cand)
                break
        except (TypeError, ValueError):
            continue

    width = int(s["width"]) if str(s.get("width", "")).isdigit() else None
    height = int(s["height"]) if str(s.get("height", "")).isdigit() else None

    # 1b) VFR fallback for resolution + a provisional displayed fps. Only invoked
    #     when the structured probe left a hole — `ffmpeg -i` prints the real
    #     stream geometry to stderr even when ffprobe's CSV/JSON comes back empty.
    ffmpeg_line: dict[str, Any] = {}
    ffmpeg_displayed_fps: Optional[float] = None
    if width is None or height is None or fps is None:
        ffmpeg_line = _ffmpeg_probe_video_line(video)
        if width is None and isinstance(ffmpeg_line.get("width"), int):
            width = ffmpeg_line["width"]
        if height is None and isinstance(ffmpeg_line.get("height"), int):
            height = ffmpeg_line["height"]
        ffmpeg_displayed_fps = ffmpeg_line.get("fps")
        if fps is None and ffmpeg_displayed_fps is not None:
            fps = float(ffmpeg_displayed_fps)
            fps_method = "ffmpeg_displayed_fps"

    # 1c) Last-ditch resolution fallback via ffprobe CSV (different formatter than
    #     the JSON probe — occasionally succeeds where JSON returned nothing).
    if width is None or height is None:
        csv_w, csv_h = _ffprobe_resolution_csv(video)
        if width is None and csv_w is not None:
            width = csv_w
        if height is None and csv_h is not None:
            height = csv_h

    # 2) Authoritative frame count. Decode-count is the gold standard but is the
    #    expensive path; fall back to container nb_frames, then a full ffmpeg
    #    decode (VFR-robust), then fps*duration.
    container_nb = None
    if str(s.get("nb_frames", "")).isdigit():
        container_nb = int(s["nb_frames"])

    decoded_nb = None
    count_method = None
    if count_frames:
        cf = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-count_frames", "-show_entries", "stream=nb_read_frames",
                "-of", "default=nokey=1:noprint_wrappers=1", str(video),
            ],
            capture_output=True, text=True, timeout=600,
        )
        out = (cf.stdout or "").strip()
        if cf.returncode == 0 and out.isdigit():
            decoded_nb = int(out)
            count_method = "decoded_count_frames"

    # ffmpeg decode-count: the VFR-robust frame tally. Run it when ffprobe gave
    # us no decoded count AND no container nb_frames (so we don't pay for a full
    # decode when a cheaper truthful number already exists). It also yields a
    # decode-measured duration we use to derive a consistent fps.
    ffmpeg_decode: dict[str, Any] = {}
    if decoded_nb is None and container_nb is None:
        ffmpeg_decode = _ffmpeg_decode_count(video)
        if isinstance(ffmpeg_decode.get("frame_count"), int):
            decoded_nb = ffmpeg_decode["frame_count"]
            count_method = "ffmpeg_decode_count"
        if duration_s is None and ffmpeg_decode.get("duration_s") is not None:
            duration_s = float(ffmpeg_decode["duration_s"])

    if decoded_nb is not None:
        frame_count = decoded_nb
    elif container_nb is not None:
        frame_count = container_nb
        count_method = "container_nb_frames"
    elif duration_s is not None and fps is not None:
        frame_count = int(round(fps * duration_s))
        count_method = "fps_times_duration_estimate"
    else:
        raise RuntimeError("ffprobe could not determine a frame count by any method")

    # 2b) If fps still unknown, or the only fps we have is the coarse ffmpeg
    #     "displayed fps", derive the authoritative fps from the actual decode:
    #     frame_count / duration. This keeps fps and frame_count consistent and
    #     truthful — it is a MEASURED rate, never an upscale. (Displayed fps is a
    #     rounded integer; the decode-derived value is exact and preferred.)
    decode_dur = ffmpeg_decode.get("duration_s")
    if (
        count_method == "ffmpeg_decode_count"
        and isinstance(decode_dur, (int, float))
        and decode_dur > 0
    ):
        measured_fps = frame_count / float(decode_dur)
        if fps is None or fps_method == "ffmpeg_displayed_fps":
            fps = measured_fps
            fps_method = "decode_frames_over_duration"
    if fps is None:
        raise RuntimeError(
            "could not determine a frame rate by any method "
            "(ffprobe avg/r empty, ffmpeg displayed fps empty, decode duration "
            "unavailable)"
        )

    return {
        "fps": fps,
        "frame_count": frame_count,
        "duration_s": duration_s,
        "width": width,
        "height": height,
        "raw": {
            "avg_frame_rate": s.get("avg_frame_rate"),
            "r_frame_rate": s.get("r_frame_rate"),
            "container_nb_frames": container_nb,
            "decoded_nb_frames": decoded_nb,
            "frame_count_method": count_method,
            "fps_method": fps_method,
            "ffmpeg_displayed_fps": ffmpeg_displayed_fps,
            "ffmpeg_decode_duration_s": ffmpeg_decode.get("duration_s"),
        },
    }


# ---------------------------------------------------------------------------
# STAGE 0 — fps re-derive (the single highest-value primitive)
# ---------------------------------------------------------------------------

def rederive_fps_overwrite_metadata(
    session_dir: Path, *, count_frames: bool = True
) -> StageResult:
    """ffprobe the video → overwrite metadata.json fps fields with the truth.

    Idempotent. A prior correction is detected via the `_fps_correction`
    provenance block: if present, the recorder's ORIGINAL claim is read back
    out of that block (never re-derived from the already-overwritten fields),
    so re-running cannot silently lose the original claim or double-correct.

    Writes/refreshes these metadata.json fields to ffprobe ground truth:
      fps_effective, average_fps, frame_count
    and a `_fps_correction` block recording {recorder_reported_fps,
    recorder_reported_frame_count, real_delivered_fps, real_frame_count, ...}.
    """
    res = StageResult(name=STAGE_FPS, started_at=_now_iso())
    t0 = time.time()
    try:
        video = find_video(session_dir)
        if video is None:
            res.status = "failed"
            res.error = (
                f"no video found in {session_dir} "
                f"(looked for {', '.join(VIDEO_CANDIDATES)})"
            )
            return res

        meta_path = session_dir / "metadata.json"
        meta: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text())
            except json.JSONDecodeError as e:
                res.status = "failed"
                res.error = f"metadata.json is not valid JSON: {e}"
                return res
        if not isinstance(meta, dict):
            res.status = "failed"
            res.error = "metadata.json top-level is not an object"
            return res

        truth = ffprobe_truth(video, count_frames=count_frames)
        real_fps = float(truth["fps"])
        real_fc = int(truth["frame_count"])

        prior = meta.get("_fps_correction")
        if isinstance(prior, dict) and "recorder_reported_fps" in prior:
            # Already corrected on a previous run. Preserve the ORIGINAL claim
            # from the provenance block; do not re-read the (overwritten) fields.
            recorder_fps = prior.get("recorder_reported_fps")
            recorder_fc = prior.get("recorder_reported_frame_count")
            already_corrected = True
        else:
            # First correction. The recorder's claim is whatever is currently in
            # the live fields (these are the recorder-written values).
            recorder_fps = (
                meta.get("fps_effective")
                if meta.get("fps_effective") is not None
                else meta.get("average_fps")
            )
            recorder_fc = meta.get("frame_count")
            already_corrected = False

        def _f(x: Any) -> Optional[float]:
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        recorder_fps_f = _f(recorder_fps)
        fps_delta = (
            abs(recorder_fps_f - real_fps) if recorder_fps_f is not None else None
        )
        anomaly = fps_delta is not None and fps_delta > FPS_ANOMALY_THRESHOLD

        fields_already_truthful = (
            _f(meta.get("fps_effective")) == real_fps
            and _f(meta.get("average_fps")) == real_fps
            and meta.get("frame_count") == real_fc
        )

        # Overwrite the live fields with ground truth (idempotent — writing the
        # same values again is harmless).
        meta["fps_effective"] = real_fps
        meta["average_fps"] = real_fps
        meta["frame_count"] = real_fc

        # Provenance block. On the first correction we capture the recorder's
        # claim; on re-runs we keep the already-captured claim untouched.
        meta["_fps_correction"] = {
            "corrected_by": "score_session.py fps-rederive (ffprobe decode ground-truth)",
            "real_delivered_fps": real_fps,
            "real_frame_count": real_fc,
            "recorder_reported_fps": recorder_fps,
            "recorder_reported_frame_count": recorder_fc,
            "frame_count_method": truth["raw"].get("frame_count_method"),
            "ffprobe_avg_frame_rate": truth["raw"].get("avg_frame_rate"),
            "duration_s": truth.get("duration_s"),
            "anomaly": anomaly,
            "corrected_at": _now_iso(),
            "note": (
                "NEVER trust recorder-reported fps. metadata corrected to the "
                "actually-muxed video stream (ffprobe). Any frame-indexed step "
                "(depth, latency, PRD frame alignment) runs on these values."
            ),
        }

        meta_path.write_text(json.dumps(meta, indent=2))

        res.status = "ok"
        res.exit_code = 0
        res.detail = {
            "real_fps": real_fps,
            "real_frame_count": real_fc,
            "recorder_reported_fps": recorder_fps,
            "recorder_reported_frame_count": recorder_fc,
            "fps_delta": round(fps_delta, 4) if fps_delta is not None else None,
            "anomaly": anomaly,
            "frame_count_method": truth["raw"].get("frame_count_method"),
            "already_corrected_on_entry": already_corrected,
            "fields_already_truthful_on_entry": fields_already_truthful,
            "video": video.name,
            "resolution": (
                [truth.get("width"), truth.get("height")]
                if truth.get("width")
                else None
            ),
        }
        if anomaly:
            logger.warning(
                "fps anomaly: recorder claimed %s, video is actually %.4f fps "
                "(%d frames) — metadata corrected",
                recorder_fps, real_fps, real_fc,
            )
        return res
    except Exception as e:  # noqa: BLE001 — stage must capture, not crash the run
        res.status = "error"
        res.error = f"{type(e).__name__}: {e}"
        return res
    finally:
        res.ended_at = _now_iso()
        res.duration_seconds = time.time() - t0


# ---------------------------------------------------------------------------
# STAGE 0b — resolution re-derive (the data-honesty twin of fps-rederive)
# ---------------------------------------------------------------------------

def _as_int_pair(value: Any) -> Optional[list[int]]:
    """Coerce a resolution-ish value into a [width, height] int pair or None.

    Accepts a 2-element list/tuple of int-castable values. Anything else
    (None, wrong length, non-numeric) returns None so we never fabricate or
    mangle a malformed claim.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return [int(value[0]), int(value[1])]
    except (TypeError, ValueError):
        return None


def rederive_resolution_overwrite_metadata(
    session_dir: Path, *, count_frames: bool = False
) -> StageResult:
    """ffprobe the video → overwrite metadata.json resolution with the truth.

    The data-honesty twin of `rederive_fps_overwrite_metadata`: never trust the
    recorder's claimed resolution; derive the TRUE width x height from the actual
    muxed video stream and write it back, preserving the recorder's original
    claim in a transparent `_resolution_correction` provenance block.

    Idempotent. A prior correction is detected via the `_resolution_correction`
    block: if present, the recorder's ORIGINAL claim is read back out of that
    block (never re-derived from the already-overwritten fields), so re-running
    cannot silently lose the original claim or double-correct.

    Overwrites whichever of these resolution fields are present to the ffprobe
    ground-truth [width, height]: capture_resolution, game_resolution. NEVER
    upscales / fabricates a higher resolution — the real (possibly lower) one is
    reported. `count_frames` is intentionally False here (we only need the cheap
    stream probe for width/height; the heavy decode-count is fps-rederive's job).
    """
    res = StageResult(name=STAGE_RESOLUTION, started_at=_now_iso())
    t0 = time.time()
    try:
        video = find_video(session_dir)
        if video is None:
            res.status = "failed"
            res.error = (
                f"no video found in {session_dir} "
                f"(looked for {', '.join(VIDEO_CANDIDATES)})"
            )
            return res

        meta_path = session_dir / "metadata.json"
        meta: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text())
            except json.JSONDecodeError as e:
                res.status = "failed"
                res.error = f"metadata.json is not valid JSON: {e}"
                return res
        if not isinstance(meta, dict):
            res.status = "failed"
            res.error = "metadata.json top-level is not an object"
            return res

        truth = ffprobe_truth(video, count_frames=count_frames)
        real_w = truth.get("width")
        real_h = truth.get("height")
        if not (isinstance(real_w, int) and isinstance(real_h, int)
                and real_w > 0 and real_h > 0):
            res.status = "failed"
            res.error = (
                "ffprobe returned no usable video resolution "
                f"(width={real_w!r}, height={real_h!r})"
            )
            return res
        real_res = [real_w, real_h]

        prior = meta.get("_resolution_correction")
        if isinstance(prior, dict) and "recorder_reported_resolution" in prior:
            # Already corrected on a previous run. Preserve the ORIGINAL claim
            # from the provenance block; do not re-read the (overwritten) fields.
            recorder_res = prior.get("recorder_reported_resolution")
            already_corrected = True
        else:
            # First correction. The recorder's claim is whatever resolution field
            # is currently live (these are the recorder-written values). Prefer
            # capture_resolution, fall back to game_resolution.
            recorder_res = None
            for fld in RESOLUTION_FIELDS:
                recorder_res = _as_int_pair(meta.get(fld))
                if recorder_res is not None:
                    break
            already_corrected = False

        recorder_res_pair = _as_int_pair(recorder_res)
        if recorder_res_pair is not None:
            res_delta = [
                abs(recorder_res_pair[0] - real_w),
                abs(recorder_res_pair[1] - real_h),
            ]
            anomaly = max(res_delta) > RESOLUTION_ANOMALY_THRESHOLD
        else:
            res_delta = None
            anomaly = False

        # Which fields the recorder actually emitted — we only overwrite those
        # (never fabricate game_resolution if the recorder didn't claim one).
        present_fields = [f for f in RESOLUTION_FIELDS if f in meta]
        fields_already_truthful = all(
            _as_int_pair(meta.get(f)) == real_res for f in present_fields
        ) and bool(present_fields)

        # Overwrite the live resolution fields with ground truth (idempotent —
        # writing the same values again is harmless). NEVER upscale: real_res is
        # exactly what ffprobe read off the stream, nothing is rounded up.
        corrected_fields = []
        for fld in present_fields:
            meta[fld] = list(real_res)
            corrected_fields.append(fld)

        # Provenance block. On the first correction we capture the recorder's
        # claim; on re-runs we keep the already-captured claim untouched.
        meta["_resolution_correction"] = {
            "corrected_by": (
                "score_session.py resolution-rederive (ffprobe video-stream "
                "ground-truth)"
            ),
            "real_width": real_w,
            "real_height": real_h,
            "real_resolution": list(real_res),
            "recorder_reported_resolution": recorder_res,
            "corrected_fields": corrected_fields,
            "ffprobe_avg_frame_rate": truth["raw"].get("avg_frame_rate"),
            "anomaly": anomaly,
            "corrected_at": _now_iso(),
            "note": (
                "NEVER trust recorder-reported resolution. metadata corrected to "
                "the actually-muxed video stream (ffprobe). Resolution is reported "
                "as-read and is NEVER upscaled or fabricated higher — if the real "
                "stream is lower than the recorder claimed, the lower truth wins."
            ),
        }

        meta_path.write_text(json.dumps(meta, indent=2))

        res.status = "ok"
        res.exit_code = 0
        res.detail = {
            "real_width": real_w,
            "real_height": real_h,
            "real_resolution": list(real_res),
            "recorder_reported_resolution": recorder_res,
            "resolution_delta": res_delta,
            "anomaly": anomaly,
            "corrected_fields": corrected_fields,
            "already_corrected_on_entry": already_corrected,
            "fields_already_truthful_on_entry": fields_already_truthful,
            "video": video.name,
        }
        if anomaly:
            logger.warning(
                "resolution anomaly: recorder claimed %s, video is actually "
                "%dx%d — metadata corrected (never upscaled)",
                recorder_res, real_w, real_h,
            )
        return res
    except Exception as e:  # noqa: BLE001 — stage must capture, not crash the run
        res.status = "error"
        res.error = f"{type(e).__name__}: {e}"
        return res
    finally:
        res.ended_at = _now_iso()
        res.duration_seconds = time.time() - t0


# ---------------------------------------------------------------------------
# STAGE 0c — systeminfo synth (the metadata-honesty step for hardware provenance)
# ---------------------------------------------------------------------------

def _systeminfo_is_complete(data: Any) -> bool:
    """True iff *data* is a mapping containing every SYSTEMINFO_REQUIRED_KEY.

    Presence-only (mirrors prd_test_systeminfo_required, which is invoked WITHOUT
    --strict): a key present with a null/empty value still counts as present.
    """
    return isinstance(data, dict) and all(k in data for k in SYSTEMINFO_REQUIRED_KEYS)


def _read_json_obj(path: Path) -> Optional[dict[str, Any]]:
    """Read *path* as a JSON object, or None if absent/unparseable/not an object."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _derive_systeminfo_from_metadata(
    meta: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Derive the 5 required systeminfo fields from a session's metadata.json.

    Pulls EXCLUSIVELY from the recorder-written `hardware_specs` block (+ the
    top-level `recorder_version` for `build`). Never fabricates: any field whose
    source is genuinely absent in metadata is emitted as ``None`` and named in
    the returned "missing source" list so the caller can log it. Because the PRD
    test runs presence-only (no --strict), a ``None`` value still satisfies the
    gate while staying truthful about what the recorder did not capture.

    Returns ``(derived, missing_sources)`` where *derived* has exactly the 5
    required keys and *missing_sources* lists the keys backed by no real datum.
    """
    specs = meta.get("hardware_specs")
    specs = specs if isinstance(specs, dict) else {}
    cpu = specs.get("cpu") if isinstance(specs.get("cpu"), dict) else {}
    system = specs.get("system") if isinstance(specs.get("system"), dict) else {}
    gpus = specs.get("gpus") if isinstance(specs.get("gpus"), list) else []

    # gpu ← hardware_specs.gpus[0].name (first GPU only; never invent one)
    gpu: Optional[str] = None
    if gpus and isinstance(gpus[0], dict):
        name = gpus[0].get("name")
        gpu = name if isinstance(name, str) and name.strip() else None

    # cpu ← hardware_specs.cpu.brand (recorder pads this with trailing spaces)
    cpu_brand = cpu.get("brand")
    cpu_val: Optional[str] = (
        cpu_brand.strip() if isinstance(cpu_brand, str) and cpu_brand.strip() else None
    )

    # ram_gb ← round(hardware_specs.system.total_memory_gb, 1)
    total_mem = system.get("total_memory_gb")
    ram_gb: Optional[float] = (
        round(float(total_mem), 1)
        if isinstance(total_mem, (int, float)) and not isinstance(total_mem, bool)
        else None
    )

    # os ← f"{os_name} {os_version}" — only when BOTH halves are real strings;
    # a lone half is reported as-is rather than glued to an empty string.
    os_name = system.get("os_name")
    os_version = system.get("os_version")
    os_name_s = os_name if isinstance(os_name, str) and os_name.strip() else None
    os_ver_s = os_version if isinstance(os_version, str) and os_version.strip() else None
    if os_name_s and os_ver_s:
        os_val: Optional[str] = f"{os_name_s} {os_ver_s}"
    else:
        os_val = os_name_s or os_ver_s  # one half, or None if neither

    # build ← top-level recorder_version (the recorder build that produced this)
    rec_ver = meta.get("recorder_version")
    build_val: Optional[str] = (
        rec_ver if isinstance(rec_ver, str) and rec_ver.strip() else None
    )

    derived: dict[str, Any] = {
        "gpu": gpu,
        "cpu": cpu_val,
        "ram_gb": ram_gb,
        "os": os_val,
        "build": build_val,
    }
    missing_sources = [k for k, v in derived.items() if v is None]
    return derived, missing_sources


def ensure_systeminfo(session_dir: Path) -> StageResult:
    """Ensure <session>/systeminfo.json carries the 5 PRD-required hardware keys.

    Truthful-only ("真货") provenance step, the hardware twin of fps/resolution
    rederive. Resolution order:

      1. If <session>/systeminfo.json already has all 5 required keys → leave it
         untouched. Recorder v2.6.14+ emits this file natively; we respect it and
         never clobber a complete one.
      2. Else if <session>/metadata/systeminfo.json (the recorder's subdir copy)
         has all 5 keys → use it as the source (merged onto whatever the
         top-level file already had), rather than synthesising from metadata.
      3. Else synthesise the 5 keys from metadata.json's `hardware_specs`,
         deriving ONLY from real recorded data. Any field genuinely absent in
         metadata is written as null and logged — hardware that was not recorded
         is NEVER fabricated.

    Additive & non-destructive: existing keys in systeminfo.json (e.g. the
    recorder's window-geometry fields) are preserved; we only fill in the
    required keys that are missing. Idempotent: a second run sees a complete file
    and no-ops via branch 1.
    """
    res = StageResult(name=STAGE_SYSTEMINFO, started_at=_now_iso())
    t0 = time.time()
    try:
        target = session_dir / "systeminfo.json"
        existing = _read_json_obj(target)

        # --- Branch 1: already complete → respect the recorder, do nothing. ---
        if _systeminfo_is_complete(existing):
            res.status = "ok"
            res.exit_code = 0
            res.detail = {
                "action": "preserved",
                "reason": "systeminfo.json already has all required keys",
                "required_keys": list(SYSTEMINFO_REQUIRED_KEYS),
                "source": "existing_systeminfo",
                "fabricated_fields": [],
            }
            logger.info(
                "[%s] systeminfo.json already complete — preserved untouched",
                STAGE_SYSTEMINFO,
            )
            return res

        base: dict[str, Any] = dict(existing) if existing else {}

        # --- Branch 2: recorder's metadata/ subdir copy, if complete. ---------
        subdir = _read_json_obj(session_dir / "metadata" / "systeminfo.json")
        if _systeminfo_is_complete(subdir):
            assert subdir is not None  # narrowed by _systeminfo_is_complete
            merged = dict(base)
            for k in SYSTEMINFO_REQUIRED_KEYS:
                merged[k] = subdir[k]
            target.write_text(json.dumps(merged, indent=2))
            res.status = "ok"
            res.exit_code = 0
            res.detail = {
                "action": "copied_from_metadata_subdir",
                "reason": "metadata/systeminfo.json supplied all required keys",
                "required_keys": list(SYSTEMINFO_REQUIRED_KEYS),
                "source": "metadata/systeminfo.json",
                "fabricated_fields": [],
            }
            logger.info(
                "[%s] used recorder's metadata/systeminfo.json for required keys",
                STAGE_SYSTEMINFO,
            )
            return res

        # --- Branch 3: synthesise from metadata.json hardware_specs. ----------
        meta = _read_json_obj(session_dir / "metadata.json")
        if meta is None:
            res.status = "failed"
            res.error = (
                "cannot synth systeminfo.json: metadata.json is missing or not a "
                "JSON object (no real hardware_specs to derive from — refusing to "
                "fabricate)"
            )
            return res

        derived, missing_sources = _derive_systeminfo_from_metadata(meta)

        # Additive: keep existing keys (window-geometry etc.), fill required ones.
        out = dict(base)
        for k in SYSTEMINFO_REQUIRED_KEYS:
            out[k] = derived[k]
        target.write_text(json.dumps(out, indent=2))

        res.status = "ok"
        res.exit_code = 0
        res.detail = {
            "action": "synthesized",
            "reason": (
                "systeminfo.json missing/incomplete — derived required keys from "
                "metadata.json hardware_specs"
            ),
            "required_keys": list(SYSTEMINFO_REQUIRED_KEYS),
            "source": "metadata.json:hardware_specs",
            "derived": derived,
            # No real datum backed these → written as null, NOT fabricated.
            "null_unrecorded_fields": missing_sources,
            "fabricated_fields": [],  # invariant: we never invent hardware
            "preserved_existing_keys": sorted(
                k for k in base if k not in SYSTEMINFO_REQUIRED_KEYS
            ),
        }
        if missing_sources:
            logger.warning(
                "[%s] %d required field(s) had no real source in metadata "
                "(written as null, NOT fabricated): %s",
                STAGE_SYSTEMINFO, len(missing_sources), ", ".join(missing_sources),
            )
        logger.info(
            "[%s] synthesised systeminfo.json from hardware_specs "
            "(gpu=%r cpu=%r ram_gb=%r os=%r build=%r)",
            STAGE_SYSTEMINFO, derived["gpu"], derived["cpu"], derived["ram_gb"],
            derived["os"], derived["build"],
        )
        return res
    except Exception as e:  # noqa: BLE001 — stage must capture, not crash the run
        res.status = "error"
        res.error = f"{type(e).__name__}: {e}"
        return res
    finally:
        res.ended_at = _now_iso()
        res.duration_seconds = time.time() - t0


# ---------------------------------------------------------------------------
# Generic subprocess stage runner
# ---------------------------------------------------------------------------

def run_subprocess_stage(
    name: str,
    cmd: list[str],
    *,
    timeout: int,
    ok_exit_codes: tuple[int, ...] = (0,),
    env_extra: Optional[dict[str, str]] = None,
    cwd: Optional[Path] = None,
) -> StageResult:
    """Run one stage script as a subprocess with structured logging.

    Captures exit code + duration + stdout/stderr tails. Never raises — a
    failure is recorded on the StageResult and returned so the orchestrator can
    decide whether to continue.
    """
    import os

    res = StageResult(name=name, started_at=_now_iso())
    logger.info("[%s] START  cmd=%s", name, " ".join(cmd))
    t0 = time.time()

    env = None
    if env_extra:
        env = {**os.environ, **env_extra}

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(cwd) if cwd else None,
        )
        res.exit_code = proc.returncode
        res.detail["stdout_tail"] = "\n".join(
            (proc.stdout or "").strip().splitlines()[-25:]
        )
        res.detail["stderr_tail"] = "\n".join(
            (proc.stderr or "").strip().splitlines()[-25:]
        )
        if proc.returncode in ok_exit_codes:
            res.status = "ok"
        else:
            res.status = "failed"
            err = (proc.stderr or proc.stdout or "").strip()
            res.error = err.splitlines()[0] if err else f"exit code {proc.returncode}"
    except subprocess.TimeoutExpired:
        res.status = "error"
        res.exit_code = -1
        res.error = f"timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001
        res.status = "error"
        res.exit_code = -1
        res.error = f"{type(e).__name__}: {e}"
    finally:
        res.ended_at = _now_iso()
        res.duration_seconds = time.time() - t0
        logger.info(
            "[%s] END    status=%s exit=%s duration=%.2fs",
            name, res.status, res.exit_code, res.duration_seconds,
        )
    return res


# ---------------------------------------------------------------------------
# STAGE 3 — parse the PRD acceptance report (the ONLY source of a score)
# ---------------------------------------------------------------------------

_SCORE_RE = re.compile(
    r"Overall Score:\*\*\s*([\d.]+)%\s*\((\d+)/(\d+)\s*tests passed\)"
)
_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|.*\|\s*(-?\d+)\s*\|\s*$")


def parse_prd_report(report_path: Path, prd_exit_code: Optional[int]) -> dict[str, Any]:
    """Parse PRD-ACCEPTANCE-REPORT.md into a structured score + per-test map.

    INTEGRITY: this is the ONLY function that produces a `passed`/score value,
    and it derives both strictly from the report prd_acceptance just wrote in
    this same invocation, cross-checked against prd_acceptance's exit code. It
    never fabricates or hardcodes a pass.

    Returns:
      {
        passed: bool,                 # PASS only if exit 0 AND no failing tests
        score_percent: float|None,    # runnable-pass percentage from the report
        passed_count: int, runnable_count: int,
        tests: [{name, status, exit_code}],  # status in pass|fail|skip
        report_present: bool,
      }
    """
    out: dict[str, Any] = {
        "passed": False,
        "score_percent": None,
        "passed_count": None,
        "runnable_count": None,
        "tests": [],
        "report_present": report_path.is_file(),
        "prd_exit_code": prd_exit_code,
    }
    if not report_path.is_file():
        out["parse_error"] = f"report not found at {report_path}"
        return out

    text = report_path.read_text()

    m = _SCORE_RE.search(text)
    if m:
        out["score_percent"] = float(m.group(1))
        out["passed_count"] = int(m.group(2))
        out["runnable_count"] = int(m.group(3))

    # Per-test rows live in the "## Summary" markdown table.
    tests: list[dict[str, Any]] = []
    in_summary = False
    for line in text.splitlines():
        if line.startswith("## Summary"):
            in_summary = True
            continue
        if in_summary and line.startswith("## ") and "Summary" not in line:
            break
        if not in_summary:
            continue
        rm = _ROW_RE.match(line)
        if not rm:
            continue
        name = rm.group(1)
        status_raw = rm.group(2)
        exit_code = int(rm.group(3))
        if "SKIP" in status_raw:
            status = "skip"
        elif "PASS" in status_raw:
            status = "pass"
        else:
            status = "fail"
        tests.append({"name": name, "status": status, "exit_code": exit_code})
    out["tests"] = tests

    any_fail = any(t["status"] == "fail" for t in tests)
    # PASS requires BOTH: prd_acceptance exit 0 (its own verdict: no failing
    # tests) AND no failing row in the report we parsed. Belt and suspenders so
    # a parse gap can never manufacture a PASS.
    out["passed"] = (prd_exit_code == 0) and (not any_fail) and bool(tests)
    return out


# ---------------------------------------------------------------------------
# STAGE 4 — clip summary (the fleet-gate aggregation unit)
# ---------------------------------------------------------------------------

# PRD tests whose semantics are FLEET-LEVEL (aggregate across >=120 clips) and
# therefore mis-pass when prd_acceptance points them at a single session's
# per-frame data. We surface this honestly rather than claiming a real
# single-session pass. (route_type globs *.json in the session dir → it counts
# action_camera.json's per-frame rows, not 120 distinct clip summaries.)
FLEET_LEVEL_TESTS = {"prd_test_route_type_distribution"}


def _route_type_distribution(session_dir: Path) -> dict[str, Any]:
    """Read action_camera.json and summarise the per-frame route_type spread.

    This is descriptive provenance for the clip summary (what locomotion modes
    this single session actually exhibits), NOT a pass/fail.
    """
    ac_path = session_dir / "action_camera.json"
    info: dict[str, Any] = {
        "source": "action_camera.json",
        "distinct_route_types": None,
        "distribution": None,
        "record_count": None,
    }
    if not ac_path.is_file():
        info["note"] = "action_camera.json missing"
        return info
    try:
        data = json.loads(ac_path.read_text())
    except json.JSONDecodeError as e:
        info["note"] = f"action_camera.json parse error: {e}"
        return info

    if isinstance(data, dict) and "frames" in data:
        rows = data["frames"]
    elif isinstance(data, list):
        rows = data
    else:
        info["note"] = "unexpected action_camera.json structure"
        return info

    counts: dict[str, int] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        rt = r.get("route_type")
        if rt is None:
            continue
        counts[str(rt)] = counts.get(str(rt), 0) + 1

    info["record_count"] = len(rows)
    info["distribution"] = dict(sorted(counts.items()))
    info["distinct_route_types"] = len(counts)
    return info


def build_clip_summary(
    session_dir: Path,
    *,
    fps_stage: StageResult,
    prd_stage: StageResult,
    prd_parsed: dict[str, Any],
    skip_depth: bool,
    resolution_stage: Optional[StageResult] = None,
) -> StageResult:
    """Emit <session_dir>/clip_summary.json — the per-session fleet-gate unit.

    Includes session id, recorder_version, real fps/frame_count, real
    resolution, prd score, per-test pass/fail/skip, route_type distribution,
    and duration. Also flags fleet-level tests that mis-pass on per-frame
    single-session data.
    """
    res = StageResult(name=STAGE_CLIP, started_at=_now_iso())
    t0 = time.time()
    try:
        meta_path = session_dir / "metadata.json"
        meta: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text())
            except json.JSONDecodeError:
                meta = {}

        rt_info = _route_type_distribution(session_dir)

        # Honesty annotation: which reported "passes" are actually fleet-level
        # tests mis-passing on per-frame data.
        fleet_mispass = [
            t["name"]
            for t in prd_parsed.get("tests", [])
            if t["name"] in FLEET_LEVEL_TESTS and t["status"] == "pass"
        ]

        # Real fps/frame_count come from the fps-rederive stage (truth), with a
        # metadata fallback (which fps-rederive itself just wrote).
        real_fps = fps_stage.detail.get("real_fps", meta.get("fps_effective"))
        real_fc = fps_stage.detail.get("real_frame_count", meta.get("frame_count"))

        # Real resolution comes from the resolution-rederive stage (truth), with
        # a metadata fallback (which resolution-rederive itself just wrote).
        res_detail = resolution_stage.detail if resolution_stage else {}
        real_resolution = res_detail.get(
            "real_resolution", meta.get("capture_resolution")
        )

        summary = {
            "schema": "oyster.clip_summary/v1",
            "generated_at": _now_iso(),
            "session_id": meta.get("session_id") or session_dir.name,
            "session_dir": str(session_dir),
            "recorder_version": meta.get("recorder_version"),
            "recorder_commit": meta.get("recorder_commit"),
            # --- truth values (ffprobe-derived) ---
            "fps_real": real_fps,
            "frame_count_real": real_fc,
            "fps_recorder_reported": fps_stage.detail.get("recorder_reported_fps"),
            "frame_count_recorder_reported": fps_stage.detail.get(
                "recorder_reported_frame_count"
            ),
            "fps_corrected": bool(fps_stage.detail.get("anomaly")),
            # --- truth resolution (ffprobe-derived, never upscaled) ---
            "resolution_real": real_resolution,
            "resolution_recorder_reported": res_detail.get(
                "recorder_reported_resolution"
            ),
            "resolution_corrected": bool(res_detail.get("anomaly")),
            "duration_s": meta.get("duration"),
            # --- PRD scoring (sole source: this run's prd_acceptance) ---
            "prd_passed": prd_parsed.get("passed"),
            "prd_score_percent": prd_parsed.get("score_percent"),
            "prd_passed_count": prd_parsed.get("passed_count"),
            "prd_runnable_count": prd_parsed.get("runnable_count"),
            "prd_exit_code": prd_stage.exit_code,
            "prd_tests": prd_parsed.get("tests", []),
            # --- route_type provenance (descriptive, per-frame, single session) ---
            "route_type": rt_info,
            # --- honesty / integrity annotations ---
            "depth_skipped": skip_depth,
            "fleet_level_tests_mispassing_on_single_session": fleet_mispass,
            "integrity_note": (
                "prd_passed / prd_score_percent are derived solely from the "
                "PRD-ACCEPTANCE-REPORT.md produced by prd_acceptance.py in THIS "
                "invocation. fleet_level_tests_mispassing_on_single_session lists "
                "aggregate (>=120-clip) tests that report PASS only because they "
                "were globbed over one session's per-frame data — they are NOT a "
                "real single-session pass and the fleet gate must re-evaluate them "
                "across the clip pool."
            ),
        }

        out_path = session_dir / "clip_summary.json"
        out_path.write_text(json.dumps(summary, indent=2))

        res.status = "ok"
        res.exit_code = 0
        res.detail = {
            "clip_summary_path": str(out_path),
            "session_id": summary["session_id"],
            "prd_passed": summary["prd_passed"],
            "prd_score_percent": summary["prd_score_percent"],
            "fps_real": summary["fps_real"],
            "frame_count_real": summary["frame_count_real"],
            "resolution_real": summary["resolution_real"],
            "fleet_mispass": fleet_mispass,
        }
        return res
    except Exception as e:  # noqa: BLE001
        res.status = "error"
        res.error = f"{type(e).__name__}: {e}"
        return res
    finally:
        res.ended_at = _now_iso()
        res.duration_seconds = time.time() - t0


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def score_session(
    session_dir: Path,
    *,
    repo_root: Path,
    skip_depth: bool = False,
    prd_timeout: int = DEFAULT_PRD_TIMEOUT,
    count_frames: bool = True,
) -> dict[str, Any]:
    """Run the Phase 0 local-truth-loop chain in order. Returns a report dict.

    The report's `passed` is True only when every stage is ok/skipped AND
    prd_acceptance returned a genuine PASS.
    """
    bin_dir = Path(__file__).resolve().parent
    build_ac = bin_dir / "build_action_camera.py"
    finalize = bin_dir / "finalize_session.py"
    prd = bin_dir / "prd_acceptance.py"

    stages: list[StageResult] = []
    run_started = _now_iso()
    wall0 = time.time()

    # ----- STAGE 0: fps re-derive -----------------------------------------
    s_fps = rederive_fps_overwrite_metadata(session_dir, count_frames=count_frames)
    stages.append(s_fps)
    logger.info(
        "[%s] END    status=%s real_fps=%s frame_count=%s anomaly=%s duration=%.2fs",
        STAGE_FPS, s_fps.status, s_fps.detail.get("real_fps"),
        s_fps.detail.get("real_frame_count"), s_fps.detail.get("anomaly"),
        s_fps.duration_seconds,
    )

    # ----- STAGE 0b: resolution re-derive (before any resolution-dependent
    # stage) — twin of fps-rederive; never trusts the recorder's claimed
    # resolution, never upscales. Cheap (stream probe only, no decode-count).
    s_res = rederive_resolution_overwrite_metadata(session_dir, count_frames=False)
    stages.append(s_res)
    logger.info(
        "[%s] END    status=%s real_resolution=%s recorder_reported=%s "
        "anomaly=%s duration=%.2fs",
        STAGE_RESOLUTION, s_res.status, s_res.detail.get("real_resolution"),
        s_res.detail.get("recorder_reported_resolution"),
        s_res.detail.get("anomaly"), s_res.duration_seconds,
    )

    # ----- STAGE 0c: systeminfo synth (metadata-honesty step) -------------
    # Ensure systeminfo.json carries the 5 PRD-required hardware keys, derived
    # truthfully from metadata.json hardware_specs. Sits with the other
    # metadata-honesty stages (after fps/resolution rederive, before scoring).
    # Respects a recorder-native complete file; never fabricates absent hardware.
    s_sys = ensure_systeminfo(session_dir)
    stages.append(s_sys)
    logger.info(
        "[%s] END    status=%s action=%s null_unrecorded=%s duration=%.2fs",
        STAGE_SYSTEMINFO, s_sys.status, s_sys.detail.get("action"),
        s_sys.detail.get("null_unrecorded_fields"), s_sys.duration_seconds,
    )

    # ----- STAGE 1: build_action_camera (MUST precede finalize) -----------
    s_ac = run_subprocess_stage(
        STAGE_ACTION_CAM,
        [sys.executable, str(build_ac), str(session_dir)],
        timeout=300,
    )
    stages.append(s_ac)

    # ----- STAGE 2: finalize_session --------------------------------------
    finalize_cmd = [
        sys.executable, str(finalize), str(session_dir),
        "--repo-root", str(repo_root),
    ]
    if skip_depth:
        finalize_cmd.append("--no-depth")  # documented --skip-depth passthrough
    # finalize returns 0 even on per-step WARNs (its contract). Depth is the
    # only heavy step; with --no-depth it is fast.
    s_fin = run_subprocess_stage(
        STAGE_FINALIZE,
        finalize_cmd,
        timeout=2400,  # depth can take minutes on GPU / much longer on CPU
    )
    stages.append(s_fin)

    # ----- STAGE 2b: build_action_camera RE-ASSERT ------------------------
    # finalize's backfill + resample steps overwrite the PRD-tuned fields
    # build_action_camera set in STAGE 1 (camera_position reverts to absolute
    # block coords; per-action timestamps get resampled onto the frame grid).
    # Re-running build_action_camera re-derives the buyer-canonical 20-field
    # file from raw inputs as the FINAL action_camera.json before scoring.
    # Deterministic + cheap → idempotent. Ordering only; no stage modified.
    s_ac2 = run_subprocess_stage(
        STAGE_ACTION_CAM_REASSERT,
        [sys.executable, str(build_ac), str(session_dir)],
        timeout=300,
    )
    stages.append(s_ac2)

    # ----- STAGE 3: prd_acceptance (sole score source) --------------------
    report_md = session_dir / "PRD-ACCEPTANCE-REPORT.md"
    s_prd = run_subprocess_stage(
        STAGE_PRD,
        [
            sys.executable, str(prd), str(session_dir),
            "-o", str(report_md), "-t", str(prd_timeout),
        ],
        timeout=prd_timeout * 20 + 120,  # generous wall for the whole suite
        ok_exit_codes=(0, 1),  # exit 1 = some test failed; still a valid scored run
    )
    prd_parsed = parse_prd_report(report_md, s_prd.exit_code)
    s_prd.detail["prd_parsed"] = prd_parsed
    if s_prd.status == "ok" and not prd_parsed["passed"]:
        # The suite ran and produced a report, but the session did not pass.
        # That is a valid (non-error) outcome — record it as a non-pass.
        s_prd.detail["verdict"] = "ran_not_passed"
    stages.append(s_prd)

    # ----- STAGE 4: clip summary ------------------------------------------
    s_clip = build_clip_summary(
        session_dir,
        fps_stage=s_fps,
        prd_stage=s_prd,
        prd_parsed=prd_parsed,
        skip_depth=skip_depth,
        resolution_stage=s_res,
    )
    stages.append(s_clip)

    # ----- Verdict --------------------------------------------------------
    stage_failures = [s.name for s in stages if not s.ok]
    prd_pass = bool(prd_parsed.get("passed"))
    # NOT-PASSED if any stage failed OR PRD did not pass. PASS only with a real
    # green prd_acceptance run AND a clean chain.
    overall_pass = prd_pass and not stage_failures

    report = {
        "schema": "oyster.score_session_report/v1",
        "session_dir": str(session_dir),
        "session_id": session_dir.name,  # refined from metadata below
        "run_started_at": run_started,
        "run_ended_at": _now_iso(),
        "wall_seconds": round(time.time() - wall0, 3),
        "skip_depth": skip_depth,
        "passed": overall_pass,
        "verdict": "PASSED" if overall_pass else "NOT-PASSED",
        "prd_passed": prd_pass,
        "prd_score_percent": prd_parsed.get("score_percent"),
        "prd_passed_count": prd_parsed.get("passed_count"),
        "prd_runnable_count": prd_parsed.get("runnable_count"),
        "resolution_real": s_res.detail.get("real_resolution"),
        "resolution_recorder_reported": s_res.detail.get(
            "recorder_reported_resolution"
        ),
        "resolution_corrected": bool(s_res.detail.get("anomaly")),
        "stage_failures": stage_failures,
        "fleet_level_tests_mispassing_on_single_session": [
            t["name"]
            for t in prd_parsed.get("tests", [])
            if t["name"] in FLEET_LEVEL_TESTS and t["status"] == "pass"
        ],
        "stages": [s.to_dict() for s in stages],
        "clip_summary_path": s_clip.detail.get("clip_summary_path"),
        "report_md_path": str(report_md),
    }
    # Pull session id out of the metadata that fps/clip stages already read.
    meta_path = session_dir / "metadata.json"
    if meta_path.is_file():
        try:
            report["session_id"] = json.loads(meta_path.read_text()).get(
                "session_id", session_dir.name
            )
        except json.JSONDecodeError:
            report["session_id"] = session_dir.name
    else:
        report["session_id"] = session_dir.name

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 0 local-truth-loop scoring orchestrator for a "
                    "GameData recorded session.",
    )
    parser.add_argument("session_dir", type=Path, help="Path to the session directory")
    parser.add_argument(
        "--skip-depth", action="store_true",
        help="Skip DA-V2 depth EXR generation (fast run). Passed through to "
             "finalize_session as --no-depth; depth PRD tests will SKIP.",
    )
    parser.add_argument(
        "--report-json", type=Path, default=None,
        help="Write the full orchestration report JSON to this path "
             "(default: <session_dir>/score_session_report.json).",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=DEFAULT_REPO_ROOT,
        help=f"Repo root passed to finalize_session (default: {DEFAULT_REPO_ROOT}).",
    )
    parser.add_argument(
        "--prd-timeout", type=int, default=DEFAULT_PRD_TIMEOUT,
        help=f"Per-test timeout for prd_acceptance (default: {DEFAULT_PRD_TIMEOUT}s).",
    )
    parser.add_argument(
        "--no-count-frames", action="store_true",
        help="Skip the exact decode frame-count (use container nb_frames). "
             "Faster but slightly less authoritative.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    session_dir = args.session_dir.expanduser().resolve()
    if not session_dir.exists() or not session_dir.is_dir():
        logger.error("session_dir not found / not a directory: %s", session_dir)
        return 2

    repo_root = args.repo_root.expanduser().resolve()

    # Hard preconditions: video + the three stage scripts must exist.
    if find_video(session_dir) is None:
        logger.error(
            "no video in %s (looked for %s) — cannot derive truth fps",
            session_dir, ", ".join(VIDEO_CANDIDATES),
        )
        return 2
    bin_dir = Path(__file__).resolve().parent
    for required in ("build_action_camera.py", "finalize_session.py", "prd_acceptance.py"):
        if not (bin_dir / required).is_file():
            logger.error("required stage script missing: %s", bin_dir / required)
            return 2

    logger.info("scoring session: %s (skip_depth=%s)", session_dir, args.skip_depth)

    report = score_session(
        session_dir,
        repo_root=repo_root,
        skip_depth=args.skip_depth,
        prd_timeout=args.prd_timeout,
        count_frames=not args.no_count_frames,
    )

    report_json = args.report_json or (session_dir / "score_session_report.json")
    report_json = report_json.expanduser()
    report_json.write_text(json.dumps(report, indent=2))

    # Human-readable summary to stdout.
    print("\n" + "=" * 70)
    print(f"score_session: {report['verdict']}  ({session_dir})")
    print(f"  session_id        : {report['session_id']}")
    fps_detail = report["stages"][0]["detail"] if report["stages"] else {}
    print(f"  fps               : recorder={fps_detail.get('recorder_reported_fps')} "
          f"-> real={fps_detail.get('real_fps')} "
          f"(frame_count {fps_detail.get('recorder_reported_frame_count')} "
          f"-> {fps_detail.get('real_frame_count')}), "
          f"anomaly={fps_detail.get('anomaly')}")
    res_detail = next(
        (s["detail"] for s in report["stages"] if s["name"] == STAGE_RESOLUTION),
        {},
    )
    print(f"  resolution        : "
          f"recorder={res_detail.get('recorder_reported_resolution')} "
          f"-> real={res_detail.get('real_resolution')}, "
          f"anomaly={res_detail.get('anomaly')} (never upscaled)")
    print(f"  PRD               : {report['prd_score_percent']}% "
          f"({report['prd_passed_count']}/{report['prd_runnable_count']}) "
          f"passed={report['prd_passed']}")
    if report["stage_failures"]:
        print(f"  stage failures    : {', '.join(report['stage_failures'])}")
    if report["fleet_level_tests_mispassing_on_single_session"]:
        print("  fleet mis-pass    : "
              + ", ".join(report["fleet_level_tests_mispassing_on_single_session"])
              + "  (NOT a real single-session pass)")
    print(f"  clip_summary      : {report['clip_summary_path']}")
    print(f"  report json       : {report_json}")
    print("=" * 70)

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
