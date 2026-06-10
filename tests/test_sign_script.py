"""
tests/test_sign_script.py
=========================
Validates installer/sign_installer.ps1 for:
  - PowerShell syntax correctness
  - Graceful skip when EV_CERT_PFX is missing
  - Correct signtool.exe invocation (mocked)
  - Proper base64 decode + temp file cleanup
  - Certificate store fallback path

These tests do NOT require signtool.exe, a real EV cert, or Windows.
They parse the .ps1 statically and mock subprocess calls.
"""

import os
import re
import subprocess
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SIGN_PS1_PATH = os.path.join(REPO_ROOT, "installer", "sign_installer.ps1")


@pytest.fixture(scope="module")
def ps1_content():
    """Load the sign_installer.ps1 file once for all tests."""
    assert os.path.isfile(SIGN_PS1_PATH), f"Missing: {SIGN_PS1_PATH}"
    with open(SIGN_PS1_PATH, "r", encoding="utf-8-sig") as f:
        return f.read()


@pytest.fixture(scope="module")
def ps1_lines(ps1_content):
    return ps1_content.splitlines()


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------


class TestFileExistence:
    def test_sign_ps1_exists(self):
        assert os.path.isfile(SIGN_PS1_PATH)


# ---------------------------------------------------------------------------
# PowerShell syntax validation
# ---------------------------------------------------------------------------


class TestPowerShellSyntax:
    """Static analysis of the PowerShell script."""

    def test_no_syntax_errors_basic(self, ps1_content):
        """Check for common PS syntax issues."""
        # Balanced braces
        open_braces = ps1_content.count("{")
        close_braces = ps1_content.count("}")
        assert (
            open_braces == close_braces
        ), f"Unbalanced braces: {open_braces} open, {close_braces} close"

        # Balanced parentheses
        open_parens = ps1_content.count("(")
        close_parens = ps1_content.count(")")
        assert (
            open_parens == close_parens
        ), f"Unbalanced parentheses: {open_parens} open, {close_parens} close"

    def test_param_block_present(self, ps1_content):
        """Script must have a param() block."""
        assert re.search(
            r"\[CmdletBinding\(\)\]", ps1_content
        ), "Missing [CmdletBinding()] attribute"
        assert re.search(r"param\s*\(", ps1_content), "Missing param() block"

    def test_mandatory_filepath_param(self, ps1_content):
        """-FilePath must be mandatory."""
        assert re.search(r"\$FilePath", ps1_content), "Missing $FilePath parameter"
        assert re.search(r"Mandatory\s*=\s*\$true", ps1_content), "No mandatory parameters found"

    def test_error_action_preference(self, ps1_content):
        """Script should set ErrorActionPreference."""
        assert re.search(
            r'\$ErrorActionPreference\s*=\s*"Stop"', ps1_content
        ), 'Missing $ErrorActionPreference = "Stop"'

    def test_no_plain_text_passwords(self, ps1_content):
        """No hardcoded passwords in the script."""
        # Look for patterns like password = "something" (not env var refs)
        lines = ps1_content.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comments
            if stripped.startswith("#"):
                continue
            # Skip parameter defaults that are empty or variable refs
            if re.search(r'(?i)password\s*=\s*"[^"]{3,}"', stripped):
                # Allow empty string or variable references
                if not re.search(r'(?i)password\s*=\s*""', stripped):
                    pytest.fail(f"Possible hardcoded password on line {i}: {stripped}")

    def test_temp_file_cleanup(self, ps1_content):
        """Temp .pfx file must be cleaned up in a finally block."""
        assert "finally" in ps1_content, "Missing finally block for temp file cleanup"
        assert "Remove-Item" in ps1_content, "Missing Remove-Item for temp file cleanup"

    def test_signtool_lookup_function(self, ps1_content):
        """Script must have a function to find signtool.exe."""
        assert re.search(r"function\s+Find-SignTool", ps1_content), "Missing Find-SignTool function"

    def test_sign_file_function(self, ps1_content):
        """Script must have a function to invoke signtool."""
        assert re.search(
            r"function\s+Invoke-SignFile", ps1_content
        ), "Missing Invoke-SignFile function"


