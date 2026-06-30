#!/usr/bin/env python3
"""Tests for bin/consent_dialog_cli.py — first-run consent dialog."""

from __future__ import annotations

from unittest import mock

from bin import consent_dialog_cli


class TestAsk:
    """Tests for _ask() function."""

    def test_ask_empty_input_returns_default_yes(self):
        """Empty input with default_yes=True should return True."""
        with mock.patch("builtins.input", return_value=""):
            result = consent_dialog_cli._ask("Test prompt?", True)
            assert result is True

    def test_ask_empty_input_returns_default_no(self):
        """Empty input with default_yes=False should return False."""
        with mock.patch("builtins.input", return_value=""):
            result = consent_dialog_cli._ask("Test prompt?", False)
            assert result is False

    def test_ask_yes_variants(self):
        """Various 'yes' inputs should return True."""
        for yes_input in ("y", "Y", "yes", "YES", "Yes"):
            with mock.patch("builtins.input", return_value=yes_input):
                result = consent_dialog_cli._ask("Test?", True)
                assert result is True

    def test_ask_no_variants(self):
        """Various 'no' inputs should return False."""
        for no_input in ("n", "N", "no", "NO", "No"):
            with mock.patch("builtins.input", return_value=no_input):
                result = consent_dialog_cli._ask("Test?", False)
                assert result is False

    def test_ask_invalid_then_valid(self):
        """Invalid input followed by valid should return the valid result."""
        inputs = iter(["maybe", "y"])
        with mock.patch("builtins.input", side_effect=lambda _: next(inputs)):
            result = consent_dialog_cli._ask("Test?", True)
            assert result is True

    def test_ask_eof_returns_false(self):
        """EOFError should return False for safety."""
        with mock.patch("builtins.input", side_effect=EOFError()):
            result = consent_dialog_cli._ask("Test?", True)
            assert result is False


class TestRunDialog:
    """Tests for run_dialog() function."""

    def test_run_dialog_returns_expected_keys(self):
        """run_dialog should return dict with expected keys."""
        # Mock _ask to return False for all prompts (user declines everything)
        with mock.patch.object(consent_dialog_cli, "_ask", return_value=False):
            result = consent_dialog_cli.run_dialog()

        expected_keys = {
            "screen_record",
            "upload",
            "oauth",
            "auto_update",
            "telemetry",
        }
        assert expected_keys.issubset(result.keys())

    def test_run_dialog_all_yes(self):
        """All prompts answered yes should return True for all."""
        with mock.patch.object(consent_dialog_cli, "_ask", return_value=True):
            result = consent_dialog_cli.run_dialog()

        for key in result:
            assert result[key] is True

    def test_run_dialog_all_no(self):
        """All prompts answered no should return False for all."""
        with mock.patch.object(consent_dialog_cli, "_ask", return_value=False):
            result = consent_dialog_cli.run_dialog()

        for key in result:
            assert result[key] is False


class TestPrompts:
    """Tests for prompt configuration."""

    def test_prompts_count(self):
        """Should have exactly 5 consent prompts."""
        assert len(consent_dialog_cli._PROMPTS) == 5

    def test_prompts_have_three_elements(self):
        """Each prompt should have (title, description, default_yes)."""
        for prompt in consent_dialog_cli._PROMPTS:
            assert len(prompt) == 3
            assert isinstance(prompt[0], str)
            assert isinstance(prompt[1], str)
            assert isinstance(prompt[2], bool)

    def test_telemetry_default_is_no(self):
        """Telemetry prompt should default to False (optional)."""
        telemetry_prompt = consent_dialog_cli._PROMPTS[4]
        assert telemetry_prompt[0] == "Anonymous Telemetry (optional)"
        assert telemetry_prompt[2] is False

    def test_other_prompts_default_yes(self):
        """All non-telemetry prompts should default to True."""
        for i, prompt in enumerate(consent_dialog_cli._PROMPTS):
            if i != 4:  # Skip telemetry
                assert prompt[2] is True


class TestMain:
    """Tests for main() CLI function."""

    def test_main_exists(self):
        """main function should exist."""
        assert hasattr(consent_dialog_cli, "main")
        assert callable(consent_dialog_cli.main)

    def test_main_is_int_return(self):
        """main should return an integer exit code."""
        with mock.patch.object(consent_dialog_cli, "run_dialog", return_value={"screen_record": True}):
            result = consent_dialog_cli.main()
            assert isinstance(result, int)
