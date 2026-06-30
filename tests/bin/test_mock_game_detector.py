"""Tests for bin/mock_game_detector.py."""

import json

from bin.mock_game_detector import detect_game, main


class TestDetectGame:
    """Tests for detect_game function."""

    def test_detect_game_returns_dict(self):
        """detect_game returns a dictionary."""
        result = detect_game()
        assert isinstance(result, dict)

    def test_detect_game_has_required_keys(self):
        """Result contains required keys: game, pid, window_title."""
        result = detect_game()
        assert "game" in result
        assert "pid" in result
        assert "window_title" in result

    def test_detect_game_default_values(self):
        """Default values are minecraft, 12345, MC 1.21.4."""
        result = detect_game()
        assert result["game"] == "minecraft"
        assert result["pid"] == 12345
        assert result["window_title"] == "MC 1.21.4"

    def test_detect_game_override_game(self):
        """override parameter changes game value."""
        result = detect_game(override={"game": "valheim"})
        assert result["game"] == "valheim"
        assert result["pid"] == 12345
        assert result["window_title"] == "MC 1.21.4"

    def test_detect_game_override_pid(self):
        """override parameter changes pid value."""
        result = detect_game(override={"pid": 99999})
        assert result["game"] == "minecraft"
        assert result["pid"] == 99999

    def test_detect_game_override_window_title(self):
        """override parameter changes window_title value."""
        result = detect_game(override={"window_title": "Custom Window"})
        assert result["window_title"] == "Custom Window"

    def test_detect_game_override_multiple(self):
        """override parameter can change multiple values."""
        result = detect_game(override={"game": "terraria", "pid": 42})
        assert result["game"] == "terraria"
        assert result["pid"] == 42

    def test_detect_game_override_preserves_unmodified(self):
        """override preserves unmodified default values."""
        result = detect_game(override={"game": "valheim"})
        assert result["window_title"] == "MC 1.21.4"
        assert result["pid"] == 12345


class TestMain:
    """Tests for main CLI function."""

    def test_main_returns_zero_on_success(self):
        """main returns 0 on successful execution."""
        exit_code = main([])
        assert exit_code == 0

    def test_main_outputs_valid_json(self):
        """main outputs valid JSON to stdout."""
        import io
        import sys

        captured = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = captured
            main([])
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        parsed = json.loads(output)
        assert isinstance(parsed, dict)
        assert "game" in parsed
        assert "pid" in parsed
        assert "window_title" in parsed

    def test_main_outputs_dict_with_defaults(self):
        """main outputs dict with default values."""
        import io
        import sys

        captured = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = captured
            main([])
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue().strip()
        parsed = json.loads(output)
        assert parsed["game"] == "minecraft"
        assert parsed["pid"] == 12345
        assert parsed["window_title"] == "MC 1.21.4"

    def test_main_includes_newline_in_output(self):
        """main output includes trailing newline."""
        import io
        import sys

        captured = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = captured
            main([])
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        assert output.endswith("\n")
