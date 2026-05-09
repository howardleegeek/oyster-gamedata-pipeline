<#
.SYNOPSIS
    OysterRecorder bundled-installer build orchestrator.

.DESCRIPTION
    Spec: specs/R05D_inno_setup_installer.md (depends_on R05A, R05B, R05C)

    Runs every dependency-fetcher and compiler in the right order, stages
    the inputs Inno Setup needs under bundle/, then invokes ISCC.exe to
    produce OysterRecorder-Setup-vX.Y.Z.exe.

    The 8 stages reflect the exact ordering the spec spells out:

        1.  fetch_jre.py           -> bundle/jre/                 (R05A)
        2.  fetch_minecraft.py     -> bundle/mc-instance/         (R05B)
        3.  fetch_fabric.py        -> bundle/mc-instance/         (R05B)
        4.  build_oysterplay_exe   -> bundle/OysterPlay.exe       (R05C)
        5.  PyInstaller --onedir   -> bundle/OysterRecorder-onedir/
        6.  copy 9 mod jars        -> bundle/mc-instance/mods/
        7.  ISCC.exe installer.iss -> dist/installer/...exe
        8.  size verification (400-500 MB sanity gate)

    Each stage hard-fails on first non-zero exit. The orchestrator is
    idempotent: rerunning it skips fetch steps whose outputs already
    exist + verify, but always re-runs ISCC to refresh the installer.

.PARAMETER AppVersion
    Semver string baked into the installer + output filename
    (OysterRecorder-Setup-v$AppVersion.exe). Defaults to the value in
    pyproject.toml when not specified.

.PARAMETER InnoSetupPath
    Optional override for the Inno Setup compiler. Defaults to the
    canonical Chocolatey install path used by build-recorder-installer.yml.

.PARAMETER SkipDeps
    Skip stages 1-6 (fetch + compile dependencies). Useful when you've
    already produced bundle/ and only want to re-run ISCC after editing
    installer.iss.

.PARAMETER Clean
    Wipe bundle/ + dist/installer/ before starting. Slow but guarantees
    no stale artifact from a previous build leaks into the new .exe.

.EXAMPLE
    pwsh ./bin/build_bundled_installer/build_all.ps1 -AppVersion 0.27.0
    # Full build, current pyproject.toml version override

.EXAMPLE
    pwsh ./bin/build_bundled_installer/build_all.ps1 -SkipDeps
    # Iterate on installer.iss without re-fetching the JRE every time

.NOTES
    Iron-law constraints from R05D this script enforces:
      * Every fetch step calls a Python module that does its own SHA-256
        verification — we never bypass that with -SkipVerify flags.
      * The 400-500 MB size gate at the end catches "we forgot to bundle
        the JRE" failures the spec explicitly calls out.
      * No silent /S flags ever passed to ISCC. The installer must show
        progress for the ~460 MB copy.
#>

[CmdletBinding()]
param(
    [string]$AppVersion = "",
    [string]$InnoSetupPath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    [switch]$SkipDeps,
    [switch]$Clean
)

# -------------------------------------------------------------------------
# Strict mode + helpers
# -------------------------------------------------------------------------
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Repo layout (same convention as fetch_jre.py)
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot    = (Resolve-Path (Join-Path $ScriptDir "../..")).Path
$BundleDir   = Join-Path $RepoRoot "bundle"
$DistDir     = Join-Path $RepoRoot "dist"
$InstallerOut= Join-Path $DistDir "installer"
$IssFile     = Join-Path $ScriptDir "installer.iss"

# Iron-law: supported MC versions that build the mod matrix. Kept in sync
# with bin/recorder_consumer_lite.py:SUPPORTED_MC_VERSIONS and
# .github/workflows/build-mc-mod.yml.
$SupportedMcVersions = @(
    "1.20.1", "1.20.2", "1.20.4", "1.20.6",
    "1.21.1", "1.21.2", "1.21.3", "1.21.4", "1.21.5"
)

# Size gate (bytes). Iron-law: < 500 MB per spec, > 400 MB sanity gate so
# we catch "we forgot to bundle the JRE" failures (unbundled .exe is ~5 MB).
$ExpectedMinBytes = 400 * 1024 * 1024   # 400 MiB
$ExpectedMaxBytes = 500 * 1024 * 1024   # 500 MiB

