#!/usr/bin/env python3
"""
Tests for bin/input_latency_analyzer.py

Fixtures:
  - Synthetic inputs.jsonl (5 W press events)
  - Synthetic game_state.jsonl (with is_paused=true ticks)
  - Synthetic input_latency.json

Assertions:
  - HONEST + PAUSE_MENU classification correct
  - honest_p99 < unfiltered p99
"""

import json
import os
import shutil
import sys
import tempfile

import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bin.input_latency_analyzer import (
    analyze,
    check_tick_lag,
    check_ticks_around,
    classify_sample,
    find_game_state_at,
    percentile,
)


@pytest.fixture
def session_dir():
    """
    Create a temporary session directory with synthetic data:
    - input_latency.json: 10 latency samples (mix of honest + contaminated)
    - inputs.jsonl: 10 input events
    - game_state.jsonl: game state ticks including paused and normal states
    """
    tmpdir = tempfile.mkdtemp()
    try:
        # --- input_latency.json ---
        # 10 samples: 7 honest (low latency), 3 contaminated (high latency from pause)
        latency_data = {
            "latencies": [12, 18, 23, 45, 67, 89, 95, 532, 575, 612],
            "timestamps": [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000],
            "method": "wasd_press_to_velocity_change",
            "count": 10,
            "p50": 56,
            "p95": 575,
            "p99": 612,
        }
        with open(os.path.join(tmpdir, "input_latency.json"), "w") as f:
            json.dump(latency_data, f)

        # --- inputs.jsonl ---
        inputs = [
            {"timestamp_ms": 100, "frame": 1, "action": "W_press"},
            {"timestamp_ms": 200, "frame": 2, "action": "W_press"},
            {"timestamp_ms": 300, "frame": 3, "action": "W_press"},
            {"timestamp_ms": 400, "frame": 4, "action": "W_press"},
            {"timestamp_ms": 500, "frame": 5, "action": "W_press"},
            {"timestamp_ms": 600, "frame": 6, "action": "W_press"},
            {"timestamp_ms": 700, "frame": 7, "action": "W_press"},
            {"timestamp_ms": 800, "frame": 8, "action": "W_press"},
            {"timestamp_ms": 900, "frame": 9, "action": "W_press"},
            {"timestamp_ms": 1000, "frame": 10, "action": "W_press"},
        ]
        with open(os.path.join(tmpdir, "inputs.jsonl"), "w") as f:
            for inp in inputs:
                f.write(json.dumps(inp) + "\n")

        # --- game_state.jsonl ---
        # Normal ticks at 16ms intervals, with pause at 800-1000ms
        game_states = []
        for i in range(70):
            ts = i * 16  # 0, 16, 32, ...
            is_paused = 800 <= ts <= 1000
            gs = {
                "timestamp_ms": ts,
                "t": ts,
                "game_state": {
                    "is_paused": is_paused,
                    "tick": i,
                },
                "player": {
                    "health": 10,
                    "active_effects": [],
                    "velocity": [0, 0, 0],
                },
            }
            game_states.append(gs)

        with open(os.path.join(tmpdir, "game_state.jsonl"), "w") as f:
            for gs in game_states:
                f.write(json.dumps(gs) + "\n")

        yield tmpdir
    finally:
        shutil.rmtree(tmpdir)


