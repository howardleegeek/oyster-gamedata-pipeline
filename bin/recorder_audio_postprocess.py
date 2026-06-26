#!/usr/bin/env python3
"""
bin/recorder_audio_postprocess.py — Recorder audio post-processor.

Wraps :mod:`bin.audio_event_track` to post-process recorder-captured WAV/MP4
clips into per-frame audio event tracks beside each clip.

Pipeline (per clip):

  1.  Locate the clip's video file (``video.mp4``) inside ``--clip-dir``.
  2.  Use ``ffmpeg`` to demux the audio track into a temporary 16 kHz mono
      PCM WAV (``audio.wav``).
  3.  Call :func:`bin.audio_event_track.process_audio` on that WAV to obtain
      per-frame peak amplitude + classified event labels.
  4.  Write ``audio_events.json`` next to the input video so downstream
      vendor manifest builders can hash + reference it.

This is a NEW FILE used by ``bin/recorder_consumer_lite.py`` (or its Rust
successor) as a sub-process *after* ffmpeg finishes finalising the clip.
``recorder_consumer_lite.py`` itself is **not edited** — it only invokes::

    python3 bin/recorder_audio_postprocess.py --clip-dir <dir>

Standalone CLI (``--help``) is provided so engineers / testers can re-run
post-processing on existing clip directories without re-recording.

Spec: G260 (W31 wave). PP1 priority. ~140 lines.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure ``bin/`` is importable when this script is run directly
# (`python3 bin/recorder_audio_postprocess.py ...`).
_BIN_DIR = Path(__file__).resolve().parent
if str(_BIN_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR.parent))


def find_clip_video(clip_dir: Path) -> Path:
    """Return the path to the clip's primary video file.

    Looks for ``video.mp4`` first (PRD canonical name), falls back to any
    single ``*.mp4`` if not present.

    Args:
        clip_dir: Directory containing one finalised clip.

    Returns:
        Absolute path to the video file.

    Raises:
        FileNotFoundError: If no .mp4 is present in ``clip_dir``.
    """
    canonical = clip_dir / "video.mp4"
    if canonical.is_file():
        return canonical.resolve()
    candidates = sorted(p for p in clip_dir.glob("*.mp4") if p.is_file())
    if not candidates:
        raise FileNotFoundError(f"No video.mp4 (or *.mp4) inside clip directory: {clip_dir}")
    return candidates[0].resolve()


def extract_audio_track(
    video_path: Path,
    out_wav: Path,
    sample_rate: int = 16000,
    ffmpeg_bin: str = "ffmpeg",
) -> Path:
    """Demux audio from ``video_path`` into a 16 kHz mono PCM WAV.

    Args:
        video_path: MP4 / MKV / WAV input clip.
        out_wav: Destination WAV path.
        sample_rate: Target sample rate (default 16000 Hz, matches Whisper /
            event classifiers and keeps file small).
        ffmpeg_bin: Override ffmpeg binary (e.g. bundled ``ffmpeg.exe``).

    Returns:
        ``out_wav`` if extraction succeeded.

    Raises:
        FileNotFoundError: ffmpeg not on PATH.
        RuntimeError: ffmpeg returned a non-zero exit code.
    """
    if shutil.which(ffmpeg_bin) is None:
        raise FileNotFoundError(f"ffmpeg binary '{ffmpeg_bin}' not found on PATH")

    cmd = [
        ffmpeg_bin,
        "-y",  # overwrite if exists
        "-i",
        str(video_path),
        "-vn",  # drop video
        "-ac",
        "1",  # mono
        "-ar",
        str(sample_rate),  # downsample
        "-acodec",
        "pcm_s16le",
        str(out_wav),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (rc={proc.returncode}): {proc.stderr[-512:]}")
    if not out_wav.is_file() or out_wav.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg produced empty audio file: {out_wav}")
    return out_wav


def run_event_classifier(
    wav_path: Path,
    frame_ms: int = 50,
) -> Dict[str, Any]:
    """Invoke :func:`bin.audio_event_track.process_audio` and return its dict.

    Args:
        wav_path: Path to a mono PCM WAV.
        frame_ms: Frame length in milliseconds.

    Returns:
        Dictionary as documented in ``audio_event_track.process_audio``.
    """
    # Imported lazily so ``--help`` stays fast and unit tests can monkey-patch.
    from bin.audio_event_track import process_audio  # type: ignore

    return process_audio(str(wav_path), frame_ms=frame_ms)


def postprocess_clip(
    clip_dir: Path,
    frame_ms: int = 50,
    sample_rate: int = 16000,
    ffmpeg_bin: str = "ffmpeg",
    keep_wav: bool = False,
) -> Path:
    """Run the full audio post-process pipeline on a single clip directory.

    Args:
        clip_dir: Directory containing ``video.mp4``.
        frame_ms: Frame length forwarded to event classifier.
        sample_rate: WAV sample rate.
        ffmpeg_bin: ffmpeg binary override.
        keep_wav: If True, leave ``audio.wav`` next to the video for QA.

    Returns:
        Path to the produced ``audio_events.json``.
    """
    clip_dir = Path(clip_dir).resolve()
    if not clip_dir.is_dir():
        raise NotADirectoryError(clip_dir)

    video = find_clip_video(clip_dir)
    out_json = clip_dir / "audio_events.json"

    if keep_wav:
        wav_path = clip_dir / "audio.wav"
        extract_audio_track(video, wav_path, sample_rate, ffmpeg_bin)
        result = run_event_classifier(wav_path, frame_ms=frame_ms)
    else:
        with tempfile.TemporaryDirectory(prefix="oyster_audio_pp_") as td:
            wav_path = Path(td) / "audio.wav"
            extract_audio_track(video, wav_path, sample_rate, ffmpeg_bin)
            result = run_event_classifier(wav_path, frame_ms=frame_ms)

    payload: Dict[str, Any] = {
        "source_video": video.name,
        "frame_ms": frame_ms,
        "sample_rate": sample_rate,
        "events": result,
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_json


def main(argv: Optional[list] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Post-process a recorder clip directory into audio_events.json"
    )
    parser.add_argument("--clip-dir", required=True, help="Directory containing video.mp4")
    parser.add_argument("--frame-ms", type=int, default=50, help="Frame length (ms)")
    parser.add_argument("--sample-rate", type=int, default=16000, help="WAV sample rate")
    parser.add_argument("--ffmpeg-bin", default="ffmpeg", help="ffmpeg binary path")
    parser.add_argument("--keep-wav", action="store_true", help="Keep audio.wav for QA")
    args = parser.parse_args(argv)

    try:
        out = postprocess_clip(
            Path(args.clip_dir),
            frame_ms=args.frame_ms,
            sample_rate=args.sample_rate,
            ffmpeg_bin=args.ffmpeg_bin,
            keep_wav=args.keep_wav,
        )
    except (FileNotFoundError, NotADirectoryError, RuntimeError) as exc:
        print(f"[recorder_audio_postprocess] ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"[recorder_audio_postprocess] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
