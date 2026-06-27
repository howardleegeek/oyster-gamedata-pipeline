#!/usr/bin/env python3
"""
test_input_latency_telemetry.py — telemetry produces valid p99 < 100ms on synthetic data

Tests the input-to-effect latency collector with synthetic inputs.jsonl + game_state.jsonl.
Verifies:
1. Latencies are computed correctly from WASD press → velocity change
2. p99 latency < 100ms on synthetic data
3. Output JSON has correct schema
4. Non-WASD keys are ignored
5. Key-up events are ignored
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

# Add bin/ to path so we can import the telemetry module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

from input_latency_telemetry import (
    VELOCITY_KEYS,
    WASD_MAP,
    compute_latencies,
    write_output,
)


def create_synthetic_inputs(path, events):
    """Write synthetic input events to a JSONL file."""
    with open(path, "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")


def create_synthetic_game_state(path, ticks):
    """Write synthetic game state ticks to a JSONL file."""
    with open(path, "w") as f:
        for tick in ticks:
            f.write(json.dumps(tick) + "\n")


class TestInputLatencyTelemetry(unittest.TestCase):
    """Test input-to-effect latency computation."""

    def setUp(self):
        """Create temporary directory for test files."""
        self.tmpdir = tempfile.mkdtemp()
        self.inputs_path = os.path.join(self.tmpdir, "inputs.jsonl")
        self.game_state_path = os.path.join(self.tmpdir, "game_state.jsonl")
        self.output_path = os.path.join(self.tmpdir, "input_latency.json")

    def tearDown(self):
        """Clean up temporary directory."""
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_basic_wasd_latency(self):
        """Single W press → velocity_z change produces correct latency."""
        base_t = 1_000_000_000_000  # 1000s in ns

        # Input: W press at t=base_t
        inputs = [
            {
                "type": "KEYBOARD",
                "vk_code": 87,  # W
                "action": "press",
                "t_ns": base_t,
            }
        ]

        # Game state: velocity_z changes 15ms after input
        game_ticks = [
            {"t_ns": base_t - 10_000_000, "velocity_x": 0.0, "velocity_z": 0.0},  # 10ms before
            {
                "t_ns": base_t + 5_000_000,
                "velocity_x": 0.0,
                "velocity_z": 0.0,
            },  # 5ms after (still 0)
            {
                "t_ns": base_t + 15_000_000,
                "velocity_x": 0.0,
                "velocity_z": 4.2,
            },  # 15ms after (changed!)
        ]

        create_synthetic_inputs(self.inputs_path, inputs)
        create_synthetic_game_state(self.game_state_path, game_ticks)

        latencies = compute_latencies(self.inputs_path, self.game_state_path)

        self.assertEqual(len(latencies), 1)
        self.assertAlmostEqual(latencies[0], 15.0, places=1)

    def test_multiple_wasd_latencies(self):
        """Multiple WASD presses produce multiple latency measurements."""
        base_t = 1_000_000_000_000

        inputs = [
            {"type": "KEYBOARD", "vk_code": 87, "action": "press", "t_ns": base_t},
            {
                "type": "KEYBOARD",
                "vk_code": 65,
                "action": "press",
                "t_ns": base_t + 100_000_000,
            },  # 100ms later
            {
                "type": "KEYBOARD",
                "vk_code": 83,
                "action": "press",
                "t_ns": base_t + 200_000_000,
            },  # 200ms later
        ]

        game_ticks = [
            {"t_ns": base_t - 1_000_000, "velocity_x": 0.0, "velocity_z": 0.0},
            {"t_ns": base_t + 12_000_000, "velocity_x": 0.0, "velocity_z": 4.0},  # W effect: 12ms
            {"t_ns": base_t + 112_000_000, "velocity_x": -3.5, "velocity_z": 4.0},  # A effect: 12ms
            {
                "t_ns": base_t + 218_000_000,
                "velocity_x": -3.5,
                "velocity_z": -4.0,
            },  # S effect: 18ms
        ]

        create_synthetic_inputs(self.inputs_path, inputs)
        create_synthetic_game_state(self.game_state_path, game_ticks)

        latencies = compute_latencies(self.inputs_path, self.game_state_path)

        self.assertEqual(len(latencies), 3)
        self.assertAlmostEqual(latencies[0], 12.0, places=1)
        self.assertAlmostEqual(latencies[1], 12.0, places=1)
        self.assertAlmostEqual(latencies[2], 18.0, places=1)

    def test_non_wasd_keys_ignored(self):
        """Non-WASD key presses are not counted as input events."""
        base_t = 1_000_000_000_000

        inputs = [
            {
                "type": "KEYBOARD",
                "vk_code": 87,
                "action": "press",
                "t_ns": base_t,
            },  # W - should count
            {
                "type": "KEYBOARD",
                "vk_code": 32,
                "action": "press",
                "t_ns": base_t + 10_000_000,
            },  # Space - ignore
            {
                "type": "KEYBOARD",
                "vk_code": 27,
                "action": "press",
                "t_ns": base_t + 20_000_000,
            },  # Esc - ignore
            {
                "type": "MOUSE",
                "vk_code": 1,
                "action": "press",
                "t_ns": base_t + 30_000_000,
            },  # Mouse - ignore
        ]

        game_ticks = [
            {"t_ns": base_t - 1_000_000, "velocity_x": 0.0, "velocity_z": 0.0},
            {"t_ns": base_t + 10_000_000, "velocity_x": 0.0, "velocity_z": 4.0},
        ]

        create_synthetic_inputs(self.inputs_path, inputs)
        create_synthetic_game_state(self.game_state_path, game_ticks)

        latencies = compute_latencies(self.inputs_path, self.game_state_path)

        self.assertEqual(len(latencies), 1)

    def test_key_up_events_ignored(self):
        """Key release events are not counted."""
        base_t = 1_000_000_000_000

        inputs = [
            {"type": "KEYBOARD", "vk_code": 87, "action": "press", "t_ns": base_t},
            {"type": "KEYBOARD", "vk_code": 87, "action": "release", "t_ns": base_t + 500_000_000},
            {"type": "KEYBOARD", "vk_code": 87, "action": "up", "t_ns": base_t + 600_000_000},
        ]

        game_ticks = [
            {"t_ns": base_t - 1_000_000, "velocity_x": 0.0, "velocity_z": 0.0},
            {"t_ns": base_t + 20_000_000, "velocity_x": 0.0, "velocity_z": 4.0},
        ]

        create_synthetic_inputs(self.inputs_path, inputs)
        create_synthetic_game_state(self.game_state_path, game_ticks)

        latencies = compute_latencies(self.inputs_path, self.game_state_path)

        self.assertEqual(len(latencies), 1)

    def test_p99_under_100ms(self):
        """p99 latency is < 100ms on synthetic data with realistic latencies."""
        base_t = 1_000_000_000_000

        # Generate 100 WASD presses with latencies between 5-30ms
        inputs = []
        game_ticks = [{"t_ns": base_t - 1_000_000, "velocity_x": 0.0, "velocity_z": 0.0}]

        vk_codes = [87, 65, 83, 68]  # W, A, S, D
        for i in range(100):
            input_t = base_t + i * 50_000_000  # 50ms apart
            latency_ms = 5 + (i % 26)  # 5-30ms range
            latency_ns = int(latency_ms * 1_000_000)

            inputs.append(
                {
                    "type": "KEYBOARD",
                    "vk_code": vk_codes[i % 4],
                    "action": "press",
                    "t_ns": input_t,
                }
            )

            game_ticks.append(
                {
                    "t_ns": input_t + latency_ns,
                    "velocity_x": float(i % 2) * 3.0,
                    "velocity_z": float((i + 1) % 2) * 4.0,
                }
            )

        create_synthetic_inputs(self.inputs_path, inputs)
        create_synthetic_game_state(self.game_state_path, game_ticks)

        latencies = compute_latencies(self.inputs_path, self.game_state_path)

        self.assertGreater(len(latencies), 0)

        # Compute p99
        sorted_latencies = sorted(latencies)
        p99_idx = int(len(sorted_latencies) * 0.99)
        p99 = sorted_latencies[min(p99_idx, len(sorted_latencies) - 1)]

        self.assertLess(p99, 100.0, f"p99 latency {p99}ms should be < 100ms")

    def test_output_schema(self):
        """Output JSON has correct schema with all required fields."""
        base_t = 1_000_000_000_000

        inputs = [
            {"type": "KEYBOARD", "vk_code": 87, "action": "press", "t_ns": base_t},
        ]
        game_ticks = [
            {"t_ns": base_t - 1_000_000, "velocity_x": 0.0, "velocity_z": 0.0},
            {"t_ns": base_t + 15_000_000, "velocity_x": 0.0, "velocity_z": 4.0},
        ]

        create_synthetic_inputs(self.inputs_path, inputs)
        create_synthetic_game_state(self.game_state_path, game_ticks)

        latencies = compute_latencies(self.inputs_path, self.game_state_path)
        write_output(latencies, self.output_path)

        # Verify output file
        with open(self.output_path) as f:
            saved = json.load(f)

        # Required fields
        self.assertIn("latencies", saved)
        self.assertIn("method", saved)
        self.assertIn("count", saved)
        self.assertIn("p50", saved)
        self.assertIn("p95", saved)
        self.assertIn("p99", saved)
        self.assertIn("min", saved)
        self.assertIn("max", saved)
        self.assertIn("mean", saved)

        # Method must be the canonical one
        self.assertEqual(saved["method"], "wasd_press_to_velocity_change")

        # Count matches
        self.assertEqual(saved["count"], len(latencies))

    def test_empty_inputs(self):
        """Empty input file produces empty latencies list."""
        create_synthetic_inputs(self.inputs_path, [])
        create_synthetic_game_state(
            self.game_state_path,
            [
                {"t_ns": 1000, "velocity_x": 0.0, "velocity_z": 0.0},
            ],
        )

        latencies = compute_latencies(self.inputs_path, self.game_state_path)
        self.assertEqual(latencies, [])

    def test_no_velocity_change(self):
        """If velocity never changes, no latency is recorded."""
        base_t = 1_000_000_000_000

        inputs = [
            {"type": "KEYBOARD", "vk_code": 87, "action": "press", "t_ns": base_t},
        ]
        game_ticks = [
            {"t_ns": base_t - 1_000_000, "velocity_x": 0.0, "velocity_z": 0.0},
            {"t_ns": base_t + 10_000_000, "velocity_x": 0.0, "velocity_z": 0.0},
            {"t_ns": base_t + 20_000_000, "velocity_x": 0.0, "velocity_z": 0.0},
        ]

        create_synthetic_inputs(self.inputs_path, inputs)
        create_synthetic_game_state(self.game_state_path, game_ticks)

        latencies = compute_latencies(self.inputs_path, self.game_state_path)
        self.assertEqual(latencies, [])

    def test_wasd_map_correctness(self):
        """WASD_MAP has correct key codes."""
        self.assertEqual(WASD_MAP[87], "forward")  # W
        self.assertEqual(WASD_MAP[65], "left")  # A
        self.assertEqual(WASD_MAP[83], "backward")  # S
        self.assertEqual(WASD_MAP[68], "right")  # D

    def test_velocity_keys_mapping(self):
        """VELOCITY_KEYS maps directions to correct velocity components."""
        self.assertEqual(VELOCITY_KEYS["forward"], "velocity_z")
        self.assertEqual(VELOCITY_KEYS["backward"], "velocity_z")
        self.assertEqual(VELOCITY_KEYS["left"], "velocity_x")
        self.assertEqual(VELOCITY_KEYS["right"], "velocity_x")


if __name__ == "__main__":
    unittest.main()