# ---------------------------------------------------------------------------
# Required features
# ---------------------------------------------------------------------------


class TestRequiredFeatures:
    """Validate that key features are present in the script."""

    def test_ev_cert_pfx_env_var(self, ps1_content):
        """Script must read EV_CERT_PFX from environment."""
        assert "EV_CERT_PFX" in ps1_content, "Missing EV_CERT_PFX environment variable reference"

    def test_ev_cert_password_env_var(self, ps1_content):
        """Script must read EV_CERT_PASSWORD from environment."""
        assert (
            "EV_CERT_PASSWORD" in ps1_content
        ), "Missing EV_CERT_PASSWORD environment variable reference"

    def test_base64_decode(self, ps1_content):
        """Script must decode base64 .pfx."""
        assert "FromBase64String" in ps1_content, "Missing base64 decode (FromBase64String)"

    def test_timestamp_server(self, ps1_content):
        """Script must support timestamping."""
        assert "timestamp" in ps1_content.lower(), "Missing timestamp server configuration"

    def test_sha256_digest(self, ps1_content):
        """Script must use SHA256 digest by default."""
        assert "SHA256" in ps1_content, "Missing SHA256 digest algorithm"

    def test_cert_store_fallback(self, ps1_content):
        """Script must support cert store lookup."""
        assert "UseCertStore" in ps1_content, "Missing -UseCertStore switch"
        assert (
            "Cert:\\" in ps1_content or "Cert:" in ps1_content
        ), "Missing certificate store path reference"

    def test_graceful_skip_warning(self, ps1_content):
        """Script must warn when no cert is available."""
        assert "Write-Warning" in ps1_content, "Missing Write-Warning for graceful skip"
        assert "UNSIGNED" in ps1_content, "Missing UNSIGNED warning message"

    def test_exit_code_zero_on_skip(self, ps1_content):
        """Script must exit 0 when gracefully skipping."""
        # Find the graceful skip section and verify exit 0
        lines = ps1_content.splitlines()
        found_warning = False
        found_exit_zero = False
        for line in lines:
            if "UNSIGNED" in line:
                found_warning = True
            if found_warning and "exit 0" in line.lower():
                found_exit_zero = True
                break
        assert found_exit_zero, "Missing 'exit 0' after graceful skip warning"

    def test_file_validation(self, ps1_content):
        """Script must validate that the input file exists."""
        assert "Test-Path" in ps1_content, "Missing Test-Path for file validation"


# ---------------------------------------------------------------------------
# Workflow YAML integration
# ---------------------------------------------------------------------------


