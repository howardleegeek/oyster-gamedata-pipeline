#!/usr/bin/env python3
"""
Input Latency Analyzer — v2 (honest-filtered)

Classifies input latency samples into buckets to distinguish honest
input latency from measurement contamination (pause menu, death screen,
potion effects, tick lag).

Usage:
    python3 bin/input_latency_analyzer.py <session_dir> [--json]

Reads:
    <session_dir>/input_latency.json
    <session_dir>/inputs.jsonl
    <session_dir>/game_state.jsonl

Writes:
    <session_dir>/input_latency_v2.json
"""

import argparse
import json
import os
import sys
from collections import defaultdict


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def load_jsonl(path):
    """Load a JSONL file, returning a list of dicts."""
    entries = []
    with open(path, "r") as f:
        for raw_line in f:
            stripped_line = raw_line.strip()
            if stripped_line:
                entries.append(json.loads(stripped_line))
    return entries


def percentile(sorted_values, p):
    """Compute the p-th percentile from a sorted list of values."""
    if not sorted_values:
        return 0
    k = (len(sorted_values) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(sorted_values):
        return sorted_values[f]
    d0 = sorted_values[f] * (c - k)
    d1 = sorted_values[c] * (k - f)
    return round(d0 + d1)


def find_game_state_at(game_states, timestamp_ms, window_ms=50):
    """
    Find the game_state entry closest to the given timestamp.
    Returns the entry dict or None.
    """
    best = None
    best_dist = float("inf")
    for gs in game_states:
        ts = gs.get("timestamp_ms", gs.get("t", 0))
        dist = abs(ts - timestamp_ms)
        if dist < best_dist:
            best_dist = dist
            best = gs
    if best_dist <= window_ms:
        return best
    return None


def check_ticks_around(game_states, timestamp_ms, window_ms=100, min_ticks=3):
    """
    Check if there are at least min_ticks game_state entries within
    +/- window_ms of the given timestamp.
    """
    low = timestamp_ms - window_ms
    high = timestamp_ms + window_ms
    count = 0
    for gs in game_states:
        ts = gs.get("timestamp_ms", gs.get("t", 0))
        if low <= ts <= high:
            count += 1
    return count >= min_ticks


def check_tick_lag(game_states, timestamp_ms, lookback_ms=200, threshold_ms=100):
    """
    Check if there was a tick lag spike in the lookback_ms window before
    the given timestamp. Returns True if any consecutive tick interval
    exceeds threshold_ms.
    """
    low = timestamp_ms - lookback_ms
    relevant = []
    for gs in game_states:
        ts = gs.get("timestamp_ms", gs.get("t", 0))
        if low <= ts <= timestamp_ms:
            relevant.append(ts)
    relevant.sort()
    for i in range(1, len(relevant)):
        interval = relevant[i] - relevant[i - 1]
        if interval > threshold_ms:
            return True
    return False


def classify_sample(latency_ms, input_timestamp_ms, game_states):
    """
    Classify a single latency sample into a bucket.

    Returns one of: HONEST, PAUSE_MENU, DEATH_SCREEN, POTION_SLOWNESS,
                    TICK_LAG, OTHER
    """
    gs = find_game_state_at(game_states, input_timestamp_ms)

    # PAUSE_MENU: game_state has is_paused == true
    if gs is not None:
        inner = gs.get("game_state", gs)
        if inner.get("is_paused", False):
            return "PAUSE_MENU"

    # DEATH_SCREEN: player health == 0
    if gs is not None:
        player = gs.get("player", {})
        if player.get("health", 1) == 0:
            return "DEATH_SCREEN"

    # POTION_SLOWNESS: active_effects contains "slowness" or "weakness"
    if gs is not None:
        player = gs.get("player", {})
        effects = player.get("active_effects", [])
        if isinstance(effects, list):
            effect_names = [e if isinstance(e, str) else e.get("name", "") for e in effects]
        elif isinstance(effects, dict):
            effect_names = list(effects.keys())
        else:
            effect_names = []
        if "slowness" in effect_names or "weakness" in effect_names:
            return "POTION_SLOWNESS"

    # TICK_LAG: tick interval > 100ms in the 200ms before input
    if check_tick_lag(game_states, input_timestamp_ms):
        return "TICK_LAG"

    # HONEST: game_state updating normally (3+ ticks within 100ms)
    if check_ticks_around(game_states, input_timestamp_ms):
        return "HONEST"

    # OTHER: none of the above but latency > 200ms
    if latency_ms > 200:
        return "OTHER"

    # Default: HONEST (low latency, no contamination detected)
    return "HONEST"


def analyze(session_dir, json_output=False):
    """
    Main analysis function.

    Args:
        session_dir: path to the session directory
        json_output: if True, output JSON to stdout

    Returns:
        dict with analysis results
    """
    # Load data
    latency_data = load_json(os.path.join(session_dir, "input_latency.json"))
    game_states = load_jsonl(os.path.join(session_dir, "game_state.jsonl"))

    # Try to load inputs.jsonl for frame info
    inputs_path = os.path.join(session_dir, "inputs.jsonl")
    inputs = load_jsonl(inputs_path) if os.path.exists(inputs_path) else []

    # Build a map from input timestamp to frame number
    input_frame_map = {}
    for inp in inputs:
        ts = inp.get("timestamp_ms", inp.get("t", 0))
        frame = inp.get("frame", inp.get("f", None))
        if frame is not None:
            input_frame_map[ts] = frame

    latencies = latency_data.get("latencies", [])
    # If we have timestamps, use them; otherwise generate synthetic ones
    timestamps = latency_data.get("timestamps", [])
    if not timestamps:
        # Generate synthetic timestamps spaced by ~16ms (60fps)
        timestamps = [i * 16 for i in range(len(latencies))]

    # Classify each sample
    buckets = defaultdict(list)  # bucket_name -> list of latency_ms
    exclusion_log = []

    for i, lat_ms in enumerate(latencies):
        ts = timestamps[i] if i < len(timestamps) else i * 16
        bucket = classify_sample(lat_ms, ts, game_states)
        buckets[bucket].append(lat_ms)

        # Log exclusion for non-HONEST samples
        if bucket != "HONEST":
            frame = input_frame_map.get(ts, i)
            exclusion_log.append(
                {
                    "frame": frame,
                    "latency_ms": lat_ms,
                    "bucket": bucket,
                }
            )

    # Compute honest percentiles
    honest_lats = sorted(buckets.get("HONEST", []))
    all_lats = sorted(latencies)

    honest_p50 = percentile(honest_lats, 50) if honest_lats else 0
    honest_p95 = percentile(honest_lats, 95) if honest_lats else 0
    honest_p99 = percentile(honest_lats, 99) if honest_lats else 0
    honest_max = max(honest_lats) if honest_lats else 0

    unfiltered_p99 = percentile(all_lats, 99) if all_lats else 0

    total = len(latencies)
    honest_count = len(honest_lats)

    # Verdict: PASS only if honest_p99 < 100ms AND no OTHER bucket
    other_count = len(buckets.get("OTHER", []))
    if honest_p99 < 100 and other_count == 0:
        verdict = "PASS"
    else:
        verdict = "FAIL"

    # Build result
    result = {
        "total": total,
        "honest": honest_count,
        "pause_menu": len(buckets.get("PAUSE_MENU", [])),
        "death_screen": len(buckets.get("DEATH_SCREEN", [])),
        "potion_slowness": len(buckets.get("POTION_SLOWNESS", [])),
        "tick_lag": len(buckets.get("TICK_LAG", [])),
        "other": other_count,
        "honest_p50": honest_p50,
        "honest_p95": honest_p95,
        "honest_p99": honest_p99,
        "honest_max": honest_max,
        "unfiltered_p99": unfiltered_p99,
        "verdict": verdict,
    }

    # Write output file
    output_path = os.path.join(session_dir, "input_latency_v2.json")
    output_data = {
        "method": "honest_filtered_p99",
        "filter_buckets": {
            "HONEST": len(buckets.get("HONEST", [])),
            "PAUSE_MENU": len(buckets.get("PAUSE_MENU", [])),
            "DEATH_SCREEN": len(buckets.get("DEATH_SCREEN", [])),
            "POTION_SLOWNESS": len(buckets.get("POTION_SLOWNESS", [])),
            "TICK_LAG": len(buckets.get("TICK_LAG", [])),
            "OTHER": len(buckets.get("OTHER", [])),
        },
        "honest_latencies": honest_lats,
        "honest_p50": honest_p50,
        "honest_p95": honest_p95,
        "honest_p99": honest_p99,
        "exclusion_reason_log": exclusion_log,
    }
    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2)

    # Output
    if json_output:
        print(json.dumps(result, indent=2))
    else:
        session_id = os.path.basename(session_dir.rstrip("/"))
        print(f"INPUT LATENCY ANALYSIS — {session_id}")
        print(f"  Total samples: {total}")
        print()
        print("  Classification:")
        for name, key in [
            ("HONEST", "honest"),
            ("PAUSE_MENU", "pause_menu"),
            ("DEATH_SCREEN", "death_screen"),
            ("POTION_SLOWNESS", "potion_slowness"),
            ("TICK_LAG", "tick_lag"),
            ("OTHER", "other"),
        ]:
            count = result[key]
            pct = (count / total * 100) if total > 0 else 0
            print(f"    {name:<18} {count:>3} ({pct:>5.1f}%)")
        print()
        print("  HONEST percentiles (filtered):")
        print(f"    p50:  {honest_p50}ms")
        print(f"    p95:  {honest_p95}ms")
        print(f"    p99:  {honest_p99}ms       ← TARGET <100ms (was {unfiltered_p99}ms unfiltered)")
        print(f"    max: {honest_max}ms")
        print()
        print(f"  Verdict: {verdict} (honest p99 {'<' if verdict == 'PASS' else '>='} 100ms)")
        if other_count > 0:
            print(f"  WARNING: {other_count} samples in OTHER bucket (high latency, unclassified)")

    return result


def main():
    parser = argparse.ArgumentParser(description="Input Latency Analyzer v2")
    parser.add_argument("session_dir", help="Path to session directory")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args()

    if not os.path.isdir(args.session_dir):
        print(f"Error: {args.session_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    analyze(args.session_dir, json_output=args.json)


if __name__ == "__main__":
    main()
