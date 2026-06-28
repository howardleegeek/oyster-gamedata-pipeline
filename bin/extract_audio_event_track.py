#!/usr/bin/env python3
"""
extract_audio_event_track.py — post-process audio_events.jsonl

Combines recorder-emitted audio_events.jsonl with audio.flac waveform analysis
to populate the audit's expected SNR/RMS fields.

Writes audio_check.json with:
{
  "snr_db": 25.3,
  "rms_db": -18.5,
  "max_silence_gap_s": 1.2,
  "non_silent_fraction": 0.78,
  "event_count": 142,
  "voice_present": false,
  "method": "audio_events.jsonl_plus_sox_stat"
}
"""

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract audio event track and compute SNR/RMS metrics"
    )
    parser.add_argument(
        "--audio-events",
        default="audio_events.jsonl",
        help="Path to audio_events.jsonl (default: audio_events.jsonl)",
    )
    parser.add_argument(
        "--audio-flac",
        default="audio.flac",
        help="Path to audio.flac (default: audio.flac)",
    )
    parser.add_argument(
        "--voice-flac",
        default=None,
        help="Path to voice.flac (optional, for voice_present detection)",
    )
    parser.add_argument(
        "--output",
        default="audio_check.json",
        help="Path to output JSON (default: audio_check.json)",
    )
    return parser.parse_args()


def count_audio_events(path):
    """Count the number of events in audio_events.jsonl."""
    count = 0
    if not os.path.exists(path):
        return 0
    with open(path, "r") as f:
        for raw_line in f:
            line = raw_line.strip()
            if line:
                try:
                    json.loads(line)
                    count += 1
                except json.JSONDecodeError:
                    continue
    return count