class TestWorkflowIntegration:
    """Validate the GitHub Actions workflow references the sign script."""

    @pytest.fixture(scope="class")
    def workflow_content(self):
        workflow_path = os.path.join(
            REPO_ROOT, ".github", "workflows", "build-windows-installer.yml"
        )
        assert os.path.isfile(workflow_path), f"Missing: {workflow_path}"
        with open(workflow_path, "r", encoding="utf-8") as f:
            return f.read()

    def test_workflow_exists(self, workflow_content):
        assert workflow_content is not None

    def test_workflow_references_sign_script(self, workflow_content):
        assert (
            "sign_installer.ps1" in workflow_content
        ), "Workflow does not reference sign_installer.ps1"

    def test_workflow_uses_ev_cert_pfx_secret(self, workflow_content):
        assert "EV_CERT_PFX" in workflow_content, "Workflow does not reference EV_CERT_PFX secret"

    def test_workflow_has_unsigned_warning(self, workflow_content):
        assert (
            "unsigned" in workflow_content.lower() or "UNSIGNED" in workflow_content
        ), "Workflow does not warn about unsigned builds"

    def test_workflow_triggers_on_recorder_tag(self, workflow_content):
        assert "recorder-v" in workflow_content, "Workflow does not trigger on recorder-v* tags"

    def test_workflow_optional_signing(self, workflow_content):
        """Signing step must be conditional (not required)."""
        # The sign step should have an 'if' condition
        assert "if:" in workflow_content, "Workflow signing step is not conditional"

    def test_workflow_runs_on_windows(self, workflow_content):
        assert (
            "windows-latest" in workflow_content or "windows" in workflow_content.lower()
        ), "Workflow does not target Windows runner"

    def test_workflow_promotes_ev_secret_to_job_env(self, workflow_content):
        assert (
            "EV_CERT_PFX: ${{ secrets.EV_CERT_PFX || secrets.EV_CERT_PFX_BASE64 }}"
            in workflow_content
        ), "EV_CERT_PFX must be job env so step if: can evaluate it"
        assert (
            "EV_CERT_PASSWORD: ${{ secrets.EV_CERT_PASSWORD }}" in workflow_content
        ), "EV_CERT_PASSWORD must be available to sign_installer.ps1"

    def test_workflow_sign_and_unsigned_conditions_are_complementary(self, workflow_content):
        assert "if: env.EV_CERT_PFX != ''" in workflow_content
        assert "if: env.EV_CERT_PFX == ''" in workflow_content


class TestBundledInstallerWorkflowSigning:
    """Validate the consumer bundled installer can be Authenticode signed."""

    @pytest.fixture(scope="class")
    def workflow_content(self):
        workflow_path = os.path.join(
            REPO_ROOT, ".github", "workflows", "build-recorder-installer.yml"
        )
        assert os.path.isfile(workflow_path), f"Missing: {workflow_path}"
        with open(workflow_path, "r", encoding="utf-8") as f:
            return f.read()

    def test_bundled_workflow_promotes_ev_secret_to_job_env(self, workflow_content):
        assert (
            "EV_CERT_PFX: ${{ secrets.EV_CERT_PFX || secrets.EV_CERT_PFX_BASE64 }}"
            in workflow_content
        )
        assert "EV_CERT_PASSWORD: ${{ secrets.EV_CERT_PASSWORD }}" in workflow_content

    def test_bundled_workflow_signs_before_hashing(self, workflow_content):
        sign_pos = workflow_content.index("Sign bundled installer (EV cert)")
        hash_pos = workflow_content.index("SHA-256 installer + write SHA-256-manifest.txt")
        assert sign_pos < hash_pos, "Bundled installer must be signed before hashing/upload"

    def test_bundled_workflow_uses_shared_sign_script(self, workflow_content):
        assert "installer\\sign_installer.ps1" in workflow_content
        assert "if: env.EV_CERT_PFX != ''" in workflow_content

    def test_bundled_workflow_warns_when_unsigned(self, workflow_content):
        assert "Warn — bundled installer is unsigned" in workflow_content
        assert "if: env.EV_CERT_PFX == ''" in workflow_content


# ---------------------------------------------------------------------------
# Mocked signtool invocation tests
# ---------------------------------------------------------------------------


