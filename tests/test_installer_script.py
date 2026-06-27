"""
tests/test_installer_script.py
==============================
Validates the Inno Setup installer script (installer/oyster-recorder.iss)
for syntax correctness, required sections, and acceptance criteria.

These tests do NOT require Inno Setup to be installed — they parse and
validate the .iss file statically.
"""

import os
import re

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ISS_PATH = os.path.join(REPO_ROOT, "installer", "oyster-recorder.iss")
BUILD_PS1_PATH = os.path.join(REPO_ROOT, "installer", "build_installer.ps1")
POSTINSTALL_PATH = os.path.join(REPO_ROOT, "installer", "postinstall_register_autostart.bat")


@pytest.fixture(scope="module")
def iss_content():
    """Load the .iss file once for all tests."""
    assert os.path.isfile(ISS_PATH), f"Missing: {ISS_PATH}"
    with open(ISS_PATH, "r", encoding="utf-8-sig") as f:
        return f.read()


@pytest.fixture(scope="module")
def ps1_content():
    """Load the build_installer.ps1 file once for all tests."""
    assert os.path.isfile(BUILD_PS1_PATH), f"Missing: {BUILD_PS1_PATH}"
    with open(BUILD_PS1_PATH, "r", encoding="utf-8-sig") as f:
        return f.read()


@pytest.fixture(scope="module")
def bat_content():
    """Load the postinstall batch file once for all tests."""
    assert os.path.isfile(POSTINSTALL_PATH), f"Missing: {POSTINSTALL_PATH}"
    with open(POSTINSTALL_PATH, "r", encoding="utf-8-sig") as f:
        return f.read()


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------


class TestFileExistence:
    def test_iss_exists(self):
        assert os.path.isfile(ISS_PATH)

    def test_build_ps1_exists(self):
        assert os.path.isfile(BUILD_PS1_PATH)

    def test_postinstall_bat_exists(self):
        assert os.path.isfile(POSTINSTALL_PATH)


# ---------------------------------------------------------------------------
# Required Inno Setup sections
# ---------------------------------------------------------------------------


class TestRequiredSections:
    """Every Inno Setup script must have these sections."""

    REQUIRED_SECTIONS = [
        "[Setup]",
        "[Files]",
        "[Icons]",
        "[Registry]",
        "[Languages]",
    ]

    @pytest.mark.parametrize("section", REQUIRED_SECTIONS)
    def test_section_present(self, iss_content, section):
        assert section in iss_content, f"Missing required section: {section}"


# ---------------------------------------------------------------------------
# [Setup] block constraints
# ---------------------------------------------------------------------------


class TestSetupBlock:
    """Validate key [Setup] directives."""

    def test_privileges_lowest(self, iss_content):
        """Must be per-user install — no admin."""
        assert re.search(r"PrivilegesRequired\s*=\s*lowest", iss_content, re.IGNORECASE), (
            "PrivilegesRequired must be 'lowest' for per-user install"
        )

    def test_localappdata_install_dir(self, iss_content):
        """Install dir must be under {localappdata}."""
        assert re.search(r"DefaultDirName\s*=\s*\{localappdata\}", iss_content, re.IGNORECASE), (
            "DefaultDirName must use {localappdata}"
        )

    def test_output_filename_pattern(self, iss_content):
        """Output must match OysterRecorder-setup-vX.Y.Z.exe pattern."""
        assert re.search(
            r"OutputBaseFilename\s*=\s*OysterRecorder-setup-v",
            iss_content,
            re.IGNORECASE,
        ), "OutputBaseFilename must start with 'OysterRecorder-setup-v'"

    def test_compression_lzma2(self, iss_content):
        """Must use lzma2 compression."""
        assert re.search(r"Compression\s*=\s*lzma2", iss_content, re.IGNORECASE), (
            "Compression must be lzma2"
        )

    def test_wizard_style_modern(self, iss_content):
        """Must use modern wizard style (Inno Setup 6.x)."""
        assert re.search(r"WizardStyle\s*=\s*modern", iss_content, re.IGNORECASE), (
            "WizardStyle must be 'modern' (Inno Setup 6.x)"
        )

    def test_app_id_present(self, iss_content):
        """Must have a valid AppId GUID defined (Inno uses {{ to escape literal {)."""
        # The AppId is defined via #define MyAppId "{{GUID}}" and referenced as AppId={#MyAppId}
        assert re.search(r"#define\s+MyAppId\s+.*\{\{[0-9A-Fa-f-]+\}\}", iss_content), (
            "AppId must be defined as a valid GUID with {{ }} escaping"
        )

    def test_solid_compression(self, iss_content):
        """Solid compression should be enabled."""
        assert re.search(r"SolidCompression\s*=\s*yes", iss_content, re.IGNORECASE), (
            "SolidCompression should be 'yes'"
        )


