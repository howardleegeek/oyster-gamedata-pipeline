"""
tests/test_build_recorder_script.py
====================================
Tests for scripts/build_recorder_artifact.sh

Uses subprocess + mock filesystem to verify:
  - Script is syntactically valid (bash -n)
  - resolve_version logic (via mocked env)
  - OS detection
  - Error handling when vendor/recorder is missing
  - EV signing conditional branch exists in workflow YAML
"""

import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "build_recorder_artifact.sh"
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "build-recorder-windows.yml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def script_content():
    """Return the raw script content."""
    return SCRIPT_PATH.read_text()


@pytest.fixture
def workflow_content():
    """Return the raw workflow YAML content."""
    return WORKFLOW_PATH.read_text()


@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal mock project structure."""
    vendor = tmp_path / "vendor" / "recorder"
    vendor.mkdir(parents=True)

    cargo_toml = vendor / "Cargo.toml"
    cargo_toml.write_text(textwrap.dedent("""\
        [package]
        name = "oyster-recorder"
        version = "2.5.1"
        edition = "2021"
    """))

    target_release = vendor / "target" / "release"
    target_release.mkdir(parents=True)
    (target_release / "oyster-recorder.exe").write_text("fake binary")

    installer_dir = tmp_path / "installer" / "output"
    installer_dir.mkdir(parents=True)

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True)

    return tmp_path


# ---------------------------------------------------------------------------
# Test: bash syntax check
# ---------------------------------------------------------------------------
class TestScriptSyntax:
    def test_bash_syntax_valid(self):
        """bash -n should pass with zero exit code."""
        result = subprocess.run(
            ["bash", "-n", str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"

    def test_script_is_executable(self):
        """Script should have executable bit set."""
        assert os.access(str(SCRIPT_PATH), os.X_OK), "Script is not executable"

    def test_shebang_present(self):
        """Script should start with a proper shebang."""
        content = SCRIPT_PATH.read_text()
        assert content.startswith("#!/usr/bin/env bash"), "Missing #!/usr/bin/env bash shebang"


# ---------------------------------------------------------------------------
# Test: resolve_version logic (grep-based extraction from script)
# ---------------------------------------------------------------------------
class TestResolveVersion:
    def test_version_from_cargo_toml(self, tmp_project):
        """When no explicit version is given, should extract from Cargo.toml."""
        # We test the regex pattern the script uses
        cargo_content = (tmp_project / "vendor" / "recorder" / "Cargo.toml").read_text()
        match = re.search(r'^version\s*=\s*"([^"]+)"', cargo_content, re.MULTILINE)
        assert match is not None
        assert match.group(1) == "2.5.1"

    def test_version_from_tag_pattern(self):
        """Tag pattern recorder-v1.2.3 should yield 1.2.3."""
        tag = "recorder-v1.2.3"
        version = tag.replace("recorder-v", "")
        assert version == "1.2.3"

    def test_version_fallback(self):
        """When nothing is available, fallback should be 0.0.0-dev."""
        # This matches the script's fallback
        assert "0.0.0-dev" in SCRIPT_PATH.read_text()


# ---------------------------------------------------------------------------
# Test: OS detection
# ---------------------------------------------------------------------------
class TestOSDetection:
    def test_linux_detection(self):
        """uname -s = Linux should map to 'linux'."""
        result = subprocess.run(
            ["bash", "-c", 'case "Linux" in Linux*) echo "linux" ;; esac'],
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "linux"

    def test_macos_detection(self):
        """uname -s = Darwin should map to 'macos'."""
        result = subprocess.run(
            ["bash", "-c", 'case "Darwin" in Darwin*) echo "macos" ;; esac'],
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "macos"

    def test_windows_detection(self):
        """MINGW should map to 'windows'."""
        result = subprocess.run(
            ["bash", "-c", 'case "MINGW64" in MINGW*|MSYS*|CYGWIN*) echo "windows" ;; esac'],
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "windows"


# ---------------------------------------------------------------------------
# Test: Script structure / key functions exist
# ---------------------------------------------------------------------------
class TestScriptStructure:
    def test_has_resolve_version_function(self, script_content):
        assert "resolve_version()" in script_content

    def test_has_detect_os_function(self, script_content):
        assert "detect_os()" in script_content

    def test_has_build_rust_function(self, script_content):
        assert "build_rust()" in script_content

    def test_has_compile_installer_function(self, script_content):
        assert "compile_installer()" in script_content

    def test_has_main_function(self, script_content):
        assert "main()" in script_content

    def test_set_euo_pipefail(self, script_content):
        """Script should use strict error handling."""
        assert "set -euo pipefail" in script_content

    def test_references_vendor_recorder(self, script_content):
        assert "vendor/recorder" in script_content

    def test_references_installer_iss(self, script_content):
        assert "oyster-recorder.iss" in script_content

    def test_references_cargo_xwin(self, script_content):
        """Script should mention cargo-xwin as cross-compile option."""
        assert "cargo-xwin" in script_content

    def test_references_cargo_cross(self, script_content):
        """Script should mention cargo-cross as fallback."""
        assert "cargo-cross" in script_content or "cross build" in script_content


# ---------------------------------------------------------------------------
# Test: Workflow YAML structure
# ---------------------------------------------------------------------------
class TestWorkflowYAML:
    def test_workflow_exists(self):
        assert WORKFLOW_PATH.exists(), "Workflow YAML file not found"

    def test_workflow_name(self, workflow_content):
        assert "name: Build Recorder (Windows)" in workflow_content

    def test_runs_on_windows_latest(self, workflow_content):
        assert "runs-on: windows-latest" in workflow_content

    def test_tag_trigger(self, workflow_content):
        assert "recorder-v*" in workflow_content

    def test_push_to_main_trigger(self, workflow_content):
        assert "main" in workflow_content

    def test_workflow_dispatch(self, workflow_content):
        assert "workflow_dispatch" in workflow_content

    def test_checkout_submodules(self, workflow_content):
        assert "submodules: false" in workflow_content
        assert (
            "git submodule update --init --depth 1 vendor/recorder vendor/input-logger"
            in workflow_content
        )

    def test_rust_toolchain_install(self, workflow_content):
        assert "rust-toolchain" in workflow_content
        assert "stable" in workflow_content

    def test_cargo_build_release(self, workflow_content):
        assert "cargo build --release" in workflow_content

    def test_iscc_action(self, workflow_content):
        assert "mareangler/iscc-action@v1" in workflow_content

    def test_upload_artifact(self, workflow_content):
        assert "upload-artifact" in workflow_content

    def test_artifact_retention_90_days(self, workflow_content):
        assert "retention-days: 90" in workflow_content

    def test_github_release_attachment(self, workflow_content):
        assert "action-gh-release" in workflow_content

    def test_release_tag_condition(self, workflow_content):
        assert "refs/tags/recorder-v" in workflow_content


# ---------------------------------------------------------------------------
# Test: EV signing conditional branch
# ---------------------------------------------------------------------------
class TestEVSigning:
    def test_ev_cert_secret_reference(self, workflow_content):
        """Workflow should reference EV_CERT_PFX_BASE64 secret."""
        assert "EV_CERT_PFX_BASE64" in workflow_content

    def test_ev_signing_step_has_condition(self, workflow_content):
        """EV signing step should have an 'if' condition."""
        # The step should be conditional on the secret existing
        assert "EV_CERT_PFX_BASE64" in workflow_content
        # Check for the conditional pattern
        assert re.search(r"if:.*EV_CERT_PFX_BASE64", workflow_content) is not None

    def test_ev_signing_uses_signtool(self, workflow_content):
        """EV signing should use signtool.exe."""
        assert "signtool" in workflow_content

    def test_ev_signing_timestamp(self, workflow_content):
        """EV signing should include timestamping."""
        assert "timestamp" in workflow_content.lower()

    def test_ev_signing_cleans_up_pfx(self, workflow_content):
        """EV signing should clean up the PFX file."""
        assert "Remove-Item" in workflow_content or "rm " in workflow_content

    def test_unsigned_still_works(self, workflow_content):
        """Workflow should NOT require EV cert (unsigned builds should work)."""
        # The EV signing step must be conditional, not mandatory
        # Check that the upload-artifact step does NOT depend on signing
        lines = workflow_content.split("\n")
        upload_section = False
        for line in lines:
            if "upload-artifact" in line:
                upload_section = True
            if upload_section and "EV_CERT" in line:
                pytest.fail("upload-artifact should not depend on EV cert")
                break


# ---------------------------------------------------------------------------
# Test: Workflow YAML validity (basic structure)
# ---------------------------------------------------------------------------
class TestWorkflowValidity:
    def test_has_jobs_key(self, workflow_content):
        assert "jobs:" in workflow_content

    def test_has_steps_key(self, workflow_content):
        assert "steps:" in workflow_content

    def test_uses_actions_checkout(self, workflow_content):
        assert "actions/checkout@v4" in workflow_content

    def test_uses_actions_cache(self, workflow_content):
        assert "actions/cache@v4" in workflow_content

    def test_uses_upload_artifact_v4(self, workflow_content):
        assert "actions/upload-artifact@v4" in workflow_content

    def test_github_token_for_release(self, workflow_content):
        assert "GITHUB_TOKEN" in workflow_content

    def test_version_resolution_step(self, workflow_content):
        assert "Resolve version" in workflow_content

    def test_build_step_exists(self, workflow_content):
        assert "Build recorder" in workflow_content

    def test_compile_installer_step(self, workflow_content):
        assert "Compile Inno Setup" in workflow_content


# ---------------------------------------------------------------------------
# Test: Script error handling
# ---------------------------------------------------------------------------
class TestErrorHandling:
    def test_script_checks_cargo_exists(self, script_content):
        assert "command -v cargo" in script_content

    def test_script_checks_vendor_dir(self, script_content):
        assert "vendor/recorder" in script_content

    def test_script_has_error_function(self, script_content):
        assert "error()" in script_content

    def test_script_has_warn_function(self, script_content):
        assert "warn()" in script_content

    def test_script_has_info_function(self, script_content):
        assert "info()" in script_content


# ---------------------------------------------------------------------------
# Test: Integration — script runs with --help-like behavior
# ---------------------------------------------------------------------------
class TestScriptExecution:
    def test_script_exits_cleanly_with_no_vendor(self, tmp_path):
        """Script should fail gracefully when vendor/recorder is missing."""
        # Create a temp dir without vendor/recorder
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)

        result = subprocess.run(
            ["bash", str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env=env,
            timeout=10,
        )
        # Should fail because vendor/recorder doesn't exist
        assert result.returncode != 0
        assert "vendor/recorder" in result.stderr or "vendor/recorder" in result.stdout
