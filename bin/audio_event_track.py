#!/usr/bin/env python3
"""
audio_event_track.py — Per-frame audio peak + event-classifier.

PRD p8 audio requirement: analyse an audio file, compute per-frame peak
amplitudes, and classify each frame into an event class (footstep, ambient,
speech, music, silence, impact, etc.).

Usage:
    python3 bin/audio_event_track.py audio.wav [--frame-ms 50] [--out out.json]

Dependencies: stdlib + numpy (optional, falls back to stdlib-only mode).
"""
from __future__ import annotations

import argparse
import array
import json
import math
import os
import struct
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_numpy: Any = None


def _get_numpy() -> Any:
    """Return numpy module if available, else None."""
    global _numpy
    if _numpy is None:
        try:
            import numpy as np  # noqa: E402
            _numpy = np
        except ImportError:
            _numpy = None
    return _numpy


EVENT_CLASSES: List[str] = [
    "silence", "ambient", "footstep", "speech", "music", "impact", "noise", "unknown",
]

_DEFAULT_THRESHOLDS: Dict[str, Tuple[float, float]] = {
    "silence": (0.0, 0.02), "ambient": (0.02, 0.10), "footstep": (0.10, 0.35),
    "speech": (0.08, 0.45), "music": (0.15, 0.70), "impact": (0.50, 1.00),
    "noise": (0.05, 0.60),
}

_ZCR_RANGES: Dict[str, Tuple[float, float]] = {
    "silence": (0.0, 0.01), "ambient": (0.01, 0.15), "footstep": (0.05, 0.25),
    "speech": (0.10, 0.40), "music": (0.05, 0.35), "impact": (0.01, 0.20),
    "noise": (0.20, 0.60),
}


def load_wav(path: str) -> Tuple[List[float], int]:
    """Load a WAV file, return (samples normalised to [-1, 1], sample_rate)."""
    with wave.open(path, "rb") as wf:
        n_ch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    if sw == 3:
        samples = [struct.unpack("<i", raw[i:i+3] + b"\x00")[0] / 8388608.0
                   for i in range(0, len(raw), 3)]
    else:
        fmt = {1: "B", 2: "h", 4: "i"}[sw]
        scale = {1: 1/127.5, 2: 1/32768.0, 4: 1/2147483648.0}[sw]
        samples = [v * scale for v in array.array(fmt, raw)]
    if n_ch == 2:
        samples = [(samples[i] + samples[i+1]) * 0.5 for i in range(0, len(samples)-1, 2)]
    return samples, sr


_logger: Any = None


def _get_logger():
    global _logger
    if _logger is None:
        import logging
        _logger = logging.getLogger(__name__)
    return _logger


def load_with_numpy(path: str) -> Tuple[List[float], int]:
    """Load audio using numpy/scipy if available, else WAV fallback."""
    np = _get_numpy()
    if np is not None:
        try:
            from scipy.io import wavfile  # type: ignore[import-not-found]
            sr, data = wavfile.read(path)
            s = data.astype(np.float64)
            if s.ndim == 2:
                s = s.mean(axis=1)
            mx = np.iinfo(data.dtype).max if np.issubdtype(data.dtype, np.integer) else 1.0
            return (s / mx).tolist(), int(sr)
        except ImportError as exc:
            _get_logger().debug("scipy.io.wavfile import failed, falling back: %s", exc)
    return load_wav(path)


def compute_peak(samples: Sequence[float]) -> float:
    """Return peak (max absolute) amplitude."""
    return max((abs(s) for s in samples), default=0.0)


def compute_rms(samples: Sequence[float]) -> float:
    """Return RMS amplitude."""
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def compute_zcr(samples: Sequence[float]) -> float:
    """Return zero-crossing rate (fraction of adjacent pairs crossing zero)."""
    if len(samples) < 2:
        return 0.0
    crossings = sum(1 for i in range(len(samples) - 1) if samples[i] * samples[i+1] < 0)
    return crossings / (len(samples) - 1)


def compute_spectral_centroid(samples: Sequence[float]) -> float:
    """Approximate spectral centroid via DFT magnitude weighting (numpy only)."""
    np = _get_numpy()
    if np is None or len(samples) < 4:
        return 0.0
    try:
        arr = np.array(samples, dtype=np.float64)
        window = np.hanning(len(arr))
        spectrum = np.abs(np.fft.rfft(arr * window))
        freqs = np.fft.rfftfreq(len(arr))
        if spectrum.sum() == 0:
            return 0.0
        return float(np.sum(freqs * spectrum) / np.sum(spectrum))
    except Exception as e:
        _get_logger().debug("audio_event_track: spectral_centroid failed: %s", e)
        return 0.0


