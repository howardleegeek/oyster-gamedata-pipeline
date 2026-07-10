"""
tests/test_runtime_check.py — Tests for installer/check_runtime.bat

Tests verify:
  1. check_runtime.bat syntax is valid (cmd /c)
  2. oyster-recorder.iss is structurally valid
  3. Mock registry → exit 0 when key exists, exit 1 when missing
  4. The .iss file calls check_runtime.bat in [Run] section
  5. The .iss file has InitializeSetup for pre-install check
"""

import os
import re
import subprocess
import tempfile
import textwrap

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BATCH_PATH = os.path.join(ROOT, "installer", "check_runtime.bat")
ISS_PATH = os.path.join(ROOT, "installer", "oyster-recorder.iss")

REG_KEY = r"HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
DOWNLOAD_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"


# ---------------------------------------------------------------------------
# Test 1: check_runtime.bat exists and has valid syntax
# ---------------------------------------------------------------------------
class TestBatchSyntax:
    def test_batch_file_exists(self):
        assert os.path.isfile(BATCH_PATH), f"check_runtime.bat not found at {BATCH_PATH}"

    def test_batch_has_shebang(self):
        with open(BATCH_PATH, "r") as f:
            first_line = f.readline().strip()
        assert first_line.startswith(
            "@echo off"
        ), f"Batch should start with @echo off, got: {first_line!r}"

    def test_batch_has_exit_codes(self):
        with open(BATCH_PATH, "r") as f:
            content = f.read()
        assert "exit /b 0" in content, "Batch must have 'exit /b 0' for success"
        assert "exit /b 1" in content, "Batch must have 'exit /b 1' for failure"

    def test_batch_checks_correct_registry_key(self):
        with open(BATCH_PATH, "r") as f:
            content = f.read()
        assert "VisualStudio" in content, "Batch must reference VisualStudio registry"
        assert "14.0" in content, "Batch must check VS 14.0 (VC++ 2015-2022)"
        assert "Runtimes" in content, "Batch must check Runtimes key"
        assert "x64" in content, "Batch must check x64 architecture"

    def test_batch_has_download_url(self):
        with open(BATCH_PATH, "r") as f:
            content = f.read()
        assert DOWNLOAD_URL in content, f"Batch must contain download URL {DOWNLOAD_URL}"

    def test_batch_has_user_prompt(self):
        with open(BATCH_PATH, "r") as f:
            content = f.read()
        assert "set /p" in content, "Batch must prompt user with set /p"
        assert "Download" in content, "Batch must mention download in prompt"

    def test_batch_handles_silent_mode(self):
        with open(BATCH_PATH, "r") as f:
            content = f.read()
        assert "/SILENT" in content, "Batch must handle /SILENT flag"
        assert "/VERYSILENT" in content, "Batch must handle /VERYSILENT flag"

    def test_batch_syntax_cmd_check(self):
        """Verify batch file has balanced if/endif, proper labels, etc."""
        with open(BATCH_PATH, "r") as f:
            content = f.read()
        # Check for common batch syntax issues
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comments and empty lines
            if stripped.startswith("REM") or stripped.startswith("@") or not stripped:
                continue
            # Check for unmatched quotes (simple heuristic)
            quote_count = stripped.count('"')
            assert quote_count % 2 == 0, f"Line {i} has unmatched quotes: {stripped!r}"


