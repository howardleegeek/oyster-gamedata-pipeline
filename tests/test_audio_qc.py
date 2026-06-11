"""Tests for bin/audio_qc_extractor.py (G196).

Covers:
* silence runs > 2s detection
* clipping / saturation detection
* sample rate consistency
* sustained NPC dialogue (frequency analysis)
* BGM vs SFX energy ratio
* tarball + directory ingestion
* JSON report shape

Synthesises fixture audio (numpy) — does not require ffmpeg at runtime
for the analysis core (the extract path uses ffmpeg only when invoked
against a real .mp4). Tests that touch ffmpeg are gated by the
``ffmpeg_available`` helper.
"""
from __future__ import annotations

import json
import struct
import subprocess
import tarfile
import wave
from pathlib import Path
from typing import Tuple

import numpy as np
import pytest

# Add bin/ to path so we can import the extractor as a module.
import sys
BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))

import audio_qc_extractor as qc  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def ffmpeg_available() -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-version"], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def write_wav(path: Path, samples: np.ndarray, sr: int = 44100, channels: int = 2) -> None:
    """Write float [-1, 1] samples to a 16-bit PCM stereo WAV."""
    if samples.ndim == 1:
        samples = np.stack([samples, samples], axis=1)
    pcm = np.clip(samples, -1.0, 1.0) * 32767.0
    pcm = pcm.astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def synth_tone(freq_hz: float, dur_s: float, sr: int = 44100, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(sr * dur_s)) / sr
    return amp * np.sin(2 * np.pi * freq_hz * t)


# ---------------------------------------------------------------------------
# silence-run detection
# ---------------------------------------------------------------------------

def test_silence_run_below_threshold_passes(tmp_path):
    sr = 22050
    # 1 second of tone, 1 second of silence, 1 second of tone.
    sig = np.concatenate([
        synth_tone(440, 1.0, sr),
        np.zeros(sr),
        synth_tone(440, 1.0, sr),
    ])
    wav = tmp_path / "audio.wav"
    write_wav(wav, sig, sr=sr)
    report = qc.analyze_wav(wav)
    long_silences = [s for s in report["silence_runs"] if s["duration_ms"] > 2000]
    assert long_silences == [], f"Should not flag 1s silence, got {long_silences}"
    assert report["sustained_silence_violation"] is False


def test_silence_run_above_threshold_flagged(tmp_path):
    sr = 22050
    # 3 seconds of silence — should violate the 2 s rule.
    sig = np.concatenate([
        synth_tone(440, 0.5, sr),
        np.zeros(int(sr * 3)),
        synth_tone(440, 0.5, sr),
    ])
    wav = tmp_path / "audio.wav"
    write_wav(wav, sig, sr=sr)
    report = qc.analyze_wav(wav)
    long_silences = [s for s in report["silence_runs"] if s["duration_ms"] > 2000]
    assert long_silences, "Should detect the 3 s silence"
    assert report["sustained_silence_violation"] is True


# ---------------------------------------------------------------------------
# clipping / saturation
# ---------------------------------------------------------------------------

def test_no_clipping(tmp_path):
    sr = 22050
    sig = synth_tone(440, 2.0, sr, amp=0.3)
    wav = tmp_path / "audio.wav"
    write_wav(wav, sig, sr=sr)
    report = qc.analyze_wav(wav)
    assert report["clipping"]["clip_ratio"] < 0.001
    assert report["clipping"]["saturated"] is False


def test_clipping_detected(tmp_path):
    sr = 22050
    sig = synth_tone(440, 2.0, sr, amp=2.0)  # heavy clipping when written
    wav = tmp_path / "audio.wav"
    write_wav(wav, sig, sr=sr)
    report = qc.analyze_wav(wav)
    assert report["clipping"]["clip_ratio"] > 0.05
    assert report["clipping"]["saturated"] is True


# ---------------------------------------------------------------------------
# sample-rate consistency
# ---------------------------------------------------------------------------

def test_sample_rate_recorded(tmp_path):
    sr = 48000
    sig = synth_tone(440, 0.5, sr)
    wav = tmp_path / "audio.wav"
    write_wav(wav, sig, sr=sr)
    report = qc.analyze_wav(wav)
    assert report["sample_rate"] == sr
    assert report["sample_rate_ok"] is True  # 48k is a standard rate


def test_unusual_sample_rate_flagged(tmp_path):
    sr = 11025  # well below the 22.05 kHz min recommended
    sig = synth_tone(440, 0.5, sr)
    wav = tmp_path / "audio.wav"
    write_wav(wav, sig, sr=sr)
    report = qc.analyze_wav(wav)
    assert report["sample_rate"] == sr
    assert report["sample_rate_ok"] is False


# ---------------------------------------------------------------------------
# NPC dialogue / sustained voice frequency
# ---------------------------------------------------------------------------