# ---------------------------------------------------------------------------
# Registry: HKCU autostart
# ---------------------------------------------------------------------------


class TestRegistryAutostart:
    """Validate the HKCU Run registry entry for autostart."""

    def test_hkcu_run_key(self, iss_content):
        """Must write to HKCU\\...\\Run."""
        assert re.search(r"Root:\s*HKCU", iss_content, re.IGNORECASE), "Registry root must be HKCU"

    def test_run_subkey(self, iss_content):
        """Must target the Windows Run key."""
        assert re.search(
            r"Software\\Microsoft\\Windows\\CurrentVersion\\Run",
            iss_content,
        ), "Must write to Software\\Microsoft\\Windows\\CurrentVersion\\Run"

    def test_value_name_oyster_recorder(self, iss_content):
        """ValueName should reference OysterRecorder (via {#MyAppName} or literal)."""
        assert re.search(
            r"ValueName:\s*.*(?:OysterRecorder|\{#MyAppName\})",
            iss_content,
            re.IGNORECASE,
        ), "ValueName must contain 'OysterRecorder' or '{#MyAppName}'"

    def test_uninsdeletevalue_flag(self, iss_content):
        """Registry entry must be cleaned up on uninstall."""
        assert re.search(r"Flags:.*uninsdeletevalue", iss_content, re.IGNORECASE), (
            "Registry entry must have uninsdeletevalue flag"
        )

    def test_exe_path_in_registry_value(self, iss_content):
        """ValueData must reference the installed exe path ({app} + exe name)."""
        assert re.search(
            r"ValueData:.*\{app\}.*(?:oyster-recorder|\{#AppExeName\})",
            iss_content,
            re.IGNORECASE,
        ), "ValueData must reference {app} and oyster-recorder.exe (or {#AppExeName})"


# ---------------------------------------------------------------------------
# Icons: Start Menu shortcut
# ---------------------------------------------------------------------------


class TestIcons:
    """Validate shortcut creation."""

    def test_start_menu_icon(self, iss_content):
        """Must create a Start Menu shortcut via {group}."""
        assert re.search(
            r"\{group\}.*(?:OysterRecorder|\{#MyAppName\})",
            iss_content,
            re.IGNORECASE,
        ), "Must create Start Menu shortcut via {group}"

    def test_desktop_icon_task(self, iss_content):
        """Desktop icon should be an optional task."""
        assert re.search(
            r"\{autodesktop\}.*Tasks:\s*desktopicon",
            iss_content,
            re.IGNORECASE,
        ), "Desktop icon must be tied to 'desktopicon' task"


# ---------------------------------------------------------------------------
# Files section
# ---------------------------------------------------------------------------


