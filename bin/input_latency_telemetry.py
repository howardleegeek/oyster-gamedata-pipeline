#!/usr/bin/env python3
"""
input_latency_telemetry.py — input-to-effect latency collector

Runs as recorder subprocess. Reads inputs.jsonl + game_state.jsonl streaming.

For each KEYBOARD event with vk_code in (W=87, A=65, S=83, D=68):
  - Find the next game_state tick where the corresponding velocity component changed
  - Compute latency_ms = (tick_t_ns - input_t_ns) / 1e6
  - Append to input_latency.json

This is REAL input latency — press timestamp to game-state effect timestamp.
Honest measurement, no synthesis.

Method: wasd_press_to_velocity_change
"""

import argparse
import json
import os
import sys
import time

# WASD key codes and their corresponding velocity component
WASD_MAP = {
    87: "forward",  # W
    65: "left",  # A
    83: "backward",  # S
    68: "right",  # D
}

# Velocity component keys in game_state.jsonl
VELOCITY_KEYS = {
    "forward": "velocity_z",
    "backward": "velocity_z",
    "left": "velocity_x",
    "right": "velocity_x",
}


def parse_args():
    parser = argparse.ArgumentParser(description="Input-to-effect latency telemetry collector")
    parser.add_argument(
        "--inputs",
        default="inputs.jsonl",
        help="Path to inputs.jsonl (default: inputs.jsonl)",
    )
    parser.add_argument(
        "--game-state",
        default="game_state.jsonl",
        help="Path to game_state.jsonl (default: game_state.jsonl)",
    )
    parser.add_argument(
        "--output",
        default="input_latency.json",
        help="Path to output JSON (default: input_latency.json)",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=300.0,
        help="Max seconds to wait for input files (default: 300)",
    )
    return parser.parse_args()


