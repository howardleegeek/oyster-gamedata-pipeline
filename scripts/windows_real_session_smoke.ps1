param(
    [string]$Repo = "howardleegeek/oyster-gamedata-pipeline",
    [string]$BackendUrl = "http://136.109.41.170:8081",
    [string]$OutputDir = "",
    [int]$LaunchSeconds = 20,
    [int]$ManualSessionMinutes = 0,
    [string]$AdminTokenEnv = "",
    [switch]$RequireUploadDelta,
    [switch]$StrictRealSession,
    [int]$MinimumGameStateRows = 30,
    [int64]$MinimumVideoBytes = 102400,
    [string]$MinecraftLaunchCommand = "",
    [switch]$InteractiveInstall,
    [switch]$RequireSignedInstaller,
    [switch]$NoGuiPreflight,
    [switch]$SkipInstall,
    [switch]$KeepInstalled
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:IsWindowsHost = ($env:OS -eq "Windows_NT") -or ($PSVersionTable.PSEdition -eq "Desktop")
$tempRoot = if ($env:TEMP) {
    $env:TEMP
} elseif ($env:TMPDIR) {
    $env:TMPDIR
} else {
    [System.IO.Path]::GetTempPath()
}

if (-not $OutputDir) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputDir = Join-Path $tempRoot "OysterRecorder-real-session-smoke-$stamp"
}

$installerDir = Join-Path $OutputDir "installer"
$launchOutputDir = Join-Path $OutputDir "launch-output"
$evidenceDir = Join-Path $OutputDir "evidence"
$reportPath = Join-Path $OutputDir "real-session-report.json"
$archivePath = Join-Path $OutputDir "OysterRecorder-real-session-evidence.zip"

New-Item -ItemType Directory -Force -Path $installerDir, $launchOutputDir, $evidenceDir | Out-Null

$script:StartedAt = (Get-Date).ToUniversalTime().ToString("o")
$script:Failed = $false
$script:RecorderProcess = $null
$script:ReleaseTag = $null
$script:InstallerPath = $null
$script:LocalAppDataRoot = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $OutputDir "LOCALAPPDATA" }
$script:AppDataRoot = if ($env:APPDATA) { $env:APPDATA } else { Join-Path $OutputDir "APPDATA" }
$script:InstallDir = Join-Path $script:LocalAppDataRoot "OysterRecorder"
$script:BeforeAdminState = $null
$script:AfterAdminState = $null
$script:AdminToken = ""
$script:RecorderInstalledBySmoke = $false
$script:RecorderLaunchedBySmoke = $false
$script:RealSessionStartedAtUtc = $null
$script:RealSessionBeforeFiles = @()
$script:RecorderConfigPath = Join-Path $script:AppDataRoot "GameData Recorder\config.json"
$script:RecorderConfigBackupPath = Join-Path $OutputDir "config.before-strict-real-session.json"
$script:MinecraftWindowFocused = $false
$script:MinecraftWindowFocusFailureReported = $false

$hostOs = "unknown"
if ($script:IsWindowsHost) {
    try {
        $hostOs = Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty Caption
    } catch {
        $hostOs = if ($env:OS) { $env:OS } else { "Windows" }
    }
} else {
    try {
        $hostOs = [System.Runtime.InteropServices.RuntimeInformation]::OSDescription
    } catch {
        $hostOs = if ($PSVersionTable.OS) { $PSVersionTable.OS } else { "non-Windows" }
    }
}

$script:Report = [ordered]@{
    started_at = $script:StartedAt
    finished_at = $null
    host = [ordered]@{
        computer_name = $env:COMPUTERNAME
        user = $env:USERNAME
        os = $hostOs
        powershell = $PSVersionTable.PSVersion.ToString()
    }
    repo = $Repo
    backend_url = $BackendUrl
    release = $null
    installer = $null
    no_gui_preflight = [bool]$NoGuiPreflight
    admin_state = [ordered]@{
        enabled = $false
        require_upload_delta = [bool]$RequireUploadDelta
        before = $null
        after = $null
        delta = $null
    }
    real_session = [ordered]@{
        strict = [bool]$StrictRealSession
        minimum_game_state_rows = $MinimumGameStateRows
        minimum_video_bytes = $MinimumVideoBytes
        started_at = $null
        roots = @()
        before_file_count = 0
        fresh_file_count = 0
        game_state = $null
        video = $null
        manifest = $null
        minecraft_launch_command = $MinecraftLaunchCommand
        recorder_config = $null
        recorder_env = [ordered]@{
            gamedata_ci_mode = $null
            gamedata_output_dir = $null
            rust_log = $null
        }
        hotkey = [ordered]@{
            start_sent = $false
            stop_sent = $false
            key = "F9"
        }
    }
    steps = @()
    artifacts = [ordered]@{}
}

function Add-Step {
    param(
        [string]$Name,
        [string]$Status,
        [string]$Detail = ""
    )
    $entry = [ordered]@{
        name = $Name
        status = $Status
        detail = $Detail
        timestamp = (Get-Date).ToUniversalTime().ToString("o")
    }
    $script:Report.steps += $entry
    Write-Host "[$Status] $Name $Detail"
    if ($Status -eq "fail") {
        $script:Failed = $true
    }
}

function Join-BackendUrl {
    param([string]$Path)
    return ($BackendUrl.TrimEnd("/") + "/" + $Path.TrimStart("/"))
}

