#!/usr/bin/env python3
"""Tests for bin/integration_smoke_runner.py."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure the bin module is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestRunCmd:
    """Tests for run_cmd function."""

    def test_successful_command_returns_true(self):
        """run_cmd should return (True, stdout, stderr) on success."""
        from bin.integration_smoke_runner import run_cmd

        success, stdout, stderr = run_cmd("echo hello")
        assert success is True
        assert stdout.strip() == "hello"
        assert stderr == ""

    def test_failed_command_returns_false(self):
        """run_cmd should return (False, stdout, stderr) on failure."""
        from bin.integration_smoke_runner import run_cmd

        success, stdout, stderr = run_cmd("ls /nonexistent_path_12345")
        assert success is False
        assert "No such file" in stderr or stdout

    def test_command_with_cwd(self):
        """run_cmd should execute in the specified working directory."""
        from bin.integration_smoke_runner import run_cmd

        with tempfile.TemporaryDirectory() as tmpdir:
            success, stdout, stderr = run_cmd("pwd", cwd=tmpdir)
            assert success is True
            assert tmpdir in stdout

    def test_command_timeout_returns_false(self):
        """run_cmd should return False on timeout."""
        from bin.integration_smoke_runner import run_cmd

        # Patch subprocess.run to raise TimeoutExpired
        with patch("bin.integration_smoke_runner.subprocess.run") as mock_run:
            import subprocess as sp
            mock_run.side_effect = sp.TimeoutExpired("cmd", 0.001)
            success, stdout, stderr = run_cmd("sleep 10")
            assert success is False
            assert "timed out" in stderr.lower()

    def test_command_exception_returns_false(self):
        """run_cmd should return False and error message on exception."""
        from bin.integration_smoke_runner import run_cmd

        # Passing a list instead of string should cause an error
        # (shell=True expects a string)
        success, stdout, stderr = run_cmd(12345)  # type: ignore
        assert success is False


class TestMain:
    """Tests for main function."""

    def test_missing_placeholders_exits_with_error(self):
        """main should exit with error when placeholders dir is missing."""
        from bin.integration_smoke_runner import main

        with patch.object(sys, "argv", ["integration_smoke_runner.py", "--placeholders", "/nonexistent", "--output", "/tmp"]):
            with pytest.raises(SystemExit) as exc_info:
                with patch("bin.integration_smoke_runner.run_cmd") as mock_run:
                    mock_run.return_value = (True, "", "")
                    main()
            assert exc_info.value.code == 1

    def test_missing_output_dir_exits_with_error(self):
        """main should exit with error when output dir is missing."""
        from bin.integration_smoke_runner import main

        with tempfile.TemporaryDirectory() as placeholders:
            # Create e2e_smoke.sh to pass the first check
            Path(placeholders, "e2e_smoke.sh").write_text("#!/bin/bash\nexit 0")
            os.chmod(Path(placeholders, "e2e_smoke.sh"), 0o755)

            with patch.object(sys, "argv", ["integration_smoke_runner.py", "--placeholders", placeholders, "--output", "/nonexistent"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 1

    def test_e2e_smoke_failure_exits_with_error(self):
        """main should exit with error when e2e_smoke.sh fails."""
        from bin.integration_smoke_runner import main

        with tempfile.TemporaryDirectory() as placeholders:
            with tempfile.TemporaryDirectory() as output:
                # Create failing e2e_smoke.sh
                Path(placeholders, "e2e_smoke.sh").write_text("#!/bin/bash\nexit 1")
                os.chmod(Path(placeholders, "e2e_smoke.sh"), 0o755)

                with patch.object(sys, "argv", ["integration_smoke_runner.py", "--placeholders", placeholders, "--output", output]):
                    with pytest.raises(SystemExit) as exc_info:
                        main()
                    assert exc_info.value.code == 1

    def test_e2e_smoke_success_continues(self):
        """main should continue to semantic_validator when e2e_smoke.sh succeeds."""
        from bin.integration_smoke_runner import main

        with tempfile.TemporaryDirectory() as placeholders:
            with tempfile.TemporaryDirectory() as output:
                # Create buyer subdir in output
                buyer_dir = Path(output, "buyer")
                buyer_dir.mkdir()

                # Create passing e2e_smoke.sh
                Path(placeholders, "e2e_smoke.sh").write_text("#!/bin/bash\nexit 0")
                os.chmod(Path(placeholders, "e2e_smoke.sh"), 0o755)

                # Create semantic_validator (valid Python script that exits 0)
                Path(placeholders, "semantic_validator.py").write_text("import sys; sys.exit(0)")
                os.chmod(Path(placeholders, "semantic_validator.py"), 0o755)

                with patch.object(sys, "argv", ["integration_smoke_runner.py", "--placeholders", placeholders, "--output", output]):
                    # Should exit with 0 on full success
                    with pytest.raises(SystemExit) as exc_info:
                        main()
                    assert exc_info.value.code == 0

    def test_semantic_validator_failure_exits_with_error(self):
        """main should exit with error when semantic_validator fails."""
        from bin.integration_smoke_runner import main

        with tempfile.TemporaryDirectory() as placeholders:
            with tempfile.TemporaryDirectory() as output:
                # Create buyer subdir in output
                buyer_dir = Path(output, "buyer")
                buyer_dir.mkdir()

                # Create passing e2e_smoke.sh
                Path(placeholders, "e2e_smoke.sh").write_text("#!/bin/bash\nexit 0")
                os.chmod(Path(placeholders, "e2e_smoke.sh"), 0o755)

                # Create failing semantic_validator (Python script that exits 1)
                Path(placeholders, "semantic_validator.py").write_text("import sys; sys.exit(1)")
                os.chmod(Path(placeholders, "semantic_validator.py"), 0o755)

                with patch.object(sys, "argv", ["integration_smoke_runner.py", "--placeholders", placeholders, "--output", output]):
                    with pytest.raises(SystemExit) as exc_info:
                        main()
                    assert exc_info.value.code == 1

    def test_semantic_validator_not_found_exits_with_error(self):
        """main should exit with error when semantic_validator is not found."""
        from bin.integration_smoke_runner import main

        with tempfile.TemporaryDirectory() as placeholders:
            with tempfile.TemporaryDirectory() as output:
                # Create buyer subdir in output
                buyer_dir = Path(output, "buyer")
                buyer_dir.mkdir()

                # Create passing e2e_smoke.sh but no semantic_validator
                Path(placeholders, "e2e_smoke.sh").write_text("#!/bin/bash\nexit 0")
                os.chmod(Path(placeholders, "e2e_smoke.sh"), 0o755)

                with patch.object(sys, "argv", ["integration_smoke_runner.py", "--placeholders", placeholders, "--output", output]):
                    with pytest.raises(SystemExit) as exc_info:
                        main()
                    assert exc_info.value.code == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
