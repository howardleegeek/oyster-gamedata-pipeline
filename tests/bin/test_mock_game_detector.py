#!/usr/bin/env python3
"""Tests for bin/mock_game_detector.py — fake game-detection for smoke/CI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Import the module under test
sys.path.insert(0, str(Path(__file__).parents[2] / "bin"))
import mock_game_detector


class TestDetectGame:
    """Tests for detect_game function (default and override behaviour)."""

    def test_returns_dict(self):
        """detect_game should return a dict."""
        result = mock_game_detector.detect_game()
        assert isinstance(result, dict)

    def test_default_keys_present(self):
        """Default result should contain game, pid, window_title keys."""
        result = mock_game_detector.detect_game()
        assert "game" in result
        assert "pid" in result
        assert "window_title" in result

    def test_default_values_are_stable(self):
        """Default values are deterministic so downstream smoke pipelines
        can diff the output."""
        result = mock_game_detector.detect_game()
        assert result == {
            "game": "minecraft",
            "pid": 12345,
            "window_title": "MC 1.21.4",
        }

    def test_default_pid_is_int(self):
        """Default pid is an int (process-id typed)."""
        result = mock_game_detector.detect_game()
        assert isinstance(result["pid"], int)
        assert result["pid"] > 0

    def test_override_replaces_game(self):
        """override['game'] should replace the default game name."""
        result = mock_game_detector.detect_game(override={"game": "csgo"})
        assert result["game"] == "csgo"
        # Other keys untouched
        assert result["pid"] == 12345
        assert result["window_title"] == "MC 1.21.4"

    def test_override_replaces_pid(self):
        """override['pid'] should replace the default pid."""
        result = mock_game_detector.detect_game(override={"pid": 99})
        assert result["pid"] == 99
        assert result["game"] == "minecraft"

    def test_override_replaces_window_title(self):
        """override['window_title'] should replace the default window title."""
        result = mock_game_detector.detect_game(override={"window_title": "HL2"})
        assert result["window_title"] == "HL2"

    def test_override_multiple_keys(self):
        """Multiple override keys all applied at once."""
        result = mock_game_detector.detect_game(
            override={"game": "doom", "pid": 7, "window_title": "DOOM Eternal"}
        )
        assert result == {
            "game": "doom",
            "pid": 7,
            "window_title": "DOOM Eternal",
        }

    def test_override_can_add_new_keys(self):
        """Override may add keys not in the default dict."""
        result = mock_game_detector.detect_game(override={"new_field": "abc"})
        assert result["new_field"] == "abc"
        # Default keys still present
        assert result["game"] == "minecraft"

    def test_empty_override_returns_defaults(self):
        """Passing an empty dict should yield default result."""
        result = mock_game_detector.detect_game(override={})
        assert result == {
            "game": "minecraft",
            "pid": 12345,
            "window_title": "MC 1.21.4",
        }

    def test_none_override_returns_defaults(self):
        """Passing None (or no arg) should yield default result."""
        result = mock_game_detector.detect_game(override=None)
        assert result == {
            "game": "minecraft",
            "pid": 12345,
            "window_title": "MC 1.21.4",
        }

    def test_override_does_not_mutate_a_second_default_call(self):
        """Each call constructs a fresh dict; one override should not leak."""
        mock_game_detector.detect_game(override={"game": "csgo"})
        fresh = mock_game_detector.detect_game()
        assert fresh["game"] == "minecraft"


class TestMainCli:
    """Tests for main() CLI entry point."""

    def test_main_returns_zero(self):
        """main() should return 0 on success."""
        assert mock_game_detector.main() == 0

    def test_main_accepts_argv_list(self):
        """main(argv=[...]) should accept a custom argv list."""
        assert mock_game_detector.main(argv=[]) == 0


class TestSubprocess:
    """Subprocess-level end-to-end test (runs the script as a CLI)."""

    def test_script_runs_and_prints_json(self, tmp_path):
        """Running the script should print valid JSON to stdout."""
        script_path = Path(__file__).parents[2] / "bin" / "mock_game_detector.py"
        assert script_path.exists(), f"missing script: {script_path}"

        proc = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"

        # Stdout should be exactly one JSON line.
        stdout = proc.stdout.strip()
        parsed = json.loads(stdout)
        assert isinstance(parsed, dict)
        assert parsed["game"] == "minecraft"
        assert parsed["pid"] == 12345
        assert parsed["window_title"] == "MC 1.21.4"