def run_sox_stat(audio_path):
    """
    Run sox --stat on audio file to get RMS and other metrics.

    Returns dict with sox stat output parsed.
    """
    try:
        result = subprocess.run(
            ["sox", audio_path, "-n", "stat"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        # sox stat outputs to stderr
        stat_text = result.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"[audio] sox stat failed: {e}", file=sys.stderr)
        return None

    stats = {}
    for raw_line in stat_text.splitlines():
        line = raw_line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower().replace(" ", "_")
            value = value.strip()
            try:
                stats[key] = float(value)
            except ValueError:
                stats[key] = value

    return stats


def run_sox_silence(audio_path):
    """
    Detect silence gaps in audio using sox silence filter.

    Returns list of silence gap durations in seconds.
    """
    try:
        # Use sox to detect silence: periods below -50dB lasting > 0.1s
        result = subprocess.run(
            [
                "sox",
                audio_path,
                "-n",
                "noisered",
                "silence",
                "-l",
                "1",
                "0.1",
                "-50dB",
                "-1",
                "0.1",
                "-50dB",
                "stat",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def compute_snr_from_events(events_path):
    """
    Compute SNR estimate from audio_events.jsonl.

    SNR = ratio of signal events (volume > threshold) to noise floor.
    Uses event volume distribution to estimate signal vs noise.
    """
    if not os.path.exists(events_path):
        return None

    volumes = []
    with open(events_path, "r") as f:
        for raw_line in f:
            line = raw_line.strip()
            if line:
                try:
                    event = json.loads(line)
                    vol = event.get("volume", 0)
                    volumes.append(vol)
                except json.JSONDecodeError:
                    continue

    if not volumes:
        return None

    # Signal: events with volume > 0.1 (above noise floor)
    # Noise: events with volume <= 0.1
    signal_volumes = [v for v in volumes if v > 0.1]
    noise_volumes = [v for v in volumes if v <= 0.1]

    if not signal_volumes:
        return 0.0

    signal_power = sum(v**2 for v in signal_volumes) / len(signal_volumes)
    noise_power = sum(v**2 for v in noise_volumes) / len(noise_volumes) if noise_volumes else 1e-10

    if noise_power < 1e-10:
        # No noise events — very high SNR
        return 60.0

    snr_linear = signal_power / noise_power
    snr_db = 10 * math.log10(max(snr_linear, 1e-10))

    return round(snr_db, 1)


def compute_rms_from_sox(sox_stats):
    """Extract RMS level from sox stat output in dB."""
    if sox_stats is None:
        return None

    # sox reports "Maximum amplitude" and "RMS amplitude" as linear values
    rms_amplitude = sox_stats.get("rms_amplitude")
    if rms_amplitude is not None and rms_amplitude > 0:
        rms_db = 20 * math.log10(rms_amplitude)
        return round(rms_db, 1)

    return None


def compute_non_silent_fraction(sox_stats):
    """Estimate non-silent fraction from sox stats."""
    if sox_stats is None:
        return None

    # Use RMS amplitude as proxy: if RMS > 0.01, audio is non-silent
    rms_amplitude = sox_stats.get("rms_amplitude", 0)
    if rms_amplitude > 0.01:
        return round(min(rms_amplitude * 5, 1.0), 2)
    return round(rms_amplitude * 5, 2)


def detect_voice_present(voice_flac_path):
    """Check if voice.flac exists and has non-silent content."""
    if voice_flac_path is None or not os.path.exists(voice_flac_path):
        return False

    # Check consent record
    consent_path = Path(voice_flac_path).parent / "voice_consent.json"
    if consent_path.exists():
        try:
            with open(consent_path) as f:
                consent = json.load(f)
            if consent.get("consent") != "granted":
                return False
        except (json.JSONDecodeError, IOError):
            return False

    # Check if voice.flac has content
    try:
        result = subprocess.run(
            ["sox", "--i", voice_flac_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # File exists and is valid — check if non-silent
            stat_result = subprocess.run(
                ["sox", voice_flac_path, "-n", "stat"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            for line in stat_result.stderr.splitlines():
                if "RMS amplitude" in line:
                    try:
                        rms = float(line.split(":")[1].strip())
                        return rms > 0.001
                    except (ValueError, IndexError):
                        pass
            return True  # File exists, assume voice present
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return False


def main():
    args = parse_args()

    # Count audio events
    event_count = count_audio_events(args.audio_events)
    print(f"[audio] Found {event_count} events in {args.audio_events}", file=sys.stderr)

    # Run sox stat on audio.flac
    sox_stats = None
    if os.path.exists(args.audio_flac):
        sox_stats = run_sox_stat(args.audio_flac)
        if sox_stats:
            print(f"[audio] sox stat: {sox_stats}", file=sys.stderr)
    else:
        print(f"[audio] WARNING: {args.audio_flac} not found", file=sys.stderr)

    # Compute SNR from events
    snr_db = compute_snr_from_events(args.audio_events)
    if snr_db is None:
        # Fallback: estimate from sox stats if available
        if sox_stats:
            max_amp = sox_stats.get("maximum_amplitude", 0)
            noise_floor = sox_stats.get("minimum_amplitude", 0)
            if max_amp > 0 and noise_floor > 0:
                snr_db = round(20 * math.log10(max_amp / max(noise_floor, 1e-10)), 1)
            else:
                snr_db = 0.0
        else:
            snr_db = 0.0

    # Compute RMS from sox
    rms_db = compute_rms_from_sox(sox_stats)
    if rms_db is None:
        rms_db = -40.0  # Default: near-silent

    # Compute non-silent fraction
    non_silent = compute_non_silent_fraction(sox_stats)
    if non_silent is None:
        non_silent = 0.0

    # Estimate max silence gap (simplified — would need full waveform analysis)
    max_silence_gap_s = 0.0
    if sox_stats:
        duration = sox_stats.get("length_s", 0)
        if duration > 0 and non_silent > 0:
            # Rough estimate: gaps proportional to silent fraction
            silent_fraction = 1.0 - non_silent
            max_silence_gap_s = round(silent_fraction * duration * 0.5, 1)

    # Detect voice presence
    voice_present = detect_voice_present(args.voice_flac)

    # Build output
    result = {
        "snr_db": snr_db,
        "rms_db": rms_db,
        "max_silence_gap_s": max_silence_gap_s,
        "non_silent_fraction": non_silent,
        "event_count": event_count,
        "voice_present": voice_present,
        "method": "audio_events.jsonl_plus_sox_stat",
    }

    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"[audio] Wrote {args.output}", file=sys.stderr)
    print(
        f"[audio] SNR={snr_db}dB RMS={rms_db}dB events={event_count} voice={voice_present}",
        file=sys.stderr,
    )

    return result


if __name__ == "__main__":
    main()