function Get-AdminToken {
    if (-not $AdminTokenEnv) {
        return ""
    }
    $token = [Environment]::GetEnvironmentVariable($AdminTokenEnv)
    if (-not $token) {
        Add-Step "admin-token" "fail" "AdminTokenEnv '$AdminTokenEnv' is not set"
        throw "AdminTokenEnv '$AdminTokenEnv' is not set"
    }
    $script:Report.admin_state.enabled = $true
    return $token.Trim()
}

function Invoke-JsonGet {
    param(
        [string]$Uri,
        [hashtable]$Headers = @{}
    )
    $headersWithAgent = @{"User-Agent" = "oyster-real-session-smoke/1.0"}
    foreach ($key in $Headers.Keys) {
        $headersWithAgent[$key] = $Headers[$key]
    }
    return Invoke-RestMethod -Uri $Uri -Headers $headersWithAgent -Method Get -TimeoutSec 30
}

function Invoke-TextGet {
    param([string]$Uri)
    $headers = @{"User-Agent" = "oyster-real-session-smoke/1.0"}
    $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -Headers $headers -Method Get -TimeoutSec 30
    return $response.Content
}

function Get-AdminStateSnapshot {
    param([string]$Label)

    if (-not $script:AdminToken) {
        Add-Step "admin-state-$Label" "skip" "No admin token env configured"
        return $null
    }

    $headers = @{ "Authorization" = "Bearer $script:AdminToken" }
    $state = Invoke-JsonGet -Uri (Join-BackendUrl "/api/v1/admin/state") -Headers $headers
    $serialized = $state | ConvertTo-Json -Depth 12 -Compress
    if ($serialized.Contains("@") -or $serialized.Contains("download_url")) {
        throw "Admin state response contains a PII marker"
    }
    Add-Step "admin-state-$Label" "pass" "Fetched non-PII state summary"
    return $state
}

function Get-CountValue {
    param(
        [object]$State,
        [string]$Name
    )
    if (-not $State) {
        return 0
    }
    if (-not $State.counts) {
        return 0
    }
    $value = $State.counts.$Name
    if ($null -eq $value) {
        return 0
    }
    return [int]$value
}

function Assert-StrictRealSessionConfig {
    if (-not $StrictRealSession) {
        return
    }
    if ($NoGuiPreflight) {
        throw "StrictRealSession cannot be used with NoGuiPreflight"
    }
    if ($SkipInstall) {
        throw "StrictRealSession cannot be used with SkipInstall"
    }
    if ($ManualSessionMinutes -le 0) {
        throw "StrictRealSession requires ManualSessionMinutes greater than 0"
    }
    if (-not $RequireUploadDelta) {
        throw "StrictRealSession requires RequireUploadDelta"
    }
    if (-not $AdminTokenEnv) {
        throw "StrictRealSession requires AdminTokenEnv"
    }
    if ($MinimumGameStateRows -lt 1) {
        throw "MinimumGameStateRows must be at least 1"
    }
    if ($MinimumVideoBytes -lt 1) {
        throw "MinimumVideoBytes must be at least 1"
    }
    Add-Step "strict-real-session-config" "pass" "manual_minutes=$ManualSessionMinutes; upload_delta=required"
}

function Assert-NoGuiPreflightConfig {
    if (-not $NoGuiPreflight) {
        return
    }
    if ($InteractiveInstall) {
        throw "NoGuiPreflight cannot be used with InteractiveInstall"
    }
    if ($MinecraftLaunchCommand) {
        throw "NoGuiPreflight cannot be used with MinecraftLaunchCommand"
    }
    if ($LaunchSeconds -gt 0 -and -not $SkipInstall) {
        Add-Step "no-gui-launch-disabled" "pass" "will not install, launch recorder, start Minecraft, or send hotkeys"
        return
    }
    Add-Step "no-gui-launch-disabled" "pass" "will not install, launch recorder, start Minecraft, or send hotkeys"
}

function Get-RecorderArtifactRoots {
    $documents = ""
    try {
        $documents = [Environment]::GetFolderPath("MyDocuments")
    } catch {
        $documents = ""
    }

    $rawRoots = @(
        $(if ($documents) { Join-Path $documents "OysterClips" }),
        $(if ($env:USERPROFILE) { Join-Path $env:USERPROFILE "Documents\OysterClips" }),
        $(if ($env:OneDrive) { Join-Path $env:OneDrive "Documents\OysterClips" }),
        (Join-Path $script:LocalAppDataRoot "GameData Recorder\recordings"),
        (Join-Path $script:AppDataRoot "GameData Recorder\recordings"),
        $launchOutputDir
    )

    $seen = @{}
    $roots = @()
    foreach ($root in $rawRoots) {
        if (-not $root) {
            continue
        }
        $full = [System.IO.Path]::GetFullPath($root)
        $key = $full.ToLowerInvariant()
        if ($seen.ContainsKey($key)) {
            continue
        }
        $seen[$key] = $true
        $roots += $full
    }
    return $roots
}

function Get-RecorderSessionFiles {
    param([datetime]$SinceUtc = [datetime]::MinValue)

    $files = @()
    foreach ($root in Get-RecorderArtifactRoots) {
        if (-not (Test-Path -LiteralPath $root)) {
            continue
        }
        $files += Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object {
                $_.LastWriteTimeUtc -ge $SinceUtc -and (
                    $_.Extension -in @(".jsonl", ".json", ".mp4", ".log", ".txt", ".gz") -or
                    $_.Name -like "*.tar.gz"
                )
            } |
            Select-Object FullName, Name, Extension, Length, LastWriteTimeUtc
    }
    return $files
}