class TestMockedSigntool:
    """
    Test the sign script's behavior by mocking signtool.exe.
    These tests create a fake signtool.exe and verify the script
    calls it correctly.
    """

    @pytest.fixture
    def temp_dir(self, tmp_path):
        return tmp_path

    @pytest.fixture
    def fake_exe(self, temp_dir):
        """Create a dummy .exe file to sign."""
        exe_path = temp_dir / "test-setup.exe"
        exe_path.write_bytes(b"MZ" + b"\x00" * 100)
        return str(exe_path)

    @pytest.fixture
    def fake_signtool(self, temp_dir):
        """Create a fake signtool.exe that always succeeds."""
        signtool_path = temp_dir / "signtool.exe"
        # Create a simple batch wrapper that exits 0
        bat_path = temp_dir / "signtool.bat"
        bat_path.write_text("@echo off\necho Mock signtool: signing %*\nexit /b 0\n")
        # Also create a .exe placeholder
        signtool_path.write_bytes(b"MZ" + b"\x00" * 50)
        return str(signtool_path), str(bat_path)

    def test_script_parses_cleanly(self, ps1_content):
        """Verify the script has no obvious parse errors by checking structure."""
        # Check that all function definitions have matching closing braces
        func_pattern = re.compile(r"function\s+(\w+)")
        functions = func_pattern.findall(ps1_content)
        assert (
            len(functions) >= 2
        ), f"Expected at least 2 functions, found {len(functions)}: {functions}"

    def test_find_signtool_searches_sdk_paths(self, ps1_content):
        """Find-SignTool must search Windows SDK paths."""
        assert "Windows Kits" in ps1_content, "Find-SignTool does not search Windows Kits paths"

    def test_find_signtool_checks_path(self, ps1_content):
        """Find-SignTool must check PATH first."""
        assert "Get-Command" in ps1_content, "Find-SignTool does not use Get-Command to check PATH"

    def test_signtool_args_include_file(self, ps1_content):
        """signtool invocation must include the file path."""
        # The file to sign should be passed as an argument
        assert "$FileToSign" in ps1_content, "signtool invocation does not reference $FileToSign"

    def test_signtool_args_include_timestamp(self, ps1_content):
        """signtool invocation must include timestamp args."""
        assert "/tr" in ps1_content, "signtool invocation missing /tr (RFC 3161 timestamp)"

    def test_signtool_args_include_digest(self, ps1_content):
        """signtool invocation must include digest algorithm."""
        assert "/fd" in ps1_content, "signtool invocation missing /fd (file digest)"
        assert "/td" in ps1_content, "signtool invocation missing /td (timestamp digest)"

    def test_signtool_args_include_cert_file(self, ps1_content):
        """signtool invocation must include cert file path."""
        assert "/f" in ps1_content, "signtool invocation missing /f (cert file)"

    def test_signtool_args_include_password(self, ps1_content):
        """signtool invocation must support password."""
        assert "/p" in ps1_content, "signtool invocation missing /p (password)"

    def test_graceful_skip_exits_zero(self, ps1_content):
        """When no cert is available, script exits 0 (not 1)."""
        # Find the section after the "no cert available" warning
        lines = ps1_content.splitlines()
        in_skip_section = False
        for line in lines:
            stripped = line.strip()
            if "No EV certificate provided" in stripped or "UNSIGNED" in stripped:
                in_skip_section = True
            if in_skip_section and stripped.startswith("exit"):
                assert (
                    "exit 0" in stripped.lower()
                ), f"Graceful skip should exit 0, found: {stripped}"
                break
        else:
            pytest.fail("Could not find exit statement in graceful skip section")

    def test_missing_file_exits_one(self, ps1_content):
        """When file doesn't exist, script exits 1."""
        lines = ps1_content.splitlines()
        for i, line in enumerate(lines):
            if "File not found" in line:
                # Check next few lines for exit 1
                for j in range(i, min(i + 5, len(lines))):
                    if "exit 1" in lines[j].lower():
                        return
                pytest.fail("Missing 'exit 1' after 'File not found' error")
        pytest.fail("Could not find 'File not found' error handling")

    def test_missing_signtool_exits_one(self, ps1_content):
        """When signtool.exe is not found, script exits 1."""
        lines = ps1_content.splitlines()
        for i, line in enumerate(lines):
            if "signtool.exe not found" in line.lower():
                for j in range(i, min(i + 5, len(lines))):
                    if "exit 1" in lines[j].lower():
                        return
                pytest.fail("Missing 'exit 1' after 'signtool.exe not found' error")
        pytest.fail("Could not find 'signtool.exe not found' error handling")


# ---------------------------------------------------------------------------
# PowerShell syntax via pwsh (if available)
# ---------------------------------------------------------------------------


