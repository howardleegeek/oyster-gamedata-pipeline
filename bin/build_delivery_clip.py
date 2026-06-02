#!/usr/bin/env python3
"""Build a buyer-ready *co-extensive* delivery clip from a scored session.

A recording session contains a video plus per-frame pose/inputs/audio/depth.
The leading frames (world loading) and trailing frames have no *real* player
pose — ``build_action_camera.py`` flags those ``pose_valid=false`` and records
the pose-covered window in ``frame_alignment.json``::

    recommended_clip_trim: {"start_frame": S, "end_frame": E}

A buyer wants every delivered frame to carry a real (interpolated) pose, so we
trim *every* modality — video, action_camera, inputs, audio, depth — to exactly
that ``[S..E]`` frame window. The cut is:

* **Non-destructive** — source files are never modified or deleted; outputs go
  only under ``<session_dir>/delivery/``.
* **Frame-accurate** — the delivered video frame count (verified with ffprobe)
  equals the delivered ``action_camera`` length equals ``E - S + 1``. We assert
  this and fail loudly otherwise.
* **Honest** — resolution and fps are preserved (never fabricated), and absent
  modalities (audio/inputs/depth) are recorded as ``"absent"`` in the manifest
  rather than invented.

Outputs in ``<session_dir>/delivery/``:

* ``video.mp4`` — frame-accurate cut of frames ``[S..E]`` (re-encoded; same
  fps/resolution).
* ``action_camera.json`` — the records for ``[S..E]``, re-indexed so delivered
  frame ``0`` is source ``S`` (``frame = i``, ``time = round(i/fps, 6)``); every
  record has ``pose_valid=true`` by construction.
* ``audio.flac`` — trimmed to ``[S/fps, (E+1)/fps]`` (only if a source exists).
* ``input_frame_map.json`` — events with ``frame ∈ [S, E]``, re-indexed
  (``frame -= S``) with recomputed ``frame_time_s`` (only if a source exists).
* depth maps (``zbuffer/`` or ``depth/``) — files whose index maps into
  ``[S, E]``, re-indexed (only if a source dir exists).
* ``delivery_manifest.json`` — provenance + integrity record.

Usage::

    python3 bin/build_delivery_clip.py "<session_dir>"
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA = "oyster.delivery_clip/v1"
DELIVERY_DIRNAME = "delivery"
VIDEO_NAMES = ("recording.mp4", "video.mp4", "game.mp4")
DEPTH_DIRNAMES = ("zbuffer", "depth")
_DEPTH_INDEX_RE = re.compile(r"(\d+)")


# --- ffprobe / ffmpeg helpers ------------------------------------------------


def _require(tool: str) -> None:
    if not shutil.which(tool):
        raise RuntimeError(f"{tool} is required but not found on PATH")


def probe_stream(path: Path) -> dict[str, Any]:
    """Return ``{width, height, fps, frame_count}`` for the first video stream.

    ffprobe of the real encoded video is authoritative for resolution and fps —
    these must reflect what is actually delivered, never a fabricated value.
    """
    _require("ffprobe")
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,nb_frames,r_frame_rate,avg_frame_rate,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {out.stderr.strip()}")
    streams = json.loads(out.stdout).get("streams", [])
    if not streams:
        raise RuntimeError(f"no video stream in {path}")
    s = streams[0]

    def _rate(value: str | None) -> float | None:
        if not value or value in ("0/0", "N/A"):
            return None
        if "/" in value:
            num, den = value.split("/", 1)
            try:
                den_f = float(den)
                return float(num) / den_f if den_f else None
            except ValueError:
                return None
        try:
            return float(value)
        except ValueError:
            return None

    fps = _rate(s.get("avg_frame_rate")) or _rate(s.get("r_frame_rate"))
    width = int(s["width"])
    height = int(s["height"])
    frame_count = None
    try:
        frame_count = int(s["nb_frames"])
    except (KeyError, ValueError, TypeError):
        frame_count = None
    return {"width": width, "height": height, "fps": fps, "frame_count": frame_count}


def count_video_frames(path: Path) -> int:
    """Authoritatively count decoded frames in a video via ffprobe.

    Uses ``-count_frames`` (decodes every packet) so the result is exact even
    when container metadata (``nb_frames``) is missing or stale.
    """
    _require("ffprobe")
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe frame count failed for {path}: {out.stderr.strip()}")
    text = out.stdout.strip()
    if not text or text == "N/A":
        raise RuntimeError(f"ffprobe returned no frame count for {path}")
    return int(text)


def audio_duration(path: Path) -> float:
    _require("ffprobe")
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe audio duration failed for {path}: {out.stderr.strip()}")
    return float(out.stdout.strip())


def _find_video(session_dir: Path) -> Path:
    for name in VIDEO_NAMES:
        p = session_dir / name
        if p.is_file():
            return p
    raise FileNotFoundError(f"no source video ({'/'.join(VIDEO_NAMES)}) in {session_dir}")


def _find_depth_dir(session_dir: Path) -> Path | None:
    for name in DEPTH_DIRNAMES:
        d = session_dir / name
        if d.is_dir() and any(d.iterdir()):
            return d
    return None


# --- Pure trimming / re-indexing logic (independently testable) --------------


def read_trim_range(alignment_path: Path) -> tuple[int, int, float, int]:
    """Read the authoritative trim window from ``frame_alignment.json``.

    Returns ``(start_frame, end_frame, fps, total_frames)``. The trim range is
    READ, never hardcoded.
    """
    data = json.loads(Path(alignment_path).read_text())
    trim = data.get("recommended_clip_trim")
    if not isinstance(trim, dict) or "start_frame" not in trim or "end_frame" not in trim:
        raise ValueError(
            f"{alignment_path} missing recommended_clip_trim.{{start_frame,end_frame}}"
        )
    start = int(trim["start_frame"])
    end = int(trim["end_frame"])
    if end < start:
        raise ValueError(f"invalid trim range: end({end}) < start({start})")
    fps = float(data.get("fps") or 0.0)
    if fps <= 0:
        raise ValueError(f"{alignment_path} missing/invalid fps")
    total = int(data.get("total_frames") or 0)
    return start, end, fps, total


def reindex_action_records(
    records: list[dict[str, Any]], start: int, end: int, fps: float
) -> list[dict[str, Any]]:
    """Slice ``[start..end]`` inclusive and re-index to a 0-based timeline.

    Delivered frame ``0`` == source ``start``. For each kept record we set
    ``frame = i`` and ``time = round(i/fps, 6)`` while preserving every other
    field (pose, intrinsics, keyCode, ...). Because the window is the
    pose-covered range, every record MUST have ``pose_valid=true`` — if any does
    not, the trim range is wrong and we raise.
    """
    by_frame = {int(r["frame"]): r for r in records}
    out: list[dict[str, Any]] = []
    for i, src_frame in enumerate(range(start, end + 1)):
        rec = by_frame.get(src_frame)
        if rec is None:
            raise ValueError(f"action_camera missing record for source frame {src_frame}")
        if not rec.get("pose_valid"):
            raise ValueError(
                "pose_valid is False at source frame "
                f"{src_frame}; trim range [{start},{end}] is not pose-covered"
            )
        new = dict(rec)  # shallow copy keeps nested pose/intrinsics objects intact
        new["frame"] = i
        new["time"] = round(i / fps, 6)
        out.append(new)
    return out


def reindex_input_events(
    events: list[dict[str, Any]], start: int, end: int, fps: float
) -> list[dict[str, Any]]:
    """Keep events with ``frame ∈ [start, end]``; re-index and recompute time."""
    out: list[dict[str, Any]] = []
    for ev in events:
        try:
            f = int(ev["frame"])
        except (KeyError, ValueError, TypeError):
            continue
        if f < start or f > end:
            continue
        new = dict(ev)
        new_frame = f - start
        new["frame"] = new_frame
        new["frame_time_s"] = round(new_frame / fps, 6)
        out.append(new)
    return out


def _depth_frame_index(name: str) -> int | None:
    m = _DEPTH_INDEX_RE.search(name)
    return int(m.group(1)) if m else None


# --- ffmpeg cut operations (side-effecting) ----------------------------------


def cut_video(src: Path, dst: Path, start: int, end: int, fps: float) -> None:
    """Frame-accurate cut of frames ``[start..end]`` inclusive.

    Re-encode is required for frame accuracy. fps and resolution are preserved
    (the select filter + matching ``-r`` keep the exact source geometry).
    """
    _require("ffmpeg")
    vf = f"select='between(n\\,{start}\\,{end})',setpts=N/FRAME_RATE/TB"
    out = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vf",
            vf,
            "-an",
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            str(dst),
        ],
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffmpeg video cut failed: {out.stderr.strip()[-2000:]}")


def cut_audio(src: Path, dst: Path, t0: float, t1: float) -> None:
    """Trim audio to ``[t0, t1]``.

    Tries stream-copy first (fast, lossless) but FLAC frame boundaries can make
    a copy misalign — the output keeps the wrong duration even though ffmpeg
    exits 0. So we verify the trimmed duration and fall back to a lossless flac
    re-encode whenever copy is missing, empty, or off by more than one audio
    frame. Time-accurate audio is required for a co-extensive delivery.
    """
    _require("ffmpeg")
    base = ["ffmpeg", "-y", "-ss", f"{t0:.6f}", "-to", f"{t1:.6f}", "-i", str(src)]
    expected = t1 - t0
    tolerance = 0.10  # seconds; flac frame is ~0.1s, copy must land within one

    copy = subprocess.run(
        base + ["-c", "copy", str(dst)], capture_output=True, text=True, timeout=600
    )
    if copy.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
        try:
            if abs(audio_duration(dst) - expected) <= tolerance:
                return
        except RuntimeError:
            pass  # unprobeable copy -> fall through to re-encode

    # copy missing/empty/misaligned -> re-encode losslessly to flac (frame-exact)
    reencode = subprocess.run(
        base + ["-c:a", "flac", str(dst)], capture_output=True, text=True, timeout=600
    )
    if reencode.returncode != 0:
        raise RuntimeError(f"ffmpeg audio trim failed: {reencode.stderr.strip()[-2000:]}")


# --- Orchestration -----------------------------------------------------------


def build_delivery_clip(session_dir: str | Path) -> dict[str, Any]:
    """Build the delivery clip for ``session_dir`` and return its manifest."""
    session = Path(session_dir)
    if not session.is_dir():
        raise FileNotFoundError(f"session_dir not found: {session}")

    alignment_path = session / "frame_alignment.json"
    if not alignment_path.is_file():
        raise FileNotFoundError(f"frame_alignment.json not found in {session}")
    start, end, fps, total_frames = read_trim_range(alignment_path)
    expected_count = end - start + 1

    action_path = session / "action_camera.json"
    if not action_path.is_file():
        raise FileNotFoundError(f"action_camera.json not found in {session}")
    records = json.loads(action_path.read_text())
    if not isinstance(records, list):
        raise ValueError("action_camera.json must be a list of per-frame records")

    src_video = _find_video(session)
    src_probe = probe_stream(src_video)
    width, height = src_probe["width"], src_probe["height"]
    # Prefer the fps recorded in alignment (the pose timeline reference) but keep
    # it consistent with the encoded video when available.
    video_fps = src_probe["fps"] or fps

    delivery = session / DELIVERY_DIRNAME
    delivery.mkdir(parents=True, exist_ok=True)

    # 1) action_camera.json (re-indexed; asserts pose_valid by construction).
    delivered_records = reindex_action_records(records, start, end, fps)
    (delivery / "action_camera.json").write_text(json.dumps(delivered_records, indent=2))

    # 2) video.mp4 (frame-accurate cut) + verify count.
    out_video = delivery / "video.mp4"
    cut_video(src_video, out_video, start, end, video_fps)
    delivered_video_frames = count_video_frames(out_video)
    if delivered_video_frames != expected_count:
        raise RuntimeError(
            "frame-accuracy check failed: delivered video has "
            f"{delivered_video_frames} frames, expected {expected_count}"
        )
    if len(delivered_records) != expected_count:
        raise RuntimeError(
            "frame-accuracy check failed: delivered action_camera has "
            f"{len(delivered_records)} records, expected {expected_count}"
        )

    t0 = round(start / fps, 6)
    t1 = round((end + 1) / fps, 6)

    artifacts: dict[str, str] = {
        "video": "video.mp4",
        "action_camera": "action_camera.json",
        "audio": "absent",
        "input_frame_map": "absent",
        "depth": "absent",
    }

    # 3) audio.flac (only if a source audio file exists).
    src_audio = session / "audio.flac"
    if src_audio.is_file():
        cut_audio(src_audio, delivery / "audio.flac", t0, t1)
        artifacts["audio"] = "audio.flac"

    # 4) input_frame_map.json (only if a source exists).
    src_input = session / "input_frame_map.json"
    if src_input.is_file():
        events = json.loads(src_input.read_text())
        if isinstance(events, list):
            delivered_events = reindex_input_events(events, start, end, fps)
            (delivery / "input_frame_map.json").write_text(json.dumps(delivered_events, indent=2))
            artifacts["input_frame_map"] = "input_frame_map.json"

    # 5) depth maps (only if a source dir exists). Copy + re-index; never fabricate.
    depth_dir = _find_depth_dir(session)
    if depth_dir is not None:
        out_depth = delivery / depth_dir.name
        out_depth.mkdir(parents=True, exist_ok=True)
        copied = 0
        for src_file in sorted(depth_dir.iterdir()):
            if not src_file.is_file():
                continue
            idx = _depth_frame_index(src_file.name)
            if idx is None or idx < start or idx > end:
                continue
            new_idx = idx - start
            suffix = "".join(src_file.suffixes) or src_file.suffix
            dst_name = f"tick_{new_idx:06d}{suffix}"
            shutil.copy2(src_file, out_depth / dst_name)
            copied += 1
        artifacts["depth"] = depth_dir.name if copied else "absent"

    # 6) delivery_manifest.json
    manifest = {
        "schema": SCHEMA,
        "source_session_dir": str(session.resolve()),
        "source_total_frames": total_frames or src_probe["frame_count"] or expected_count,
        "delivered_frame_count": expected_count,
        "trim_frame_range": [start, end],
        "trim_time_range_s": [t0, t1],
        "fps": fps,
        "resolution": [width, height],
        "artifacts": artifacts,
        "note": (
            "co-extensive: every delivered frame has real (interpolated) pose; "
            "trimmed to pose-covered window"
        ),
    }
    (delivery / "delivery_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a co-extensive buyer-ready delivery clip from a session."
    )
    parser.add_argument("session_dir", help="path to the recording session directory")
    args = parser.parse_args(argv)

    manifest = build_delivery_clip(args.session_dir)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