function Write-Stage {
    param([int]$Num, [string]$Title)
    Write-Host ""
    Write-Host "=========================================================" -ForegroundColor Cyan
    Write-Host (" Stage {0}: {1}" -f $Num, $Title) -ForegroundColor Cyan
    Write-Host "=========================================================" -ForegroundColor Cyan
}

function Invoke-Step {
    <#
    .SYNOPSIS Run an external command, hard-fail on non-zero exit.
    #>
    param(
        [Parameter(Mandatory)] [string]$Description,
        [Parameter(Mandatory)] [string]$Cmd,
        [string[]]$Arguments = @(),
        [string]$Cwd = $RepoRoot
    )
    Write-Host ("--> {0}" -f $Description) -ForegroundColor Yellow
    Write-Host ("    cmd: {0} {1}" -f $Cmd, ($Arguments -join " ")) -ForegroundColor DarkGray
    Push-Location $Cwd
    try {
        & $Cmd @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw ("Step '{0}' failed: exit code {1}" -f $Description, $LASTEXITCODE)
        }
    }
    finally {
        Pop-Location
    }
}

function Resolve-AppVersion {
    if ($AppVersion -ne "") { return $AppVersion }

    # Read pyproject.toml without external deps. We just grep for the
    # version line in [project].
    $pyproj = Join-Path $RepoRoot "pyproject.toml"
    if (-not (Test-Path $pyproj)) {
        throw "Cannot resolve -AppVersion: pyproject.toml not found at $pyproj"
    }
    $line = Select-String -Path $pyproj -Pattern '^\s*version\s*=\s*"([^"]+)"' -List
    if (-not $line) {
        throw "Cannot resolve -AppVersion: no version line found in pyproject.toml"
    }
    $resolved = $line.Matches[0].Groups[1].Value
    Write-Host ("    resolved version from pyproject.toml: {0}" -f $resolved) -ForegroundColor DarkGray
    return $resolved
}

function Test-RequiredPath {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [string]$Reason
    )
    if (-not (Test-Path $Path)) {
        throw ("Preflight failed: required path not found: {0} ({1})" -f $Path, $Reason)
    }
}

# -------------------------------------------------------------------------
# Banner + setup
# -------------------------------------------------------------------------
$ResolvedVersion = Resolve-AppVersion

Write-Host ""
Write-Host "OysterRecorder bundled-installer builder" -ForegroundColor Green
Write-Host ("  Repo:    {0}" -f $RepoRoot)
Write-Host ("  Version: {0}" -f $ResolvedVersion)
Write-Host ("  Bundle:  {0}" -f $BundleDir)
Write-Host ("  Output:  {0}" -f $InstallerOut)

if ($Clean) {
    Write-Host ""
    Write-Host "-Clean: wiping bundle/ + dist/installer/" -ForegroundColor Yellow
    if (Test-Path $BundleDir)    { Remove-Item -Recurse -Force $BundleDir }
    if (Test-Path $InstallerOut) { Remove-Item -Recurse -Force $InstallerOut }
}

# Ensure bundle/ exists so downstream steps don't have to
New-Item -ItemType Directory -Force -Path $BundleDir | Out-Null

# Pick the right Python — prefer .venv, fall back to PATH.
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}
Write-Host ("  Python:  {0}" -f $Python) -ForegroundColor DarkGray

# -------------------------------------------------------------------------
# Stage 1: JRE fetch (R05A)
# -------------------------------------------------------------------------
if (-not $SkipDeps) {
    Write-Stage 1 "Fetch portable JRE (Eclipse Temurin 21 LTS)"
    Invoke-Step "fetch_jre.py" $Python @(
        (Join-Path $ScriptDir "fetch_jre.py")
    )
}
Test-RequiredPath (Join-Path $BundleDir "jre\bin\javaw.exe") "JRE fetch must succeed before installer compile"

# -------------------------------------------------------------------------
# Stage 2: Minecraft vanilla fetch (R05B)
# -------------------------------------------------------------------------
$FetchMc = Join-Path $ScriptDir "fetch_minecraft.py"
if (-not $SkipDeps) {
    Write-Stage 2 "Fetch vanilla Minecraft 1.21.4 client + libraries"
    if (-not (Test-Path $FetchMc)) {
        # R05B has not landed yet — warn but allow build to continue if
        # bundle/mc-instance was prepared by a separate run.
        Write-Host "WARNING: fetch_minecraft.py not present (R05B). Skipping." -ForegroundColor Yellow
    }
    else {
        Invoke-Step "fetch_minecraft.py" $Python @($FetchMc)
    }
}

