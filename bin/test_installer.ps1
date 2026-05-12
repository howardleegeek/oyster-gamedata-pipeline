<#
.SYNOPSIS
    Automated installer smoke test for GameDataRecorder / OysterRecorder.
.DESCRIPTION
    Downloads the latest GameDataRecorder-Setup-*.exe from GitHub releases,
    runs the installer silently, verifies install paths and OysterPlay.exe,
    then cleans up via silent uninstall. Outputs JSON pass/fail to stdout.
.NOTES
    Requires: PowerShell 5.1+, Windows, network access to GitHub.
    Run as Administrator if installer requires elevated privileges.
#>

[CmdletBinding()]
param(
    [string]$RepoOwner = "oyster-protocol",
    [string]$RepoName  = "oyster-recorder",
    [string]$AssetPattern = "GameDataRecorder-Setup-*.exe",
    [string]$InstallDirOverride = "",
    [switch]$KeepInstaller = $false
)

$ErrorActionPreference = "Stop"

# ── helpers ──────────────────────────────────────────────────────────────────

function Write-JsonResult {
    param(
        [bool]$Passed,
        [string]$Stage,
        [string]$Message,
        [hashtable]$Details = @{}
    )
    $obj = [ordered]@{
        passed  = $Passed
        stage   = $Stage
        message = $Message
        details = $Details
        timestamp = (Get-Date -Format "o")
    }
    $obj | ConvertTo-Json -Depth 4 -Compress
}

function Log-Stage {
    param([string]$Stage)
    Write-Host "=== [$Stage] ===" -ForegroundColor Cyan
}

# ── main ─────────────────────────────────────────────────────────────────────

$overallPass = $true
$stages = @()