def segment_frames(
    samples: Sequence[float], sample_rate: int,
    frame_ms: int = 50, hop_ms: Optional[int] = None,
) -> List[List[float]]:
    """Split samples into overlapping frames."""
    frame_len = max(1, int(sample_rate * frame_ms / 1000))
    hop = max(1, int(sample_rate * (hop_ms or frame_ms // 2) / 1000))
    frames: List[List[float]] = []
    start = 0
    while start + frame_len <= len(samples):
        frames.append(list(samples[start:start + frame_len]))
        start += hop
    if not frames and samples:
        frames.append(list(samples))
    return frames


def classify_frame(
    peak: float, zcr: float, rms: float,
    thresholds: Optional[Dict[str, Tuple[float, float]]] = None,
    zcr_ranges: Optional[Dict[str, Tuple[float, float]]] = None,
) -> str:
    """
    Classify a frame into an event class using peak + ZCR heuristics.
    Each class scores +1 if peak is in range and +1 if ZCR is in range.
    Highest score wins; ties broken by closest peak-range centre.
    """
    thresholds = thresholds or _DEFAULT_THRESHOLDS
    zcr_ranges = zcr_ranges or _ZCR_RANGES
    scores: Dict[str, int] = {}
    peak_dists: Dict[str, float] = {}
    for cls in EVENT_CLASSES:
        if cls == "unknown":
            continue
        score = 0
        p_lo, p_hi = thresholds.get(cls, (0.0, 1.0))
        z_lo, z_hi = zcr_ranges.get(cls, (0.0, 1.0))
        if p_lo <= peak <= p_hi:
            score += 1
        if z_lo <= zcr <= z_hi:
            score += 1
        scores[cls] = score
        peak_dists[cls] = abs(peak - (p_lo + p_hi) / 2)
    best_score = max(scores.values()) if scores else 0
    candidates = [c for c, s in scores.items() if s == best_score]
    if best_score == 0:
        return "unknown"
    if len(candidates) == 1:
        return candidates[0]
    return min(candidates, key=lambda c: peak_dists.get(c, 1.0))


def process_audio(
    path: str, frame_ms: int = 50,
    hop_ms: Optional[int] = None, out_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Process an audio file and return per-frame event tracking data."""
    samples, sr = load_with_numpy(path)
    frames = segment_frames(samples, sr, frame_ms, hop_ms)
    results: List[Dict[str, Any]] = []
    for idx, frame in enumerate(frames):
        peak = compute_peak(frame)
        rms = compute_rms(frame)
        zcr = compute_zcr(frame)
        centroid = compute_spectral_centroid(frame)
        event = classify_frame(peak, zcr, rms)
        t_start = idx * (hop_ms or frame_ms // 2) / 1000.0
        results.append({
            "frame": idx, "time_s": round(t_start, 4),
            "peak": round(peak, 6), "rms": round(rms, 6),
            "zcr": round(zcr, 6), "spectral_centroid": round(centroid, 4),
            "event": event,
        })
    event_counts: Dict[str, int] = {}
    for r in results:
        event_counts[r["event"]] = event_counts.get(r["event"], 0) + 1
    output: Dict[str, Any] = {
        "source": str(path), "sample_rate": sr,
        "total_samples": len(samples),
        "duration_s": round(len(samples) / sr, 4) if sr else 0.0,
        "frame_ms": frame_ms, "hop_ms": hop_ms or frame_ms // 2,
        "num_frames": len(results), "event_summary": event_counts,
        "frames": results,
    }
    if out_path:
        out_p = Path(out_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as fh:
            json.dump(output, fh, indent=2)
    return output


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="Per-frame audio peak + event-classifier (PRD p8 audio req).",
        epilog="Event classes: silence, ambient, footstep, speech, music, impact, noise, unknown.",
    )
    parser.add_argument("audio", type=str, help="Path to audio file (WAV supported natively).")
    parser.add_argument(
        "--frame-ms", type=int, default=50, help="Frame length in ms (default: 50)."
    )
    parser.add_argument(
        "--hop-ms", type=int, default=None, help="Hop size in ms (default: frame_ms//2)."
    )
    parser.add_argument(
        "--out", type=str, default=None, help="Output JSON file path (default: stdout)."
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress summary output.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for CLI usage."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not os.path.isfile(args.audio):
        print(f"Error: file not found: {args.audio}", file=sys.stderr)
        return 1
    try:
        result = process_audio(
            path=args.audio, frame_ms=args.frame_ms,
            hop_ms=args.hop_ms, out_path=args.out,
        )
    except wave.Error as exc:
        print(f"Error: invalid WAV file: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error processing audio: {exc}", file=sys.stderr)
        return 3
    if not args.quiet:
        summary = result.get("event_summary", {})
        print(f"Processed: {args.audio}")
        print(f"  Duration : {result['duration_s']:.2f}s")
        print(f"  Frames   : {result['num_frames']}")
        print(f"  Events   : {json.dumps(summary, indent=4)}")
        if args.out:
            print(f"  Output   : {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