class TestFilesSection:
    """Validate [Files] entries."""

    def test_main_exe_included(self, iss_content):
        """oyster-recorder.exe must be in [Files] (literal or via {#AppExeName})."""
        assert re.search(
            r"Source:.*(?:oyster-recorder\.exe|\{#AppExeName\})",
            iss_content,
            re.IGNORECASE,
        ), "oyster-recorder.exe (or {#AppExeName}) must be listed in [Files]"

    def test_dest_dir_app(self, iss_content):
        """Files must be installed to {app}."""
        assert re.search(r"DestDir:\s*\"\{app\}\"", iss_content), "Files must be installed to {app}"

    def test_ignoreversion_flag(self, iss_content):
        """Main exe should have ignoreversion flag."""
        assert re.search(r"Flags:.*ignoreversion", iss_content, re.IGNORECASE), (
            "Files should have ignoreversion flag"
        )

    def test_obs_runtime_files_are_required(self, iss_content):
        """OBS core DLLs must be explicit required installer inputs."""
        required = [
            "obs.dll",
            "libobs-d3d11.dll",
            "libobs-opengl.dll",
            "libobs-winrt.dll",
            "obs-ffmpeg-mux.exe",
        ]
        for name in required:
            assert re.search(
                rf'Source:\s*"\{{#SourceDir\}}\\{re.escape(name)}"',
                iss_content,
                re.IGNORECASE,
            ), f"Installer must explicitly include required OBS runtime file {name}"

    def test_obs_plugin_and_data_dirs_are_recursive(self, iss_content):
        """OBS plugin/data directories must be shipped recursively."""
        assert re.search(
            r'Source:\s*"\{#SourceDir\}\\obs-plugins\\\*".*'
            r'DestDir:\s*"\{app\}\\obs-plugins".*'
            r"recursesubdirs.*createallsubdirs",
            iss_content,
            re.IGNORECASE | re.DOTALL,
        ), "Installer must copy obs-plugins recursively"
        assert re.search(
            r'Source:\s*"\{#SourceDir\}\\data\\\*".*'
            r'DestDir:\s*"\{app\}\\data".*'
            r"recursesubdirs.*createallsubdirs",
            iss_content,
            re.IGNORECASE | re.DOTALL,
        ), "Installer must copy OBS data recursively"


# ---------------------------------------------------------------------------
# [Run] section: post-install launch
# ---------------------------------------------------------------------------


class TestRunSection:
    """Validate post-install run behavior."""

    def test_skipifsilent_flag(self, iss_content):
        """Post-install run must have skipifsilent for /SILENT support."""
        assert re.search(r"Flags:.*skipifsilent", iss_content, re.IGNORECASE), (
            "[Run] must have skipifsilent flag for silent install support"
        )

    def test_nowait_flag(self, iss_content):
        """Post-install run should be nowait."""
        assert re.search(r"Flags:.*nowait", iss_content, re.IGNORECASE), (
            "[Run] should have nowait flag"
        )

    def test_postinstall_flag(self, iss_content):
        """Post-install run should have postinstall flag."""
        assert re.search(r"Flags:.*postinstall", iss_content, re.IGNORECASE), (
            "[Run] should have postinstall flag"
        )


# ---------------------------------------------------------------------------
# Version injection
# ---------------------------------------------------------------------------


class TestVersionInjection:
    """Validate that version is injectable via /D switch."""

    def test_appversion_define(self, iss_content):
        """Must have #ifndef AppVersion for CI injection."""
        assert re.search(r"#ifndef\s+AppVersion", iss_content), (
            "Must have #ifndef AppVersion for CI injection"
        )

    def test_appversion_fallback(self, iss_content):
        """Fallback version should be a dev string."""
        assert re.search(r'#define\s+AppVersion\s+"[\d.]+-dev"', iss_content), (
            "Fallback AppVersion should be a dev string"
        )


# ---------------------------------------------------------------------------
# build_installer.ps1 validation
# ---------------------------------------------------------------------------


class TestBuildInstallerPS1:
    """Validate the PowerShell build script."""

    def test_version_parameter(self, ps1_content):
        """Must accept -Version parameter."""
        assert re.search(r"\$Version", ps1_content), "Script must use $Version parameter"

    def test_iscc_invocation(self, ps1_content):
        """Must invoke ISCC.exe."""
        assert re.search(r"iscc|ISCC", ps1_content, re.IGNORECASE), "Script must invoke ISCC"

    def test_output_dir_parameter(self, ps1_content):
        """Must support -OutputDir parameter."""
        assert re.search(r"\$OutputDir", ps1_content), "Script must support $OutputDir"

    def test_exit_code_check(self, ps1_content):
        """Must check ISCC exit code."""
        assert re.search(r"ExitCode|exit\s+\d", ps1_content), "Script must check ISCC exit code"

    def test_size_warning(self, ps1_content):
        """Must warn if installer exceeds 50MB."""
        assert re.search(r"50", ps1_content), "Script must check 50MB size threshold"

    def test_recorder_exe_validation(self, ps1_content):
        """Must validate recorder exe exists before building."""
        assert re.search(
            r"Test-Path.*RecorderExe|Test-Path.*oyster-recorder",
            ps1_content,
        ), "Script must validate recorder exe exists"


