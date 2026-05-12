#!/usr/bin/env python3
"""
audio_continuity_check.py — Audio continuity self-check for PRD §3.1

Requirements:
1. ffprobe -show_streams session_dir/recording.mp4 — verify audio stream exists (AAC)
2. Sample audio at 1Hz, compute dBFS per sample
3. Flag if > 2s contiguous below -60 dB (silence) → suggests dropout
4. Output JSON to session_dir/audio_check.json with overall pass/fail

Usage:
    python3 bin/audio_continuity_check.py /path/to/session_dir

Exit codes:
    0 — success, audio continuity check passed
    1 — audio continuity check failed (silence gaps > 2s detected)
    2 — error (missing ffprobe, no audio stream, etc.)
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SILENCE_THRESHOLD_DB = -60.0  # dBFS threshold for silence
MAX_CONTIGUOUS_SILENCE_SECONDS = 2.0  # Maximum allowed contiguous silence
SAMPLE_RATE_HZ = 1  # Sample at 1Hz for dBFS analysis


def _ensure_ffprobe() -> bool:
    """Return True if ffprobe is available on PATH."""
    try:
        subprocess.run(
            ["ffprobe", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.error("ffprobe not found on PATH")
        return False


def _run_ffprobe(args: List[str]) -> str:
    """Run ffprobe with the given args and return stdout."""
    result = subprocess.run(
        ["ffprobe", "-v", "error"] + args,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def check_audio_stream_exists(video_path: Path) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Check if audio stream exists and is AAC codec.
    
    Returns:
        Tuple of (exists: bool, stream_info: dict or None)
    """
    try:
        raw = _run_ffprobe([
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,codec_type,sample_rate,channels,duration",
            "-of", "json",
            str(video_path),
        ])
        data = json.loads(raw)
        
        if not data.get("streams"):
            return False, None
            
        stream = data["streams"][0]
        codec_name = stream.get("codec_name", "").lower()
        
        # Check if audio stream exists and is AAC
        exists = stream.get("codec_type") == "audio" and codec_name == "aac"
        return exists, stream if exists else None
        
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error(f"Failed to probe audio stream: {e}")
        return False, None


def extract_audio_samples(video_path: Path) -> Optional[List[float]]:
    """
    Extract audio samples at 1Hz and compute dBFS for each second.
    
    Returns:
        List of dBFS values for each second, or None on error.
    """
    try:
        # First get audio duration
        raw = _run_ffprobe([
            "-select_streams", "a:0",
            "-show_entries", "stream=duration",
            "-of", "json",
            str(video_path),
        ])
        data = json.loads(raw)
        duration = float(data["streams"][0].get("duration", 0))
        
        if duration <= 0:
            logger.error(f"Invalid audio duration: {duration}")
            return None
            
        # Create temp file for raw audio extraction
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            
        try:
            # Extract audio to raw PCM at 1Hz sampling
            # We'll extract at original sample rate first, then analyze
            cmd = [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-ac", "1",  # Convert to mono for simplicity
                "-ar", "44100",  # Standard sample rate
                "-acodec", "pcm_s16le",
                str(tmp_path),
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
            )
            
            if result.returncode != 0:
                logger.error(f"ffmpeg extraction failed: {result.stderr[:300]}")
                return None
            
            # Now analyze the WAV file
            # For simplicity, we'll read the WAV file and compute RMS per second
            import wave
            import struct
            
            with wave.open(str(tmp_path), 'rb') as wav:
                n_channels = wav.getnchannels()
                sample_width = wav.getsampwidth()
                frame_rate = wav.getframerate()
                n_frames = wav.getnframes()
                
                # Read all frames
                frames = wav.readframes(n_frames)
                
                # Convert bytes to samples based on sample width
                if sample_width == 2:  # 16-bit
                    fmt = f"{n_frames * n_channels}h"
                    samples = struct.unpack(fmt, frames)
                elif sample_width == 4:  # 32-bit
                    fmt = f"{n_frames * n_channels}i"
                    samples = struct.unpack(fmt, frames)
                else:
                    logger.error(f"Unsupported sample width: {sample_width}")
                    return None
                
                # Convert to mono if stereo
                if n_channels == 2:
                    mono_samples = []
                    for i in range(0, len(samples), 2):
                        mono_samples.append((samples[i] + samples[i+1]) / 2)
                    samples = mono_samples
                
                # Calculate dBFS for each second
                samples_per_second = frame_rate
                dbfs_values = []
                
                for second in range(int(duration)):
                    start_idx = second * samples_per_second
                    end_idx = min((second + 1) * samples_per_second, len(samples))
                    
                    if start_idx >= len(samples):
                        break
                        
                    segment = samples[start_idx:end_idx]
                    
                    if not segment:
                        dbfs_values.append(-float('inf'))  # No audio
                        continue
                    
                    # Calculate RMS
                    sum_squares = sum(s * s for s in segment)
                    rms = math.sqrt(sum_squares / len(segment))
                    
                    # Convert to dBFS (16-bit signed max = 32767)
                    if rms > 0:
                        dbfs = 20 * math.log10(rms / 32767.0)
                    else:
                        dbfs = -float('inf')
                    
                    dbfs_values.append(dbfs)
                
                return dbfs_values
                
        finally:
            # Clean up temp file
            if tmp_path.exists():
                tmp_path.unlink()
                
    except Exception as e:
        logger.error(f"Error extracting audio samples: {e}")
        return None