def test_pure_tone_not_flagged_as_dialogue(tmp_path):
    # A pure 440 Hz tone is in the musical range, not dialogue.
    sr = 22050
    sig = synth_tone(440, 5.0, sr, amp=0.4)
    wav = tmp_path / "audio.wav"
    write_wav(wav, sig, sr=sr)
    report = qc.analyze_wav(wav)
    assert report["dialogue"]["sustained_dialogue"] is False


def test_sustained_voice_band_flagged(tmp_path):
    # Synthesise a chord of voice-band frequencies (300-3000 Hz) for >50% of
    # the audio — this is the heuristic for NPC dialogue spam.
    sr = 22050
    dur = 6.0
    sig = (
        synth_tone(500, dur, sr, amp=0.2)
        + synth_tone(1200, dur, sr, amp=0.2)
        + synth_tone(2400, dur, sr, amp=0.2)
    )
    wav = tmp_path / "audio.wav"
    write_wav(wav, sig, sr=sr)
    report = qc.analyze_wav(wav)
    assert report["dialogue"]["voice_band_ratio"] > 0.5
    assert report["dialogue"]["sustained_dialogue"] is True


# ---------------------------------------------------------------------------
# BGM vs SFX ratio
# ---------------------------------------------------------------------------

def test_bgm_sfx_balance_reported(tmp_path):
    # Steady background tone (BGM) + occasional impulses (SFX).
    sr = 22050
    dur = 5.0
    bgm = synth_tone(220, dur, sr, amp=0.1)
    sfx = np.zeros_like(bgm)
    impulse_positions = np.linspace(0, len(sfx) - 1000, 5).astype(int)
    for p in impulse_positions:
        sfx[p : p + 1000] += np.random.default_rng(0).uniform(-0.6, 0.6, 1000)
    sig = bgm + sfx
    wav = tmp_path / "audio.wav"
    write_wav(wav, sig, sr=sr)
    report = qc.analyze_wav(wav)
    assert "bgm_energy_ratio" in report["mix"]
    assert "sfx_event_count" in report["mix"]
    assert report["mix"]["sfx_event_count"] >= 1


# ---------------------------------------------------------------------------
# directory ingestion
# ---------------------------------------------------------------------------

def test_directory_ingestion_with_wav(tmp_path):
    """When a directory contains a pre-extracted audio.wav, the extractor
    should analyse it directly without invoking ffmpeg."""
    sr = 22050
    sig = synth_tone(440, 1.0, sr)
    write_wav(tmp_path / "audio.wav", sig, sr=sr)
    report = qc.qc_clip(tmp_path)
    assert report["status"] in {"ok", "warn"}
    assert "silence_runs" in report
    assert (tmp_path / "audio_qc_report.json").is_file()


# ---------------------------------------------------------------------------
# tarball ingestion (sample tarball)
# ---------------------------------------------------------------------------

SAMPLE_TAR = Path(__file__).resolve().parents[1] / "samples" / "buyer-spec-v1-rc1.tar.gz"


@pytest.mark.skipif(  # skip when ffmpeg or sample tarball not present
    not (ffmpeg_available() and SAMPLE_TAR.is_file()),
    reason="ffmpeg or sample tarball missing",
)
def test_tarball_ingestion_no_audio_stream(tmp_path):
    """The shipped sample tarball is a stub bundle with a video-only
    track. The extractor must surface this as a structured 'no_audio_stream'
    violation rather than crash."""
    report = qc.qc_clip(SAMPLE_TAR, scratch_dir=tmp_path)
    assert report["status"] == "fail"
    assert "no_audio_stream" in report["violations"]


def test_tarball_ingestion_with_audio(tmp_path):
    """Build a minimal tarball that contains audio.wav and verify the
    extractor analyses it correctly."""
    sr = 22050
    sig = synth_tone(440, 1.0, sr, amp=0.3)
    audio_dir = tmp_path / "clip-test"
    audio_dir.mkdir()
    write_wav(audio_dir / "audio.wav", sig, sr=sr)
    tar_path = tmp_path / "clip-test.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(audio_dir, arcname="clip-test")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    report = qc.qc_clip(tar_path, scratch_dir=scratch)
    assert "silence_runs" in report
    assert report["sample_rate"] == sr
    assert isinstance(report["silence_runs"], list)


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

def test_cli_directory(tmp_path, capsys):
    sr = 22050
    write_wav(tmp_path / "audio.wav", synth_tone(440, 1.0, sr), sr=sr)
    rc = qc.main([str(tmp_path)])
    captured = capsys.readouterr()
    assert rc in (0, 2), f"unexpected rc={rc}, stdout={captured.out}"
    assert (tmp_path / "audio_qc_report.json").is_file()
