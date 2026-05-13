#!/usr/bin/env python3
"""audio_qc_extractor.py — G196 audio QC extractor for batch ingest.

Ingests a single clip (either a ``.tar.gz`` tarball or a directory holding
``video.mp4`` / ``audio.wav`` / ``audio.flac``), extracts the audio track if
needed, then runs a suite of audio quality checks tailored to the buyer-spec
PRD (``docs/PRD.md`` §3.1 & §6).

Checks performed
----------------
1. **silence runs > 2 s** — sustained quiet periods (vendor mute / dead BGM).
2. **clipping / saturation** — fraction of samples at digital max
   (``≥ 0.999``); flagged when over 1 %.
3. **sample-rate consistency** — confirms 22.05 / 44.1 / 48 / 96 kHz; flags
   exotic / under-sampled streams.
4. **sustained NPC dialogue** — energy ratio inside the voice band
   (300–3 400 Hz) versus the full spectrum; flagged when dialogue dominates
   the clip (``> 0.5``) — proxy for NPC dialogue spam.
5. **BGM vs SFX balance** — DC-removed energy of low-frequency BGM band
   (60–500 Hz) vs transient SFX impulses (RMS spikes above
   ``5 × baseline``).

Outputs
-------
Writes ``audio_qc_report.json`` next to the input clip with the shape::

    {
      "clip_id": "...",
      "status": "ok" | "warn" | "fail",
      "duration_s": ...,
      "sample_rate": 44100,
      "sample_rate_ok": true,
      "channels": 2,
      "silence_runs": [{"start_s": ..., "end_s": ..., "duration_ms": ...}],
      "sustained_silence_violation": false,
      "clipping": {"clip_ratio": 0.0, "peak_dbfs": -1.2, "saturated": false},
      "dialogue": {"voice_band_ratio": 0.31, "sustained_dialogue": false},
      "mix": {"bgm_energy_ratio": 0.18, "sfx_event_count": 5},
      "violations": [...]
    }

Exit codes
----------
* ``0`` — all checks passed (report written; ``status: ok``)
* ``2`` — one or more quality gates failed (report written; ``status: fail``)
* ``1`` — non-recoverable error (missing ffmpeg, bad input, etc.)

CLI
---
::

    python3 bin/audio_qc_extractor.py path/to/clip-00042_v1.tar.gz
    python3 bin/audio_qc_extractor.py path/to/clip_directory
    python3 bin/audio_qc_extractor.py path/to/clip_directory -o report.json
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tarfile
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("audio_qc_extractor")

# ---------------------------------------------------------------------------
# constants — thresholds derive from PRD §3.1 "声音必须存在 + 连续 + 无外界
# 噪声 + 无 NPC 对话刷屏". Numbers conservative; tighten in lint v3 if needed.
# ---------------------------------------------------------------------------
SILENCE_THRESHOLD_DB = -50.0
SILENCE_MIN_RUN_MS = 2000.0           # > 2 s sustained silence violates spec
CLIPPING_AMP = 0.999
CLIPPING_RATIO_FLAG = 0.01            # > 1 % saturated samples
STANDARD_SAMPLE_RATES = {22050, 32000, 44100, 48000, 96000}
VOICE_BAND_HZ = (300.0, 3400.0)       # telephony voice band
DIALOGUE_RATIO_FLAG = 0.5             # voice energy / total energy
BGM_BAND_HZ = (60.0, 500.0)
SFX_RMS_MULTIPLIER = 3.0              # SFX impulse if RMS frame > N × median
FRAME_MS = 50.0                       # analysis frame size


# ---------------------------------------------------------------------------
# light dataclasses
# ---------------------------------------------------------------------------

@dataclass
class QCReport:
    clip_id: str
    status: str = "ok"
    duration_s: float = 0.0
    sample_rate: int = 0
    sample_rate_ok: bool = False
    channels: int = 0
    silence_runs: List[Dict[str, float]] = field(default_factory=list)
    sustained_silence_violation: bool = False
    clipping: Dict[str, Any] = field(default_factory=dict)
    dialogue: Dict[str, Any] = field(default_factory=dict)
    mix: Dict[str, Any] = field(default_factory=dict)
    violations: List[str] = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "status": self.status,
            "source": self.source,
            "duration_s": round(self.duration_s, 3),
            "sample_rate": self.sample_rate,
            "sample_rate_ok": self.sample_rate_ok,
            "channels": self.channels,
            "silence_runs": self.silence_runs,
            "sustained_silence_violation": self.sustained_silence_violation,
            "clipping": self.clipping,
            "dialogue": self.dialogue,
            "mix": self.mix,
            "violations": self.violations,
        }


# ---------------------------------------------------------------------------
# dependency probing
# ---------------------------------------------------------------------------

def _get_numpy():
    try:
        import numpy as np
        return np
    except ImportError:  # pragma: no cover — pyproject lists numpy
        logger.error("numpy is required for audio QC analysis")
        return None


def _ffmpeg_ok() -> bool:
    for exe in ("ffmpeg", "ffprobe"):
        if shutil.which(exe) is None:
            return False
    return True


# ---------------------------------------------------------------------------
# wav decoding (no ffmpeg dep for the analysis core)
# ---------------------------------------------------------------------------

def _read_wav(path: Path):
    """Return (samples_mono_float64, sample_rate, channels)."""
    np = _get_numpy()
    if np is None:
        raise RuntimeError("numpy required")
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        n = wf.getnframes()
        raw = wf.readframes(n)
    if sampwidth == 2:
        dtype = "<i2"
        norm = 32768.0
    elif sampwidth == 1:
        dtype = "u1"
        norm = 128.0
    elif sampwidth == 4:
        dtype = "<i4"
        norm = 2147483648.0
    else:
        raise ValueError(f"unsupported sample width: {sampwidth}")
    arr = np.frombuffer(raw, dtype=dtype).astype(np.float64)
    if sampwidth == 1:
        arr -= norm
    arr /= norm
    if channels > 1:
        arr = arr.reshape(-1, channels).mean(axis=1)
    return arr, sr, channels


# ---------------------------------------------------------------------------
# audio extraction (only used when input is video.mp4)
# ---------------------------------------------------------------------------

def _has_audio_stream(video: Path) -> bool:
    """Use ffprobe to check whether ``video`` has any audio streams."""
    if not _ffmpeg_ok():
        return False
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        str(video),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode == 0 and bool(res.stdout.strip())


def _extract_audio_with_ffmpeg(video: Path, out_wav: Path, target_sr: int = 22050) -> bool:
    """Pull the audio track out of a video as mono PCM WAV. Returns True on
    success (and out_wav exists)."""
    if not _ffmpeg_ok():
        logger.error("ffmpeg/ffprobe not available")
        return False
    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-vn", "-acodec", "pcm_s16le",
        "-ar", str(target_sr), "-ac", "1",
        str(out_wav),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error("ffmpeg failed: %s", res.stderr[-400:])
        return False
    return out_wav.is_file() and out_wav.stat().st_size > 44


# ---------------------------------------------------------------------------
# silence-run detection
# ---------------------------------------------------------------------------

def _detect_silence_runs(samples, sr: int) -> Tuple[List[Dict[str, float]], bool]:
    np = _get_numpy()
    if np is None or len(samples) == 0:
        return [], False
    threshold = 10.0 ** (SILENCE_THRESHOLD_DB / 20.0)
    is_silent = np.abs(samples) < threshold
    min_run = int(SILENCE_MIN_RUN_MS / 1000.0 * sr)
    runs: List[Dict[str, float]] = []
    in_run = False
    start_idx = 0
    for i, s in enumerate(is_silent):
        if s and not in_run:
            in_run = True
            start_idx = i
        elif not s and in_run:
            in_run = False
            dur = i - start_idx
            if dur >= min_run:
                runs.append({
                    "start_s": round(start_idx / sr, 3),
                    "end_s": round(i / sr, 3),
                    "duration_ms": round(dur / sr * 1000.0, 1),
                })
    if in_run:
        dur = len(is_silent) - start_idx
        if dur >= min_run:
            runs.append({
                "start_s": round(start_idx / sr, 3),
                "end_s": round(len(is_silent) / sr, 3),
                "duration_ms": round(dur / sr * 1000.0, 1),
            })
    return runs, len(runs) > 0


# ---------------------------------------------------------------------------
# clipping / saturation
# ---------------------------------------------------------------------------

def _analyse_clipping(samples) -> Dict[str, Any]:
    np = _get_numpy()
    if np is None or len(samples) == 0:
        return {"clip_ratio": 0.0, "peak_dbfs": -96.0, "saturated": False}
    abs_s = np.abs(samples)
    clip_count = int(np.sum(abs_s >= CLIPPING_AMP))
    clip_ratio = clip_count / len(samples)
    peak = float(np.max(abs_s)) or 1e-9
    peak_dbfs = 20.0 * np.log10(min(peak, 1.0))
    return {
        "clip_ratio": round(clip_ratio, 6),
        "peak_dbfs": round(peak_dbfs, 2),
        "saturated": clip_ratio > CLIPPING_RATIO_FLAG,
    }


# ---------------------------------------------------------------------------
# frequency-band energy via FFT (numpy-only — keep scipy optional)
# ---------------------------------------------------------------------------

def _band_energy_ratio(samples, sr: int, band: Tuple[float, float]) -> float:
    np = _get_numpy()
    if np is None or len(samples) < 64:
        return 0.0
    n = len(samples)
    # Use rFFT for real signal; window with Hann to reduce leakage.
    win = np.hanning(n)
    spec = np.fft.rfft(samples * win)
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    power = (np.abs(spec) ** 2)
    total = float(np.sum(power)) + 1e-12
    band_mask = (freqs >= band[0]) & (freqs <= band[1])
    band_power = float(np.sum(power[band_mask]))
    return band_power / total


def _spectral_spread(samples, sr: int, band: Tuple[float, float]) -> float:
    """Return a [0, 1] measure of how spread-out spectral energy is inside
    ``band``. Pure / dominant tones → near 0; broadband or multi-tone
    voice-like content → 0.5+.

    Computed as ``1 - peak_bin_power / band_power``. A single pure tone
    concentrates energy in one bin (after Hann smearing, dominantly in
    the centre bin) → spread is small. A chord of multiple voice-band
    formants distributes energy across many bins → spread approaches 1.
    """
    np = _get_numpy()
    if np is None or len(samples) < 64:
        return 0.0
    win = np.hanning(len(samples))
    spec = np.abs(np.fft.rfft(samples * win)) ** 2
    freqs = np.fft.rfftfreq(len(samples), d=1.0 / sr)
    mask = (freqs >= band[0]) & (freqs <= band[1])
    if not np.any(mask):
        return 0.0
    band_spec = spec[mask]
    band_power = float(np.sum(band_spec)) + 1e-12
    peak_power = float(np.max(band_spec))
    return 1.0 - peak_power / band_power


def _voice_band_dominance(samples, sr: int) -> float:
    """Fraction of frames where voice-band energy > 0.4 of total AND the
    energy is spectrally spread (i.e. not a pure tone).

    A pure 440 Hz tone has all energy in one FFT bin; spread → 0, so it
    won't be flagged as dialogue. Real voice has formants spread over
    300–3400 Hz; spread → 0.5+.
    """
    np = _get_numpy()
    if np is None:
        return 0.0
    frame = int(sr * FRAME_MS / 1000.0)
    if frame < 64 or len(samples) < frame * 2:
        # Single-shot ratio for short clips.
        ratio = _band_energy_ratio(samples, sr, VOICE_BAND_HZ)
        spread = _spectral_spread(samples, sr, VOICE_BAND_HZ)
        return ratio if spread > 0.5 else 0.0
    n_frames = len(samples) // frame
    hits = 0
    for i in range(n_frames):
        chunk = samples[i * frame : (i + 1) * frame]
        ratio = _band_energy_ratio(chunk, sr, VOICE_BAND_HZ)
        spread = _spectral_spread(chunk, sr, VOICE_BAND_HZ)
        # Voice-like: high band energy ratio AND no single bin dominates.
        if ratio > 0.4 and spread > 0.5:
            hits += 1
    return hits / n_frames


# ---------------------------------------------------------------------------
# BGM vs SFX
# ---------------------------------------------------------------------------

def _bgm_vs_sfx(samples, sr: int) -> Dict[str, Any]:
    """BGM = sustained low-mid energy. SFX = transient bursts.
    Returns {bgm_energy_ratio, sfx_event_count}."""
    np = _get_numpy()
    if np is None or len(samples) == 0:
        return {"bgm_energy_ratio": 0.0, "sfx_event_count": 0}
    bgm_ratio = _band_energy_ratio(samples, sr, BGM_BAND_HZ)
    frame = max(1, int(sr * FRAME_MS / 1000.0))
    n_frames = len(samples) // frame
    if n_frames < 4:
        return {
            "bgm_energy_ratio": round(bgm_ratio, 4),
            "sfx_event_count": 0,
        }
    rms = np.array([
        float(np.sqrt(np.mean(samples[i * frame : (i + 1) * frame] ** 2)))
        for i in range(n_frames)
    ])
    baseline = float(np.median(rms)) + 1e-9
    impulses = rms > baseline * SFX_RMS_MULTIPLIER
    # Count islands of consecutive impulses as a single event.
    events = 0
    prev = False
    for x in impulses:
        if x and not prev:
            events += 1
        prev = x
    return {
        "bgm_energy_ratio": round(bgm_ratio, 4),
        "sfx_event_count": int(events),
    }


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def _default_report_path(clip_path: Path, data_dir: Path) -> Path:
    """Choose where to write ``audio_qc_report.json``.

    * For directory inputs, the report lands inside the directory.
    * For tarball inputs, the report lands inside the extracted *data*
      directory (i.e. inside the scratch dir). This prevents the extractor
      from writing into the source tree (e.g. ``samples/``) when the user
      gave us a read-only tarball.
    """
    if clip_path.is_dir():
        return clip_path / "audio_qc_report.json"
    return data_dir / "audio_qc_report.json"


def analyze_wav(wav_path: Path, clip_id: str = "") -> Dict[str, Any]:
    """Run the full audio QC suite on a pre-extracted WAV file.

    Returns a dict matching ``QCReport.to_dict()``.
    """
    samples, sr, channels = _read_wav(wav_path)
    duration_s = len(samples) / sr if sr else 0.0
    report = QCReport(clip_id=clip_id or wav_path.stem)
    report.duration_s = duration_s
    report.sample_rate = sr
    report.sample_rate_ok = sr in STANDARD_SAMPLE_RATES and sr >= 22050
    report.channels = channels
    report.source = str(wav_path)

    runs, _ = _detect_silence_runs(samples, sr)
    report.silence_runs = runs
    report.sustained_silence_violation = any(
        r["duration_ms"] > SILENCE_MIN_RUN_MS for r in runs
    )

    report.clipping = _analyse_clipping(samples)
    voice_band_ratio = _voice_band_dominance(samples, sr)
    report.dialogue = {
        "voice_band_ratio": round(voice_band_ratio, 4),
        "sustained_dialogue": voice_band_ratio > DIALOGUE_RATIO_FLAG,
    }
    report.mix = _bgm_vs_sfx(samples, sr)

    # Compose violations + final status.
    if report.sustained_silence_violation:
        report.violations.append("sustained_silence_gt_2s")
    if report.clipping.get("saturated"):
        report.violations.append("clipping_over_1pct")
    if not report.sample_rate_ok:
        report.violations.append(f"unusual_sample_rate:{sr}")
    if report.dialogue.get("sustained_dialogue"):
        report.violations.append("sustained_npc_dialogue")
    report.status = "fail" if report.violations else "ok"
    return report.to_dict()


def qc_clip(
    clip_path: Path,
    output: Optional[Path] = None,
    scratch_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run audio QC on a clip (tarball or directory).

    * If ``clip_path`` is a directory containing ``audio.wav`` / ``audio.flac``,
      analyse it directly.
    * If ``clip_path`` is a directory containing ``video.mp4``, extract audio
      via ffmpeg first.
    * If ``clip_path`` is a ``.tar.gz``, extract to ``scratch_dir`` (or a
      temporary directory) and then proceed as above.

    Writes ``audio_qc_report.json`` next to the input (or at ``output``).
    """
    clip_path = Path(clip_path)
    if not clip_path.exists():
        raise FileNotFoundError(clip_path)

    workdir = scratch_dir or Path(tempfile.mkdtemp(prefix="audio_qc_"))
    workdir.mkdir(parents=True, exist_ok=True)
    cleanup = scratch_dir is None

    try:
        # 1. resolve to a working directory containing audio.
        if clip_path.is_file() and clip_path.suffixes[-2:] == [".tar", ".gz"]:
            with tarfile.open(clip_path, "r:gz") as tar:
                # filter param available 3.12+; guard for older interpreters.
                try:
                    tar.extractall(workdir, filter="data")
                except TypeError:
                    tar.extractall(workdir)
            data_dir = workdir
            # If the tarball contained a single root directory, descend into it.
            children = [p for p in workdir.iterdir() if p.is_dir()]
            if len(children) == 1 and not (workdir / "video.mp4").exists():
                data_dir = children[0]
        elif clip_path.is_dir():
            data_dir = clip_path
        else:
            raise ValueError(
                f"Unsupported input: {clip_path} (expect dir or .tar.gz)",
            )

        # 2. find or extract a wav file for analysis.
        wav_path = (data_dir / "audio.wav")
        if not wav_path.is_file():
            flac_path = data_dir / "audio.flac"
            video_path = data_dir / "video.mp4"
            extracted = workdir / "extracted.wav"
            if flac_path.is_file():
                if not _extract_audio_with_ffmpeg(flac_path, extracted):
                    raise RuntimeError("failed to decode audio.flac")
                wav_path = extracted
            elif video_path.is_file():
                # A clip whose video.mp4 has NO audio stream is itself a
                # PRD violation — emit a structured report instead of
                # exploding, so batch pipelines keep going.
                if not _has_audio_stream(video_path):
                    report = QCReport(clip_id=clip_path.name).to_dict()
                    report["status"] = "fail"
                    report["violations"] = ["no_audio_stream"]
                    report["source"] = str(video_path)
                    out_json = output or _default_report_path(clip_path, data_dir)
                    out_json.parent.mkdir(parents=True, exist_ok=True)
                    out_json.write_text(
                        json.dumps(report, indent=2), encoding="utf-8",
                    )
                    report["report_path"] = str(out_json)
                    return report
                if not _extract_audio_with_ffmpeg(video_path, extracted):
                    raise RuntimeError(
                        "failed to extract audio from video.mp4 — "
                        "is ffmpeg installed?"
                    )
                wav_path = extracted
            else:
                raise FileNotFoundError(
                    f"no audio.wav / audio.flac / video.mp4 inside {data_dir}",
                )

        # 3. analyse.
        report = analyze_wav(wav_path, clip_id=clip_path.name)

        # 4. emit JSON next to the input.
        out_json = output or _default_report_path(clip_path, data_dir)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["report_path"] = str(out_json)
        return report
    finally:
        if cleanup:
            shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audio_qc_extractor",
        description=(
            "Run G196 audio QC on a clip (tarball or directory). "
            "Emits audio_qc_report.json."
        ),
    )
    parser.add_argument(
        "clip", type=Path,
        help="Path to a clip .tar.gz or a clip directory.",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Explicit output path for the JSON report.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose logging.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    try:
        report = qc_clip(args.clip, output=args.output)
    except FileNotFoundError as exc:
        logger.error("Input not found: %s", exc)
        return 1
    except Exception as exc:
        logger.exception("audio QC failed: %s", exc)
        return 1

    print(f"audio_qc_status: {report['status']}  "
          f"violations: {len(report['violations'])}  "
          f"silence_runs: {len(report['silence_runs'])}  "
          f"sr: {report['sample_rate']}")
    if report["violations"]:
        for v in report["violations"]:
            print(f"  - {v}")
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