# -------------------------------------------------------------------------
# Stage 3: Fabric loader fetch (R05B)
# -------------------------------------------------------------------------
$FetchFabric = Join-Path $ScriptDir "fetch_fabric.py"
if (-not $SkipDeps) {
    Write-Stage 3 "Fetch Fabric loader 0.16.10 + libraries"
    if (-not (Test-Path $FetchFabric)) {
        Write-Host "WARNING: fetch_fabric.py not present (R05B). Skipping." -ForegroundColor Yellow
    }
    else {
        Invoke-Step "fetch_fabric.py" $Python @($FetchFabric)
    }
}

# -------------------------------------------------------------------------
# Stage 4: Compile OysterPlay.exe (R05C)
# -------------------------------------------------------------------------
if (-not $SkipDeps) {
    Write-Stage 4 "Compile OysterPlay.exe (PyInstaller --onefile)"
    Invoke-Step "build_oysterplay_exe.py" $Python @(
        (Join-Path $ScriptDir "build_oysterplay_exe.py"),
        "--onefile",
        "--clean"
    )

    # build_oysterplay_exe.py drops dist/OysterPlay.exe — relocate to bundle/
    $built = Join-Path $RepoRoot "dist\OysterPlay.exe"
    $dest  = Join-Path $BundleDir "OysterPlay.exe"
    if (-not (Test-Path $built)) {
        throw ("OysterPlay.exe was not produced at {0}" -f $built)
    }
    Copy-Item -Force $built $dest
    Write-Host ("    staged {0} -> {1}" -f $built, $dest) -ForegroundColor DarkGray
}
Test-RequiredPath (Join-Path $BundleDir "OysterPlay.exe") "OysterPlay.exe must exist before installer compile"

# -------------------------------------------------------------------------
# Stage 5: PyInstaller --onedir for OysterRecorder
# -------------------------------------------------------------------------
if (-not $SkipDeps) {
    Write-Stage 5 "Compile OysterRecorder --onedir (PyInstaller)"

    # Mirror the invocation in .github/workflows/build-recorder-exe.yml.
    # We use --distpath bundle/OysterRecorder-onedir directly so the
    # output lands where installer.iss expects it.
    $RecorderEntry = Join-Path $RepoRoot "bin\recorder_consumer_lite.py"
    Test-RequiredPath $RecorderEntry "OysterRecorder entry script (R05 v0.27.0)"

    Invoke-Step "pyinstaller --onedir" $Python @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name", "OysterRecorder-onedir",
        "--distpath", $BundleDir,
        "--workpath", (Join-Path $RepoRoot "build\OysterRecorder-onedir"),
        "--specpath", (Join-Path $RepoRoot "build\OysterRecorder-onedir"),
        "--paths", (Join-Path $RepoRoot "bin"),
        $RecorderEntry
    )
}
Test-RequiredPath (Join-Path $BundleDir "OysterRecorder-onedir") "OysterRecorder onedir must exist"

# -------------------------------------------------------------------------
# Stage 6: Copy mod jars (one per supported MC version)
# -------------------------------------------------------------------------
if (-not $SkipDeps) {
    Write-Stage 6 ("Stage {0} mod jars (one per supported MC version)" -f $SupportedMcVersions.Count)

    $ModsDir = Join-Path $BundleDir "mc-instance\mods"
    New-Item -ItemType Directory -Force -Path $ModsDir | Out-Null

    # Discover artifacts under mc-mod/build/libs/ — convention from build-mc-mod.yml.
    $ModBuildLibs = Join-Path $RepoRoot "mc-mod\build\libs"
    if (-not (Test-Path $ModBuildLibs)) {
        throw ("Mod artifacts not found at {0} — run mc-mod gradlew build first." -f $ModBuildLibs)
    }

    $copied = 0
    foreach ($mcv in $SupportedMcVersions) {
        # Match build-mc-mod.yml convention: oyster-recorder-mod-<modVer>+mc<MCV>.jar
        $pattern = "oyster-recorder-mod-*+mc${mcv}.jar"
        $found = Get-ChildItem -Path $ModBuildLibs -Filter $pattern -ErrorAction SilentlyContinue |
                 Where-Object { $_.Name -notmatch "-(sources|javadoc)\.jar$" } |
                 Select-Object -First 1
        if (-not $found) {
            throw ("Missing mod jar for MC {0} (looked for {1} in {2})" -f $mcv, $pattern, $ModBuildLibs)
        }
        Copy-Item -Force $found.FullName $ModsDir
        Write-Host ("    {0,-7} -> {1}" -f $mcv, $found.Name) -ForegroundColor DarkGray
        $copied++
    }
    Write-Host ("    staged {0} mod jars" -f $copied) -ForegroundColor DarkGray
}