function Count-JsonlRows {
    param([string]$Path)

    $count = 0
    Get-Content -LiteralPath $Path -ReadCount 1000 -ErrorAction Stop |
        ForEach-Object { $count += $_.Count }
    return $count
}

function Set-JsonProperty {
    param(
        [object]$Object,
        [string]$Name,
        [object]$Value
    )
    if ($Object.PSObject.Properties.Name -contains $Name) {
        $Object.$Name = $Value
        return
    }
    $Object | Add-Member -MemberType NoteProperty -Name $Name -Value $Value
}

function Enable-StrictRealSessionRecorderConfig {
    if (-not $StrictRealSession) {
        Add-Step "strict-recorder-config" "skip" "StrictRealSession not set"
        return
    }

    $configDir = Split-Path $script:RecorderConfigPath -Parent
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null

    if (Test-Path -LiteralPath $script:RecorderConfigPath) {
        Copy-Item -LiteralPath $script:RecorderConfigPath -Destination $script:RecorderConfigBackupPath -Force
        $config = Get-Content -LiteralPath $script:RecorderConfigPath -Raw | ConvertFrom-Json
    } else {
        $config = [pscustomobject]@{}
    }

    if ($config.PSObject.Properties.Name -notcontains "credentials" -or -not $config.credentials) {
        Set-JsonProperty -Object $config -Name "credentials" -Value ([pscustomobject]@{})
    }
    Set-JsonProperty -Object $config.credentials -Name "hasConsented" -Value $true
    if ($config.credentials.PSObject.Properties.Name -notcontains "consentGivenAtVersion") {
        Set-JsonProperty -Object $config.credentials -Name "consentGivenAtVersion" -Value "strict-real-session-smoke"
    }

    if ($config.PSObject.Properties.Name -notcontains "preferences" -or -not $config.preferences) {
        Set-JsonProperty -Object $config -Name "preferences" -Value ([pscustomobject]@{})
    }
    Set-JsonProperty -Object $config.preferences -Name "autoUploadOnCompletion" -Value $true
    Set-JsonProperty -Object $config.preferences -Name "deleteUploadedFiles" -Value $false

    $config | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $script:RecorderConfigPath -Encoding UTF8
    $script:Report.real_session.recorder_config = [ordered]@{
        path = $script:RecorderConfigPath
        backup_path = $(if (Test-Path -LiteralPath $script:RecorderConfigBackupPath) { $script:RecorderConfigBackupPath } else { $null })
        auto_upload_on_completion = $true
        delete_uploaded_files = $false
    }
    Add-Step "strict-recorder-config" "pass" "autoUploadOnCompletion=true; deleteUploadedFiles=false"
}

function Restore-StrictRealSessionRecorderConfig {
    if (-not $StrictRealSession) {
        return
    }
    if (Test-Path -LiteralPath $script:RecorderConfigBackupPath) {
        Copy-Item -LiteralPath $script:RecorderConfigBackupPath -Destination $script:RecorderConfigPath -Force
        Add-Step "restore-recorder-config" "pass" "restored $script:RecorderConfigPath"
    }
}

function Start-MinecraftLaunchCommand {
    if (-not $MinecraftLaunchCommand) {
        Add-Step "minecraft-launch-command" "skip" "No MinecraftLaunchCommand configured"
        return
    }
    Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", $MinecraftLaunchCommand)
    Add-Step "minecraft-launch-command" "pass" $MinecraftLaunchCommand
}

