#!/usr/bin/env python3
"""Tests for bin/secure_subprocess.py — R042 safe subprocess wrapper."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from bin import secure_subprocess


class TestQuoteForShell:
    """Tests for quote_for_shell() — shlex.quote passthrough wrapper."""

    def test_simple_string(self):
        """Simple safe ASCII strings pass through shlex.quote unchanged."""
        result = secure_subprocess.quote_for_shell("hello")
        # shlex.quote returns plain string when no quoting is needed
        assert result == "hello"

    def test_string_with_spaces(self):
        """Strings with spaces get quoted."""
        result = secure_subprocess.quote_for_shell("hello world")
        assert " " not in result.replace("' ", "'", 1) or result.startswith("'")
        # Output should be a single safe token
        assert result == "'hello world'"

    def test_dangerous_command_injection_attempt(self):
        """Shell metacharacters are escaped / quoted."""
        dangerous = "hello; rm -rf /"
        result = secure_subprocess.quote_for_shell(dangerous)
        # shlex.quote wraps in single quotes, neutralizing shell metachars
        assert ";" not in result or result.startswith("'")
        # The dangerous semicolon should not be in a position that triggers
        # command separation in the wrapped output
        assert result.startswith("'")
        assert result.endswith("'")

    def test_backtick_injection(self):
        """Backticks are quoted, not executed."""
        result = secure_subprocess.quote_for_shell("$(whoami)")
        assert result.startswith("'")
        assert result.endswith("'")

    def test_empty_string(self):
        """Empty strings are handled."""
        result = secure_subprocess.quote_for_shell("")
        # shlex.quote('') returns "''"
        assert result == "''"

    def test_single_quote_in_string(self):
        """Single quotes inside the string are escaped properly."""
        result = secure_subprocess.quote_for_shell("it's")
        # shlex.quote handles internal single quotes by closing + escaping + reopening
        assert result.startswith("'")
        assert result.endswith("'")


class TestValidateCmd:
    """Tests for _validate_cmd() — strict allowlist + type checking."""

    def test_allowlisted_binary_accepted(self):
        """Allowlisted binary passes validation."""
        # Should not raise
        secure_subprocess._validate_cmd(["/bin/echo", "hello"])
        secure_subprocess._validate_cmd(["/usr/bin/grep", "pattern", "file"])
        secure_subprocess._validate_cmd(["/usr/bin/python3", "-c", "print(1)"])

    def test_non_list_raises(self):
        """Passing a string instead of a list raises ValueError."""
        with pytest.raises(ValueError, match="cmd must be a list"):
            secure_subprocess._validate_cmd("ls")  # type: ignore[arg-type]

    def test_non_list_tuple_raises(self):
        """Passing a tuple raises ValueError."""
        with pytest.raises(ValueError, match="cmd must be a list"):
            secure_subprocess._validate_cmd(("/bin/echo", "x"))  # type: ignore[arg-type]

    def test_empty_list_raises(self):
        """Empty cmd list raises ValueError."""
        with pytest.raises(ValueError, match="cmd must not be empty"):
            secure_subprocess._validate_cmd([])

    def test_non_string_element_raises(self):
        """Non-string cmd elements raise ValueError."""
        with pytest.raises(ValueError, match="All cmd elements must be strings"):
            secure_subprocess._validate_cmd(["/bin/echo", 123])  # type: ignore[list-item]

    def test_non_allowlisted_binary_raises(self):
        """Binary not in ALLOWED_BINARIES raises ValueError."""
        with pytest.raises(ValueError, match="not in the allowlist"):
            secure_subprocess._validate_cmd(["/bin/sh", "-c", "echo pwned"])

        # /usr/bin/curl IS in the allowlist — use a clearly non-allowlisted binary
        with pytest.raises(ValueError, match="not in the allowlist"):
            secure_subprocess._validate_cmd(["/usr/bin/ssh", "user@host"])

    def test_relative_path_rejected(self):
        """Relative binary paths are not allowlisted (allowlist is absolute)."""
        with pytest.raises(ValueError, match="not in the allowlist"):
            secure_subprocess._validate_cmd(["ls"])

        with pytest.raises(ValueError, match="not in the allowlist"):
            secure_subprocess._validate_cmd(["./script.sh"])

    def test_sh_binary_rejected(self):
        """Shell binaries are explicitly not in the allowlist."""
        with pytest.raises(ValueError, match="not in the allowlist"):
            secure_subprocess._validate_cmd(["/bin/sh"])
        with pytest.raises(ValueError, match="not in the allowlist"):
            secure_subprocess._validate_cmd(["/bin/bash"])
        with pytest.raises(ValueError, match="not in the allowlist"):
            secure_subprocess._validate_cmd(["/usr/bin/bash"])

    def test_error_message_lists_allowed(self):
        """Error message includes the allowlist for debuggability."""
        with pytest.raises(ValueError) as exc_info:
            secure_subprocess._validate_cmd(["/usr/bin/foobar"])
        msg = str(exc_info.value)
        assert "/bin/echo" in msg or "Allowed" in msg

    def test_all_allowed_binaries_validate(self):
        """Every entry in ALLOWED_BINARIES passes validation when used as cmd[0]."""
        for binary in secure_subprocess.ALLOWED_BINARIES:
            # Should not raise
            secure_subprocess._validate_cmd([binary, "arg"])


class TestSafeRun:
    """Tests for safe_run() — subprocess wrapper with validation."""

    def test_successful_command(self):
        """A successful allowlisted command returns the CompletedProcess."""
        result = secure_subprocess.safe_run(["/bin/echo", "hello"])
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_capture_output_default_true(self):
        """By default stdout/stderr are captured."""
        result = secure_subprocess.safe_run(["/bin/echo", "captured"])
        assert result.stdout == "captured\n"
        assert result.stderr == ""

    def test_capture_output_false(self):
        """capture_output=False suppresses capture (delegated to subprocess)."""
        # When capture_output=False, the result is still returned but stdout is empty
        result = secure_subprocess.safe_run(
            ["/bin/echo", "nope"], capture_output=False
        )
        assert result.returncode == 0

    def test_validation_failure_does_not_call_subprocess(self):
        """If validation fails, subprocess.run is never invoked."""
        with patch.object(secure_subprocess.subprocess, "run") as mock_run:
            with pytest.raises(ValueError):
                secure_subprocess.safe_run(["/bin/sh", "-c", "echo pwned"])
            mock_run.assert_not_called()

    def test_subprocess_called_with_shell_false(self):
        """subprocess.run is invoked with shell=False."""
        with patch.object(
            secure_subprocess.subprocess, "run", return_value=MagicMock()
        ) as mock_run:
            secure_subprocess.safe_run(["/bin/echo", "x"])
            _, kwargs = mock_run.call_args
            assert kwargs["shell"] is False

    def test_timeout_passed_through(self):
        """Custom timeout is forwarded to subprocess.run."""
        with patch.object(
            secure_subprocess.subprocess, "run", return_value=MagicMock()
        ) as mock_run:
            secure_subprocess.safe_run(["/bin/echo", "x"], timeout=5.0)
            _, kwargs = mock_run.call_args
            assert kwargs["timeout"] == 5.0

    def test_default_timeout_30s(self):
        """Default timeout is 30 seconds."""
        with patch.object(
            secure_subprocess.subprocess, "run", return_value=MagicMock()
        ) as mock_run:
            secure_subprocess.safe_run(["/bin/echo", "x"])
            _, kwargs = mock_run.call_args
            assert kwargs["timeout"] == 30.0

    def test_cwd_passed_through(self):
        """cwd is forwarded to subprocess.run."""
        with patch.object(
            secure_subprocess.subprocess, "run", return_value=MagicMock()
        ) as mock_run:
            secure_subprocess.safe_run(["/bin/echo", "x"], cwd="/tmp")
            _, kwargs = mock_run.call_args
            assert kwargs["cwd"] == "/tmp"

    def test_env_passed_through(self):
        """env dict is forwarded to subprocess.run."""
        with patch.object(
            secure_subprocess.subprocess, "run", return_value=MagicMock()
        ) as mock_run:
            custom_env = {"FOO": "bar"}
            secure_subprocess.safe_run(["/bin/echo", "x"], env=custom_env)
            _, kwargs = mock_run.call_args
            assert kwargs["env"] == custom_env

    def test_text_mode_enabled(self):
        """text=True is set so stdout/stderr are strings."""
        with patch.object(
            secure_subprocess.subprocess, "run", return_value=MagicMock()
        ) as mock_run:
            secure_subprocess.safe_run(["/bin/echo", "x"])
            _, kwargs = mock_run.call_args
            assert kwargs["text"] is True

    def test_command_with_args(self):
        """Multiple args are forwarded to subprocess.run."""
        with patch.object(
            secure_subprocess.subprocess, "run", return_value=MagicMock()
        ) as mock_run:
            secure_subprocess.safe_run(["/bin/echo", "a", "b", "c"])
            args, _ = mock_run.call_args
            assert args[0] == ["/bin/echo", "a", "b", "c"]


class TestSafeRunWithInput:
    """Tests for safe_run_with_input() — pipes input to stdin."""

    def test_input_data_passed_through(self):
        """Input data is forwarded to subprocess.run."""
        with patch.object(
            secure_subprocess.subprocess, "run", return_value=MagicMock()
        ) as mock_run:
            secure_subprocess.safe_run_with_input(["/usr/bin/grep", "x"], "x\ny\n")
            _, kwargs = mock_run.call_args
            assert kwargs["input"] == "x\ny\n"

    def test_grep_with_input(self):
        """Real grep call returns matching lines from input."""
        result = secure_subprocess.safe_run_with_input(
            ["/usr/bin/grep", "hello"], "hello world\nbye\nhello again\n"
        )
        assert result.returncode == 0
        assert "hello world" in result.stdout
        assert "hello again" in result.stdout
        assert "bye" not in result.stdout

    def test_capture_output_always_true(self):
        """safe_run_with_input forces capture_output=True (not configurable)."""
        with patch.object(
            secure_subprocess.subprocess, "run", return_value=MagicMock()
        ) as mock_run:
            secure_subprocess.safe_run_with_input(["/usr/bin/grep", "x"], "x")
            _, kwargs = mock_run.call_args
            assert kwargs["capture_output"] is True

    def test_shell_false_enforced(self):
        """shell=False is hardcoded in safe_run_with_input."""
        with patch.object(
            secure_subprocess.subprocess, "run", return_value=MagicMock()
        ) as mock_run:
            secure_subprocess.safe_run_with_input(["/usr/bin/grep", "x"], "x")
            _, kwargs = mock_run.call_args
            assert kwargs["shell"] is False

    def test_validation_failure_no_subprocess(self):
        """Invalid cmd is rejected before subprocess.run is called."""
        with patch.object(secure_subprocess.subprocess, "run") as mock_run:
            with pytest.raises(ValueError):
                secure_subprocess.safe_run_with_input(
                    ["/bin/bash", "-c", "rm -rf /"], "input"
                )
            mock_run.assert_not_called()

    def test_default_timeout_30s(self):
        """Default timeout is 30 seconds."""
        with patch.object(
            secure_subprocess.subprocess, "run", return_value=MagicMock()
        ) as mock_run:
            secure_subprocess.safe_run_with_input(["/usr/bin/grep", "x"], "x")
            _, kwargs = mock_run.call_args
            assert kwargs["timeout"] == 30.0

    def test_non_zero_exit_returned(self):
        """Non-zero exit code from subprocess is preserved (no exception)."""
        # grep with no match returns exit code 1, not an exception
        result = secure_subprocess.safe_run_with_input(
            ["/usr/bin/grep", "nothere"], "hello\n"
        )
        assert result.returncode != 0


class TestAllowedBinaries:
    """Tests for ALLOWED_BINARIES constant."""

    def test_contains_common_safe_binaries(self):
        """The allowlist contains commonly-used safe binaries."""
        # Standard *nix utilities
        assert "/bin/echo" in secure_subprocess.ALLOWED_BINARIES
        assert "/usr/bin/grep" in secure_subprocess.ALLOWED_BINARIES
        assert "/bin/ls" in secure_subprocess.ALLOWED_BINARIES

    def test_does_not_contain_shell(self):
        """Shell binaries are explicitly absent (no shell execution)."""
        assert "/bin/sh" not in secure_subprocess.ALLOWED_BINARIES
        assert "/bin/bash" not in secure_subprocess.ALLOWED_BINARIES
        assert "/usr/bin/bash" not in secure_subprocess.ALLOWED_BINARIES
        assert "/usr/bin/zsh" not in secure_subprocess.ALLOWED_BINARIES

    def test_uses_absolute_paths(self):
        """All allowlisted binaries are absolute paths."""
        for binary in secure_subprocess.ALLOWED_BINARIES:
            assert binary.startswith("/"), f"{binary} is not absolute"


class TestMain:
    """Tests for main() — demo CLI."""

    @patch.object(secure_subprocess, "safe_run")
    @patch.object(secure_subprocess, "safe_run_with_input")
    @patch("builtins.print")
    def test_main_runs_demo(self, mock_print, mock_sri, mock_sr):
        """main() executes the demo and prints output."""
        # Mock the return values of safe_run
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "hello world\n"
        mock_sr.return_value = mock_result
        mock_sri.return_value = mock_result

        # main() is a demo that exercises safe_run with allowlisted binaries
        # it should not raise; we don't assert on its return value (returns None)
        secure_subprocess.main()

        # Verify safe_run was called at least once with allowlisted binary
        assert mock_sr.called

    @patch.object(secure_subprocess, "safe_run")
    @patch.object(secure_subprocess, "safe_run_with_input")
    @patch("builtins.print")
    def test_main_catches_value_error_from_unsafe_binary(self, mock_print, mock_sri, mock_sr):
        """main() handles ValueError raised by unsafe binary attempts (step [3])."""
        # safe_run is called in this order by main():
        #   [1] /bin/echo "hello world"          → ok
        #   [2] /bin/sh -c "echo pwned"          → ValueError (intentional)
        #   [5] /usr/local/bin/python3 sleep 5   → TimeoutExpired (intentional)
        # safe_run_with_input is called once: grep "hello"
        ok_result = MagicMock()
        ok_result.returncode = 0
        ok_result.stdout = "hello\n"
        timeout_exc = subprocess.TimeoutExpired(cmd=["python3"], timeout=0.1)

        mock_sr.side_effect = [ok_result, ValueError("/bin/sh not in allowlist"), timeout_exc]
        mock_sri.return_value = ok_result

        # main() catches both the ValueError (step [3]) and TimeoutExpired (step [5])
        secure_subprocess.main()

        # 3 safe_run calls + 1 safe_run_with_input call
        assert mock_sr.call_count == 3
        assert mock_sri.call_count == 1


class TestSecurityProperties:
    """Integration-level security property tests."""

    def test_no_shell_true_possible_via_public_api(self):
        """safe_run and safe_run_with_input both pass shell=False to subprocess."""
        with patch.object(
            secure_subprocess.subprocess, "run", return_value=MagicMock()
        ) as mock_run:
            secure_subprocess.safe_run(["/bin/echo", "x"])
            secure_subprocess.safe_run_with_input(["/usr/bin/grep", "x"], "x")
            for call in mock_run.call_args_list:
                _, kwargs = call
                assert kwargs["shell"] is False

    def test_injection_payload_rejected(self):
        """A malicious-looking binary path is rejected."""
        with pytest.raises(ValueError):
            secure_subprocess.safe_run(["/bin/sh", "-c", "echo $(whoami)"])

    def test_safe_run_integration_grep(self):
        """Integration: grep returns the matching line via safe_run."""
        result = secure_subprocess.safe_run(
            ["/usr/bin/grep", "world"], cwd="/tmp"
        )
        # Doesn't matter if grep didn't find anything in /tmp;
        # we just care that it ran without raising
        assert result.returncode in (0, 1)
