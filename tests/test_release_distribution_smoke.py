"""Tests for the latest-release distribution smoke workflow."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_release_smoke_script_checks_installer_asset() -> None:
    script = (REPO_ROOT / "scripts" / "verify_latest_release_assets.sh").read_text()

    assert "OysterRecorder-[Ss]etup-" in script
    assert "GameDataRecorder-Setup-recorder" not in script
    assert "SHA256SUMS.txt" in script
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
    assert "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" in workflow
    assert "Start-Process" in workflow
    assert "GAMEDATA_CI_MODE" in workflow
    assert "RUST_BACKTRACE" in workflow
    assert "$process.HasExited" in workflow
    assert "RECORDER_PID" in workflow
    assert "Stop-Process" in workflow
    assert "unins000.exe" in workflow
    assert "windows-installer-smoke-logs" in workflow