function Focus-MinecraftWindow {
    $process = Get-Process -Name "javaw" -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -and $_.MainWindowHandle -ne 0 } |
        Sort-Object StartTime -Descending |
        Select-Object -First 1
    if (-not $process) {
        return $false
    }

    try {
        if (-not ([System.Management.Automation.PSTypeName]"OysterFocusNativeMethods").Type) {
            Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class OysterFocusNativeMethods {
    public struct RECT {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("kernel32.dll")]
    public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")]
    public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("user32.dll")]
    public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern IntPtr SetActiveWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern IntPtr SetFocus(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool AllowSetForegroundWindow(int dwProcessId);
    [DllImport("user32.dll")]
    public static extern bool GetClientRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
}
"@
        }

        $currentForeground = [OysterFocusNativeMethods]::GetForegroundWindow()
        [uint32]$foregroundPid = 0
        $foregroundThread = [OysterFocusNativeMethods]::GetWindowThreadProcessId($currentForeground, [ref]$foregroundPid)
        [uint32]$targetPid = 0
        $targetThread = [OysterFocusNativeMethods]::GetWindowThreadProcessId($process.MainWindowHandle, [ref]$targetPid)
        $currentThread = [OysterFocusNativeMethods]::GetCurrentThreadId()
        [OysterFocusNativeMethods]::AllowSetForegroundWindow(-1) | Out-Null
        [OysterFocusNativeMethods]::ShowWindow($process.MainWindowHandle, 9) | Out-Null
        [OysterFocusNativeMethods]::ShowWindow($process.MainWindowHandle, 3) | Out-Null
        $attachedCurrent = $false
        $attachedForeground = $false
        try {
            if ($targetThread -ne 0 -and $currentThread -ne $targetThread) {
                $attachedCurrent = [OysterFocusNativeMethods]::AttachThreadInput($currentThread, $targetThread, $true)
            }
            if ($targetThread -ne 0 -and $foregroundThread -ne 0 -and $foregroundThread -ne $targetThread) {
                $attachedForeground = [OysterFocusNativeMethods]::AttachThreadInput($foregroundThread, $targetThread, $true)
            }
            [OysterFocusNativeMethods]::BringWindowToTop($process.MainWindowHandle) | Out-Null
            [OysterFocusNativeMethods]::SetActiveWindow($process.MainWindowHandle) | Out-Null
            [OysterFocusNativeMethods]::SetFocus($process.MainWindowHandle) | Out-Null
            $setForeground = [OysterFocusNativeMethods]::SetForegroundWindow($process.MainWindowHandle)
        } finally {
            if ($attachedForeground) {
                [OysterFocusNativeMethods]::AttachThreadInput($foregroundThread, $targetThread, $false) | Out-Null
            }
            if ($attachedCurrent) {
                [OysterFocusNativeMethods]::AttachThreadInput($currentThread, $targetThread, $false) | Out-Null
            }
        }

        Start-Sleep -Milliseconds 500
        $verifiedForeground = [OysterFocusNativeMethods]::GetForegroundWindow()
        [uint32]$verifiedPid = 0
        [OysterFocusNativeMethods]::GetWindowThreadProcessId($verifiedForeground, [ref]$verifiedPid) | Out-Null
        $clientRect = New-Object OysterFocusNativeMethods+RECT
        $clientWidth = 0
        $clientHeight = 0
        if ([OysterFocusNativeMethods]::GetClientRect($process.MainWindowHandle, [ref]$clientRect)) {
            $clientWidth = [Math]::Max(0, $clientRect.Right - $clientRect.Left)
            $clientHeight = [Math]::Max(0, $clientRect.Bottom - $clientRect.Top)
        }
        if (-not $script:MinecraftWindowFocused) {
            if ([int]$verifiedPid -eq [int]$process.Id) {
                Add-Step "minecraft-window-focus" "pass" "pid=$($process.Id); hwnd=$($process.MainWindowHandle); foreground_pid=$verifiedPid; client=${clientWidth}x${clientHeight}"
                $script:MinecraftWindowFocused = $true
            } elseif (-not $script:MinecraftWindowFocusFailureReported) {
                Add-Step "minecraft-window-focus" "skip" "target_pid=$($process.Id); foreground_pid=$verifiedPid; set_foreground=$setForeground; client=${clientWidth}x${clientHeight}"
                $script:MinecraftWindowFocusFailureReported = $true
            }
        }
        return ([int]$verifiedPid -eq [int]$process.Id)
    } catch {
        if (-not $script:MinecraftWindowFocused -and -not $script:MinecraftWindowFocusFailureReported) {
            Add-Step "minecraft-window-focus" "skip" $_.Exception.Message
            $script:MinecraftWindowFocusFailureReported = $true
        }
        return $false
    }
}

function Wait-ForMinecraftWindow {
    if (-not $MinecraftLaunchCommand -and -not $StrictRealSession) {
        Add-Step "minecraft-window-ready" "skip" "No MinecraftLaunchCommand configured"
        return
    }

    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        if (Focus-MinecraftWindow) {
            Add-Step "minecraft-window-ready" "pass" "javaw is foreground before manual session window"
            return
        }
        if ($script:RecorderProcess -and $script:RecorderProcess.HasExited) {
            throw "Recorder exited while waiting for Minecraft window with code $($script:RecorderProcess.ExitCode)"
        }
        Start-Sleep -Seconds 2
    }

    $message = "Minecraft javaw window was not foreground within 90 seconds"
    if ($StrictRealSession) {
        throw "StrictRealSession $message"
    }
    Add-Step "minecraft-window-ready" "skip" $message
}

function Assert-StrictGameWindowReady {
    if (-not $StrictRealSession) {
        Add-Step "strict-game-window-precheck" "skip" "StrictRealSession not set"
        return
    }
    if ($MinecraftLaunchCommand) {
        Add-Step "strict-game-window-precheck" "skip" "MinecraftLaunchCommand will launch the game"
        return
    }
    if (Focus-MinecraftWindow) {
        Add-Step "strict-game-window-precheck" "pass" "existing javaw window is ready"
        return
    }

    throw "StrictRealSession requires an existing Minecraft javaw window when MinecraftLaunchCommand is empty"
}

function Stop-StaleRecorderProcesses {
    if ($NoGuiPreflight) {
        Add-Step "stale-recorder-cleanup" "skip" "NoGuiPreflight set"
        return
    }

    $names = @("gamedata-recorder", "OysterRecorder", "obs-ffmpeg-mux")
    $stopped = @()
    foreach ($name in $names) {
        Get-Process -Name $name -ErrorAction SilentlyContinue |
            ForEach-Object {
                $detail = "$($_.ProcessName):$($_.Id)"
                try {
                    Stop-Process -Id $_.Id -Force -ErrorAction Stop
                    $stopped += $detail
                } catch {
                    Add-Step "stale-recorder-cleanup" "fail" "could not stop ${detail}: $($_.Exception.Message)"
                    throw
                }
            }
    }

    if ($stopped.Count -eq 0) {
        Add-Step "stale-recorder-cleanup" "pass" "no stale recorder or OBS helper processes"
        return
    }

    Start-Sleep -Seconds 1
    Add-Step "stale-recorder-cleanup" "pass" ("stopped " + ($stopped -join ", "))
}