@pytest.fixture
def session_dir_with_death():
    """Session with death screen contamination."""
    tmpdir = tempfile.mkdtemp()
    try:
        latency_data = {
            "latencies": [15, 20, 25, 30, 35, 400, 450, 500],
            "timestamps": [100, 200, 300, 400, 500, 600, 700, 800],
            "method": "wasd_press_to_velocity_change",
            "count": 8,
            "p50": 32,
            "p95": 450,
            "p99": 500,
        }
        with open(os.path.join(tmpdir, "input_latency.json"), "w") as f:
            json.dump(latency_data, f)

        inputs = [
            {"timestamp_ms": 100, "frame": 1, "action": "W_press"},
            {"timestamp_ms": 200, "frame": 2, "action": "W_press"},
            {"timestamp_ms": 300, "frame": 3, "action": "W_press"},
            {"timestamp_ms": 400, "frame": 4, "action": "W_press"},
            {"timestamp_ms": 500, "frame": 5, "action": "W_press"},
            {"timestamp_ms": 600, "frame": 6, "action": "W_press"},
            {"timestamp_ms": 700, "frame": 7, "action": "W_press"},
            {"timestamp_ms": 800, "frame": 8, "action": "W_press"},
        ]
        with open(os.path.join(tmpdir, "inputs.jsonl"), "w") as f:
            for inp in inputs:
                f.write(json.dumps(inp) + "\n")

        game_states = []
        for i in range(60):
            ts = i * 16
            # Death at timestamps near 600, 700, 800
            # Closest game state ticks: 592, 608, 688, 704, 784, 800
            # Use range 590-810 to cover all of them
            health = 0 if 590 <= ts <= 810 else 10
            gs = {
                "timestamp_ms": ts,
                "t": ts,
                "game_state": {"is_paused": False, "tick": i},
                "player": {"health": health, "active_effects": []},
            }
            game_states.append(gs)
        with open(os.path.join(tmpdir, "game_state.jsonl"), "w") as f:
            for gs in game_states:
                f.write(json.dumps(gs) + "\n")

        yield tmpdir
    finally:
        shutil.rmtree(tmpdir)


@pytest.fixture
def session_dir_with_potion():
    """Session with potion slowness contamination."""
    tmpdir = tempfile.mkdtemp()
    try:
        latency_data = {
            "latencies": [10, 15, 20, 25, 30, 250, 300, 350],
            "timestamps": [100, 200, 300, 400, 500, 600, 700, 800],
            "method": "wasd_press_to_velocity_change",
            "count": 8,
            "p50": 27,
            "p95": 300,
            "p99": 350,
        }
        with open(os.path.join(tmpdir, "input_latency.json"), "w") as f:
            json.dump(latency_data, f)

        inputs = [
            {"timestamp_ms": 100, "frame": 1, "action": "W_press"},
            {"timestamp_ms": 200, "frame": 2, "action": "W_press"},
            {"timestamp_ms": 300, "frame": 3, "action": "W_press"},
            {"timestamp_ms": 400, "frame": 4, "action": "W_press"},
            {"timestamp_ms": 500, "frame": 5, "action": "W_press"},
            {"timestamp_ms": 600, "frame": 6, "action": "W_press"},
            {"timestamp_ms": 700, "frame": 7, "action": "W_press"},
            {"timestamp_ms": 800, "frame": 8, "action": "W_press"},
        ]
        with open(os.path.join(tmpdir, "inputs.jsonl"), "w") as f:
            for inp in inputs:
                f.write(json.dumps(inp) + "\n")

        game_states = []
        for i in range(60):
            ts = i * 16
            # Potion at timestamps near 600, 700, 800
            # Closest game state ticks: 592, 608, 688, 704, 784, 800
            effects = ["slowness"] if 590 <= ts <= 810 else []
            gs = {
                "timestamp_ms": ts,
                "t": ts,
                "game_state": {"is_paused": False, "tick": i},
                "player": {"health": 10, "active_effects": effects},
            }
            game_states.append(gs)
        with open(os.path.join(tmpdir, "game_state.jsonl"), "w") as f:
            for gs in game_states:
                f.write(json.dumps(gs) + "\n")

        yield tmpdir
    finally:
        shutil.rmtree(tmpdir)


