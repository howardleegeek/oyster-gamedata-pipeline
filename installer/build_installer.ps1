# ============================================================================
# build_installer.ps1 — CI script to build OysterRecorder Windows installer
# ============================================================================
#
# Usage:
#   .\installer\build_installer.ps1 -Version "1.2.3" [-RecorderExe "path\to\oyster-recorder.exe"] [-OutputDir "path"]
#
# Inputs:
#   -Version       : Semver string (e.g. "1.2.3") — required
#   -RecorderExe   : Path to compiled oyster-recorder.exe — optional, defaults to
#                    vendor/recorder/target/release/oyster-recorder.exe
#   -OutputDir     : Where to place the resulting setup.exe — optional, defaults to
#                    installer/output
#
# Output:
#   OysterRecorder-setup-vX.Y.Z.exe
#
# Requirements:
#   - Inno Setup 6.x installed (ISCC.exe on PATH or at default location)
#   - Works under Wine + Inno Setup on Linux CI
#
# Exit codes:
#   0 — success
#   1 — missing inputs
#   2 — ISCC compilation failed
# ============================================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Version,

    [Parameter(Mandatory = $false)]
    [string]$RecorderExe = "",

    [Parameter(Mandatory = $false)]
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

# --- Resolve paths ----------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Split-Path -Parent $ScriptDir

if ([string]::IsNullOrEmpty($RecorderExe)) {
    $RecorderExe = Join-Path $ProjectRoot "vendor\recorder\target\release\oyster-recorder.exe"
}

if ([string]::IsNullOrEmpty($OutputDir)) {
    $OutputDir = Join-Path $ProjectRoot "installer\output"
}

$ISScript = Join-Path $ScriptDir "oyster-recorder.iss"
$PostInstallBat = Join-Path $ScriptDir "postinstall_register_autostart.bat"

# --- Validate inputs --------------------------------------------------------
if (-not (Test-Path $ISScript)) {
    Write-Error "Inno Setup script not found: $ISScript"
    exit 1
}

if (-not (Test-Path $RecorderExe)) {
    Write-Error "Recorder executable not found: $RecorderExe"
    Write-Host "Hint: Build with 'cargo build --release' in vendor/recorder first."
    exit 1
}

if (-not (Test-Path $PostInstallBat)) {
    Write-Warning "postinstall_register_autostart.bat not found at $PostInstallBat"
}

# --- Locate ISCC.exe --------------------------------------------------------
function Find-ISCC {
    # 1. Check PATH
    $iscc = Get-Command "iscc.exe" -ErrorAction SilentlyContinue
    if ($iscc) { return $iscc.Source }

    # 2. Default Inno Setup 6.x install locations
    $candidates = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Inno Setup 6\ISCC.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }

    # 3. Wine + Inno Setup (Linux CI)
    $wineIscc = Get-Command "wineiscc" -ErrorAction SilentlyContinue
    if ($wineIscc) { return $wineIscc.Source }
    $wineCmd = Get-Command "wine" -ErrorAction SilentlyContinue
    if ($wineCmd) {
        $winePath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
        # Try common Wine prefixes
        $winePrefixes = @(
            "$env:HOME/.wine",
            "/home/ci/.wine",
            "$env:WINEPREFIX"
        )
        foreach ($prefix in $winePrefixes) {
            if ([string]::IsNullOrEmpty($prefix)) { continue }
            $mapped = $winePath -replace "C:", "$prefix/dosdevices/c:"
            # Just try running via wine with default path
            $testCmd = "wine `"$winePath`" /?"
            $result = Invoke-Expression $testCmd 2>$null
            if ($LASTEXITCODE -eq 0) {
                return "wine `"$winePath`""
            }
        }
    }

    return $null
}

$ISCC = Find-ISCC
if (-not $ISCC) {
    Write-Error "ISCC.exe (Inno Setup Compiler) not found."
    Write-Host "Install Inno Setup 6.x: https://jrsoftware.org/isdl.php"
    Write-Host "For Linux CI: wine + Inno Setup 6.x"
    exit 1
}

Write-Host "Using ISCC: $ISCC"

# --- Prepare output directory -----------------------------------------------
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

# --- Copy postinstall batch to source dir so it gets bundled ----------------
$SourceDir = Split-Path -Parent $RecorderExe
$DestBat = Join-Path $SourceDir "postinstall_register_autostart.bat"
if (Test-Path $PostInstallBat) {
    Copy-Item -Path $PostInstallBat -Destination $DestBat -Force
    Write-Host "Copied postinstall batch to $DestBat"
}

# --- Build ISCC command line ------------------------------------------------
$SourceDirArg = $SourceDir -replace "\\", "\\"
$VersionArg = $Version

$isccArgs = @(
    "/DAppVersion=$VersionArg",
    "/DSourceDir=$SourceDirArg",
    "/O$OutputDir",
    "`"$ISScript`""
)

Write-Host "Building installer..."
Write-Host "  Version : $Version"
Write-Host "  Source  : $RecorderExe"
Write-Host "  Output  : $OutputDir"
Write-Host "  Command : $ISCC $($isccArgs -join ' ')"

# --- Run ISCC ---------------------------------------------------------------
$process = Start-Process -FilePath $ISCC -ArgumentList $isccArgs -NoNewWindow -Wait -PassThru

if ($process.ExitCode -ne 0) {
    Write-Error "ISCC compilation failed with exit code $($process.ExitCode)"
    exit 2
}

# --- Verify output ----------------------------------------------------------
$ExpectedExe = Join-Path $OutputDir "OysterRecorder-setup-v$Version.exe"
if (-not (Test-Path $ExpectedExe)) {
    # Try to find any .exe in output dir
    $found = Get-ChildItem -Path $OutputDir -Filter "OysterRecorder-setup-*.exe" -ErrorAction SilentlyContinue
    if ($found) {
        $ExpectedExe = $found.FullName
        Write-Host "Found installer at: $ExpectedExe"
    } else {
        Write-Error "Expected output not found: $ExpectedExe"
        exit 2
    }
}

$sizeMB = [math]::Round((Get-Item $ExpectedExe).Length / 1MB, 2)
Write-Host "Installer built successfully: $ExpectedExe ($sizeMB MB)"

# --- Size check (acceptance criterion: <= 50MB without JRE/MC) --------------
if ($sizeMB -gt 50) {
    Write-Warning "Installer size ($sizeMB MB) exceeds 50 MB threshold."
    Write-Warning "Ensure JRE and Minecraft are NOT being bundled."
}

# --- Cleanup temp batch copy ------------------------------------------------
if (Test-Path $DestBat) {
    Remove-Item $DestBat -Force -ErrorAction SilentlyContinue
}

Write-Host "Done."
exit 0