function Send-RecordingHotkey {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("start", "stop")]
        [string]$Action
    )

    if (-not $StrictRealSession) {
        Add-Step "recording-hotkey-$Action" "skip" "StrictRealSession not set"
        return
    }

    $stepName = "recording-hotkey-$Action"
    Focus-MinecraftWindow | Out-Null
    try {
        if (-not ([System.Management.Automation.PSTypeName]"OysterKeyboardNativeMethods").Type) {
            Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class OysterKeyboardNativeMethods {
    [DllImport("user32.dll")]
    public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
}
"@
        }
        $vkF9 = [byte]0x78
        $keyEventFKeyUp = [uint32]0x0002
        [OysterKeyboardNativeMethods]::keybd_event($vkF9, 0, 0, [UIntPtr]::Zero)
        Start-Sleep -Milliseconds 120
        [OysterKeyboardNativeMethods]::keybd_event($vkF9, 0, $keyEventFKeyUp, [UIntPtr]::Zero)
        if ($Action -eq "start") {
            $script:Report.real_session.hotkey.start_sent = $true
        } else {
            $script:Report.real_session.hotkey.stop_sent = $true
        }
        Add-Step $stepName "pass" "sent F9 $Action hotkey"
        Start-Sleep -Seconds 2
    } catch {
        Add-Step $stepName "fail" $_.Exception.Message
        throw
    }
}

function Start-RealSessionArtifactSnapshot {
    if (-not $StrictRealSession) {
        Add-Step "real-session-artifact-snapshot" "skip" "StrictRealSession not set"
        return
    }

    $script:RealSessionStartedAtUtc = (Get-Date).ToUniversalTime().AddSeconds(-5)
    $script:RealSessionBeforeFiles = @(Get-RecorderSessionFiles)
    $roots = @(Get-RecorderArtifactRoots)
    $script:Report.real_session.started_at = $script:RealSessionStartedAtUtc.ToString("o")
    $script:Report.real_session.roots = $roots
    $script:Report.real_session.before_file_count = $script:RealSessionBeforeFiles.Count
    Add-Step "real-session-artifact-snapshot" "pass" "roots=$($roots.Count); before_files=$($script:RealSessionBeforeFiles.Count)"
}

function Verify-StrictRealSessionArtifacts {
    if (-not $StrictRealSession) {
        Add-Step "verify-real-session-artifacts" "skip" "StrictRealSession not set"
        return
    }
    if (-not $script:RealSessionStartedAtUtc) {
        throw "StrictRealSession artifact snapshot was not started"
    }

    Start-Sleep -Seconds 5
    $freshFiles = @(Get-RecorderSessionFiles -SinceUtc $script:RealSessionStartedAtUtc)
    $script:Report.real_session.fresh_file_count = $freshFiles.Count

    $bestGameState = $null
    $bestGameStateRows = 0
    foreach ($file in ($freshFiles | Where-Object { $_.Name -in @("game_state.jsonl", "states.jsonl") })) {
        $rows = Count-JsonlRows -Path $file.FullName
        if ($rows -gt $bestGameStateRows) {
            $bestGameState = $file
            $bestGameStateRows = $rows
        }
    }
    if (-not $bestGameState -or $bestGameStateRows -lt $MinimumGameStateRows) {
        throw "StrictRealSession did not find fresh game_state/states JSONL with at least $MinimumGameStateRows rows"
    }
    $script:Report.real_session.game_state = [ordered]@{
        path = $bestGameState.FullName
        rows = $bestGameStateRows
        size_bytes = $bestGameState.Length
        last_write_utc = $bestGameState.LastWriteTimeUtc.ToString("o")
    }

    $video = $freshFiles |
        Where-Object { $_.Extension -eq ".mp4" -and $_.Length -ge $MinimumVideoBytes } |
        Sort-Object Length -Descending |
        Select-Object -First 1
    if (-not $video) {
        throw "StrictRealSession did not find a fresh MP4 at least $MinimumVideoBytes bytes"
    }
    $script:Report.real_session.video = [ordered]@{
        path = $video.FullName
        size_bytes = $video.Length
        last_write_utc = $video.LastWriteTimeUtc.ToString("o")
    }

    $manifest = $freshFiles |
        Where-Object { $_.Name -in @("session_manifest.json", "manifest.json", "metadata.json", "MANIFEST.json", "MANIFEST.signed.json") } |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if (-not $manifest) {
        throw "StrictRealSession did not find a fresh session manifest or metadata JSON"
    }
    $script:Report.real_session.manifest = [ordered]@{
        path = $manifest.FullName
        size_bytes = $manifest.Length
        last_write_utc = $manifest.LastWriteTimeUtc.ToString("o")
    }

    Add-Step "verify-real-session-artifacts" "pass" "fresh_files=$($freshFiles.Count); game_state_rows=$bestGameStateRows; video_bytes=$($video.Length)"
}

function Save-Report {
    $script:Report.finished_at = (Get-Date).ToUniversalTime().ToString("o")
    $script:Report.artifacts.report_json = $reportPath
    $script:Report.artifacts.evidence_zip = $archivePath
    $script:Report | ConvertTo-Json -Depth 16 | Set-Content -Path $reportPath -Encoding UTF8
}