@pytest.fixture
def session_dir_with_tick_lag():
    """Session with tick lag contamination."""
    tmpdir = tempfile.mkdtemp()
    try:
        latency_data = {
            "latencies": [12, 18, 22, 28, 35, 400, 450, 500],
            "timestamps": [100, 200, 300, 400, 500, 600, 700, 800],
            "method": "wasd_press_to_velocity_change",
            "count": 8,
            "p50": 31,
            "p95": 450,
            "p99": 500,
        }
        with open(os.path.join(tmpdir, "input_latency.json"), "w") as f:
            json.dump(latency_data, f)

        inputs = [
            {"timestamp_ms": 100, "frame": 1, "action": "W_press"},
            {"timestamp_ms": 200, "frame": 2, "action": "W_press"},
            {"timestamp_ms": 300, "frame": 3, "action": "W_press"},
            {"timestamp_ms": 400, "frame": 4, "action": "W_press"},
            {"timestamp_ms": 500, "frame": 5, "action": "W_press"},
            {"timestamp_ms": 600, "frame": 6, "action": "W_press"},
            {"timestamp_ms": 700, "frame": 7, "action": "W_press"},
            {"timestamp_ms": 800, "frame": 8, "action": "W_press"},
        ]
        with open(os.path.join(tmpdir, "inputs.jsonl"), "w") as f:
            for inp in inputs:
                f.write(json.dumps(inp) + "\n")

        # Game states with a tick lag spike before 600ms
        game_states = []
        tick_times = [
            0,
            16,
            32,
            48,
            64,
            80,
            96,
            112,
            128,
            144,
            160,
            176,
            192,
            208,
            224,
            240,
            256,
            272,
            288,
            304,
            320,
            336,
            352,
            368,
            384,
            400,
            416,
            432,
            448,
            464,
            480,
            496,
            512,
            528,
            544,
            560,
            576,
            592,
            608,
            624,
            # Lag spike: gap of 150ms
            774,
            790,
            806,
            822,
            838,
            854,
            870,
            886,
            902,
            918,
        ]
        for i, ts in enumerate(tick_times):
            gs = {
                "timestamp_ms": ts,
                "t": ts,
                "game_state": {"is_paused": False, "tick": i},
                "player": {"health": 10, "active_effects": []},
            }
            game_states.append(gs)
        with open(os.path.join(tmpdir, "game_state.jsonl"), "w") as f:
            for gs in game_states:
                f.write(json.dumps(gs) + "\n")

        yield tmpdir
    finally:
        shutil.rmtree(tmpdir)


# ============================================================
# Unit tests for helper functions
# ============================================================


class TestPercentile:
    def test_p50(self):
        assert percentile([10, 20, 30, 40, 50], 50) == 30

    def test_p95(self):
        vals = list(range(1, 101))
        assert percentile(vals, 95) == 95

    def test_p99(self):
        vals = list(range(1, 101))
        assert percentile(vals, 99) == 99

    def test_empty(self):
        assert percentile([], 50) == 0

    def test_single(self):
        assert percentile([42], 50) == 42


class TestFindGameStateAt:
    def test_exact_match(self):
        game_states = [
            {"timestamp_ms": 100, "game_state": {"is_paused": False}},
            {"timestamp_ms": 200, "game_state": {"is_paused": True}},
        ]
        gs = find_game_state_at(game_states, 200)
        assert gs is not None
        assert gs["game_state"]["is_paused"] is True

    def test_close_match(self):
        game_states = [
            {"timestamp_ms": 100, "game_state": {"is_paused": False}},
            {"timestamp_ms": 200, "game_state": {"is_paused": True}},
        ]
        gs = find_game_state_at(game_states, 195)
        assert gs is not None
        assert gs["game_state"]["is_paused"] is True

    def test_no_match(self):
        game_states = [
            {"timestamp_ms": 100, "game_state": {"is_paused": False}},
        ]
        gs = find_game_state_at(game_states, 9999)
        assert gs is None


class TestCheckTicksAround:
    def test_enough_ticks(self):
        game_states = [{"timestamp_ms": i * 10} for i in range(20)]
        assert check_ticks_around(game_states, 100, window_ms=100, min_ticks=3) is True

    def test_not_enough_ticks(self):
        game_states = [{"timestamp_ms": 0}, {"timestamp_ms": 500}]
        assert check_ticks_around(game_states, 100, window_ms=100, min_ticks=3) is False