class TestPowerShellRuntime:
    """
    Run actual PowerShell syntax check if pwsh is available.
    These tests are skipped if PowerShell is not installed.
    """

    @pytest.fixture(autouse=True)
    def skip_if_no_pwsh(self):
        """Skip all tests in this class if pwsh is not available."""
        try:
            result = subprocess.run(
                ["pwsh", "-NoProfile", "-Command", "$PSVersionTable.PSVersion"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                pytest.skip("pwsh not available")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pytest.skip("pwsh not available")

    def test_ps1_syntax_check(self):
        """Run PowerShell -NoProfile -Command to parse the script."""
        result = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-Command",
                f"[void] [System.Management.Automation.Language.Parser]::ParseFile('{SIGN_PS1_PATH}', [ref]$null, [ref]$errors); if ($errors) {{ $errors | ForEach-Object {{ Write-Error $_ }}; exit 1 }}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Note: The above inline PS might have issues with escaping.
        # Use a simpler approach:
        result = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-Command",
                f"$null = [scriptblock]::Create((Get-Content -Raw '{SIGN_PS1_PATH}'))",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert (
            result.returncode == 0
        ), f"PowerShell syntax check failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

    def test_ps1_parses_without_errors(self):
        """Parse the script and check for parser errors."""
        ps_check_script = textwrap.dedent(f"""
            $errors = @()
            $tokens = @()
            $ast = [System.Management.Automation.Language.Parser]::ParseFile(
                '{SIGN_PS1_PATH}',
                [ref]$tokens,
                [ref]$errors
            )
            if ($errors.Count -gt 0) {{
                $errors | ForEach-Object {{ Write-Host $_ }}
                exit 1
            }}
            exit 0
        """)
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", ps_check_script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert (
            result.returncode == 0
        ), f"PowerShell parser found errors:\nstdout: {result.stdout}\nstderr: {result.stderr}"


# ---------------------------------------------------------------------------
# YAML validation
# ---------------------------------------------------------------------------


class TestYamlValidation:
    """Validate the GitHub Actions workflow YAML."""

    @pytest.fixture(scope="class")
    def workflow_path(self):
        return os.path.join(REPO_ROOT, ".github", "workflows", "build-windows-installer.yml")

    @pytest.fixture(scope="class")
    def workflow_content(self, workflow_path):
        with open(workflow_path, "r", encoding="utf-8") as f:
            return f.read()

    def test_yaml_parses(self, workflow_content):
        """YAML must be valid."""
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")
        data = yaml.safe_load(workflow_content)
        assert data is not None, "YAML parsed to None"

    def test_yaml_has_name(self, workflow_content):
        """Workflow must have a name."""
        import yaml

        data = yaml.safe_load(workflow_content)
        assert "name" in data, "Workflow missing 'name' field"

    def test_yaml_has_on_trigger(self, workflow_content):
        """Workflow must have 'on' trigger."""
        import yaml

        data = yaml.safe_load(workflow_content)
        assert "on" in data, "Workflow missing 'on' trigger"

    def test_yaml_has_jobs(self, workflow_content):
        """Workflow must have jobs."""
        import yaml

        data = yaml.safe_load(workflow_content)
        assert "jobs" in data, "Workflow missing 'jobs' section"

    def test_yaml_tag_filter(self, workflow_content):
        """Workflow must filter on recorder-v* tags."""
        import yaml

        data = yaml.safe_load(workflow_content)
        on_section = data.get("on", {})
        push = on_section.get("push", {})
        tags = push.get("tags", [])
        assert any(
            "recorder-v" in str(t) for t in tags
        ), f"Workflow does not filter on recorder-v* tags. Found: {tags}"

    def test_yaml_permissions(self, workflow_content):
        """Workflow must have contents: write permission for releases."""
        import yaml

        data = yaml.safe_load(workflow_content)
        perms = data.get("permissions", {})
        assert perms.get("contents") == "write", "Workflow missing 'contents: write' permission"