function Copy-EvidenceFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return
    }

    $item = Get-Item -LiteralPath $Path
    if (-not $item.PSIsContainer) {
        Copy-Item -LiteralPath $Path -Destination $evidenceDir -Force -ErrorAction SilentlyContinue
        return
    }

    $safeName = ($item.FullName -replace "[:\\\/]", "_").Trim("_")
    $dest = Join-Path $evidenceDir $safeName
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Get-ChildItem -LiteralPath $item.FullName -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in @(".log", ".txt", ".json", ".jsonl", ".xml") } |
        ForEach-Object {
            $relative = $_.FullName.Substring($item.FullName.Length).TrimStart("\", "/")
            $target = Join-Path $dest $relative
            New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $target -Force -ErrorAction SilentlyContinue
        }
}

function Collect-Evidence {
    Copy-EvidenceFile -Path $installerDir
    Copy-EvidenceFile -Path $launchOutputDir
    foreach ($root in Get-RecorderArtifactRoots) {
        Copy-EvidenceFile -Path $root
    }
    Copy-EvidenceFile -Path (Join-Path $script:AppDataRoot "GameData Recorder")
    Copy-EvidenceFile -Path (Join-Path $script:LocalAppDataRoot "GameData Recorder")
    Copy-EvidenceFile -Path $script:InstallDir

    if (Test-Path $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }
    if (Test-Path $evidenceDir) {
        $evidenceItems = @(Get-ChildItem -LiteralPath $evidenceDir -Force -ErrorAction SilentlyContinue)
        if ($evidenceItems.Count -eq 0) {
            Set-Content -Path (Join-Path $evidenceDir "EMPTY-EVIDENCE.txt") -Value "No evidence files were collected." -Encoding UTF8
        }
        Compress-Archive -Path (Join-Path $evidenceDir "*") -DestinationPath $archivePath -Force
    }
}

function Resolve-LatestRelease {
    $release = Invoke-JsonGet -Uri "https://api.github.com/repos/$Repo/releases/latest"
    $installer = $release.assets |
        Where-Object { $_.name -match "^OysterRecorder-[Ss]etup-.*\.exe$" } |
        Select-Object -First 1
    $sha = $release.assets |
        Where-Object { $_.name -eq "SHA256SUMS.txt" } |
        Select-Object -First 1

    if (-not $installer) {
        throw "Latest release $($release.tag_name) has no OysterRecorder setup exe"
    }
    if (-not $sha) {
        throw "Latest release $($release.tag_name) has no SHA256SUMS.txt"
    }

    $script:ReleaseTag = $release.tag_name
    $script:Report.release = [ordered]@{
        tag = $release.tag_name
        name = $release.name
        published_at = $release.published_at
        url = $release.html_url
        installer_asset = $installer.name
        installer_url = $installer.browser_download_url
        sha_asset = $sha.name
        sha_url = $sha.browser_download_url
    }

    Add-Step "resolve-latest-release" "pass" "$($release.tag_name) / $($installer.name)"
    return [ordered]@{ release = $release; installer = $installer; sha = $sha }
}

function Download-ReleaseAssets {
    param([object]$Assets)

    $installerPath = Join-Path $installerDir $Assets.installer.name
    $shaPath = Join-Path $installerDir "SHA256SUMS.txt"
    Invoke-WebRequest -UseBasicParsing -Uri $Assets.installer.browser_download_url -OutFile $installerPath -TimeoutSec 300
    Invoke-WebRequest -UseBasicParsing -Uri $Assets.sha.browser_download_url -OutFile $shaPath -TimeoutSec 60
    Unblock-ReleaseInstaller -InstallerPath $installerPath
    $script:InstallerPath = $installerPath
    Add-Step "download-assets" "pass" $installerPath
    return [ordered]@{ installer = $installerPath; sha = $shaPath }
}

function Unblock-ReleaseInstaller {
    param([string]$InstallerPath)

    try {
        Unblock-File -Path $InstallerPath -ErrorAction Stop
        Add-Step "unblock-installer" "pass" $InstallerPath
    } catch {
        Add-Step "unblock-installer" "skip" $_.Exception.Message
    }
}

function Verify-Checksum {
    param(
        [string]$InstallerPath,
        [string]$ShaPath
    )

    $installerName = Split-Path $InstallerPath -Leaf
    $line = Select-String -Path $ShaPath -Pattern ([regex]::Escape($installerName)) | Select-Object -First 1
    if (-not $line) {
        throw "SHA256SUMS.txt does not mention $installerName"
    }

    $expected = ($line.Line -split "\s+")[0].ToLowerInvariant()
    $actual = (Get-FileHash -Algorithm SHA256 -Path $InstallerPath).Hash.ToLowerInvariant()
    if ($expected -ne $actual) {
        throw "SHA256 mismatch. expected=$expected actual=$actual"
    }

    $script:Report.installer = [ordered]@{
        path = $InstallerPath
        sha256 = $actual
        size_bytes = (Get-Item $InstallerPath).Length
    }
    Add-Step "verify-checksum" "pass" $actual
}