class TestCheckTickLag:
    def test_no_lag(self):
        game_states = [{"timestamp_ms": i * 16} for i in range(20)]
        assert check_tick_lag(game_states, 300, lookback_ms=200, threshold_ms=100) is False

    def test_lag_detected(self):
        game_states = [
            {"timestamp_ms": 0},
            {"timestamp_ms": 16},
            {"timestamp_ms": 32},
            {"timestamp_ms": 48},
            {"timestamp_ms": 64},
            {"timestamp_ms": 80},
            {"timestamp_ms": 96},
            {"timestamp_ms": 112},
            {"timestamp_ms": 128},
            {"timestamp_ms": 144},
            {"timestamp_ms": 160},
            {"timestamp_ms": 176},
            {"timestamp_ms": 192},
            {"timestamp_ms": 208},
            {"timestamp_ms": 224},
            {"timestamp_ms": 240},
            {"timestamp_ms": 256},
            {"timestamp_ms": 272},
            {"timestamp_ms": 288},
            {"timestamp_ms": 304},
            # Lag spike: 150ms gap
            {"timestamp_ms": 454},
        ]
        assert check_tick_lag(game_states, 500, lookback_ms=200, threshold_ms=100) is True


class TestClassifySample:
    def test_honest(self):
        game_states = [
            {
                "timestamp_ms": i * 16,
                "game_state": {"is_paused": False},
                "player": {"health": 10, "active_effects": []},
            }
            for i in range(20)
        ]
        bucket = classify_sample(25, 100, game_states)
        assert bucket == "HONEST"

    def test_pause_menu(self):
        game_states = [
            {
                "timestamp_ms": i * 16,
                "game_state": {"is_paused": (80 <= i * 16 <= 120)},
                "player": {"health": 10, "active_effects": []},
            }
            for i in range(20)
        ]
        bucket = classify_sample(532, 100, game_states)
        assert bucket == "PAUSE_MENU"

    def test_death_screen(self):
        game_states = [
            {
                "timestamp_ms": i * 16,
                "game_state": {"is_paused": False},
                "player": {"health": 0 if 80 <= i * 16 <= 120 else 10, "active_effects": []},
            }
            for i in range(20)
        ]
        bucket = classify_sample(450, 100, game_states)
        assert bucket == "DEATH_SCREEN"

    def test_potion_slowness(self):
        game_states = [
            {
                "timestamp_ms": i * 16,
                "game_state": {"is_paused": False},
                "player": {
                    "health": 10,
                    "active_effects": ["slowness"] if 80 <= i * 16 <= 120 else [],
                },
            }
            for i in range(20)
        ]
        bucket = classify_sample(250, 100, game_states)
        assert bucket == "POTION_SLOWNESS"

    def test_potion_weakness(self):
        game_states = [
            {
                "timestamp_ms": i * 16,
                "game_state": {"is_paused": False},
                "player": {
                    "health": 10,
                    "active_effects": ["weakness"] if 80 <= i * 16 <= 120 else [],
                },
            }
            for i in range(20)
        ]
        bucket = classify_sample(250, 100, game_states)
        assert bucket == "POTION_SLOWNESS"

    def test_tick_lag(self):
        game_states = [
            {
                "timestamp_ms": i * 16,
                "game_state": {"is_paused": False},
                "player": {"health": 10, "active_effects": []},
            }
            for i in range(20)
        ]
        # Add a lag spike
        game_states.append(
            {
                "timestamp_ms": 500,
                "game_state": {"is_paused": False},
                "player": {"health": 10, "active_effects": []},
            }
        )
        bucket = classify_sample(400, 500, game_states)
        assert bucket == "TICK_LAG"

    def test_other_high_latency(self):
        # No game states at all — high latency with no contamination detected
        game_states = []
        bucket = classify_sample(300, 100, game_states)
        assert bucket == "OTHER"

    def test_other_low_latency(self):
        # No game states, but low latency — should be HONEST
        game_states = []
        bucket = classify_sample(20, 100, game_states)
        assert bucket == "HONEST"


# ============================================================
# Integration tests for analyze()
# ============================================================