# -------------------------------------------------------------------------
# Stage 6b: Stage combined manifest.json
# -------------------------------------------------------------------------
$BuildManifest = Join-Path $ScriptDir "manifest.json"
$BundleManifest = Join-Path $BundleDir "manifest.json"
Copy-Item -Force $BuildManifest $BundleManifest
Write-Host ("    manifest -> {0}" -f $BundleManifest) -ForegroundColor DarkGray

# -------------------------------------------------------------------------
# Preflight: confirm everything installer.iss declares as Source: exists.
# Mirrors the [Files] section of installer.iss — keep these in sync.
# -------------------------------------------------------------------------
Write-Stage 7 "Preflight + ISCC.exe (Inno Setup compile)"

Test-RequiredPath (Join-Path $BundleDir "jre\bin\javaw.exe")             "[Files] (1) bundled JRE"
Test-RequiredPath (Join-Path $BundleDir "mc-instance")                   "[Files] (2) MC instance"
Test-RequiredPath (Join-Path $BundleDir "OysterRecorder-onedir")         "[Files] (3) recorder onedir"
Test-RequiredPath (Join-Path $BundleDir "OysterPlay.exe")                "[Files] (4) launcher"
Test-RequiredPath (Join-Path $BundleDir "manifest.json")                 "[Files] (5) manifest"
Test-RequiredPath $IssFile                                               "installer.iss"

if (-not (Test-Path $InnoSetupPath)) {
    # Fallback: try $env:ProgramFiles location (Inno 6 64-bit installer)
    $alt = "C:\Program Files\Inno Setup 6\ISCC.exe"
    if (Test-Path $alt) {
        $InnoSetupPath = $alt
    }
    else {
        throw ("ISCC.exe not found at {0} or {1}. Install Inno Setup 6 (e.g. ``choco install innosetup``)." -f $InnoSetupPath, $alt)
    }
}

New-Item -ItemType Directory -Force -Path $InstallerOut | Out-Null

# -------------------------------------------------------------------------
# Stage 7: ISCC.exe installer.iss /DAppVersion=...
# -------------------------------------------------------------------------
# /DAppVersion=     -- baked into installer + output filename
# /Qp                -- quiet + progress (no /SILENT, per spec)
# /O                 -- override OutputDir from .iss (insurance)
# /F                 -- override OutputBaseFilename
$IsccArgs = @(
    "/Qp",
    ("/DAppVersion={0}" -f $ResolvedVersion),
    ("/O{0}" -f $InstallerOut),
    ("/FOysterRecorder-Setup-v{0}" -f $ResolvedVersion),
    $IssFile
)
Invoke-Step "ISCC.exe" $InnoSetupPath $IsccArgs

# -------------------------------------------------------------------------
# Stage 8: Verify output size (400-500 MB sanity gate)
# -------------------------------------------------------------------------
Write-Stage 8 "Verify output size"

$OutExe = Join-Path $InstallerOut ("OysterRecorder-Setup-v{0}.exe" -f $ResolvedVersion)
Test-RequiredPath $OutExe "ISCC must produce the .exe installer"

$bytes = (Get-Item $OutExe).Length
$mib   = [math]::Round($bytes / 1MB, 2)

Write-Host ("    {0} = {1:N0} bytes ({2} MiB)" -f $OutExe, $bytes, $mib)

if ($bytes -lt $ExpectedMinBytes) {
    throw ("Installer is too small ({0} MiB < {1} MiB minimum). Did the JRE / MC bundle land?" `
        -f $mib, ($ExpectedMinBytes / 1MB))
}
if ($bytes -gt $ExpectedMaxBytes) {
    throw ("Installer is too large ({0} MiB > {1} MiB maximum). Trim assets or check Solid compression." `
        -f $mib, ($ExpectedMaxBytes / 1MB))
}

Write-Host ""
Write-Host "BUILD SUCCEEDED" -ForegroundColor Green
Write-Host ("  -> {0} ({1} MiB)" -f $OutExe, $mib)
Write-Host ""
