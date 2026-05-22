"""Contract tests for the Windows real-session smoke harness."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "windows_real_session_smoke.ps1"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_script_exists_and_targets_windows_real_session_flow():
    text = _script()
    assert "windows_real_session_smoke.ps1 must run on Windows" in text
    assert "OysterRecorder-real-session-smoke" in text
    assert "ManualSessionMinutes" in text


def test_resolves_latest_release_and_downloads_required_assets():
    text = _script()
    assert "https://api.github.com/repos/$Repo/releases/latest" in text
    assert "^OysterRecorder-[Ss]etup-.*\\.exe$" in text
    assert "SHA256SUMS.txt" in text
    assert "Invoke-WebRequest" in text
    assert "Verify-Checksum" in text


def test_validates_public_backend_health_and_appcast_path():
    text = _script()
    assert 'Join-BackendUrl "/healthz"' in text
    assert 'Join-BackendUrl "/api/v1/updates/appcast.xml"' in text
    assert "$script:ReleaseTag" in text
    assert "OysterRecorder-setup-v2.6.0.exe" in text


def test_reports_signature_but_only_requires_it_when_flag_set():
    text = _script()
    assert "Get-AuthenticodeSignature" in text
    assert "RequireSignedInstaller" in text
    assert "authenticode_status" in text
    assert 'if ($RequireSignedInstaller -and $signature.Status -ne "Valid")' in text


def test_installs_launches_and_uninstalls_recorder():
    text = _script()
    assert "/VERYSILENT" in text
    assert "InteractiveInstall" in text
    assert 'Join-Path $script:InstallDir "gamedata-recorder.exe"' in text
    assert "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" in text
    assert '-ArgumentList @("--tray")' in text
    assert "unins000.exe" in text
    assert "KeepInstalled" in text


def test_admin_token_is_env_only_and_delta_is_optional():
    text = _script()
    assert "[string]$AdminTokenEnv" in text
    assert "GetEnvironmentVariable($AdminTokenEnv)" in text
    assert "Authorization" in text
    assert "Bearer $script:AdminToken" in text
    assert "RequireUploadDelta" in text
    assert "No backend upload/session counter increased" in text
    assert "[string]$AdminToken =" not in text
    assert "[string]$Token =" not in text


def test_generates_machine_readable_report_and_evidence_zip():
    text = _script()
    assert "real-session-report.json" in text
    assert "OysterRecorder-real-session-evidence.zip" in text
    assert "ConvertTo-Json -Depth 16" in text
    assert "Compress-Archive" in text
    assert "GameData Recorder" in text
