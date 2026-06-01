#!/usr/bin/env python3
"""
G073 · bin/prd_test_action_per_second.py

PRD p6 #6: Validate the actions-per-second rate is within the 0.5 to 5.0 range
("at least one action per second", camera rotation counts). Out-of-band
captures are flagged as low-quality.

Rate definition (the honest one)
================================
The metric is the **true average discrete-action rate** over the whole
session: (number of discrete player actions) / (session duration). A discrete
action is a deliberate input event:

  * KEYBOARD key-DOWN transition  (pressed=True / event_args[1] is True)
  * MOUSE_BUTTON press            (event_args[1] is True)
  * SCROLL event

Key-UP transitions and continuous MOUSE_MOVE are NOT discrete actions (a press
is the action; the release is not a second action, and mouse drift would swamp
the cadence). Duration is the span between the first and last recorded input
timestamp.

This replaces the previous ``median(1/Δt)`` over consecutive ``action_camera``
records, which measured the *instantaneous burst* rate: a bursty-but-calm
session (e.g. several keys tapped within one second, then quiet for ten) read
as a huge median even though the true average was ~1 action/sec. The average
is what the PRD band [0.5, 5.0] is about.

When this test is pointed at a session's ``action_camera.json`` (its harness
invocation), it reads the sibling ``inputs.jsonl`` directly for the real event
stream. The legacy list/text/action_camera loaders remain for direct rate-list
inputs and unit tests.
"""

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

MIN_ACTIONS_PER_SECOND = 0.5
MAX_ACTIONS_PER_SECOND = 5.0

# Discrete-action event types per the PRD action-rate definition. MOUSE_MOVE is
# deliberately excluded: continuous pointer motion is not a discrete action.
_DISCRETE_KEYBOARD = "KEYBOARD"
_DISCRETE_MOUSE_BUTTON = "MOUSE_BUTTON"
_DISCRETE_SCROLL = "SCROLL"


def calculate_median_actions_per_second(actions: list[float]) -> float:
    """
    Calculate the median actions-per-second from a list of action rates.

    Args:
        actions: List of action rates (actions per second).

    Returns:
        Median value of the action rates.

    Raises:
        ValueError: If actions list is empty.
    """
    if not actions:
        raise ValueError("Actions list cannot be empty")
    return float(statistics.median(actions))


def is_quality_acceptable(median_aps: float) -> bool:
    """
    Check if the median actions-per-second falls within acceptable range.

    Args:
        median_aps: Median actions-per-second value to validate.

    Returns:
        True if within [0.5, 5.0] range, False otherwise.
    """
    return MIN_ACTIONS_PER_SECOND <= median_aps <= MAX_ACTIONS_PER_SECOND


def analyze_capture_quality(actions: list[float]) -> dict[str, Any]:
    """
    Analyze capture quality based on action rates.

    Args:
        actions: List of action rates (actions per second).

    Returns:
        Dictionary with median, min, max, and quality assessment.
    """
    median_aps = calculate_median_actions_per_second(actions)
    is_acceptable = is_quality_acceptable(median_aps)

    return {
        "median_actions_per_second": round(median_aps, 4),
        "min_actions_per_second": round(min(actions), 4),
        "max_actions_per_second": round(max(actions), 4),
        "sample_count": len(actions),
        "quality_status": "acceptable" if is_acceptable else "low-quality",
        "in_range": is_acceptable,
    }


def _event_is_pressed(ev: dict) -> bool:
    """True if a KEYBOARD/MOUSE_BUTTON event is a DOWN/press transition.

    The recorder records both presses and releases. The press state lives in
    ``event_args[1]`` (``[code, True]`` = press, ``[code, False]`` = release),
    with a redundant top-level ``pressed`` field on KEYBOARD events. We trust
    ``event_args[1]`` first (present on both event kinds), then ``pressed``.
    A record with neither is treated as a press (best-effort: better to count a
    deliberate event than silently drop it), but well-formed recorder data
    always carries the flag.
    """
    args = ev.get("event_args")
    if isinstance(args, list) and len(args) >= 2 and isinstance(args[1], bool):
        return args[1]
    pressed = ev.get("pressed")
    if isinstance(pressed, bool):
        return pressed
    return True