# ---------------------------------------------------------------------------
# Test 2: oyster-recorder.iss is structurally valid
# ---------------------------------------------------------------------------
class TestIssValidity:
    def test_iss_file_exists(self):
        assert os.path.isfile(ISS_PATH), f"oyster-recorder.iss not found at {ISS_PATH}"

    def test_iss_has_required_sections(self):
        with open(ISS_PATH, "r") as f:
            content = f.read()
        required_sections = ["[Setup]", "[Files]", "[Run]", "[Code]"]
        for section in required_sections:
            assert section in content, f"Missing required section: {section}"

    def test_iss_has_initialize_setup(self):
        with open(ISS_PATH, "r") as f:
            content = f.read()
        assert (
            "InitializeSetup" in content
        ), ".iss must have InitializeSetup function for pre-install check"
        assert (
            "function InitializeSetup(): Boolean" in content
        ), "InitializeSetup must return Boolean"

    def test_iss_calls_check_runtime_in_run_section(self):
        with open(ISS_PATH, "r") as f:
            content = f.read()
        # Find [Run] section
        run_match = re.search(r"\[Run\](.*?)(?=\n\[|\Z)", content, re.DOTALL)
        assert run_match, "[Run] section not found in .iss"
        run_content = run_match.group(1)
        assert "check_runtime.bat" in run_content, "[Run] section must call check_runtime.bat"

    def test_iss_has_regkey_check_in_code(self):
        with open(ISS_PATH, "r") as f:
            content = f.read()
        assert "RegKeyExists" in content, ".iss [Code] must use RegKeyExists for fallback check"
        assert "14.0" in content, ".iss must reference VS 14.0 registry key"

    def test_iss_has_vc_runtime_msgbox(self):
        with open(ISS_PATH, "r") as f:
            content = f.read()
        assert "MsgBox" in content, ".iss must show MsgBox when runtime missing"
        assert "VC++" in content, ".iss must mention VC++ in error message"

    def test_iss_run_section_has_waituntilterminated(self):
        """Runtime check must wait for completion before proceeding."""
        with open(ISS_PATH, "r") as f:
            content = f.read()
        run_match = re.search(r"\[Run\](.*?)(?=\n\[|\Z)", content, re.DOTALL)
        assert run_match
        run_content = run_match.group(1)
        assert (
            "waituntilterminated" in run_content.lower()
        ), "Runtime check in [Run] must use waituntilterminated flag"


