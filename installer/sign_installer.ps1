# ============================================================================
# sign_installer.ps1 — Sign OysterRecorder installer with EV certificate
# ============================================================================
#
# Usage (CI):
#   $env:EV_CERT_PFX = "<base64-encoded .pfx>"
#   $env:EV_CERT_PASSWORD = "<pfx password>"
#   .\installer\sign_installer.ps1 -FilePath "installer\output\OysterRecorder-setup-v1.0.0.exe"
#
# Usage (local dev with cert in store):
#   .\installer\sign_installer.ps1 -FilePath "setup.exe" -UseCertStore
#
# This script:
#   1. If EV_CERT_PFX env var is set: decodes it to a temp .pfx and signs
#   2. If -UseCertStore is passed: finds the EV cert in the local cert store
#   3. If neither: exits gracefully with a warning (unsigned build)
#
# Exit codes:
#   0 — success (or gracefully skipped)
#   1 — signing failed
# ============================================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$FilePath,

    [Parameter(Mandatory = $false)]
    [switch]$UseCertStore,

    [Parameter(Mandatory = $false)]
    [string]$TimestampServer = "http://timestamp.digicert.com",

    [Parameter(Mandatory = $false)]
    [ValidateSet("SHA256", "SHA1")]
    [string]$DigestAlgorithm = "SHA256"
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Helper: locate signtool.exe
# ---------------------------------------------------------------------------
function Find-SignTool {
    # 1. Check PATH
    $st = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
    if ($st) { return $st.Source }

    # 2. Windows SDK common locations
    $sdkPaths = @(
        "C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe",
        "C:\Program Files (x86)\Windows Kits\10\bin\10.0.22000.0\x64\signtool.exe",
        "C:\Program Files (x86)\Windows Kits\10\bin\10.0.19041.0\x64\signtool.exe",
        "C:\Program Files (x86)\Windows Kits\10\bin\10.0.18362.0\x64\signtool.exe",
        "C:\Program Files (x86)\Windows Kits\10\bin\x64\signtool.exe",
        "C:\Program Files (x86)\Windows Kits\8.1\bin\x64\signtool.exe"
    )
    foreach ($p in $sdkPaths) {
        if (Test-Path $p) { return $p }
    }

    # 3. Search under Program Files recursively (fallback, slower)
    $searchRoots = @(
        "C:\Program Files (x86)\Windows Kits",
        "C:\Program Files\Windows Kits"
    )
    foreach ($root in $searchRoots) {
        if (Test-Path $root) {
            $found = Get-ChildItem -Path $root -Filter "signtool.exe" -Recurse -ErrorAction SilentlyContinue |
                     Where-Object { $_.FullName -match "x64" } |
                     Select-Object -First 1
            if ($found) { return $found.FullName }
        }
    }

    return $null
}

# ---------------------------------------------------------------------------
# Helper: sign a file with signtool
# ---------------------------------------------------------------------------
function Invoke-SignFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SignToolPath,

        [Parameter(Mandatory = $true)]
        [string]$FileToSign,

        [Parameter(Mandatory = $true)]
        [string]$CertPath,

        [Parameter(Mandatory = $false)]
        [string]$CertPassword = "",

        [Parameter(Mandatory = $false)]
        [string]$TimestampUrl = "http://timestamp.digicert.com",

        [Parameter(Mandatory = $false)]
        [string]$Alg = "SHA256"
    )

    $signtoolArgs = @(
        "sign"
        "/f", $CertPath
        "/fd", $Alg
        "/tr", $TimestampUrl
        "/td", $Alg
    )

    if ($CertPassword) {
        $signtoolArgs += "/p", $CertPassword
    }

    $signtoolArgs += $FileToSign

    Write-Host "Running: $SignToolPath $($signtoolArgs -join ' ')"
    $process = Start-Process -FilePath $SignToolPath `
                             -ArgumentList $signtoolArgs `
                             -NoNewWindow `
                             -Wait `
                             -PassThru `
                             -RedirectStandardOutput "$env:TEMP\signtool_stdout.log" `
                             -RedirectStandardError "$env:TEMP\signtool_stderr.log"

    $stdout = Get-Content "$env:TEMP\signtool_stdout.log" -Raw -ErrorAction SilentlyContinue
    $stderr = Get-Content "$env:TEMP\signtool_stderr.log" -Raw -ErrorAction SilentlyContinue

    if ($stdout) { Write-Host $stdout }
    if ($stderr) { Write-Host $stderr }

    if ($process.ExitCode -ne 0) {
        Write-Error "signtool.exe exited with code $($process.ExitCode)"
        return $false
    }

    Write-Host "Successfully signed: $FileToSign"
    return $true
}

# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

# Validate file exists
if (-not (Test-Path $FilePath)) {
    Write-Error "File not found: $FilePath"
    exit 1
}

$signTool = Find-SignTool
if (-not $signTool) {
    Write-Error "signtool.exe not found. Install Windows SDK or add it to PATH."
    exit 1
}

Write-Host "Found signtool.exe at: $signTool"

# --- Mode 1: EV_CERT_PFX from environment (CI mode) ------------------------
$evCertPfx = $env:EV_CERT_PFX
$evCertPassword = $env:EV_CERT_PASSWORD

if ($evCertPfx -and -not $UseCertStore) {
    Write-Host "Signing mode: EV_CERT_PFX (CI mode)"

    # Decode base64 .pfx to temp file
    $tempPfx = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), "ev_cert_$([System.Guid]::NewGuid()).pfx")
    try {
        $pfxBytes = [System.Convert]::FromBase64String($evCertPfx)
        [System.IO.File]::WriteAllBytes($tempPfx, $pfxBytes)
        Write-Host "Decoded EV certificate to temp file"

        $success = Invoke-SignFile `
            -SignToolPath $signTool `
            -FileToSign $FilePath `
            -CertPath $tempPfx `
            -CertPassword $evCertPassword `
            -TimestampUrl $TimestampServer `
            -Alg $DigestAlgorithm

        if (-not $success) {
            Write-Error "Failed to sign $FilePath"
            exit 1
        }
    }
    finally {
        # Clean up temp .pfx
        if (Test-Path $tempPfx) {
            Remove-Item $tempPfx -Force -ErrorAction SilentlyContinue
            Write-Host "Cleaned up temp certificate file"
        }
    }

    exit 0
}

# --- Mode 2: Use certificate from local store (dev mode) --------------------
if ($UseCertStore) {
    Write-Host "Signing mode: Certificate Store (local dev)"

    # Find code-signing cert with private key
    $cert = Get-ChildItem -Path Cert:\CurrentUser\My, Cert:\LocalMachine\My -ErrorAction SilentlyContinue |
            Where-Object {
                $_.HasPrivateKey -and
                $_.EnhancedKeyUsageList -match "Code Signing"
            } |
            Sort-Object -Property NotAfter -Descending |
            Select-Object -First 1

    if (-not $cert) {
        Write-Error "No code-signing certificate found in certificate store."
        Write-Error "Import your EV cert or use EV_CERT_PFX environment variable."
        exit 1
    }

    Write-Host "Using certificate: $($cert.Subject) (expires: $($cert.NotAfter))"

    $success = Invoke-SignFile `
        -SignToolPath $signTool `
        -FileToSign $FilePath `
        -CertPath $cert.PSPath `
        -TimestampUrl $TimestampServer `
        -Alg $DigestAlgorithm

    if (-not $success) {
        Write-Error "Failed to sign $FilePath"
        exit 1
    }

    exit 0
}

# --- Mode 3: No cert available — graceful skip ------------------------------
Write-Warning "No EV certificate provided (EV_CERT_PFX not set, -UseCertStore not passed)."
Write-Warning "File will remain UNSIGNED: $FilePath"
Write-Warning "To sign, set EV_CERT_PFX (base64 .pfx) and EV_CERT_PASSWORD env vars, or pass -UseCertStore."

exit 0