# ---------------------------------------------------------------------------
# postinstall_register_autostart.bat validation
# ---------------------------------------------------------------------------


class TestPostinstallBat:
    """Validate the post-install batch file."""

    def test_reg_add_command(self, bat_content):
        """Must use reg add to write the Run key."""
        assert re.search(r"reg\s+add", bat_content, re.IGNORECASE), (
            "Batch must use 'reg add' command"
        )

    def test_hkcu_run_key(self, bat_content):
        """Must target HKCU Run key."""
        assert re.search(r"HKCU.*Run", bat_content, re.IGNORECASE), "Batch must target HKCU Run key"

    def test_oyster_recorder_value(self, bat_content):
        """Must reference OysterRecorder as value name."""
        assert re.search(r"OysterRecorder", bat_content, re.IGNORECASE), (
            "Batch must reference OysterRecorder"
        )

    def test_tray_parameter(self, bat_content):
        """Must include --tray parameter."""
        assert re.search(r"--tray", bat_content), "Batch must include --tray parameter"

    def test_error_handling(self, bat_content):
        """Must have error handling."""
        assert re.search(r"errorlevel|ERROR", bat_content, re.IGNORECASE), (
            "Batch must check errorlevel"
        )


# ---------------------------------------------------------------------------
# No-JRE / No-Minecraft bundling check
# ---------------------------------------------------------------------------


class TestNoBundledJRE:
    """Ensure the installer does NOT bundle JRE or Minecraft."""

    def test_no_jre_in_files(self, iss_content):
        """No JRE references in [Files]."""
        files_section = re.search(r"\[Files\](.*?)(?=\[|$)", iss_content, re.DOTALL | re.IGNORECASE)
        if files_section:
            files_text = files_section.group(1)
            assert not re.search(r"jre|jdk|java", files_text, re.IGNORECASE), (
                "[Files] must not reference JRE/JDK/Java"
            )

    def test_no_minecraft_in_files(self, iss_content):
        """No Minecraft references in [Files]."""
        files_section = re.search(r"\[Files\](.*?)(?=\[|$)", iss_content, re.DOTALL | re.IGNORECASE)
        if files_section:
            files_text = files_section.group(1)
            assert not re.search(r"minecraft|\.minecraft", files_text, re.IGNORECASE), (
                "[Files] must not reference Minecraft"
            )


# ---------------------------------------------------------------------------
# Code section: IsAppRunning helper
# ---------------------------------------------------------------------------


class TestCodeSection:
    """Validate [Code] section helpers."""

    def test_is_app_running_function(self, iss_content):
        """Must have IsAppRunning helper."""
        assert re.search(r"function\s+IsAppRunning", iss_content), (
            "Must define IsAppRunning() function"
        )

    def test_tasklist_check(self, iss_content):
        """IsAppRunning should use tasklist."""
        assert re.search(r"tasklist", iss_content, re.IGNORECASE), (
            "IsAppRunning should use tasklist.exe"
        )

    def test_cur_step_changed(self, iss_content):
        """Must have CurStepChanged for post-install hooks."""
        assert re.search(r"procedure\s+CurStepChanged", iss_content), (
            "Must define CurStepChanged() procedure"
        )


# ---------------------------------------------------------------------------
# Uninstall section
# ---------------------------------------------------------------------------


class TestUninstall:
    """Validate uninstall behavior."""

    def test_uninstall_run_kill(self, iss_content):
        """Uninstall must kill running process."""
        assert re.search(r"\[UninstallRun\]", iss_content), "Must have [UninstallRun] section"

    def test_taskkill_force(self, iss_content):
        """Uninstall must use taskkill /F (may span lines with \\)."""
        assert re.search(r"taskkill.*?/F", iss_content, re.IGNORECASE | re.DOTALL), (
            "Uninstall must use taskkill /F"
        )

    def test_uninstall_display_icon(self, iss_content):
        """Must set UninstallDisplayIcon."""
        assert re.search(r"UninstallDisplayIcon", iss_content), "Must set UninstallDisplayIcon"

    def test_uninstall_delete(self, iss_content):
        """Must have [UninstallDelete] section."""
        assert re.search(r"\[UninstallDelete\]", iss_content), "Must have [UninstallDelete] section"