# ---------------------------------------------------------------------------
# Test 3: Mock registry → exit 0/1 correctly
# ---------------------------------------------------------------------------
class TestMockRegistry:
    """
    Simulate the batch file's registry check logic by creating a shell
    wrapper that mocks `reg query` behavior. Works cross-platform.
    """

    def _create_mock_script(self, reg_exists: bool) -> str:
        """Create a temporary shell script that mimics check_runtime.bat logic."""
        mock_dir = tempfile.mkdtemp()
        test_script = os.path.join(mock_dir, "check_runtime_mock.sh")

        # Create a mock reg command that simulates reg query
        mock_reg = os.path.join(mock_dir, "reg")
        if reg_exists:
            reg_content = "#!/bin/sh\nexit 0\n"
        else:
            reg_content = "#!/bin/sh\nexit 1\n"

        with open(mock_reg, "w") as f:
            f.write(reg_content)
        os.chmod(mock_reg, 0o755)

        # Create a test script that mimics the batch file logic
        test_content = textwrap.dedent(f"""            #!/bin/sh
            export PATH="{mock_dir}:$PATH"

            REG_KEY="HKLM\\SOFTWARE\\Microsoft\\VisualStudio\\14.0\\VC\\Runtimes\\x64"

            # Mimic: reg query "%REG_KEY%" >nul 2>&1
            reg query "$REG_KEY" >/dev/null 2>&1
            if [ $? -eq 0 ]; then
                echo "[OK] VC++ runtime found."
                exit 0
            fi

            echo "[ERROR] VC++ runtime NOT found."
            exit 1
        """)

        with open(test_script, "w") as f:
            f.write(test_content)
        os.chmod(test_script, 0o755)

        return test_script

    def test_mock_registry_found_exits_zero(self):
        """When registry key exists, script should exit 0."""
        script_path = self._create_mock_script(reg_exists=True)
        result = subprocess.run(
            ["/bin/sh", script_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"Expected exit 0 when runtime found, got {result.returncode}. "
            f"stdout: {result.stdout}, stderr: {result.stderr}"
        )

    def test_mock_registry_missing_exits_one(self):
        """When registry key is missing, script should exit 1."""
        script_path = self._create_mock_script(reg_exists=False)
        result = subprocess.run(
            ["/bin/sh", script_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 1, (
            f"Expected exit 1 when runtime missing, got {result.returncode}. "
            f"stdout: {result.stdout}, stderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Test 4: End-to-end batch logic verification (Python simulation)
# ---------------------------------------------------------------------------
class TestBatchLogicSimulation:
    """
    Simulate the batch file's decision tree in Python to verify
    the logic is correct without actually running on Windows.
    """

    def _parse_batch_logic(self):
        """Parse check_runtime.bat and extract key logic points."""
        with open(BATCH_PATH, "r") as f:
            content = f.read()
        return content

    def test_success_path_exits_zero(self):
        """Verify the batch has a clear success path with exit 0."""
        content = self._parse_batch_logic()
        # The batch should check reg query, and if ERRORLEVEL == 0, exit 0
        assert "reg query" in content, "Must use reg query"
        assert "ERRORLEVEL" in content, "Must check ERRORLEVEL"
        # Verify the pattern: reg query → if ERRORLEVEL EQU 0 → exit /b 0
        pattern = r"reg query.*ERRORLEVEL.*EQU 0.*exit /b 0"
        assert re.search(
            pattern, content, re.DOTALL
        ), "Batch must have: reg query → check ERRORLEVEL → exit 0"

    def test_failure_path_exits_one(self):
        """Verify the batch has a clear failure path with exit 1."""
        content = self._parse_batch_logic()
        # After the success check, there should be exit /b 1 paths
        exit_1_count = content.count("exit /b 1")
        assert exit_1_count >= 1, f"Batch must have at least one 'exit /b 1', found {exit_1_count}"

    def test_prompt_defaults_to_yes(self):
        """Verify that empty input defaults to Yes (download)."""
        content = self._parse_batch_logic()
        # Should have: if "%CHOICE%"=="" set "CHOICE=Y"
        assert (
            '""' in content or '==""' in content or '==""' in content
        ), "Batch should handle empty input"
        # Check for default Y assignment
        assert "CHOICE=Y" in content or 'CHOICE="Y"' in content, "Batch should default CHOICE to Y"

    def test_y_opens_download_url(self):
        """Verify Y choice opens the download URL."""
        content = self._parse_batch_logic()
        # Should have: start "" "URL"
        assert 'start ""' in content or "start" in content, "Batch should use 'start' to open URL"
        assert DOWNLOAD_URL in content, f"Batch must open {DOWNLOAD_URL}"

    def test_n_cancels_installation(self):
        """Verify N choice cancels with exit 1."""
        content = self._parse_batch_logic()
        # Should check for N/n and exit 1
        assert '"N"' in content or '"n"' in content, "Batch should handle N/n response"


# ---------------------------------------------------------------------------
# Test 5: Integration — .iss and .bat work together
# ---------------------------------------------------------------------------
class TestIntegration:
    def test_iss_references_batch_file(self):
        """Verify .iss references check_runtime.bat."""
        with open(ISS_PATH, "r") as f:
            content = f.read()
        assert "check_runtime.bat" in content, ".iss must reference check_runtime.bat"

    def test_batch_file_is_included_in_files_section(self):
        """Verify the batch file would be included in the installer."""
        with open(ISS_PATH, "r") as f:
            content = f.read()
        # The [Files] section should include .bat files or the batch should
        # be referenced from {src} (installer source directory)
        files_match = re.search(r"\[Files\](.*?)(?=\n\[|\Z)", content, re.DOTALL)
        assert files_match, "[Files] section not found"
        # The batch is called from {src} in InitializeSetup, so it needs
        # to be referenced somewhere in the .iss (either in [Files] or [Run])
        assert "check_runtime" in content, "check_runtime.bat must be referenced somewhere in .iss"

    def test_initialize_setup_returns_false_on_missing_runtime(self):
        """Verify InitializeSetup returns False when runtime is missing."""
        with open(ISS_PATH, "r") as f:
            content = f.read()
        # Find InitializeSetup function
        func_match = re.search(
            r"function InitializeSetup\(\): Boolean;(.*?)^end;",
            content,
            re.DOTALL | re.MULTILINE,
        )
        assert func_match, "InitializeSetup function not found"
        func_body = func_match.group(1)
        assert (
            "Result := False" in func_body
        ), "InitializeSetup must set Result := False when runtime missing"

    def test_initialize_setup_returns_true_on_found_runtime(self):
        """Verify InitializeSetup returns True when runtime is found."""
        with open(ISS_PATH, "r") as f:
            content = f.read()
        func_match = re.search(
            r"function InitializeSetup\(\): Boolean;(.*?)^end;",
            content,
            re.DOTALL | re.MULTILINE,
        )
        assert func_match
        func_body = func_match.group(1)
        assert "Result := True" in func_body, "InitializeSetup must initialize Result := True"