class TestAnalyze:
    def test_pause_menu_classification(self, session_dir):
        """Assert HONEST + PAUSE_MENU classification correct."""
        result = analyze(session_dir, json_output=True)

        # 7 honest (timestamps 100-700), 3 pause (timestamps 800-1000)
        assert result["honest"] == 7
        assert result["pause_menu"] == 3
        assert result["death_screen"] == 0
        assert result["potion_slowness"] == 0
        assert result["tick_lag"] == 0
        assert result["other"] == 0

    def test_honest_p99_less_than_unfiltered(self, session_dir):
        """Assert honest_p99 < unfiltered p99."""
        result = analyze(session_dir, json_output=True)
        assert result["honest_p99"] < result["unfiltered_p99"]

    def test_verdict_pass(self, session_dir):
        """Honest p99 < 100ms and no OTHER → PASS."""
        result = analyze(session_dir, json_output=True)
        assert result["verdict"] == "PASS"

    def test_output_file_created(self, session_dir):
        """input_latency_v2.json is written."""
        analyze(session_dir, json_output=True)
        output_path = os.path.join(session_dir, "input_latency_v2.json")
        assert os.path.exists(output_path)

        with open(output_path) as f:
            data = json.load(f)

        assert data["method"] == "honest_filtered_p99"
        assert "HONEST" in data["filter_buckets"]
        assert "PAUSE_MENU" in data["filter_buckets"]
        assert len(data["honest_latencies"]) == 7
        assert len(data["exclusion_reason_log"]) == 3

    def test_death_screen_classification(self, session_dir_with_death):
        """Death screen samples classified correctly."""
        result = analyze(session_dir_with_death, json_output=True)
        assert result["death_screen"] == 3
        assert result["honest"] == 5
        assert result["verdict"] == "PASS"

    def test_potion_slowness_classification(self, session_dir_with_potion):
        """Potion slowness samples classified correctly."""
        result = analyze(session_dir_with_potion, json_output=True)
        assert result["potion_slowness"] == 3
        assert result["honest"] == 5
        assert result["verdict"] == "PASS"

    def test_tick_lag_classification(self, session_dir_with_tick_lag):
        """Tick lag samples classified correctly."""
        result = analyze(session_dir_with_tick_lag, json_output=True)
        assert result["tick_lag"] >= 1
        assert result["honest"] >= 1

    def test_json_output_format(self, session_dir):
        """--json output has all required keys."""
        result = analyze(session_dir, json_output=True)
        required_keys = [
            "total",
            "honest",
            "pause_menu",
            "death_screen",
            "potion_slowness",
            "tick_lag",
            "other",
            "honest_p50",
            "honest_p95",
            "honest_p99",
            "honest_max",
            "verdict",
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

    def test_other_bucket_causes_fail(self):
        """OTHER bucket with high latency should cause FAIL verdict."""
        tmpdir = tempfile.mkdtemp()
        try:
            latency_data = {
                "latencies": [15, 20, 25, 300, 350],
                "timestamps": [100, 200, 300, 400, 500],
                "method": "wasd_press_to_velocity_change",
                "count": 5,
            }
            with open(os.path.join(tmpdir, "input_latency.json"), "w") as f:
                json.dump(latency_data, f)

            # No game states → high latency samples go to OTHER
            with open(os.path.join(tmpdir, "game_state.jsonl"), "w") as f:
                pass  # empty

            with open(os.path.join(tmpdir, "inputs.jsonl"), "w") as f:
                pass  # empty

            result = analyze(tmpdir, json_output=True)
            assert result["other"] == 2
            assert result["verdict"] == "FAIL"
        finally:
            shutil.rmtree(tmpdir)

    def test_exclusion_log_has_frame_info(self, session_dir):
        """Exclusion log entries have frame, latency_ms, bucket."""
        analyze(session_dir, json_output=True)
        output_path = os.path.join(session_dir, "input_latency_v2.json")
        with open(output_path) as f:
            data = json.load(f)

        for entry in data["exclusion_reason_log"]:
            assert "frame" in entry
            assert "latency_ms" in entry
            assert "bucket" in entry
            assert entry["bucket"] == "PAUSE_MENU"
