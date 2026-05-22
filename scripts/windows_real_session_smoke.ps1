param(
    [string]$Repo = "howardleegeek/oyster-gamedata-pipeline",
    [string]$BackendUrl = "http://136.109.41.170:8081",
    [string]$OutputDir = "",
    [int]$LaunchSeconds = 20,
    [int]$ManualSessionMinutes = 0,
    [string]$AdminTokenEnv = "",
    [switch]$RequireUploadDelta,
    [switch]$InteractiveInstall,
    [switch]$RequireSignedInstaller,
    [switch]$SkipInstall,
    [switch]$KeepInstalled
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $OutputDir) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputDir = Join-Path $env:TEMP "OysterRecorder-real-session-smoke-$stamp"
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
$script:InstallDir = Join-Path $env:LOCALAPPDATA "OysterRecorder"
$script:BeforeAdminState = $null
$script:AfterAdminState = $null
$script:AdminToken = ""

$script:Report = [ordered]@{
    started_at = $script:StartedAt
    finished_at = $null
    host = [ordered]@{
        computer_name = $env:COMPUTERNAME
        user = $env:USERNAME
        os = (Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty Caption)
        powershell = $PSVersionTable.PSVersion.ToString()
    }
    repo = $Repo
    backend_url = $BackendUrl
    release = $null
    installer = $null
    admin_state = [ordered]@{
        enabled = $false
        require_upload_delta = [bool]$RequireUploadDelta
        before = $null
        after = $null
        delta = $null
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
        return ""
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
    Copy-EvidenceFile -Path (Join-Path $env:APPDATA "GameData Recorder")
    Copy-EvidenceFile -Path (Join-Path $env:LOCALAPPDATA "GameData Recorder")
    Copy-EvidenceFile -Path $script:InstallDir

    if (Test-Path $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }
    if (Test-Path $evidenceDir) {
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
    $script:InstallerPath = $installerPath
    Add-Step "download-assets" "pass" $installerPath
    return [ordered]@{ installer = $installerPath; sha = $shaPath }
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
    if (-not $appcast.Contains("OysterRecorder-setup-v2.6.0.exe")) {
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
    $env:RUST_BACKTRACE = "1"

    $script:RecorderProcess = Start-Process `
        -FilePath $exe `
        -ArgumentList @("--tray") `
        -WorkingDirectory $script:InstallDir `
        -PassThru

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
    if ($script:RecorderProcess -and -not $script:RecorderProcess.HasExited) {
        Stop-Process -Id $script:RecorderProcess.Id -Force -ErrorAction SilentlyContinue
        Add-Step "stop-recorder" "pass" "pid=$($script:RecorderProcess.Id)"
    }
    Get-Process -Name "gamedata-recorder" -ErrorAction SilentlyContinue |
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
    $isWindowsHost = ($env:OS -eq "Windows_NT") -or ($PSVersionTable.PSEdition -eq "Desktop")
    if (-not $isWindowsHost) {
        throw "windows_real_session_smoke.ps1 must run on Windows"
    }

    $script:AdminToken = Get-AdminToken
    $assets = Resolve-LatestRelease
    $downloaded = Download-ReleaseAssets -Assets $assets
    Verify-Checksum -InstallerPath $downloaded.installer -ShaPath $downloaded.sha
    Verify-Backend
    Verify-Signature -InstallerPath $downloaded.installer
    $script:BeforeAdminState = Get-AdminStateSnapshot -Label "before"
    $script:Report.admin_state.before = $script:BeforeAdminState

    Install-Recorder -InstallerPath $downloaded.installer
    Verify-InstalledRecorder
    Launch-Recorder
    Run-ManualSessionWindow

    $script:AfterAdminState = Get-AdminStateSnapshot -Label "after"
    $script:Report.admin_state.after = $script:AfterAdminState
    Verify-UploadDelta
} catch {
    Add-Step "fatal" "fail" $_.Exception.Message
} finally {
    try {
        Stop-Recorder
    } catch {
        Add-Step "stop-recorder" "fail" $_.Exception.Message
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