function Verify-Backend {
    $health = Invoke-JsonGet -Uri (Join-BackendUrl "/healthz")
    if ($health.status -ne "ok") {
        throw "Backend /healthz did not return status=ok"
    }
    Add-Step "backend-healthz" "pass" "status=ok"

    $appcast = Invoke-TextGet -Uri (Join-BackendUrl "/api/v1/updates/appcast.xml")
    if (-not $appcast.Contains($script:ReleaseTag)) {
        throw "Appcast does not reference $script:ReleaseTag"
    }
    if (-not $appcast.Contains("OysterRecorder-Setup-v0.12.3.exe")) {
        throw "Appcast does not reference the OysterRecorder installer"
    }
    Add-Step "backend-appcast" "pass" "points to $script:ReleaseTag"
}

function Verify-Signature {
    param([string]$InstallerPath)

    $signature = Get-AuthenticodeSignature -FilePath $InstallerPath
    $signer = "<none>"
    if ($signature.SignerCertificate) {
        $signer = $signature.SignerCertificate.Subject
    }

    Add-Step "authenticode-signature" "pass" "status=$($signature.Status); signer=$signer"
    $script:Report.installer.authenticode_status = $signature.Status.ToString()
    $script:Report.installer.authenticode_signer = $signer

    if ($RequireSignedInstaller -and $signature.Status -ne "Valid") {
        throw "RequireSignedInstaller set but Authenticode status is $($signature.Status)"
    }
}

function Install-Recorder {
    param([string]$InstallerPath)

    if ($SkipInstall) {
        Add-Step "install-recorder" "skip" "SkipInstall set"
        return
    }

    if ($InteractiveInstall) {
        $process = Start-Process -FilePath $InstallerPath -Wait -PassThru
    } else {
        $installLog = Join-Path $OutputDir "OysterRecorder-install.log"
        $args = @(
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/LOG=$installLog"
        )
        $process = Start-Process -FilePath $InstallerPath -ArgumentList $args -Wait -PassThru
    }

    if ($process.ExitCode -ne 0) {
        throw "Installer exited with $($process.ExitCode)"
    }
    $script:RecorderInstalledBySmoke = $true
    Add-Step "install-recorder" "pass" "installed to $script:InstallDir"
}

