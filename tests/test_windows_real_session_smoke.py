"""Contract tests for the Windows real-session smoke harness."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "windows_real_session_smoke.ps1"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "windows-real-session-smoke.yml"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_script_exists_and_targets_windows_real_session_flow():
    text = _script()
    assert "windows_real_session_smoke.ps1 must run on Windows" in text
    assert "OysterRecorder-real-session-smoke" in text
    assert "ManualSessionMinutes" in text


def test_manual_workflow_runs_only_on_dispatch_with_self_hosted_runner():
    text = _workflow()
    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "push:" not in text
    assert "runner_labels_json" in text
    assert "runs-on: ${{ fromJSON(inputs.runner_labels_json) }}" in text
    assert "timeout-minutes: 60" in text


def test_manual_workflow_invokes_strict_real_session_script():
    text = _workflow()
    assert "scripts\\windows_real_session_smoke.ps1" in text
    assert "$smokeArgs = @{" in text
    assert "@smokeArgs" in text
    assert "$args = @(" not in text
    assert "allow_gui_launch" in text
    assert '$allowGuiLaunch = "${{ inputs.allow_gui_launch }}" -eq "true"' in text
    assert "$smokeArgs.StrictRealSession = $true" in text
    assert "$smokeArgs.RequireUploadDelta = $true" in text
    assert "$smokeArgs.NoGuiPreflight = $true" in text
    assert "ManualSessionMinutes = [int]" in text
    assert '$smokeArgs.AdminTokenEnv = "TESTER_ADMIN_TOKEN"' in text
    assert "TESTER_ADMIN_TOKEN: ${{ secrets.TESTER_ADMIN_TOKEN }}" in text
    assert "MinimumGameStateRows = [int]" in text
    assert "MinimumVideoBytes = [int64]" in text
    assert "minecraft_launch_command" in text
    assert "MinecraftLaunchCommand" in text
    assert "windows-real-session-evidence" in text


def test_collects_host_metadata_without_preempting_windows_guard():
    text = _script()
    assert "$script:IsWindowsHost =" in text
    assert '$hostOs = "unknown"' in text
    assert "os = $hostOs" in text
    assert text.index("$script:IsWindowsHost =") < text.index("$script:Report =")
    assert text.index('$hostOs = "unknown"') < text.index("Get-CimInstance")
    assert "if (-not $script:IsWindowsHost)" in text


def test_resolves_latest_release_and_downloads_required_assets():
    text = _script()
    assert "https://api.github.com/repos/$Repo/releases/latest" in text
    assert "^OysterRecorder-[Ss]etup-.*\\.exe$" in text
    assert "SHA256SUMS.txt" in text
    assert "Invoke-WebRequest" in text
    assert "Unblock-ReleaseInstaller" in text
    assert "Unblock-File -Path $InstallerPath" in text
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
    assert 'throw "AdminTokenEnv' in text
    assert "Authorization" in text
    assert "Bearer $script:AdminToken" in text
    assert "RequireUploadDelta" in text
    assert "No backend upload/session counter increased" in text
    assert "[string]$AdminToken =" not in text
    assert "[string]$Token =" not in text


def test_strict_real_session_mode_requires_hard_evidence():
    text = _script()
    assert "[switch]$StrictRealSession" in text
    assert "[int]$MinimumGameStateRows = 30" in text
    assert "[int64]$MinimumVideoBytes = 102400" in text
    assert "Assert-StrictRealSessionConfig" in text
    assert "StrictRealSession cannot be used with NoGuiPreflight" in text
    assert "StrictRealSession cannot be used with SkipInstall" in text
    assert "StrictRealSession requires ManualSessionMinutes greater than 0" in text
    assert "StrictRealSession requires RequireUploadDelta" in text
    assert "StrictRealSession requires AdminTokenEnv" in text


def test_no_gui_preflight_does_not_execute_installer_or_recorder():
    text = _script()
    assert "[switch]$NoGuiPreflight" in text
    assert "no_gui_preflight = [bool]$NoGuiPreflight" in text
    assert "Assert-NoGuiPreflightConfig" in text
    assert "NoGuiPreflight cannot be used with InteractiveInstall" in text
    assert "NoGuiPreflight cannot be used with MinecraftLaunchCommand" in text
    assert "no-gui-launch-disabled" in text
    assert "no-gui-preflight" in text
    assert "without executing installer or recorder" in text
    no_gui_branch = text.index("if ($NoGuiPreflight) {")
    install_call = text.index("Install-Recorder -InstallerPath")
    assert no_gui_branch < install_call


def test_strict_real_session_snapshots_and_validates_artifacts():
    text = _script()
    assert "Get-RecorderArtifactRoots" in text
    assert "Documents\\OysterClips" in text
    assert "GameData Recorder\\recordings" in text
    assert "Select-Object FullName, Name, Extension, Length, LastWriteTimeUtc" in text
    assert "Start-RealSessionArtifactSnapshot" in text
    assert "Verify-StrictRealSessionArtifacts" in text
    assert "game_state.jsonl" in text
    assert "states.jsonl" in text
    assert "StrictRealSession did not find fresh game_state/states JSONL" in text
    assert "StrictRealSession did not find a fresh MP4" in text
    assert "session_manifest.json" in text
    assert "metadata.json" in text
    assert "StrictRealSession did not find a fresh session manifest or metadata JSON" in text


def test_strict_real_session_forces_upload_config_temporarily():
    text = _script()
    assert '[string]$MinecraftLaunchCommand = ""' in text
    assert "Enable-StrictRealSessionRecorderConfig" in text
    assert "Restore-StrictRealSessionRecorderConfig" in text
    assert "GameData Recorder\\config.json" in text
    assert "config.before-strict-real-session.json" in text
    assert "autoUploadOnCompletion" in text
    assert "deleteUploadedFiles" in text
    assert '"autoUploadOnCompletion" -Value $true' in text
    assert '"deleteUploadedFiles" -Value $false' in text
    assert "restore-recorder-config" in text


def test_strict_real_session_can_launch_minecraft_command():
    text = _script()
    assert "Start-MinecraftLaunchCommand" in text
    assert "Focus-MinecraftWindow" in text
    assert "Wait-ForMinecraftWindow" in text
    assert "if (-not $MinecraftLaunchCommand -and -not $StrictRealSession)" in text
    assert "SetForegroundWindow" in text
    assert "GetForegroundWindow" in text
    assert "GetClientRect" in text
    assert "AttachThreadInput" in text
    assert "minecraft-window-focus" in text
    assert "client=${clientWidth}x${clientHeight}" in text
    assert "[OysterFocusNativeMethods]::ShowWindow($process.MainWindowHandle, 3)" in text
    assert "minecraft-window-ready" in text
    assert '$message = "Minecraft javaw window was not foreground within 90 seconds"' in text
    assert 'throw "StrictRealSession $message"' in text
    assert "minecraft_launch_command = $MinecraftLaunchCommand" in text
    assert (
        'Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", $MinecraftLaunchCommand)' in text
    )
    launch_call = text.rindex("Launch-Recorder")
    minecraft_call = text.rindex("Start-MinecraftLaunchCommand")
    wait_call = text.rindex("Wait-ForMinecraftWindow")
    hotkey_start_call = text.rindex('Send-RecordingHotkey -Action "start"')
    manual_call = text.rindex("Run-ManualSessionWindow")
    hotkey_stop_call = text.rindex('Send-RecordingHotkey -Action "stop"')
    verify_call = text.rindex("Verify-StrictRealSessionArtifacts")
    assert launch_call < minecraft_call < wait_call < hotkey_start_call < manual_call
    assert manual_call < hotkey_stop_call < verify_call


def test_strict_real_session_cleans_stale_recorder_processes_before_launch():
    text = _script()
    assert "Stop-StaleRecorderProcesses" in text
    assert "stale-recorder-cleanup" in text
    assert '"gamedata-recorder", "OysterRecorder", "obs-ffmpeg-mux"' in text
    assert "Stop-Process -Id $_.Id -Force" in text

    no_gui_branch = text.index("if ($NoGuiPreflight) {")
    cleanup_call = text.rindex("Stop-StaleRecorderProcesses")
    admin_state_call = text.rindex('$script:AdminToken = Get-AdminToken')
    install_call = text.rindex("Install-Recorder -InstallerPath")
    assert no_gui_branch < cleanup_call < admin_state_call < install_call


def test_strict_real_session_sends_f9_hotkeys_for_video_chain():
    text = _script()
    assert "Send-RecordingHotkey" in text
    assert 'ValidateSet("start", "stop")' in text
    assert "keybd_event" in text
    assert "$vkF9 = [byte]0x78" in text
    assert '$stepName = "recording-hotkey-$Action"' in text
    assert "$script:Report.real_session.hotkey.start_sent = $true" in text
    assert "$script:Report.real_session.hotkey.stop_sent = $true" in text


def test_recorder_launch_enables_targeted_debug_logs():
    text = _script()
    assert '$env:GAMEDATA_CI_MODE = "1"' in text
    assert "$script:Report.real_session.recorder_env.gamedata_ci_mode" in text
    assert "$script:Report.real_session.recorder_env.gamedata_output_dir" in text
    assert "$script:Report.real_session.recorder_env.rust_log" in text
    assert '$env:RUST_LOG = "gamedata_recorder=debug,info"' in text
    assert '$env:RUST_BACKTRACE = "1"' in text


def test_strict_real_session_artifact_report_is_machine_readable():
    text = _script()
    assert "real_session = [ordered]@{" in text
    assert "minimum_game_state_rows = $MinimumGameStateRows" in text
    assert "minimum_video_bytes = $MinimumVideoBytes" in text
    assert "before_file_count" in text
    assert "fresh_file_count" in text
    assert "game_state = [ordered]@{" in text
    assert "video = [ordered]@{" in text
    assert "manifest = [ordered]@{" in text
    assert "recorder_config = $null" in text
    assert "recorder_env = [ordered]@{" in text
    assert "hotkey = [ordered]@{" in text


def test_cleanup_only_touches_recorder_created_by_smoke_run():
    text = _script()
    assert "$script:RecorderInstalledBySmoke = $false" in text
    assert "$script:RecorderLaunchedBySmoke = $false" in text
    assert "$script:RecorderInstalledBySmoke = $true" in text
    assert "$script:RecorderLaunchedBySmoke = $true" in text
    assert "Recorder not launched by this smoke run" in text
    assert "Recorder not installed by this smoke run" in text
    assert "[System.StringComparison]::OrdinalIgnoreCase" in text


def test_checksum_parser_and_evidence_archive_are_hardened():
    text = _script()
    assert text.count('$expected = ($line.Line -split "\\s+")[0].ToLowerInvariant()') == 1
    assert "EMPTY-EVIDENCE.txt" in text
    assert "No evidence files were collected." in text


def test_generates_machine_readable_report_and_evidence_zip():
    text = _script()
    assert "real-session-report.json" in text
    assert "OysterRecorder-real-session-evidence.zip" in text
    assert "ConvertTo-Json -Depth 16" in text
    assert "Compress-Archive" in text
    assert "GameData Recorder" in text
