#!/usr/bin/env python3
"""
audio_track_extractor.py — Extract audio from video.mp4 via ffmpeg,
validate continuity (no gaps >50ms per PRD p8), check volume/distortion,
and emit audio.flac + audio_qc.json.

Usage:
    python3 bin/audio_track_extractor.py video.mp4 [--output-dir .] [--verbose]

Exit codes:
    0 — success (audio.flac + audio_qc.json written)
    1 — general error (missing ffmpeg, bad input, etc.)
    2 — quality gate failure (gaps >50ms or distortion detected)
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

MAX_GAP_MS: float = 50.0
DEFAULT_SAMPLE_RATE: int = 44100
DEFAULT_CHANNELS: int = 2
SILENCE_THRESHOLD_DB: float = -50.0
DISTORTION_CLIP_RATIO: float = 0.01


def _get_numpy():
    """Return numpy module or None if unavailable."""
    try:
        import numpy as np  # noqa: F811

        return np
    except ImportError:
        return None


def _ensure_ffmpeg() -> bool:
    """Return True if ffmpeg and ffprobe are available on PATH."""
    for exe in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run(
                [exe, "-version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.error("%s not found on PATH", exe)
            return False
    return True


def _run_ffprobe(args: List[str]) -> str:
    """Run ffprobe with the given args and return stdout."""
    result = subprocess.run(
        ["ffprobe", "-v", "error"] + args,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _get_audio_info(audio_path: Path) -> Dict[str, Any]:
    """Return dict with duration, sample_rate, channels, codec_name."""
    raw = _run_ffprobe(
        [
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=duration,sample_rate,channels,codec_name",
            "-of",
            "json",
            str(audio_path),
        ]
    )
    stream = json.loads(raw)["streams"][0]
    return {
        "duration_seconds": float(stream.get("duration", 0)),
        "sample_rate": int(stream.get("sample_rate", DEFAULT_SAMPLE_RATE)),
        "channels": int(stream.get("channels", DEFAULT_CHANNELS)),
        "codec_name": stream.get("codec_name", "unknown"),
    }


def _decode_to_wav(audio_path: Path, wav_path: Path) -> bool:
    """Decode audio file to raw PCM WAV for analysis."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(audio_path),
        "-acodec",
        "pcm_s16le",
        "-ar",
        "44100",
        "-ac",
        "2",
        str(wav_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("ffmpeg decode failed: %s", result.stderr[:300])
        return False
    return wav_path.exists() and wav_path.stat().st_size > 44


def _read_pcm_samples(wav_path: Path) -> Optional["np.ndarray"]:
    """Read PCM samples from a WAV file, return numpy array of floats [-1,1]."""
    np = _get_numpy()
    if np is None:
        return None
    with open(wav_path, "rb") as fh:
        header = fh.read(44)
        if len(header) < 44 or header[:4] != b"RIFF":
            logger.error("Invalid WAV header")
            return None
        data = fh.read()
    return np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0


def _detect_gaps(
    samples: "np.ndarray",
    sample_rate: int,
    threshold_db: float = SILENCE_THRESHOLD_DB,
    min_gap_ms: float = MAX_GAP_MS,
) -> List[Dict[str, float]]:
    """Detect silent gaps in audio exceeding *min_gap_ms*.
    Returns list of dicts with keys: start_sec, end_sec, duration_ms."""
    np = _get_numpy()
    if np is None or samples is None:
        return []
    threshold = 10.0 ** (threshold_db / 20.0)
    min_gap_samples = int(min_gap_ms / 1000.0 * sample_rate)
    is_silent = np.abs(samples) < threshold
    gaps: List[Dict[str, float]] = []
    in_gap, gap_start = False, 0
    for i, silent in enumerate(is_silent):
        if silent and not in_gap:
            in_gap, gap_start = True, i
        elif not silent and in_gap:
            in_gap = False
            dur = i - gap_start
            if dur >= min_gap_samples:
                gaps.append(
                    {
                        "start_sec": round(gap_start / sample_rate, 4),
                        "end_sec": round(i / sample_rate, 4),
                        "duration_ms": round(dur / sample_rate * 1000, 2),
                    }
                )
    if in_gap:
        dur = len(is_silent) - gap_start
        if dur >= min_gap_samples:
            gaps.append(
                {
                    "start_sec": round(gap_start / sample_rate, 4),
                    "end_sec": round(len(is_silent) / sample_rate, 4),
                    "duration_ms": round(dur / sample_rate * 1000, 2),
                }
            )
    return gaps


def _analyze_volume_and_distortion(
    samples: "np.ndarray",
) -> Dict[str, Any]:
    """Compute RMS volume (dBFS), peak level, and clipping ratio."""
    np = _get_numpy()
    if np is None:
        return {"rms_dbfs": None, "peak_dbfs": None, "clip_ratio": None, "distortion_flag": False}
    rms = float(np.sqrt(np.mean(samples**2)))
    peak = float(np.max(np.abs(samples)))
    clip_count = int(np.sum(np.abs(samples) >= 0.999))
    clip_ratio = clip_count / len(samples) if len(samples) > 0 else 0.0
    rms_dbfs = 20.0 * np.log10(rms) if rms > 0 else -96.0
    peak_dbfs = 20.0 * np.log10(peak) if peak > 0 else -96.0
    return {
        "rms_dbfs": round(float(rms_dbfs), 2),
        "peak_dbfs": round(float(peak_dbfs), 2),
        "clip_ratio": round(clip_ratio, 6),
        "distortion_flag": clip_ratio > DISTORTION_CLIP_RATIO,
    }


def extract_and_validate(
    video_path: Path,
    output_dir: Path,
    verbose: bool = False,
) -> Tuple[int, Dict[str, Any]]:
    """Full pipeline: extract audio → validate continuity → check quality.
    Returns (exit_code, qc_report)."""
    qc: Dict[str, Any] = {
        "source_video": str(video_path),
        "status": "unknown",
        "audio_info": {},
        "continuity": {},
        "quality": {},
        "gates_passed": True,
    }
    if not video_path.is_file():
        logger.error("Video file not found: %s", video_path)
        qc["status"] = "error: video not found"
        return 1, qc
    if not _ensure_ffmpeg():
        qc["status"] = "error: ffmpeg/ffprobe missing"
        return 1, qc

    # Extract audio to FLAC
    flac_path = output_dir / "audio.flac"
    extract_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "flac",
        "-ar",
        str(DEFAULT_SAMPLE_RATE),
        "-ac",
        str(DEFAULT_CHANNELS),
        str(flac_path),
    ]
    logger.info("Extracting audio: %s", " ".join(extract_cmd))
    result = subprocess.run(extract_cmd, capture_output=True, text=True)
    if result.returncode != 0 or not flac_path.is_file():
        logger.error("ffmpeg extraction failed: %s", result.stderr[:300])
        qc["status"] = "error: extraction failed"
        return 1, qc
    logger.info("Extracted FLAC: %s (%d bytes)", flac_path, flac_path.stat().st_size)

    # Gather audio metadata
    try:
        audio_info = _get_audio_info(flac_path)
    except Exception as exc:
        logger.warning("ffprobe metadata failed: %s", exc)
        audio_info = {"error": str(exc)}
    qc["audio_info"] = audio_info

    # Decode to WAV for analysis
    np = _get_numpy()
    if np is None:
        logger.warning("numpy unavailable; skipping deep analysis")
        qc["status"] = "partial: numpy missing"
        return 0, qc

    with tempfile.TemporaryDirectory(prefix="audio_qc_") as tmpdir:
        wav_path = Path(tmpdir) / "decoded.wav"
        if not _decode_to_wav(flac_path, wav_path):
            qc["status"] = "error: decode failed"
            return 1, qc
        samples = _read_pcm_samples(wav_path)
        if samples is None:
            qc["status"] = "error: sample read failed"
            return 1, qc
        sample_rate = audio_info.get("sample_rate", DEFAULT_SAMPLE_RATE)

        # Continuity check
        gaps = _detect_gaps(samples, sample_rate)
        max_gap_ms = max((g["duration_ms"] for g in gaps), default=0.0)
        continuity_ok = max_gap_ms <= MAX_GAP_MS
        qc["continuity"] = {
            "max_gap_ms": max_gap_ms,
            "gap_count": len(gaps),
            "gaps": gaps[:20],
            "threshold_ms": MAX_GAP_MS,
            "passed": continuity_ok,
        }

        # Volume / distortion check
        quality = _analyze_volume_and_distortion(samples)
        qc["quality"] = quality

    # Gate evaluation
    gates_ok = continuity_ok and not quality.get("distortion_flag", False)
    qc["gates_passed"] = gates_ok
    qc["status"] = "ok" if gates_ok else "gate_failure"
    return (0 if gates_ok else 2), qc


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point with argparse CLI."""
    parser = argparse.ArgumentParser(
        description="Extract audio from video, validate continuity & quality.",
    )
    parser.add_argument("video", type=Path, help="Path to input video file")
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path("."),
        help="Directory for audio.flac and audio_qc.json (default: cwd)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    exit_code, qc_report = extract_and_validate(
        video_path=args.video,
        output_dir=args.output_dir,
        verbose=args.verbose,
    )
    qc_path = args.output_dir / "audio_qc.json"
    with open(qc_path, "w", encoding="utf-8") as fh:
        json.dump(qc_report, fh, indent=2, ensure_ascii=False)
    logger.info("QC report written: %s", qc_path)
    if exit_code == 0:
        print(f"OK  audio.flac + audio_qc.json written to {args.output_dir}")
    elif exit_code == 2:
        print(f"WARN  Quality gate failed — see {qc_path}", file=sys.stderr)
    else:
        print(f"ERROR Extraction failed — see {qc_path}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