def count_discrete_actions(events: list[dict[str, Any]]) -> tuple[int, float | None, float | None]:
    """Count discrete actions and find the first/last event timestamp.

    Discrete actions = KEYBOARD key-DOWN transitions + MOUSE_BUTTON presses +
    SCROLL events. Returns ``(count, first_ts, last_ts)``; the timestamps span
    *all* recorded events (not just discrete ones) so duration reflects the real
    capture window. ``first_ts``/``last_ts`` are ``None`` when no event carries a
    numeric timestamp.
    """
    count = 0
    first_ts: float | None = None
    last_ts: float | None = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ts = ev.get("timestamp")
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            ts_f = float(ts)
            if first_ts is None or ts_f < first_ts:
                first_ts = ts_f
            if last_ts is None or ts_f > last_ts:
                last_ts = ts_f
        etype = ev.get("event_type")
        if etype == _DISCRETE_KEYBOARD:
            if _event_is_pressed(ev):
                count += 1
        elif etype == _DISCRETE_MOUSE_BUTTON:
            if _event_is_pressed(ev):
                count += 1
        elif etype == _DISCRETE_SCROLL:
            count += 1
    return count, first_ts, last_ts


def _iter_jsonl(filepath: Path) -> list[dict[str, Any]]:
    """Load a JSON-Lines file into a list of dicts, skipping blank/bad lines."""
    rows: list[dict[str, Any]] = []
    with open(filepath, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def average_action_rate_from_inputs(inputs_path: Path) -> float:
    """True average discrete-action rate (actions/sec) from inputs.jsonl.

    rate = discrete_action_count / (last_ts - first_ts). This is the genuine
    average cadence the PRD band [0.5, 5.0] targets — not a burst median.

    Raises:
        FileNotFoundError: inputs.jsonl missing.
        ValueError: no discrete actions, no timestamps, or zero/negative span.
    """
    if not inputs_path.exists():
        raise FileNotFoundError(f"inputs file not found: {inputs_path}")
    events = _iter_jsonl(inputs_path)
    count, first_ts, last_ts = count_discrete_actions(events)
    if first_ts is None or last_ts is None:
        raise ValueError("inputs.jsonl contains no timestamped events")
    duration = last_ts - first_ts
    if duration <= 0:
        raise ValueError("inputs.jsonl duration is zero or negative")
    if count == 0:
        # Zero deliberate actions over a real window is a genuine (fail-low)
        # signal, not an error — return 0.0 so the band check fails honestly.
        return 0.0
    return count / duration


def _sibling_inputs_path(input_arg: Path) -> Path | None:
    """Return the inputs.jsonl that lives alongside an action_camera.json arg.

    The PRD harness invokes this test as ``-i <session>/action_camera.json``.
    The real event stream is ``<session>/inputs.jsonl``. Return that path when
    it exists so we measure the true action rate instead of frame cadence.
    """
    if input_arg.name == "inputs.jsonl":
        return input_arg if input_arg.exists() else None
    candidate = input_arg.parent / "inputs.jsonl"
    return candidate if candidate.exists() else None


def _has_timestamp_field(records: list[dict]) -> bool:
    """Check if a list of records has a timestamp-like field."""
    if not records:
        return False
    first = records[0]
    if not isinstance(first, dict):
        return False
    return any(k in first for k in ("timestamp", "time", "frame"))


def _is_camera_data_dict(data: dict) -> bool:
    """Check if a dict looks like camera data (not action records)."""
    camera_keys = {"camera_position", "intrinsics", "world_cube_radius"}
    return bool(camera_keys & set(data.keys()))


def _extract_action_rates_from_action_camera(records: list[dict]) -> list[float]:
    """
    Extract action rates from action_camera.json format.

    Calculates actions-per-second by measuring time deltas between consecutive
    action records. Each record represents one action frame.

    Args:
        records: List of action records with 'timestamp' or 'time' field.

    Returns:
        List of action rates (actions per second) calculated from time deltas.
    """
    if len(records) < 2:
        raise ValueError("action_camera.json must contain at least 2 records to calculate action rates")

    # Detect which timestamp field is used
    first = records[0]
    if "timestamp" in first:
        ts_key = "timestamp"
    elif "time" in first:
        ts_key = "time"
    elif "frame" in first:
        ts_key = "frame"
    else:
        raise ValueError("No timestamp field found in action_camera records")

    # Sort by timestamp to ensure correct order
    sorted_records = sorted(records, key=lambda r: r.get(ts_key, 0))

    action_rates = []
    for i in range(1, len(sorted_records)):
        prev_ts = sorted_records[i - 1].get(ts_key, 0)
        curr_ts = sorted_records[i].get(ts_key, 0)
        delta_t = curr_ts - prev_ts

        if delta_t > 0:
            # 1 action per delta_t seconds = actions per second
            action_rates.append(1.0 / delta_t)

    if not action_rates:
        raise ValueError("Could not calculate action rates from action_camera.json")

    return action_rates


def load_actions_from_file(filepath: Path) -> list[float]:
    """
    Load action rates from a JSON or text file.

    Supports four formats:
    1. JSON list of numbers: [1.0, 2.0, 3.0]
    2. action_camera.json: List of action records with timestamps
    3. Camera data dict: {"camera_position": ..., "world_cube_radius": ...} → returns []
    4. Plain text: one value per line

    Args:
        filepath: Path to the input file.

    Returns:
        List of action rates.

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If file format is invalid.
    """
    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    content = filepath.read_text().strip()

    if filepath.suffix == ".json":
        data = json.loads(content)
        if isinstance(data, list):
            # Check if it's a list of numbers (direct format)
            if data and isinstance(data[0], (int, float)):
                return [float(x) for x in data]
            # Check if it's action_camera.json format (list of objects with timestamps)
            if data and isinstance(data[0], dict) and _has_timestamp_field(data):
                return _extract_action_rates_from_action_camera(data)
        elif isinstance(data, dict):
            # Camera data dict — not actionable, return empty
            if _is_camera_data_dict(data):
                return []
        raise ValueError("JSON file must contain a list of numbers or action_camera.json format")

    # Plain text: one value per line
    return [float(line.strip()) for line in content.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    """
    Main entry point for the actions-per-second quality test.

    Args:
        argv: Command line arguments (defaults to sys.argv).

    Returns:
        Exit code: 0 if quality is acceptable, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description="Validate median actions-per-second is within 0.5 to 5.0 range"
    )
    parser.add_argument(
        "-a", "--actions", nargs="+", type=float,
        help="Direct list of action rates to validate"
    )
    parser.add_argument(
        "-i", "--input", type=Path,
        help="Path to JSON/text file of action rates, or a session "
             "action_camera.json (its sibling inputs.jsonl is read for the "
             "true average action rate)"
    )
    parser.add_argument(
        "-j", "--json", action="store_true",
        help="Output results as JSON"
    )

    # Check for no args before parse (parser.error exits with code 2)
    check_argv = argv if argv is not None else sys.argv[1:]
    if not check_argv:
        print("Error: Either --actions or --input is required", file=sys.stderr)
        return 1

    args = parser.parse_args(check_argv)

    if not args.actions and not args.input:
        print("Error: Either --actions or --input is required", file=sys.stderr)
        return 1

    try:
        # Session path: when --input is (or sits beside) a real inputs.jsonl,
        # measure the TRUE average discrete-action rate from the event stream
        # rather than frame cadence. This is the PRD-correct metric.
        inputs_path = _sibling_inputs_path(args.input) if args.input else None
        if inputs_path is not None:
            rate = average_action_rate_from_inputs(inputs_path)
            result = analyze_capture_quality([rate])
            result["source"] = "inputs.jsonl"
            result["inputs_path"] = str(inputs_path)
        else:
            if args.actions:
                actions = args.actions
            else:
                actions = load_actions_from_file(args.input)

            if not actions:
                print("No actions to analyze")
                return 2

            result = analyze_capture_quality(actions)

        if args.json:
            print(json.dumps(result))
        else:
            print(f"Actions/sec (avg): {result['median_actions_per_second']}")
            print(f"Range: [{result['min_actions_per_second']}, {result['max_actions_per_second']}]")
            print(f"Sample count: {result['sample_count']}")
            print(f"Quality: {result['quality_status']}")
            print(f"Status: {'PASSED' if result['in_range'] else 'FAILED'}")

        return 0 if result["in_range"] else 1

    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
