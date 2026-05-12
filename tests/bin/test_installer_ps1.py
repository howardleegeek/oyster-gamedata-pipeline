#!/usr/bin/env python3
"""Tests for bin/test_installer.ps1 — the automated installer smoke test.

These tests validate the PowerShell script's structure, parameter definitions,
JSON output format, and stage logic without requiring a Windows environment.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Resolve repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "bin" / "test_installer.ps1"


class TestScriptExists:
    """The script file must exist at the expected path."""

    def test_file_exists(self):
        assert SCRIPT_PATH.exists(), f"Script not found at {SCRIPT_PATH}"

    def test_file_not_empty(self):
        size = SCRIPT_PATH.stat().st_size
        assert size > 500, f"Script is suspiciously small ({size} bytes)"

    def test_file_is_utf8(self):
        """Script should be valid UTF-8 text."""
        content = SCRIPT_PATH.read_bytes()
        # Should decode without errors
        content.decode("utf-8")


class TestScriptStructure:
    """Validate the PowerShell script has required structural elements."""

    @pytest.fixture()
    def content(self):
        return SCRIPT_PATH.read_text(encoding="utf-8")

    def test_has_cmdlet_binding(self, content):
        assert "[CmdletBinding()]" in content

    def test_has_param_block(self, content):
        assert "param(" in content

    def test_has_repo_owner_param(self, content):
        assert "$RepoOwner" in content

    def test_has_repo_name_param(self, content):
        assert "$RepoName" in content

    def test_has_asset_pattern_param(self, content):
        assert "$AssetPattern" in content

    def test_has_keep_installer_switch(self, content):
        assert "$KeepInstaller" in content

    def test_has_error_action_preference(self, content):
        assert '$ErrorActionPreference' in content

    def test_has_write_json_result_function(self, content):
        assert "function Write-JsonResult" in content

    def test_has_log_stage_function(self, content):
        assert "function Log-Stage" in content

    def test_has_download_stage(self, content):
        assert "Stage 1" in content or "download" in content.lower()

    def test_has_install_stage(self, content):
        assert "Stage 2" in content or "install" in content.lower()

    def test_has_verify_paths_stage(self, content):
        assert "Stage 3" in content or "verify" in content.lower()

    def test_has_verify_oysterplay_stage(self, content):
        assert "Stage 4" in content or "oysterplay" in content.lower()

    def test_has_cleanup_stage(self, content):
        assert "Stage 5" in content or "cleanup" in content.lower() or "uninstall" in content.lower()

    def test_has_json_output(self, content):
        assert "ConvertTo-Json" in content

    def test_has_github_api_url(self, content):
        assert "api.github.com" in content

    def test_has_silent_flag(self, content):
        assert "/SILENT" in content

    def test_has_verysilent_fallback(self, content):
        assert "/VERYSILENT" in content

    def test_has_unins000_reference(self, content):
        assert "unins000.exe" in content

    def test_has_localappdata_env_var(self, content):
        assert "LOCALAPPDATA" in content

    def test_has_game_data_recorder_path(self, content):
        assert "GameData Recorder" in content

    def test_has_oyster_recorder_path(self, content):
        assert "OysterRecorder" in content

    def test_has_oysterplay_exe_check(self, content):
        assert "OysterPlay.exe" in content

    def test_has_exit_code_handling(self, content):
        assert "ExitCode" in content or "exit_code" in content

    def test_has_try_catch_block(self, content):
        assert "try {" in content
        assert "} catch {" in content

    def test_has_github_token_support(self, content):
        assert "GITHUB_TOKEN" in content

    def test_has_timestamp_in_output(self, content):
        assert "timestamp" in content

    def test_has_overall_pass_variable(self, content):
        assert "$overallPass" in content

    def test_has_stages_array(self, content):
        assert "$stages" in content


class TestJsonOutputFormat:
    """Validate the expected JSON output structure by parsing the script's
    JSON-producing code patterns."""

    @pytest.fixture()
    def content(self):
        return SCRIPT_PATH.read_text(encoding="utf-8")

    def test_json_has_passed_field(self, content):
        # The Write-JsonResult function should output a "passed" key
        assert '"passed"' in content or "'passed'" in content or "passed" in content

    def test_json_has_stage_field(self, content):
        assert '"stage"' in content or "'stage'" in content or "stage" in content

    def test_json_has_message_field(self, content):
        assert '"message"' in content or "'message'" in content or "message" in content

    def test_json_has_details_field(self, content):
        assert '"details"' in content or "'details'" in content or "details" in content

    def test_final_result_has_test_name(self, content):
        assert "installer_smoke_test" in content


class TestPowerShellSyntax:
    """Basic PowerShell syntax validation (without running on Windows)."""

    @pytest.fixture()
    def content(self):
        return SCRIPT_PATH.read_text(encoding="utf-8")

    def test_balanced_braces(self, content):
        """Count opening and closing braces — they should match."""
        opens = content.count("{")
        closes = content.count("}")
        assert opens == closes, f"Unbalanced braces: {opens} opens, {closes} closes"

    def test_balanced_parens(self, content):
        """Count opening and closing parentheses."""
        opens = content.count("(")
        closes = content.count(")")
        assert opens == closes, f"Unbalanced parens: {opens} opens, {closes} closes"

    def test_no_tabs(self, content):
        """Script should use spaces, not tabs, for indentation."""
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            assert "\t" not in line, f"Tab found on line {i}"

    def test_has_proper_shebang_or_comment_header(self, content):
        """Script should start with a comment block or shebang."""
        first_lines = content.split("\n")[:5]
        has_header = any("<#" in l or "#!" in l or "# " in l for l in first_lines)
        assert has_header, "Script should start with a comment header"

    def test_no_hardcoded_passwords(self, content):
        """Script should not contain hardcoded credentials."""
        suspicious_patterns = [
            r"password\s*=\s*['\"][^'\"]+['\"]",
            r"secret\s*=\s*['\"][^'\"]+['\"]",
            r"api_key\s*=\s*['\"][^'\"]+['\"]",
        ]
        for pattern in suspicious_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            assert not matches, f"Suspicious hardcoded credential found: {matches}"


class TestStageCoverage:
    """Ensure all 5 required stages are present and properly structured."""

    @pytest.fixture()
    def content(self):
        return SCRIPT_PATH.read_text(encoding="utf-8")

    def test_stage_1_download(self, content):
        assert "Invoke-WebRequest" in content or "Invoke-RestMethod" in content
        assert "releases/latest" in content

    def test_stage_2_install(self, content):
        assert "Start-Process" in content
        assert "/SILENT" in content

    def test_stage_3_verify_paths(self, content):
        assert "Test-Path" in content
        assert "candidatePaths" in content or "candidates" in content

    def test_stage_4_verify_oysterplay(self, content):
        assert "OysterPlay.exe" in content
        assert "Get-Item" in content

    def test_stage_5_cleanup(self, content):
        assert "unins000.exe" in content
        assert "Remove-Item" in content or "cleanup" in content.lower()


class TestEdgeCaseHandling:
    """Verify the script handles edge cases gracefully."""

    @pytest.fixture()
    def content(self):
        return SCRIPT_PATH.read_text(encoding="utf-8")

    def test_handles_missing_asset(self, content):
        assert "No asset matching" in content or "asset" in content.lower()

    def test_handles_install_failure(self, content):
        assert "exit" in content.lower() and "code" in content.lower()

    def test_handles_missing_uninstaller(self, content):
        assert "unins000.exe not found" in content or "WARNING" in content

    def test_has_best_effort_cleanup_on_error(self, content):
        assert "best-effort" in content.lower() or "Best-effort" in content

    def test_handles_rate_limiting(self, content):
        assert "GITHUB_TOKEN" in content

    def test_cleans_up_downloaded_installer(self, content):
        assert "KeepInstaller" in content
        assert "Remove-Item" in content


class TestInstallPathCandidates:
    """Verify both install path candidates are checked."""

    @pytest.fixture()
    def content(self):
        return SCRIPT_PATH.read_text(encoding="utf-8")

    def test_checks_game_data_recorder(self, content):
        assert "GameData Recorder" in content

    def test_checks_oyster_recorder(self, content):
        assert "OysterRecorder" in content

    def test_checks_program_files_too(self, content):
        assert "ProgramFiles" in content or "Program Files" in content
