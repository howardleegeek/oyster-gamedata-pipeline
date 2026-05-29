"""Tests for the latest-release distribution smoke workflow."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_release_smoke_script_checks_installer_asset() -> None:
    script = (REPO_ROOT / "scripts" / "verify_latest_release_assets.sh").read_text()

    assert "OysterRecorder-[Ss]etup-" in script
    assert "GameDataRecorder-Setup-recorder" not in script
    assert "SHA256SUMS.txt" in script
    assert "CURRENT_CONSUMER_TAG" in script
    assert "does not match latest release" in script
    assert "Verified source release anchor" in script
    assert "curl -fsSIL -L" in script
    assert "## Windows installer" in script
    assert "SmartScreen" in script
    assert "sha256:" in script


def test_release_smoke_workflow_runs_on_schedule_and_manual_dispatch() -> None:
    workflow = (REPO_ROOT / ".github/workflows/release-distribution-smoke.yml").read_text()

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "contents: read" in workflow
    assert "bash scripts/verify_latest_release_assets.sh" in workflow


def test_windows_build_stages_obs_runtime_before_inno_compile() -> None:
    workflow = (REPO_ROOT / ".github/workflows/build-recorder-windows.yml").read_text()

    assert "cargo build --release --locked" in workflow
    assert "RECORDER_RUNTIME_REPO: howardleegeek/gamedata-recorder" in workflow
    assert "RECORDER_RUNTIME_TAG: v2.6.0" in workflow
    assert "RECORDER_RUNTIME_ASSET: gamedata-recorder-windows-x64.zip" in workflow
    assert "gh release download $env:RECORDER_RUNTIME_TAG" in workflow
    assert "installer\\staging" in workflow
    assert "Staged installer runtime missing required OBS asset" in workflow
    assert "Staged runtime PE architecture mismatch" in workflow
    assert "allowedX86RuntimeHelpers" in workflow
    assert "Allowed OBS 32-bit runtime helper" in workflow
    assert "Verified staged runtime PE architecture: x86-64" in workflow
    assert "obs.dll" in workflow
    assert "libobs-d3d11.dll" in workflow
    assert "libobs-opengl.dll" in workflow
    assert "libobs-winrt.dll" in workflow
    assert "obs-ffmpeg-mux.exe" in workflow
    assert "obs-plugins" in workflow
    assert "data" in workflow
    assert "/DSourceDir=$srcDir" in workflow


def test_recorder_pyinstaller_workflows_bundle_ffprobe_with_ffmpeg() -> None:
    workflows = {
        ".github/workflows/build-recorder-exe.yml": 2,
        ".github/workflows/build-recorder-installer.yml": 1,
    }

    for relative, expected_pyinstaller_invocations in workflows.items():
        workflow = (REPO_ROOT / relative).read_text()
        assert "ffmpeg-master-latest-win64-gpl.zip" in workflow
        assert "ffprobe.exe" in workflow
        assert "$ffprobeBin" in workflow
        assert workflow.count('--add-binary "ffmpeg.exe;."') == expected_pyinstaller_invocations
        assert workflow.count('--add-binary "ffprobe.exe;."') == expected_pyinstaller_invocations


def test_bundled_installer_stages_proven_rust_recorder_runtime() -> None:
    workflow = (REPO_ROOT / ".github/workflows/build-recorder-installer.yml").read_text()
    installer = (REPO_ROOT / "bin/build_bundled_installer/installer.iss").read_text()

    assert "RECORDER_RUNTIME_REPO: howardleegeek/gamedata-recorder" in workflow
    assert "RECORDER_RUNTIME_TAG: v2.6.0" in workflow
    assert "RECORDER_RUNTIME_ASSET: gamedata-recorder-windows-x64.zip" in workflow
    assert "Stage Rust gamedata-recorder v2.6.0 runtime" in workflow
    assert "gh release download $env:RECORDER_RUNTIME_TAG" in workflow
    assert "bundle/gamedata-recorder/gamedata-recorder.exe" in workflow
    assert "bundle/gamedata-recorder/obs-plugins/64bit/win-capture.dll" in workflow
    assert "Staged Rust recorder runtime missing required path" in workflow
    assert "Preflight bundled Rust recorder path" in workflow

    assert 'Source: "{#BundleRoot}\\\\gamedata-recorder\\\\*"' in installer
    assert 'DestDir: "{app}\\\\gamedata-recorder"' in installer
    assert "PROVEN Rust gamedata-recorder v2.6.0 runtime" in installer


def test_windows_installer_smoke_workflow_exercises_real_install_path() -> None:
    workflow = (REPO_ROOT / ".github/workflows/windows-installer-smoke.yml").read_text()

    assert "runs-on: windows-latest" in workflow
    assert "gh release download" in workflow
    assert "SHA256SUMS.txt" in workflow
    assert "Get-FileHash" in workflow
    assert "Report Authenticode signature" in workflow
    assert "Get-AuthenticodeSignature -FilePath $installer" in workflow
    assert "Authenticode status:" in workflow
    assert "REQUIRE_SIGNED_INSTALLER: ${{ vars.REQUIRE_SIGNED_INSTALLER }}" in workflow
    assert '$env:REQUIRE_SIGNED_INSTALLER -ceq "true"' in workflow
    assert '$signature.Status -ne "Valid"' in workflow
    assert "/VERYSILENT" in workflow
    assert "$env:LOCALAPPDATA" in workflow
    assert "gamedata-recorder.exe" in workflow
    assert "OysterPlay.exe" in workflow
    assert "OysterRecorder-onedir\\OysterRecorder-onedir.exe" in workflow
    assert "INSTALL_LAYOUT=$layout" in workflow
    assert "Verify OBS runtime dependencies" in workflow
    assert "obs.dll" in workflow
    assert "Bundled runtime dependency missing" in workflow
    assert "Bundled recorder ffmpeg.exe missing" in workflow
    assert "libobs-d3d11.dll" in workflow
    assert "libobs-opengl.dll" in workflow
    assert "libobs-winrt.dll" in workflow
    assert "obs-ffmpeg-mux.exe" in workflow
    assert "OBS runtime dependency missing" in workflow
    assert "OBS runtime directory missing" in workflow
    assert "Verify installed PE architecture" in workflow
    assert "Bundled recorder ffprobe.exe missing" in workflow
    assert "Installed PE architecture mismatch" in workflow
    assert "allowedX86RuntimeHelpers" in workflow
    assert "Allowed OBS 32-bit runtime helper" in workflow
    assert 'Where-Object { $_.Name -notlike "unins*.exe" }' in workflow
    assert "launch_tray_smoke" in workflow
    assert "default: true" in workflow
    assert "inputs.launch_tray_smoke == true" in workflow
    assert "github.event_name != 'workflow_dispatch'" in workflow
    assert "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" in workflow
    assert "Start-Process" in workflow
    assert "GAMEDATA_CI_MODE" in workflow
    assert "RUST_BACKTRACE" in workflow
    assert "$process.HasExited" in workflow
    assert "RECORDER_PID" in workflow
    assert "Stop-Process" in workflow
    assert "unins000.exe" in workflow
    assert "windows-installer-smoke-logs" in workflow