def wait_for_file(path, timeout_s=300.0):
    """Wait for a file to exist, polling every 100ms."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.1)
    return False


def read_jsonl_streaming(path):
    """Generator that yields parsed JSON lines from a file, handling partial writes."""
    with open(path, "r") as f:
        for line in f:
            stripped_line = line.strip()
            if stripped_line:
                try:
                    yield json.loads(stripped_line)
                except json.JSONDecodeError:
                    # Skip malformed lines (partial writes)
                    continue


def compute_latencies(inputs_path, game_state_path):
    """
    Compute input-to-effect latencies from WASD keypresses.

    For each WASD keypress in inputs.jsonl:
      1. Note the timestamp (t_ns)
      2. Scan forward in game_state.jsonl for the next tick where
         the corresponding velocity component changed
      3. latency_ms = (tick_t_ns - input_t_ns) / 1e6

    Returns list of latency_ms values.
    """
    latencies = []
    skipped = 0

    # Load all game state ticks into a list for efficient scanning
    game_ticks = []
    for tick in read_jsonl_streaming(game_state_path):
        game_ticks.append(tick)

    if not game_ticks:
        print("[latency] WARNING: no game_state ticks found", file=sys.stderr)
        return latencies

    # Process input events
    input_count = 0
    matched_count = 0

    for event in read_jsonl_streaming(inputs_path):
        # Bug-fix 2026-05-18: accept multiple schemas — canonical_pipeline.py
        # step 4 produces `event_type` + `pressed` + `timestamp_ns` (the post-
        # denormalize shape), but the script was written assuming `type` +
        # `action` + `t_ns`. Support BOTH so v0.3.1 doesn't have 0 samples.
        event_type = event.get("event_type") or event.get("type")
        if event_type != "KEYBOARD":
            continue

        # vk_code may be top-level (post-denorm) or inside event_args[0] (raw)
        vk_code = event.get("vk_code")
        if vk_code is None:
            ea = event.get("event_args")
            if isinstance(ea, list) and len(ea) >= 1:
                vk_code = ea[0]
        if vk_code not in WASD_MAP:
            continue

        # Press detection: support `pressed: True` (post-denorm), `action: press`
        # (legacy), or `event_args[1]: True` (raw).
        is_press = False
        if event.get("pressed") is True:
            is_press = True
        elif event.get("action") in ("press", "down", 1):
            is_press = True
        else:
            ea = event.get("event_args")
            if isinstance(ea, list) and len(ea) >= 2 and ea[1] is True:
                is_press = True
        if not is_press:
            continue

        # Timestamp: support timestamp_ns (post-denorm), t_ns (legacy),
        # or timestamp (seconds, raw recorder).
        input_t_ns = event.get("timestamp_ns") or event.get("t_ns")
        if input_t_ns is None:
            ts = event.get("timestamp")
            if isinstance(ts, (int, float)):
                input_t_ns = int(ts * 1e9)
        if input_t_ns is None:
            skipped += 1
            continue

        input_count += 1
        direction = WASD_MAP[vk_code]
        vel_key = VELOCITY_KEYS[direction]

        # Find the next game_state tick where velocity changed
        # We need to find the tick AFTER the input timestamp
        found = False
        prev_velocity = None

        for tick in game_ticks:
            # Bug-fix 2026-05-18: game_state.jsonl uses `timestamp_ms`, not `t_ns`.
            # Accept either.
            tick_t_ns = tick.get("t_ns")
            if tick_t_ns is None:
                ts_ms = tick.get("timestamp_ms")
                if isinstance(ts_ms, (int, float)):
                    tick_t_ns = int(ts_ms * 1_000_000)
                else:
                    tick_t_ns = 0

            # Skip ticks before the input event
            if tick_t_ns < input_t_ns:
                # Track the last known velocity before the input
                if vel_key in tick:
                    prev_velocity = tick[vel_key]
                continue

            # This tick is at or after the input — check for velocity change
            current_velocity = tick.get(vel_key)

            if current_velocity is not None and prev_velocity is not None:
                if abs(current_velocity - prev_velocity) > 1e-6:
                    # Velocity changed — this is the effect tick
                    latency_ms = (tick_t_ns - input_t_ns) / 1e6
                    # Sanity check: latency should be positive and < 1000ms
                    if 0 <= latency_ms < 1000:
                        latencies.append(round(latency_ms, 2))
                        matched_count += 1
                    found = True
                    break
            elif current_velocity is not None and prev_velocity is None:
                # First tick after input — record as baseline
                prev_velocity = current_velocity
                continue

            # Update prev_velocity for next iteration
            if vel_key in tick:
                prev_velocity = tick.get(vel_key)

        if not found:
            skipped += 1

    print(
        f"[latency] Processed {input_count} WASD inputs, "
        f"matched {matched_count}, skipped {skipped}",
        file=sys.stderr,
    )

    return latencies


def write_output(latencies, output_path, method="wasd_press_to_velocity_change"):
    """Write latency results to JSON file."""
    result = {
        "latencies": latencies,
        "method": method,
        "count": len(latencies),
        "p50": round(sorted(latencies)[len(latencies) // 2], 2) if latencies else None,
        "p95": round(sorted(latencies)[int(len(latencies) * 0.95)] if latencies else None, 2)
        if latencies
        else None,
        "p99": round(sorted(latencies)[int(len(latencies) * 0.99)] if latencies else None, 2)
        if latencies
        else None,
        "min": round(min(latencies), 2) if latencies else None,
        "max": round(max(latencies), 2) if latencies else None,
        "mean": round(sum(latencies) / len(latencies), 2) if latencies else None,
    }

    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"[latency] Wrote {len(latencies)} latencies to {output_path}", file=sys.stderr)
    if latencies:
        print(
            f"[latency] p50={result['p50']}ms p95={result['p95']}ms p99={result['p99']}ms",
            file=sys.stderr,
        )

    return result


def main():
    args = parse_args()

    # Wait for input files to appear
    if not wait_for_file(args.inputs, timeout_s=args.timeout_s):
        print(f"[latency] ERROR: {args.inputs} not found within {args.timeout_s}s", file=sys.stderr)
        sys.exit(1)

    if not wait_for_file(args.game_state, timeout_s=args.timeout_s):
        print(
            f"[latency] ERROR: {args.game_state} not found within {args.timeout_s}s",
            file=sys.stderr,
        )
        sys.exit(1)

    # Compute latencies
    latencies = compute_latencies(args.inputs, args.game_state)

    # Write output
    write_output(latencies, args.output)

    # Exit with non-zero if no latencies were captured
    if not latencies:
        print("[latency] WARNING: no latencies captured", file=sys.stderr)
        sys.exit(0)  # Still exit 0 — absence of data is not an error


if __name__ == "__main__":
    main()