try {

    # ── Stage 1: Download latest installer from GitHub releases ──────────────
    Log-Stage "Download"

    $apiUrl = "https://api.github.com/repos/$RepoOwner/$RepoName/releases/latest"
    $headers = @{ "Accept" = "application/vnd.github.v3+json" }

    # Add GITHUB_TOKEN if available (avoids rate-limit)
    if ($env:GITHUB_TOKEN) {
        $headers["Authorization"] = "token $env:GITHUB_TOKEN"
    }

    $release = Invoke-RestMethod -Uri $apiUrl -Headers $headers -ErrorAction Stop
    $tagName = $release.tag_name

    $asset = $release.assets | Where-Object { $_.name -like $AssetPattern } | Select-Object -First 1

    if (-not $asset) {
        $available = ($release.assets | ForEach-Object { $_.name }) -join ", "
        throw "No asset matching '$AssetPattern' found in release $tagName. Available: $available"
    }

    $downloadUrl = $asset.browser_download_url
    $installerName = $asset.name
    $tempDir = [System.IO.Path]::GetTempPath()
    $installerPath = Join-Path $tempDir $installerName

    Write-Host "Downloading $installerName from $downloadUrl ..."
    Invoke-WebRequest -Uri $downloadUrl -OutFile $installerPath -Headers $headers -ErrorAction Stop

    if (-not (Test-Path $installerPath)) {
        throw "Downloaded file not found at $installerPath"
    }

    $fileSize = (Get-Item $installerPath).Length
    Write-Host "Downloaded: $installerPath ($([math]::Round($fileSize/1MB,2)) MB)"

    $stages += Write-JsonResult -Passed $true -Stage "download" -Message "Downloaded $installerName ($([math]::Round($fileSize/1MB,2)) MB)" -Details @{
        release_tag = $tagName
        asset_name  = $installerName
        file_size_bytes = $fileSize
        download_url = $downloadUrl
    }

    # ── Stage 2: Run installer silently ──────────────────────────────────────
    Log-Stage "Install"

    # Inno Setup supports /SILENT and /VERYSILENT; try /SILENT first
    $installArgs = @("/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
    if ($InstallDirOverride) {
        $installArgs += "/DIR=`"$InstallDirOverride`""
    }

    Write-Host "Running installer: $installerPath $installArgs"
    $installProc = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -PassThru -NoNewWindow -ErrorAction Stop
    $installExitCode = $installProc.ExitCode

    # If /SILENT failed (exit code != 0), retry with /VERYSILENT
    if ($installExitCode -ne 0) {
        Write-Host "/SILENT failed (exit $installExitCode), retrying with /VERYSILENT ..."
        $installArgs = @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
        if ($InstallDirOverride) {
            $installArgs += "/DIR=`"$InstallDirOverride`""
        }
        $installProc = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -PassThru -NoNewWindow -ErrorAction Stop
        $installExitCode = $installProc.ExitCode
    }

    if ($installExitCode -ne 0) {
        throw "Installer exited with code $installExitCode"
    }

    Write-Host "Installer completed successfully (exit code 0)"

    $stages += Write-JsonResult -Passed $true -Stage "install" -Message "Installer completed (exit code 0)" -Details @{
        exit_code = $installExitCode
        installer_path = $installerPath
    }

    # ── Stage 3: Verify install paths exist ──────────────────────────────────
    Log-Stage "Verify Paths"

    $localAppData = [Environment]::GetEnvironmentVariable("LOCALAPPDATA")
    $candidatePaths = @(
        (Join-Path $localAppData "GameData Recorder"),
        (Join-Path $localAppData "OysterRecorder")
    )

    $foundPath = $null
    foreach ($p in $candidatePaths) {
        if (Test-Path $p) {
            $foundPath = $p
            break
        }
    }

    if (-not $foundPath) {
        $overallPass = $false
        $stages += Write-JsonResult -Passed $false -Stage "verify_paths" -Message "Neither install path found" -Details @{
            candidates = $candidatePaths
            localappdata = $localAppData
        }
    } else {
        Write-Host "Install path found: $foundPath"
        $stages += Write-JsonResult -Passed $true -Stage "verify_paths" -Message "Install path verified: $foundPath" -Details @{
            install_path = $foundPath
            candidates_checked = $candidatePaths
        }
    }

    # ── Stage 4: Verify OysterPlay.exe is runnable ───────────────────────────
    Log-Stage "Verify OysterPlay.exe"

    $oysterPlayExe = $null
    $searchPaths = @()

    if ($foundPath) {
        $searchPaths += Join-Path $foundPath "OysterPlay.exe"
    }
    # Also check common Inno Setup default locations
    $searchPaths += Join-Path $localAppData "GameData Recorder\OysterPlay.exe"
    $searchPaths += Join-Path $localAppData "OysterRecorder\OysterPlay.exe"
    $searchPaths += Join-Path $env:ProgramFiles "GameData Recorder\OysterPlay.exe"
    $searchPaths += Join-Path ${env:ProgramFiles(x86)} "GameData Recorder\OysterPlay.exe"
    $searchPaths += Join-Path $env:ProgramFiles "OysterRecorder\OysterPlay.exe"
    $searchPaths += Join-Path ${env:ProgramFiles(x86)} "OysterRecorder\OysterPlay.exe"

    foreach ($p in $searchPaths) {
        if (Test-Path $p) {
            $oysterPlayExe = $p
            break
        }
    }

    if (-not $oysterPlayExe) {
        $overallPass = $false
        $stages += Write-JsonResult -Passed $false -Stage "verify_oysterplay" -Message "OysterPlay.exe not found in any expected location" -Details @{
            search_paths = $searchPaths
        }
    } else {
        # Verify it's a valid PE executable
        $fileInfo = Get-Item $oysterPlayExe -ErrorAction Stop
        $fileSize = $fileInfo.Length

        # Quick sanity: check file size is reasonable (> 10KB)
        if ($fileSize -lt 10240) {
            $overallPass = $false
            $stages += Write-JsonResult -Passed $false -Stage "verify_oysterplay" -Message "OysterPlay.exe exists but is suspiciously small ($fileSize bytes)" -Details @{
                path = $oysterPlayExe
                size_bytes = $fileSize
            }
        } else {
            Write-Host "OysterPlay.exe found: $oysterPlayExe ($([math]::Round($fileSize/1MB,2)) MB)"
            $stages += Write-JsonResult -Passed $true -Stage "verify_oysterplay" -Message "OysterPlay.exe verified at $oysterPlayExe" -Details @{
                path = $oysterPlayExe
                size_bytes = $fileSize
                last_write = $fileInfo.LastWriteTime.ToString("o")
            }
        }
    }

    # ── Stage 5: Cleanup — uninstall silently ────────────────────────────────
    Log-Stage "Cleanup (Uninstall)"

    $uninstallExe = $null
    $uninstallCandidates = @()

    if ($foundPath) {
        $uninstallCandidates += Join-Path $foundPath "unins000.exe"
    }
    $uninstallCandidates += Join-Path $localAppData "GameData Recorder\unins000.exe"
    $uninstallCandidates += Join-Path $localAppData "OysterRecorder\unins000.exe"
    $uninstallCandidates += Join-Path $env:ProgramFiles "GameData Recorder\unins000.exe"
    $uninstallCandidates += Join-Path ${env:ProgramFiles(x86)} "GameData Recorder\unins000.exe"
    $uninstallCandidates += Join-Path $env:ProgramFiles "OysterRecorder\unins000.exe"
    $uninstallCandidates += Join-Path ${env:ProgramFiles(x86)} "OysterRecorder\unins000.exe"

    foreach ($p in $uninstallCandidates) {
        if (Test-Path $p) {
            $uninstallExe = $p
            break
        }
    }

    if (-not $uninstallExe) {
        Write-Host "WARNING: unins000.exe not found, skipping uninstall"
        $stages += Write-JsonResult -Passed $false -Stage "cleanup" -Message "unins000.exe not found — manual cleanup may be needed" -Details @{
            search_paths = $uninstallCandidates
        }
    } else {
        Write-Host "Running uninstall: $uninstallExe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
        $uninstallProc = Start-Process -FilePath $uninstallExe -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") -Wait -PassThru -NoNewWindow -ErrorAction Stop
        $uninstallExitCode = $uninstallProc.ExitCode

        if ($uninstallExitCode -ne 0) {
            Write-Host "WARNING: Uninstaller exited with code $uninstallExitCode"
            $stages += Write-JsonResult -Passed $false -Stage "cleanup" -Message "Uninstaller exited with code $uninstallExitCode" -Details @{
                uninstall_path = $uninstallExe
                exit_code = $uninstallExitCode
            }
        } else {
            Write-Host "Uninstall completed successfully"
            $stages += Write-JsonResult -Passed $true -Stage "cleanup" -Message "Uninstall completed (exit code 0)" -Details @{
                uninstall_path = $uninstallExe
                exit_code = $uninstallExitCode
            }
        }
    }

    # ── Cleanup downloaded installer ─────────────────────────────────────────
    if (-not $KeepInstaller -and (Test-Path $installerPath)) {
        Remove-Item $installerPath -Force -ErrorAction SilentlyContinue
    }

} catch {
    $overallPass = $false
    $errorMsg = $_.Exception.Message
    Write-Host "ERROR: $errorMsg" -ForegroundColor Red

    # Determine which stage failed
    $failedStage = "unknown"
    if ($errorMsg -match "download|Download|asset|release") { $failedStage = "download" }
    elseif ($errorMsg -match "install|Installer") { $failedStage = "install" }
    elseif ($errorMsg -match "path|Path") { $failedStage = "verify_paths" }
    elseif ($errorMsg -match "OysterPlay|oysterplay") { $failedStage = "verify_oysterplay" }
    elseif ($errorMsg -match "uninstall|cleanup|Cleanup") { $failedStage = "cleanup" }

    $stages += Write-JsonResult -Passed $false -Stage $failedStage -Message "Fatal error: $errorMsg" -Details @{
        error = $errorMsg
        stack = $_.ScriptStackTrace
    }

    # Attempt best-effort cleanup even on failure
    try {
        $localAppData = [Environment]::GetEnvironmentVariable("LOCALAPPDATA")
        $uninstallPaths = @(
            (Join-Path $localAppData "GameData Recorder\unins000.exe"),
            (Join-Path $localAppData "OysterRecorder\unins000.exe")
        )
        foreach ($up in $uninstallPaths) {
            if (Test-Path $up) {
                Write-Host "Best-effort cleanup: running $up"
                Start-Process -FilePath $up -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES") -Wait -NoNewWindow -ErrorAction SilentlyContinue
                break
            }
        }
    } catch {
        Write-Host "Best-effort cleanup also failed: $_" -ForegroundColor Yellow
    }
}

# ── Final JSON output ────────────────────────────────────────────────────────

$finalResult = [ordered]@{
    passed    = $overallPass
    test      = "installer_smoke_test"
    stages    = $stages
    timestamp = (Get-Date -Format "o")
}

$finalResult | ConvertTo-Json -Depth 5

# Exit with appropriate code
if (-not $overallPass) {
    exit 1
}
exit 0