function Verify-InstalledRecorder {
    if ($SkipInstall) {
        Add-Step "verify-installed-recorder" "skip" "SkipInstall set"
        return
    }

    $exe = Join-Path $script:InstallDir "gamedata-recorder.exe"
    if (-not (Test-Path $exe)) {
        throw "Installed recorder exe missing: $exe"
    }

    $run = Get-ItemProperty `
        -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" `
        -Name "OysterRecorder" `
        -ErrorAction Stop
    if ($run.OysterRecorder -notmatch "gamedata-recorder\.exe" -or $run.OysterRecorder -notmatch "--tray") {
        throw "Unexpected OysterRecorder Run value: $($run.OysterRecorder)"
    }

    Add-Step "verify-installed-recorder" "pass" "exe and autostart registry present"
}

function Launch-Recorder {
    if ($SkipInstall) {
        Add-Step "launch-recorder" "skip" "SkipInstall set"
        return
    }

    $exe = Join-Path $script:InstallDir "gamedata-recorder.exe"
    $env:GAMEDATA_API_URL = $BackendUrl
    $env:GAMEDATA_OUTPUT_DIR = $launchOutputDir
    $env:GAMEDATA_CI_MODE = "1"
    if (-not $env:RUST_LOG) {
        $env:RUST_LOG = "gamedata_recorder=debug,info"
    }
    $env:RUST_BACKTRACE = "1"
    $script:Report.real_session.recorder_env.gamedata_ci_mode = $env:GAMEDATA_CI_MODE
    $script:Report.real_session.recorder_env.gamedata_output_dir = $env:GAMEDATA_OUTPUT_DIR
    $script:Report.real_session.recorder_env.rust_log = $env:RUST_LOG

    $script:RecorderProcess = Start-Process `
        -FilePath $exe `
        -ArgumentList @("--tray") `
        -WorkingDirectory $script:InstallDir `
        -PassThru

    $script:RecorderLaunchedBySmoke = $true
    Start-Sleep -Seconds $LaunchSeconds
    if ($script:RecorderProcess.HasExited) {
        throw "Recorder exited during launch smoke with code $($script:RecorderProcess.ExitCode)"
    }

    Add-Step "launch-recorder" "pass" "pid=$($script:RecorderProcess.Id)"
}

function Run-ManualSessionWindow {
    if ($ManualSessionMinutes -le 0) {
        Add-Step "manual-session-window" "skip" "ManualSessionMinutes is 0"
        return
    }

    Add-Step "manual-session-window" "pass" "Play Minecraft now for $ManualSessionMinutes minute(s); the recorder should stay running"
    $deadline = (Get-Date).AddMinutes($ManualSessionMinutes)
    while ((Get-Date) -lt $deadline) {
        Focus-MinecraftWindow | Out-Null
        Start-Sleep -Seconds 10
        if ($script:RecorderProcess -and $script:RecorderProcess.HasExited) {
            throw "Recorder exited during manual session window with code $($script:RecorderProcess.ExitCode)"
        }
    }
}

function Verify-UploadDelta {
    if (-not $RequireUploadDelta) {
        Add-Step "verify-upload-delta" "skip" "RequireUploadDelta not set"
        return
    }
    if (-not $script:BeforeAdminState -or -not $script:AfterAdminState) {
        throw "RequireUploadDelta needs AdminTokenEnv so before/after admin state can be compared"
    }

    $beforeUploads = Get-CountValue -State $script:BeforeAdminState -Name "uploads"
    $afterUploads = Get-CountValue -State $script:AfterAdminState -Name "uploads"
    $beforeSessions = Get-CountValue -State $script:BeforeAdminState -Name "sessions"
    $afterSessions = Get-CountValue -State $script:AfterAdminState -Name "sessions"

    $script:Report.admin_state.delta = [ordered]@{
        uploads = ($afterUploads - $beforeUploads)
        sessions = ($afterSessions - $beforeSessions)
    }

    if ($afterUploads -le $beforeUploads -and $afterSessions -le $beforeSessions) {
        throw "No backend upload/session counter increased during the real session window"
    }

    Add-Step "verify-upload-delta" "pass" "uploads_delta=$($afterUploads - $beforeUploads); sessions_delta=$($afterSessions - $beforeSessions)"
}

function Stop-Recorder {
    if (-not $script:RecorderLaunchedBySmoke) {
        Add-Step "stop-recorder" "skip" "Recorder not launched by this smoke run"
        return
    }

    if ($script:RecorderProcess -and -not $script:RecorderProcess.HasExited) {
        Stop-Process -Id $script:RecorderProcess.Id -Force -ErrorAction SilentlyContinue
        Add-Step "stop-recorder" "pass" "pid=$($script:RecorderProcess.Id)"
    }
    $exe = Join-Path $script:InstallDir "gamedata-recorder.exe"
    Get-Process -Name "gamedata-recorder" -ErrorAction SilentlyContinue |
        Where-Object {
            try {
                $_.Path -and [string]::Equals($_.Path, $exe, [System.StringComparison]::OrdinalIgnoreCase)
            } catch {
                $false
            }
        } |
        Stop-Process -Force -ErrorAction SilentlyContinue
}

function Uninstall-Recorder {
    if ($SkipInstall) {
        Add-Step "uninstall-recorder" "skip" "SkipInstall set"
        return
    }
    if ($KeepInstalled) {
        Add-Step "uninstall-recorder" "skip" "KeepInstalled set"
        return
    }
    if (-not $script:RecorderInstalledBySmoke) {
        Add-Step "uninstall-recorder" "skip" "Recorder not installed by this smoke run"
        return
    }

    $uninstaller = Join-Path $script:InstallDir "unins000.exe"
    if (Test-Path $uninstaller) {
        $uninstallLog = Join-Path $OutputDir "OysterRecorder-uninstall.log"
        $args = @(
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/LOG=$uninstallLog"
        )
        $process = Start-Process -FilePath $uninstaller -ArgumentList $args -Wait -PassThru
        if ($process.ExitCode -ne 0) {
            throw "Uninstaller exited with $($process.ExitCode)"
        }
    }

    Remove-ItemProperty `
        -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" `
        -Name "OysterRecorder" `
        -ErrorAction SilentlyContinue

    $exe = Join-Path $script:InstallDir "gamedata-recorder.exe"
    if (Test-Path $exe) {
        throw "Recorder exe still exists after uninstall: $exe"
    }

    Add-Step "uninstall-recorder" "pass" "installed exe and autostart removed"
}

try {
    if (-not $script:IsWindowsHost) {
        throw "windows_real_session_smoke.ps1 must run on Windows"
    }

    Assert-NoGuiPreflightConfig
    Assert-StrictRealSessionConfig
    $assets = Resolve-LatestRelease
    $downloaded = Download-ReleaseAssets -Assets $assets
    Verify-Checksum -InstallerPath $downloaded.installer -ShaPath $downloaded.sha
    Verify-Backend
    Verify-Signature -InstallerPath $downloaded.installer

    if ($NoGuiPreflight) {
        Add-Step "no-gui-preflight" "pass" "release, checksum, backend, and signature checks completed without executing installer or recorder"
    } else {
        Stop-StaleRecorderProcesses
        Assert-StrictGameWindowReady
        $script:AdminToken = Get-AdminToken
        $script:BeforeAdminState = Get-AdminStateSnapshot -Label "before"
        $script:Report.admin_state.before = $script:BeforeAdminState
        Start-RealSessionArtifactSnapshot

        Install-Recorder -InstallerPath $downloaded.installer
        Verify-InstalledRecorder
        Enable-StrictRealSessionRecorderConfig
        Launch-Recorder
        Start-MinecraftLaunchCommand
        Wait-ForMinecraftWindow
        Send-RecordingHotkey -Action "start"
        Run-ManualSessionWindow
        Send-RecordingHotkey -Action "stop"
        Verify-StrictRealSessionArtifacts

        $script:AfterAdminState = Get-AdminStateSnapshot -Label "after"
        $script:Report.admin_state.after = $script:AfterAdminState
        Verify-UploadDelta
    }
} catch {
    Add-Step "fatal" "fail" $_.Exception.Message
} finally {
    try {
        Stop-Recorder
    } catch {
        Add-Step "stop-recorder" "fail" $_.Exception.Message
    }

    try {
        Restore-StrictRealSessionRecorderConfig
    } catch {
        Add-Step "restore-recorder-config" "fail" $_.Exception.Message
    }

    try {
        Uninstall-Recorder
    } catch {
        Add-Step "uninstall-recorder" "fail" $_.Exception.Message
    }

    try {
        Collect-Evidence
    } catch {
        Add-Step "collect-evidence" "fail" $_.Exception.Message
    }

    Save-Report
    Write-Host "Report: $reportPath"
    Write-Host "Evidence: $archivePath"
}

if ($script:Failed) {
    exit 1
}
exit 0
