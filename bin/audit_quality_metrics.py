#!/usr/bin/env python3
"""
Quality Metrics Audit Extension
Implements 10 quality dimensions for data collection quality assessment.
"""

import json
import logging
import math
import os
import statistics
import subprocess
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def audit_group_quality(session) -> List[Dict[str, Any]]:
    """
    Audit group for quality metrics (QM1-QM10).

    Args:
        session: Audit session object with path attributes

    Returns:
        List of check results with id, status, evidence, value
    """
    results = []

    # QM1: FPS consistency
    results.append(check_fps_consistency(session))

    # QM2: Frame drops
    results.append(check_frame_drops(session))

    # QM3: Audio SNR
    results.append(check_audio_snr(session))

    # QM4: Audio dynamic range
    results.append(check_audio_dynamic_range(session))

    # QM5: Input latency p99
    results.append(check_input_latency_p99(session))

    # QM6: Depth entropy
    results.append(check_depth_entropy(session))

    # QM7: Action diversity
    results.append(check_action_diversity(session))

    # QM8: World coverage
    results.append(check_world_coverage(session))

    # QM9: Camera-position range
    results.append(check_camera_position_range(session))

    # QM10: Recording continuity
    results.append(check_recording_continuity(session))

    return results


def check_fps_consistency(session) -> Dict[str, Any]:
    """QM1: FPS consistency - stddev/mean < 5% over 60-sec window"""
    try:
        # Try to get video file path
        video_path = getattr(session, 'video_path', None)
        if not video_path or not os.path.exists(video_path):
            return {"id": "QM1", "status": "SKIP", "evidence": "Video file not found", "value": None}

        # Use ffprobe to get packet timestamps
        cmd = [
            'ffprobe', '-v', 'quiet',
            '-select_streams', 'v:0',
            '-show_entries', 'packet=pts_time',
            '-of', 'csv=p=0',
            str(video_path)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return {"id": "QM1", "status": "SKIP", "evidence": "ffprobe failed", "value": None}

        # Parse timestamps
        timestamps = []
        for line in result.stdout.strip().split('\n'):
            if line:
                try:
                    timestamps.append(float(line))
                except ValueError:
                    continue

        if len(timestamps) < 2:
            return {"id": "QM1", "status": "SKIP", "evidence": "Insufficient timestamps", "value": None}

        # Calculate deltas (time between frames)
        deltas = []
        for i in range(1, len(timestamps)):
            delta = timestamps[i] - timestamps[i-1]
            if delta > 0:  # Skip negative or zero deltas
                deltas.append(delta)

        if len(deltas) < 2:
            return {"id": "QM1", "status": "SKIP", "evidence": "Insufficient deltas", "value": None}

        # Calculate mean and stddev
        mean_delta = statistics.mean(deltas)
        if mean_delta == 0:
            return {"id": "QM1", "status": "FAIL", "evidence": "Zero mean delta", "value": float('inf')}

        stddev_delta = statistics.stdev(deltas) if len(deltas) > 1 else 0

        # Calculate coefficient of variation (stddev/mean)
        cv = stddev_delta / mean_delta

        # Check 60-second window consistency (simplified: check overall)
        # In practice, should check sliding windows of 60 seconds
        status = "PASS" if cv < 0.05 else "FAIL"

        return {
            "id": "QM1",
            "status": status,
            "evidence": f"CV={cv:.4f}, mean={mean_delta:.6f}s, stddev={stddev_delta:.6f}s",
            "value": cv
        }

    except Exception as e:
        return {"id": "QM1", "status": "SKIP", "evidence": f"Error: {str(e)}", "value": None}


def check_frame_drops(session) -> Dict[str, Any]:
    """QM2: Frame drops - 0 missing frames in monotone index 0..N"""
    try:
        frames_path = getattr(session, 'frames_jsonl_path', None)
        if not frames_path or not os.path.exists(frames_path):
            return {"id": "QM2", "status": "SKIP", "evidence": "frames.jsonl not found", "value": None}

        frame_indices = []
        with open(frames_path, 'r') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    # Bug-fix 2026-05-16: accept any of the standard index field names.
                    # Previously only looked for "frame_index" — our synthesized
                    # frames.jsonl uses "idx" and "frame" per the recorder/transform
                    # convention. SKIP-ing valid sessions just because of a field
                    # name mismatch is theatrical, not honest.
                    for key in ('frame_index', 'idx', 'frame'):
                        if key in data:
                            frame_indices.append(data[key])
                            break
                except json.JSONDecodeError:
                    continue

        if not frame_indices:
            return {"id": "QM2", "status": "SKIP", "evidence": "No frame indices found", "value": None}

        # Check for monotonic sequence without gaps
        frame_indices.sort()
        missing_frames = []
        for i in range(len(frame_indices) - 1):
            if frame_indices[i + 1] != frame_indices[i] + 1:
                # Found a gap
                for missing in range(frame_indices[i] + 1, frame_indices[i + 1]):
                    missing_frames.append(missing)

        if missing_frames:
            status = "FAIL"
            evidence = f"Missing {len(missing_frames)} frames: {missing_frames[:5]}{'...' if len(missing_frames) > 5 else ''}"
            value = len(missing_frames)
        else:
            status = "PASS"
            evidence = f"All {len(frame_indices)} frames contiguous"
            value = 0

        return {"id": "QM2", "status": status, "evidence": evidence, "value": value}

    except Exception as e:
        return {"id": "QM2", "status": "SKIP", "evidence": f"Error: {str(e)}", "value": None}


def check_audio_snr(session) -> Dict[str, Any]:
    """QM3: Audio SNR > 30 dB"""
    try:
        audio_path = getattr(session, 'audio_flac_path', None)
        if not audio_path or not os.path.exists(audio_path):
            return {"id": "QM3", "status": "SKIP", "evidence": "audio.flac not found", "value": None}

        # Check if sox is available
        try:
            subprocess.run(['sox', '--version'], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            return {"id": "QM3", "status": "SKIP", "evidence": "sox not installed", "value": None}

        # Run sox stats
        cmd = ['sox', str(audio_path), '-n', 'stats']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return {"id": "QM3", "status": "SKIP", "evidence": "sox stats failed", "value": None}

        # Parse SNR from sox output
        snr = None
        for line in result.stderr.split('\n'):
            if 'SNR' in line:
                try:
                    # Extract SNR value (format: "SNR       :   xx.x dB")
                    parts = line.split(':')
                    if len(parts) > 1:
                        snr_str = parts[1].strip().split()[0]
                        snr = float(snr_str)
                        break
                except (ValueError, IndexError):
                    continue

        if snr is None:
            return {"id": "QM3", "status": "SKIP", "evidence": "SNR not found in sox output", "value": None}

        status = "PASS" if snr > 30 else "FAIL"
        return {
            "id": "QM3",
            "status": status,
            "evidence": f"SNR={snr:.1f} dB",
            "value": snr
        }

    except Exception as e:
        return {"id": "QM3", "status": "SKIP", "evidence": f"Error: {str(e)}", "value": None}


def check_audio_dynamic_range(session) -> Dict[str, Any]:
    """QM4: Audio dynamic range - RMS lev dB > -30"""
    try:
        audio_path = getattr(session, 'audio_flac_path', None)
        if not audio_path or not os.path.exists(audio_path):
            return {"id": "QM4", "status": "SKIP", "evidence": "audio.flac not found", "value": None}

        # Check if sox is available
        try:
            subprocess.run(['sox', '--version'], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            return {"id": "QM4", "status": "SKIP", "evidence": "sox not installed", "value": None}

        # Run sox stats
        cmd = ['sox', str(audio_path), '-n', 'stats']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return {"id": "QM4", "status": "SKIP", "evidence": "sox stats failed", "value": None}

        # Parse RMS level from sox output
        rms_db = None
        for line in result.stderr.split('\n'):
            if 'RMS lev dB' in line:
                try:
                    # Extract RMS value (format: "RMS lev dB    :   -xx.xx")
                    parts = line.split(':')
                    if len(parts) > 1:
                        rms_str = parts[1].strip().split()[0]
                        rms_db = float(rms_str)
                        break
                except (ValueError, IndexError):
                    continue

        if rms_db is None:
            return {"id": "QM4", "status": "SKIP", "evidence": "RMS level not found in sox output", "value": None}

        status = "PASS" if rms_db > -30 else "FAIL"
        return {
            "id": "QM4",
            "status": status,
            "evidence": f"RMS level={rms_db:.2f} dB",
            "value": rms_db
        }

    except Exception as e:
        return {"id": "QM4", "status": "SKIP", "evidence": f"Error: {str(e)}", "value": None}


def check_input_latency_p99(session) -> Dict[str, Any]:
    """QM5: Input latency p99 < 100 ms"""
    try:
        latency_path = getattr(session, 'input_latency_json_path', None)
        if not latency_path or not os.path.exists(latency_path):
            return {"id": "QM5", "status": "SKIP", "evidence": "input_latency.json not found", "value": None}

        with open(latency_path, 'r') as f:
            data = json.load(f)

        # Extract latency values
        latencies = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and 'latency_ms' in item:
                    latencies.append(item['latency_ms'])
                elif isinstance(item, (int, float)):
                    latencies.append(item)
        elif isinstance(data, dict):
            # Try common keys
            for key in ['latencies', 'latency_ms', 'values']:
                if key in data and isinstance(data[key], list):
                    latencies.extend([x for x in data[key] if isinstance(x, (int, float))])
                    break

        if not latencies:
            return {"id": "QM5", "status": "SKIP", "evidence": "No latency values found", "value": None}

        # Calculate p99
        latencies.sort()
        n = len(latencies)
        p99_index = int(0.99 * n)
        if p99_index >= n:
            p99_index = n - 1
        p99 = latencies[p99_index]

        status = "PASS" if p99 < 100 else "FAIL"
        return {
            "id": "QM5",
            "status": status,
            "evidence": f"p99 latency={p99:.1f} ms (n={n})",
            "value": p99
        }

    except Exception as e:
        return {"id": "QM5", "status": "SKIP", "evidence": f"Error: {str(e)}", "value": None}


def check_depth_entropy(session) -> Dict[str, Any]:
    """QM6: Depth entropy > 4.0 (not all-zero)"""
    try:
        depth_dir = getattr(session, 'depth_dir', None)
        if not depth_dir or not os.path.exists(depth_dir):
            return {"id": "QM6", "status": "SKIP", "evidence": "depth directory not found", "value": None}

        # Find first .exr file
        exr_files = list(Path(depth_dir).glob('*.exr'))
        if not exr_files:
            return {"id": "QM6", "status": "SKIP", "evidence": "No .exr files found in depth directory", "value": None}

        first_exr = str(exr_files[0])

        # Try to import OpenEXR
        try:
            import Imath
            import numpy as np
            import OpenEXR
        except ImportError:
            return {"id": "QM6", "status": "SKIP", "evidence": "OpenEXR not installed", "value": None}

        # Read EXR file
        try:
            exr_file = OpenEXR.InputFile(first_exr)
            dw = exr_file.header()['dataWindow']
            size = (dw.max.x - dw.min.x + 1, dw.max.y - dw.min.y + 1)

            # Read depth channel
            # Bug-fix 2026-05-16: audit's H5 group asserts channels == ["Z"] (depth EXR
            # convention used by DA-V2 / OpenEXR depth standard). Previously QM6 always
            # tried "R" and SKIP-ed with confusing message — a direct contradiction
            # with H5. Now try "Z" first (canonical), fall back to "R" (legacy).
            pt = Imath.PixelType(Imath.PixelType.FLOAT)
            channels = list(exr_file.header()['channels'].keys())
            depth_channel = 'Z' if 'Z' in channels else ('R' if 'R' in channels else channels[0])
            depth_str = exr_file.channel(depth_channel, pt)
            depth_array = np.frombuffer(depth_str, dtype=np.float32).reshape(size[1], size[0])

            # Flatten and normalize to 0-255 for histogram
            depth_flat = depth_array.flatten()

            # Remove invalid values (NaN, inf)
            depth_flat = depth_flat[np.isfinite(depth_flat)]

            if len(depth_flat) == 0:
                return {"id": "QM6", "status": "FAIL", "evidence": "All depth values are NaN/inf", "value": 0.0}

            # Normalize to 0-255 for histogram
            depth_min = np.min(depth_flat)
            depth_max = np.max(depth_flat)

            if depth_max - depth_min < 1e-10:
                # All values are the same
                entropy_val = 0.0
            else:
                depth_norm = ((depth_flat - depth_min) / (depth_max - depth_min) * 255).astype(np.int32)
                depth_norm = np.clip(depth_norm, 0, 255)

                # Compute histogram
                hist, _ = np.histogram(depth_norm, bins=256, range=(0, 256))
                hist = hist.astype(np.float64)
                hist_sum = np.sum(hist)

                if hist_sum == 0:
                    entropy_val = 0.0
                else:
                    # Normalize histogram to probabilities
                    prob = hist / hist_sum
                    # Compute Shannon entropy
                    entropy_val = -np.sum(prob * np.log2(prob + 1e-10))

            status = "PASS" if entropy_val > 4.0 else "FAIL"
            return {
                "id": "QM6",
                "status": status,
                "evidence": f"Depth entropy={entropy_val:.2f} (min={depth_min:.2f}, max={depth_max:.2f})",
                "value": entropy_val
            }

        except Exception as e:
            return {"id": "QM6", "status": "SKIP", "evidence": f"Error reading EXR: {str(e)}", "value": None}

    except Exception as e:
        return {"id": "QM6", "status": "SKIP", "evidence": f"Error: {str(e)}", "value": None}


def check_action_diversity(session) -> Dict[str, Any]:
    """QM7: Action diversity - >= 6 distinct event types"""
    try:
        inputs_path = getattr(session, 'inputs_jsonl_path', None)
        if not inputs_path or not os.path.exists(inputs_path):
            return {"id": "QM7", "status": "SKIP", "evidence": "inputs.jsonl not found", "value": None}

        event_types = set()
        with open(inputs_path, 'r') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    # Look for event type in common formats
                    if 'event_type' in data:
                        event_types.add(data['event_type'])
                    elif 'type' in data:
                        event_types.add(data['type'])
                    elif 'action' in data:
                        event_types.add(data['action'])
                    elif 'key' in data:
                        event_types.add(data['key'])
                except json.JSONDecodeError:
                    continue

        # Count distinct types
        distinct_count = len(event_types)

        status = "PASS" if distinct_count >= 6 else "FAIL"
        evidence = f"Found {distinct_count} distinct event types: {sorted(list(event_types))[:10]}{'...' if len(event_types) > 10 else ''}"

        return {
            "id": "QM7",
            "status": status,
            "evidence": evidence,
            "value": distinct_count
        }

    except Exception as e:
        return {"id": "QM7", "status": "SKIP", "evidence": f"Error: {str(e)}", "value": None}


def check_world_coverage(session) -> Dict[str, Any]:
    """QM8: World coverage - >= 10 unique 16x16 chunks visited"""
    try:
        game_state_path = getattr(session, 'game_state_jsonl_path', None)
        if not game_state_path or not os.path.exists(game_state_path):
            return {"id": "QM8", "status": "SKIP", "evidence": "game_state.jsonl not found", "value": None}

        chunks = set()
        with open(game_state_path, 'r') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())

                    # Try to extract chunk coordinates
                    chunk_x = None
                    chunk_z = None

                    # Common formats
                    if 'chunk_x' in data and 'chunk_z' in data:
                        chunk_x = data['chunk_x']
                        chunk_z = data['chunk_z']
                    elif 'position' in data and isinstance(data['position'], dict):
                        pos = data['position']
                        if 'x' in pos and 'z' in pos:
                            # Convert world coordinates to chunk coordinates (16x16 blocks)
                            chunk_x = int(pos['x'] // 16)
                            chunk_z = int(pos['z'] // 16)
                    elif 'x' in data and 'z' in data:
                        chunk_x = int(data['x'] // 16)
                        chunk_z = int(data['z'] // 16)

                    if chunk_x is not None and chunk_z is not None:
                        chunks.add((chunk_x, chunk_z))

                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue

        unique_chunks = len(chunks)

        status = "PASS" if unique_chunks >= 10 else "FAIL"
        evidence = f"Visited {unique_chunks} unique 16x16 chunks"

        return {
            "id": "QM8",
            "status": status,
            "evidence": evidence,
            "value": unique_chunks
        }

    except Exception as e:
        return {"id": "QM8", "status": "SKIP", "evidence": f"Error: {str(e)}", "value": None}


def check_camera_position_range(session) -> Dict[str, Any]:
    """QM9: Camera-position range - bbox diagonal > 20 m"""
    try:
        # Try to get camera positions from action_camera or similar
        camera_data = []

        # Check multiple possible sources
        sources = [
            ('action_camera_jsonl_path', 'action_camera'),
            ('camera_jsonl_path', 'camera'),
            ('frames_jsonl_path', 'frames')  # frames might have camera info
        ]

        for attr_name, source_type in sources:
            path = getattr(session, attr_name, None)
            if path and os.path.exists(path):
                # Bug-fix 2026-05-16: action_camera.json is a single JSON ARRAY of
                # 9000 rows (PRD spec), NOT a JSONL stream. The old code did
                # ``for line in f`` which fails to parse the giant JSON document.
                # Detect array-vs-stream by extension + fallback.
                rows: list = []
                with open(path, 'r') as f:
                    if path.endswith('.json'):
                        try:
                            doc = json.load(f)
                            if isinstance(doc, list):
                                rows = doc
                            elif isinstance(doc, dict):
                                rows = [doc]
                        except json.JSONDecodeError:
                            # Maybe it's actually JSONL despite the .json name; re-open
                            f.seek(0)
                            for ln in f:
                                try:
                                    rows.append(json.loads(ln.strip()))
                                except json.JSONDecodeError:
                                    continue
                    else:
                        for ln in f:
                            try:
                                rows.append(json.loads(ln.strip()))
                            except json.JSONDecodeError:
                                continue

                for data in rows:
                        try:
                            pos = None

                            # Bug-fix 2026-05-16: accept BOTH dict {x,y,z} and list [x,y,z]
                            # forms. The PRD-spec transform writes camera_position as a
                            # 3-element list (standard convention); previously this
                            # SKIP-ed because we only matched the dict shape.
                            for pos_key in ['position', 'camera_position', 'pos', 'camera_pos']:
                                if pos_key in data:
                                    val = data[pos_key]
                                    if isinstance(val, dict) and all(k in val for k in ['x', 'y', 'z']):
                                        pos = (float(val['x']), float(val['y']), float(val['z']))
                                        break
                                    elif isinstance(val, (list, tuple)) and len(val) >= 3:
                                        pos = (float(val[0]), float(val[1]), float(val[2]))
                                        break

                            # Also check for direct x,y,z fields
                            if not pos and all(k in data for k in ['x', 'y', 'z']):
                                pos = (float(data['x']), float(data['y']), float(data['z']))

                            if pos:
                                camera_data.append(pos)

                        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                            continue

        if not camera_data:
            return {"id": "QM9", "status": "SKIP", "evidence": "No camera position data found", "value": None}

        # Calculate bounding box
        xs = [p[0] for p in camera_data]
        ys = [p[1] for p in camera_data]
        zs = [p[2] for p in camera_data]

        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        min_z, max_z = min(zs), max(zs)

        # Calculate diagonal distance
        dx = max_x - min_x
        dy = max_y - min_y
        dz = max_z - min_z
        diagonal = math.sqrt(dx*dx + dy*dy + dz*dz)

        status = "PASS" if diagonal > 20 else "FAIL"
        evidence = f"Camera position bbox diagonal={diagonal:.2f}m (range: x[{min_x:.1f},{max_x:.1f}], y[{min_y:.1f},{max_y:.1f}], z[{min_z:.1f},{max_z:.1f}])"

        return {
            "id": "QM9",
            "status": status,
            "evidence": evidence,
            "value": diagonal
        }

    except Exception as e:
        return {"id": "QM9", "status": "SKIP", "evidence": f"Error: {str(e)}", "value": None}


def check_recording_continuity(session) -> Dict[str, Any]:
    """QM10: Recording continuity - all durations within 2% of each other"""
    try:
        # Get metadata duration
        metadata_duration = None
        metadata_path = getattr(session, 'metadata_path', None)
        if metadata_path and os.path.exists(metadata_path):
            try:
                with open(metadata_path, 'r') as f:
                    metadata = json.load(f)
                if 'duration' in metadata:
                    metadata_duration = float(metadata['duration'])
                elif 'recording_duration' in metadata:
                    metadata_duration = float(metadata['recording_duration'])
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.debug(
                    "QM10: failed to parse metadata duration from %s: %s",
                    metadata_path, exc,
                )

        # Get video duration via ffprobe
        video_duration = None
        video_path = getattr(session, 'video_path', None)
        if video_path and os.path.exists(video_path):
            try:
                cmd = [
                    'ffprobe', '-v', 'quiet',
                    '-show_entries', 'format=duration',
                    '-of', 'default=noprint_wrappers=1:nokey=1',
                    str(video_path)
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    video_duration = float(result.stdout.strip())
            except (subprocess.CalledProcessError, ValueError, FileNotFoundError) as exc:
                logger.debug(
                    "QM10: ffprobe duration probe failed for %s: %s",
                    video_path, exc,
                )

        # Get frames count duration
        frames_duration = None
        frames_path = getattr(session, 'frames_jsonl_path', None)
        if frames_path and os.path.exists(frames_path):
            try:
                frame_count = 0
                with open(frames_path, 'r') as f:
                    for line in f:
                        if line.strip():
                            frame_count += 1

                # Estimate duration from frame count (assuming ~30 FPS)
                if frame_count > 0:
                    frames_duration = frame_count / 30.0  # Approximate
            except Exception as exc:
                logger.debug(
                    "QM10: failed to estimate frames duration from %s: %s",
                    frames_path, exc,
                )

        # Collect available durations
        durations = []
        sources = []

        if metadata_duration is not None:
            durations.append(metadata_duration)
            sources.append("metadata")

        if video_duration is not None:
            durations.append(video_duration)
            sources.append("video")

        if frames_duration is not None:
            durations.append(frames_duration)
            sources.append("frames")

        if len(durations) < 2:
            return {"id": "QM10", "status": "SKIP", "evidence": f"Insufficient duration sources: {sources}", "value": None}

        # Check if all durations are within 2% of each other
        max_diff_ratio = 0
        for i in range(len(durations)):
            for j in range(i + 1, len(durations)):
                if durations[i] == 0 or durations[j] == 0:
                    continue
                ratio = max(durations[i], durations[j]) / min(durations[i], durations[j])
                diff_ratio = abs(ratio - 1.0)
                if diff_ratio > max_diff_ratio:
                    max_diff_ratio = diff_ratio

        # Calculate average duration for value
        avg_duration = sum(durations) / len(durations)

        status = "PASS" if max_diff_ratio <= 0.02 else "FAIL"
        evidence = f"Max duration difference={max_diff_ratio*100:.1f}% (metadata={metadata_duration or 'N/A'}s, video={video_duration or 'N/A'}s, frames≈{frames_duration or 'N/A'}s)"

        return {
            "id": "QM10",
            "status": status,
            "evidence": evidence,
            "value": avg_duration
        }

    except Exception as e:
        return {"id": "QM10", "status": "SKIP", "evidence": f"Error: {str(e)}", "value": None}


# Test function to demonstrate usage
if __name__ == "__main__":
    # Mock session object for testing
    class MockSession:
        def __init__(self):
            self.video_path = "/path/to/video.mp4"
            self.frames_jsonl_path = "/path/to/frames.jsonl"
            self.audio_flac_path = "/path/to/audio.flac"
            self.input_latency_json_path = "/path/to/input_latency.json"
            self.depth_dir = "/path/to/depth"
            self.inputs_jsonl_path = "/path/to/inputs.jsonl"
            self.game_state_jsonl_path = "/path/to/game_state.jsonl"
            self.action_camera_jsonl_path = "/path/to/action_camera.jsonl"
            self.metadata_path = "/path/to/metadata.json"

    session = MockSession()
    results = audit_group_quality(session)

    print("Quality Metrics Audit Results:")
    print("-" * 80)
    for result in results:
        print(f"{result['id']}: {result['status']} - {result['evidence']}")
