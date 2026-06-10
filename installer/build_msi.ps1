# Howard-facing script to build .msi on Windows.
# Usage: .\installer\build_msi.ps1 -Version 0.4.0
param(
  [Parameter(Mandatory=$true)][string]$Version
)
$ErrorActionPreference = "Stop"

# 1. Generate wxs
python bin\build_wxs.py --version $Version `
    --recorder-exe dist\OysterRecorder.exe `
    --mods-dir dist\mods `
    --template installer\oyster-recorder.wxs.template `
    --output installer\oyster-recorder.wxs

# 2. WiX build
wix build installer\oyster-recorder.wxs -out dist\OysterRecorder-$Version.msi
Write-Host "✓ MSI built: dist\OysterRecorder-$Version.msi"
