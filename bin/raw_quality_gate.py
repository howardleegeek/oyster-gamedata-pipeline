#!/usr/bin/env python3
"""Raw capture quality gate for recorder session directories.

This gate runs before post-processing. It checks whether the raw video has
changing pixels, whether game-state positions moved, and whether mouse motion
events were captured.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

DEFAULT_FRAME_SAMPLES = 12
DOMINANT_SHA_FROZEN_RATIO = 0.70
MEAN_DIFF_FROZEN_THRESHOLD = 0.5 / 255.0
MOVING_PATH_BLOCKS_THRESHOLD = 20.0
GAME_STATE_LIVE_PATH_THRESHOLD = MOVING_PATH_BLOCKS_THRESHOLD
PASS_SCORE = 100.0
WARN_SCORE = 60.0
FAIL_SCORE = 0.0
FRAME_SCALE = "scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2"
MIN_VIDEO_BYTES = 1024


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _find_first_existing(session_dir: Path, candidates: tuple[str, ...]) -> Path | None:
    for rel_path in candidates:
        path = session_dir / rel_path
        if path.is_file():
            return path
    return None


def _find_video(session_dir: Path) -> Path | None:
    preferred_candidates = (
        "video.mp4",
        "recording.mp4",
        "main_record.mp4",
        "recordings/main_record.mp4",
        "recordings/video.mp4",
    )
    candidate_priorities = {
        session_dir / rel_path: priority for priority, rel_path in enumerate(preferred_candidates)
    }

    candidates = set(candidate_priorities)
    candidates.update(session_dir.glob("*.mp4"))
    candidates.update(session_dir.glob("**/*.mp4"))

    valid_candidates = []
    for path in candidates:
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size < MIN_VIDEO_BYTES:
            continue
        priority = candidate_priorities.get(path, len(candidate_priorities))
        valid_candidates.append((size, priority, path))
    if not valid_candidates:
        return None

    return sorted(valid_candidates, key=lambda item: (-item[0], item[1], str(item[2])))[0][2]


def _ffprobe_duration(video_path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return _finite_float_or_none(proc.stdout.strip())


def _ffmpeg_duration(video_path: Path) -> float | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(video_path), "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600.0 + int(minutes) * 60.0 + float(seconds)


def _video_duration(video_path: Path) -> float | None:
    return _ffprobe_duration(video_path) or _ffmpeg_duration(video_path)


def _sample_timestamps(duration_sec: float, sample_count: int) -> list[float]:
    if sample_count <= 0:
        return []
    if duration_sec <= 0 or not math.isfinite(duration_sec):
        return [0.0]
    if sample_count == 1:
        return [duration_sec / 2.0]
    start = min(0.05, duration_sec * 0.1)
    end = max(start, duration_sec * 0.95)
    return [float(value) for value in np.linspace(start, end, sample_count)]


def _extract_frame(video_path: Path, timestamp_sec: float) -> np.ndarray | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{timestamp_sec:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                FRAME_SCALE,
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "pipe:1",
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        with Image.open(io.BytesIO(proc.stdout)) as image:
            return np.asarray(image.convert("RGB"), dtype=np.uint8)
    except OSError:
        return None


def _mean_pairwise_abs_diff(frames: list[np.ndarray]) -> float:
    if len(frames) < 2:
        return 0.0

    diffs = []
    for idx, left in enumerate(frames[:-1]):
        left_f = left.astype(np.float32)
        for right in frames[idx + 1 :]:
            right_f = right.astype(np.float32)
            diffs.append(float(np.mean(np.abs(left_f - right_f)) / 255.0))
    return float(np.mean(diffs)) if diffs else 0.0


def analyze_video(
    video_path: Path | None, sample_count: int = DEFAULT_FRAME_SAMPLES
) -> dict[str, Any]:
    if video_path is None:
        return {
            "path": None,
            "readable": False,
            "live": False,
            "frozen": False,
            "sampled_frames": 0,
            "unique_frame_count": 0,
            "unique_frame_ratio": 0.0,
            "dominant_frame_ratio": 0.0,
            "mean_pairwise_diff": 0.0,
            "duration_sec": None,
            "reason": "video mp4 missing",
        }

    duration = _video_duration(video_path)
    if duration is None or duration <= 0:
        return {
            "path": str(video_path),
            "readable": False,
            "live": False,
            "frozen": False,
            "sampled_frames": 0,
            "unique_frame_count": 0,
            "unique_frame_ratio": 0.0,
            "dominant_frame_ratio": 0.0,
            "mean_pairwise_diff": 0.0,
            "duration_sec": duration,
            "reason": "video unreadable or duration unavailable",
        }

    frames = [
        frame
        for frame in (
            _extract_frame(video_path, ts) for ts in _sample_timestamps(duration, sample_count)
        )
        if frame is not None
    ]
    if not frames:
        return {
            "path": str(video_path),
            "readable": False,
            "live": False,
            "frozen": False,
            "sampled_frames": 0,
            "unique_frame_count": 0,
            "unique_frame_ratio": 0.0,
            "dominant_frame_ratio": 0.0,
            "mean_pairwise_diff": 0.0,
            "duration_sec": duration,
            "reason": "ffmpeg could not sample any frames",
        }

    shas = [hashlib.sha256(frame.tobytes()).hexdigest() for frame in frames]
    counts = collections.Counter(shas)
    unique_count = len(counts)
    dominant_ratio = max(counts.values()) / len(shas)
    unique_ratio = unique_count / len(shas)
    mean_diff = _mean_pairwise_abs_diff(frames)
    frozen = dominant_ratio > DOMINANT_SHA_FROZEN_RATIO or mean_diff < MEAN_DIFF_FROZEN_THRESHOLD

    return {
        "path": str(video_path),
        "readable": True,
        "live": not frozen,
        "frozen": frozen,
        "sampled_frames": len(frames),
        "unique_frame_count": unique_count,
        "unique_frame_ratio": unique_ratio,
        "dominant_frame_ratio": dominant_ratio,
        "mean_pairwise_diff": mean_diff,
        "duration_sec": duration,
        "reason": None,
    }


def _finite_float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _axis_value(payload: dict[str, Any], axis: str) -> float | None:
    aliases = {
        "x": ("x", "pos_x", "player_x"),
        "y": ("y", "pos_y", "player_y"),
        "z": ("z", "pos_z", "player_z"),
    }[axis]
    for key in aliases:
        parsed = _finite_float_or_none(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def _coords_from_mapping(
    payload: dict[str, Any], depth: int = 0
) -> tuple[float, float, float] | None:
    x = _axis_value(payload, "x")
    y = _axis_value(payload, "y")
    z = _axis_value(payload, "z")
    if x is not None and y is not None and z is not None:
        return x, y, z

    for key in ("position", "player_position", "pos"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            coords = _coords_from_mapping(nested, depth + 1)
            if coords is not None:
                return coords

    player = payload.get("player")
    if isinstance(player, dict):
        coords = _coords_from_mapping(player, depth + 1)
        if coords is not None:
            return coords

    if depth >= 1:
        return None

    for key in ("event_args", "state", "payload", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            coords = _coords_from_mapping(nested, depth + 1)
            if coords is not None:
                return coords
    return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
    except OSError:
        return []
    return rows


def analyze_game_state(game_state_path: Path | None) -> dict[str, Any]:
    if game_state_path is None or not game_state_path.is_file():
        return {
            "path": str(game_state_path) if game_state_path else None,
            "rows": 0,
            "position_count": 0,
            "path_blocks": 0.0,
            "position_std_blocks": 0.0,
            "live": False,
            "empty": True,
            "reason": "game_state.jsonl missing",
        }

    rows = _read_jsonl(game_state_path)
    positions = [coords for row in rows if (coords := _coords_from_mapping(row)) is not None]
    if not positions:
        return {
            "path": str(game_state_path),
            "rows": len(rows),
            "position_count": 0,
            "path_blocks": 0.0,
            "position_std_blocks": 0.0,
            "live": False,
            "empty": True,
            "reason": "game_state.jsonl empty or has no player positions",
        }

    path_blocks = 0.0
    for left, right in zip(positions, positions[1:]):
        path_blocks += math.dist(left, right)

    arr = np.asarray(positions, dtype=np.float64)
    std_blocks = float(np.linalg.norm(np.std(arr, axis=0))) if len(positions) > 1 else 0.0

    return {
        "path": str(game_state_path),
        "rows": len(rows),
        "position_count": len(positions),
        "path_blocks": float(path_blocks),
        "position_std_blocks": std_blocks,
        "live": path_blocks > GAME_STATE_LIVE_PATH_THRESHOLD,
        "empty": False,
        "reason": None,
    }


def _event_text(payload: dict[str, Any]) -> str:
    parts = []
    for key in ("type", "event", "kind", "action", "name", "input_type"):
        value = payload.get(key)
        if isinstance(value, str):
            parts.append(value.lower())
    return " ".join(parts)


def _has_mouse_delta_keys(payload: dict[str, Any]) -> bool:
    for key in (
        "dx",
        "dy",
        "delta_x",
        "delta_y",
        "mouse_dx",
        "mouse_dy",
        "movement_x",
        "movement_y",
    ):
        if _finite_float_or_none(payload.get(key)) not in (None, 0.0):
            return True
    return False


def _is_mouse_motion_event(payload: dict[str, Any], depth: int = 0) -> bool:
    text = _event_text(payload)
    if "mouse" in text and ("move" in text or "delta" in text or "motion" in text):
        return True
    if _has_mouse_delta_keys(payload) and ("mouse" in text or depth > 0):
        return True
    if depth >= 2:
        return False
    for key in ("event_args", "data", "payload", "input"):
        nested = payload.get(key)
        if isinstance(nested, dict) and _is_mouse_motion_event(nested, depth + 1):
            return True
    return False


def mouse_present(inputs_path: Path | None) -> bool:
    if inputs_path is None or not inputs_path.is_file():
        return False
    return any(_is_mouse_motion_event(row) for row in _read_jsonl(inputs_path))


def _metadata_registration_tier(session_dir: Path) -> str | None:
    metadata_path = session_dir / "metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    diagnostics = metadata.get("input_capture_diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    tier = diagnostics.get("registration_tier")
    return tier if isinstance(tier, str) else None


def evaluate_session(
    session_dir: Path, sample_count: int = DEFAULT_FRAME_SAMPLES
) -> dict[str, Any]:
    video = analyze_video(_find_video(session_dir), sample_count)
    game_state = analyze_game_state(
        _find_first_existing(
            session_dir, ("game_state.jsonl", "states.jsonl", "streams/states.jsonl")
        )
    )
    inputs_path = _find_first_existing(
        session_dir, ("inputs.jsonl", "actions.jsonl", "streams/actions.jsonl")
    )
    has_mouse = mouse_present(inputs_path)
    registration_tier = _metadata_registration_tier(session_dir)

    frozen_while_moving = bool(
        video["frozen"] and game_state["path_blocks"] > MOVING_PATH_BLOCKS_THRESHOLD
    )
    reasons: list[str] = []
    fail = False

    if not video["readable"]:
        fail = True
        reasons.append(str(video["reason"] or "video unreadable"))
    elif not video["live"]:
        fail = True
        reasons.append("video appears frozen")

    if game_state["empty"]:
        fail = True
        reasons.append(str(game_state["reason"] or "game_state.jsonl empty"))
    elif not game_state["live"]:
        reasons.append(
            f"game_state path length {game_state['path_blocks']:.2f} blocks "
            f"<= live threshold {GAME_STATE_LIVE_PATH_THRESHOLD:.2f}"
        )

    if frozen_while_moving:
        fail = True
        reasons.append("broken video capture while player was moving")

    if not has_mouse:
        if registration_tier == "none":
            reasons.append("mouse movement absent and metadata registration_tier is none")
        else:
            reasons.append("mouse movement absent from inputs.jsonl")

    verdict = "FAIL" if fail else "PASS"
    if verdict == "PASS" and reasons:
        verdict = "WARN"
    if verdict == "FAIL":
        score = FAIL_SCORE
    elif verdict == "WARN":
        score = WARN_SCORE
    else:
        score = PASS_SCORE

    result = {
        "verdict": verdict,
        "score": score,
        "score_percent": score,
        "score_10": round(score / 10.0, 2),
        "video_live": bool(video["live"]),
        "game_state_live": bool(game_state["live"]),
        "frozen_while_moving": frozen_while_moving,
        "video_unique_frame_ratio": round(float(video["unique_frame_ratio"]), 6),
        "game_state_path_blocks": round(float(game_state["path_blocks"]), 6),
        "mouse_present": has_mouse,
        "reasons": reasons,
        "video_sampled_frames": int(video["sampled_frames"]),
        "video_unique_frame_count": int(video["unique_frame_count"]),
        "video_dominant_frame_ratio": round(float(video["dominant_frame_ratio"]), 6),
        "video_mean_pairwise_diff": round(float(video["mean_pairwise_diff"]), 9),
        "video_duration_sec": video["duration_sec"],
        "game_state_position_count": int(game_state["position_count"]),
        "game_state_position_std_blocks": round(float(game_state["position_std_blocks"]), 6),
        "input_registration_tier": registration_tier,
    }
    return result


def write_result(session_dir: Path, result: dict[str, Any]) -> Path:
    output_path = session_dir / "raw_quality.json"
    output_path.write_text(_json_dumps(result) + "\n", encoding="utf-8")
    return output_path


def _human_report(result: dict[str, Any], output_path: Path) -> str:
    reasons = result.get("reasons") or []
    lines = [
        f"RAW QUALITY GATE: {result['verdict']}",
        f"  video_live: {result['video_live']} "
        f"(unique_ratio={result['video_unique_frame_ratio']:.3f}, "
        f"mean_diff={result['video_mean_pairwise_diff']:.6f})",
        f"  game_state_live: {result['game_state_live']} "
        f"(path_blocks={result['game_state_path_blocks']:.2f})",
        f"  frozen_while_moving: {result['frozen_while_moving']}",
        f"  mouse_present: {result['mouse_present']}",
        f"  wrote: {output_path}",
    ]
    if reasons:
        lines.append("  reasons:")
        lines.extend(f"    - {reason}" for reason in reasons)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Raw capture quality gate")
    parser.add_argument(
        "session_dir", type=Path, help="Session directory containing raw capture files"
    )
    parser.add_argument("--json", action="store_true", help="Print raw_quality.json payload")
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_FRAME_SAMPLES,
        help=f"Number of video frames to sample (default: {DEFAULT_FRAME_SAMPLES})",
    )
    args = parser.parse_args(argv)

    session_dir = args.session_dir
    if not session_dir.is_dir():
        print(f"Error: {session_dir} is not a directory", file=sys.stderr)
        return 1

    result = evaluate_session(session_dir, sample_count=max(1, args.samples))
    output_path = write_result(session_dir, result)

    if args.json:
        print(_json_dumps(result))
    else:
        print(_human_report(result, output_path))

    return 1 if result["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