def analyze_audio_continuity(dbfs_values: List[float]) -> Dict[str, Any]:
    """
    Analyze dBFS values for continuity issues.
    
    Returns:
        Dictionary with analysis results including silent gaps.
    """
    if not dbfs_values:
        return {
            "pass": False,
            "reason": "No audio samples to analyze",
            "silent_gaps": [],
            "max_contiguous_silence": 0.0,
            "total_silence_seconds": 0.0,
        }
    
    silent_gaps = []
    current_gap_start = None
    total_silence_seconds = 0.0
    
    for i, dbfs in enumerate(dbfs_values):
        is_silent = dbfs < SILENCE_THRESHOLD_DB
        
        if is_silent:
            total_silence_seconds += 1.0
            if current_gap_start is None:
                current_gap_start = i
        else:
            if current_gap_start is not None:
                gap_duration = i - current_gap_start
                if gap_duration > 0:
                    silent_gaps.append({
                        "start_second": current_gap_start,
                        "end_second": i - 1,
                        "duration_seconds": gap_duration,
                    })
                current_gap_start = None
    
    # Check for gap at the end
    if current_gap_start is not None:
        gap_duration = len(dbfs_values) - current_gap_start
        if gap_duration > 0:
            silent_gaps.append({
                "start_second": current_gap_start,
                "end_second": len(dbfs_values) - 1,
                "duration_seconds": gap_duration,
            })
    
    # Find problematic gaps (> 2 seconds)
    problematic_gaps = [gap for gap in silent_gaps if gap["duration_seconds"] > MAX_CONTIGUOUS_SILENCE_SECONDS]
    max_gap_duration = max((gap["duration_seconds"] for gap in silent_gaps), default=0.0)
    
    return {
        "pass": len(problematic_gaps) == 0,
        "reason": "No problematic silent gaps" if len(problematic_gaps) == 0 else f"Found {len(problematic_gaps)} silent gaps > {MAX_CONTIGUOUS_SILENCE_SECONDS}s",
        "silent_gaps": silent_gaps,
        "problematic_gaps": problematic_gaps,
        "max_contiguous_silence": max_gap_duration,
        "total_silence_seconds": total_silence_seconds,
        "threshold_dbfs": SILENCE_THRESHOLD_DB,
        "max_allowed_gap_seconds": MAX_CONTIGUOUS_SILENCE_SECONDS,
        "sample_count": len(dbfs_values),
        "dbfs_summary": {
            "min": min(dbfs_values) if dbfs_values else None,
            "max": max(dbfs_values) if dbfs_values else None,
            "avg": sum(dbfs_values) / len(dbfs_values) if dbfs_values else None,
        }
    }


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Audio continuity self-check for PRD §3.1"
    )
    parser.add_argument(
        "session_dir",
        type=Path,
        help="Path to session directory containing recording.mp4"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )
    args = parser.parse_args()
    
    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    
    # Check session directory exists
    session_dir = args.session_dir
    if not session_dir.exists():
        logger.error(f"Session directory does not exist: {session_dir}")
        return 2
    
    # Check for recording.mp4
    video_path = session_dir / "recording.mp4"
    if not video_path.exists():
        logger.error(f"Video file not found: {video_path}")
        return 2
    
    # Check ffprobe availability
    if not _ensure_ffprobe():
        logger.error("ffprobe not available. Please install ffmpeg/ffprobe.")
        return 2
    
    logger.info(f"Checking audio continuity for: {video_path}")
    
    # Step 1: Check audio stream exists and is AAC
    audio_exists, stream_info = check_audio_stream_exists(video_path)
    if not audio_exists:
        result = {
            "pass": False,
            "reason": "No AAC audio stream found in recording.mp4",
            "video_file": str(video_path),
            "timestamp": None,
        }
        logger.error("No AAC audio stream found in recording.mp4")
    else:
        logger.info(f"Audio stream found: {stream_info}")
        
        # Step 2: Extract audio samples and compute dBFS
        logger.info("Extracting audio samples at 1Hz...")
        dbfs_values = extract_audio_samples(video_path)
        
        if dbfs_values is None:
            result = {
                "pass": False,
                "reason": "Failed to extract audio samples",
                "video_file": str(video_path),
                "timestamp": None,
            }
            logger.error("Failed to extract audio samples")
        else:
            logger.info(f"Analyzing {len(dbfs_values)} seconds of audio...")
            
            # Step 3: Analyze continuity
            analysis = analyze_audio_continuity(dbfs_values)
            
            # Combine results
            result = {
                "pass": analysis["pass"],
                "reason": analysis["reason"],
                "video_file": str(video_path),
                "audio_stream_info": stream_info,
                "analysis": analysis,
                "timestamp": None,  # Could add timestamp here
            }
            
            if analysis["pass"]:
                logger.info(f"Audio continuity check PASSED: {analysis['reason']}")
            else:
                logger.warning(f"Audio continuity check FAILED: {analysis['reason']}")
                for gap in analysis["problematic_gaps"]:
                    logger.warning(f"  Silent gap: {gap['start_second']}s to {gap['end_second']}s ({gap['duration_seconds']:.1f}s)")
    
    # Step 4: Write JSON output
    output_path = session_dir / "audio_check.json"
    try:
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        logger.info(f"Audio check results written to: {output_path}")
    except Exception as e:
        logger.error(f"Failed to write output file: {e}")
        return 2
    
    # Return appropriate exit code
    if not audio_exists:
        return 2  # Error: no audio stream
    elif "analysis" in result and not result["analysis"]["pass"]:
        return 1  # Failed: problematic silent gaps
    else:
        return 0  # Success


if __name__ == "__main__":
    sys.exit(main())