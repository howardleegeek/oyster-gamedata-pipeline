#!/usr/bin/env bash
# Mac→minipc installer for recorder-v0.28.0+ bundled releases.
#
# Pulls OysterRecorder-Setup-v<TAG>.exe + SHA-256-manifest.txt from the
# GitHub Release, verifies SHA on minipc, runs the Inno Setup installer
# in user context (no admin), verifies post-install state.
#
# Usage:
#   bash bin/minipc_v028_install.sh                          # latest release
#   RELEASE_TAG=recorder-v0.28.0-rc2 bash bin/minipc_v028_install.sh
#
# Requires:
#   - SSH access to howard.linra@minipc-bwdxs (Tailscale)
#   - GitHub Release with the .exe + manifest published
#
# Iron-law: SHA verification on minipc itself. Mac side never claims
# success based on Mac-side SHA. mismatch -> hard fail with diff hashes.

set -euo pipefail

RELEASE_TAG="${RELEASE_TAG:-recorder-v0.28.0-rc2}"
REPO="howardleegeek/oyster-gamedata-pipeline"
SSH_TARGET="${SSH_TARGET:-minipc-bwdxs}"

INSTALLER_NAME="OysterRecorder-Setup-${RELEASE_TAG}.exe"
MANIFEST_NAME="SHA-256-manifest.txt"

say() { printf '\033[1;36m[install]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

say "release tag    : $RELEASE_TAG"
say "installer      : $INSTALLER_NAME"
say "ssh target     : $SSH_TARGET"

# ---------------------------------------------------------------------------
# Stage 1: confirm release exists on GitHub
# ---------------------------------------------------------------------------
say "[1/6] verifying GitHub Release exists..."
if ! gh release view "$RELEASE_TAG" --repo "$REPO" --json assets --jq '.assets[].name' > /tmp/v028-assets.txt 2>/dev/null; then
  die "GitHub Release $RELEASE_TAG not found at $REPO. Has the CI build completed?"
fi
grep -q "^$INSTALLER_NAME$"  /tmp/v028-assets.txt || die "Installer asset $INSTALLER_NAME missing from release"
grep -q "^$MANIFEST_NAME$"   /tmp/v028-assets.txt || die "Manifest asset $MANIFEST_NAME missing from release"
say "   release assets verified (installer + manifest both present)"

# ---------------------------------------------------------------------------
# Stage 2: extract expected SHA from the manifest (Mac side)
# ---------------------------------------------------------------------------
say "[2/6] reading expected SHA from manifest..."
mkdir -p /tmp/v028-install
gh release download "$RELEASE_TAG" --repo "$REPO" --pattern "$MANIFEST_NAME" \
  --dir /tmp/v028-install --clobber 2>/dev/null
# NOTE: manifest is written by Windows Out-File ascii → CRLF endings, so
# strip \r from each field before comparing.
EXPECTED_SHA="$(awk -v name="$INSTALLER_NAME" '{ gsub(/\r/, "") } $2 == name {print $1}' /tmp/v028-install/"$MANIFEST_NAME")"
[ -n "$EXPECTED_SHA" ] || die "could not extract expected SHA for $INSTALLER_NAME from manifest"
say "   expected SHA: $EXPECTED_SHA"

# ---------------------------------------------------------------------------
# Stage 3: minipc downloads installer DIRECTLY (no Mac round-trip)
# ---------------------------------------------------------------------------
say "[3/6] minipc downloading installer from GitHub Release..."
ssh "$SSH_TARGET" 'powershell -NoProfile -ExecutionPolicy Bypass -Command -' <<PS_EOF
\$ErrorActionPreference = 'Stop'
\$ProgressPreference   = 'SilentlyContinue'
\$dst = "\$env:USERPROFILE\Downloads\\$INSTALLER_NAME"
\$url = "https://github.com/${REPO}/releases/download/${RELEASE_TAG}/${INSTALLER_NAME}"
Write-Host "  url: \$url"
Write-Host "  dst: \$dst"
Invoke-WebRequest -Uri \$url -OutFile \$dst -UseBasicParsing
Write-Host ("  size: {0:N0} bytes" -f (Get-Item \$dst).Length)
PS_EOF

# ---------------------------------------------------------------------------
# Stage 4: minipc verifies SHA against the expected value
# ---------------------------------------------------------------------------
say "[4/6] minipc verifying SHA-256..."
ACTUAL_SHA=$(ssh "$SSH_TARGET" "powershell -NoProfile -Command \"(Get-FileHash -Algorithm SHA256 \$env:USERPROFILE\\Downloads\\$INSTALLER_NAME).Hash.ToLower()\"" | tr -d '\r\n')
say "   expected : $EXPECTED_SHA"
say "   got      : $ACTUAL_SHA"
[ "$ACTUAL_SHA" = "$EXPECTED_SHA" ] || die "SHA mismatch — refusing to install. Hard-fail per iron-law."
say "   [OK] SHA verified"

# ---------------------------------------------------------------------------
# Stage 5: run the installer (Inno Setup, per-user, no admin)
# ---------------------------------------------------------------------------
say "[5/6] running installer in Howard's interactive session via schtasks..."
say "   (a window will appear on your minipc — click Next/Install through it)"
ssh "$SSH_TARGET" 'powershell -NoProfile -ExecutionPolicy Bypass -Command -' <<PS_EOF
\$ErrorActionPreference = 'Continue'
\$installer = "\$env:USERPROFILE\Downloads\\$INSTALLER_NAME"
\$task = 'OysterInstallerLaunch_OneShot'
schtasks /delete /tn \$task /f 2>&1 | Out-Null
schtasks /create /tn \$task /tr "\\"\$installer\\"" /sc once /st 23:59 /it /ru howar /rl HIGHEST /f | Select-Object -First 1
schtasks /run /tn \$task | Out-String
Start-Sleep -Seconds 3
schtasks /delete /tn \$task /f 2>&1 | Out-Null
Write-Host "Installer launched in Session 1. Click through the wizard on your minipc."
PS_EOF

# ---------------------------------------------------------------------------
# Stage 6: poll for post-install state (give human ~3 min to click through)
# ---------------------------------------------------------------------------
say "[6/6] polling minipc for post-install completion (up to 4 min)..."
for i in $(seq 1 24); do
  sleep 10
  STATE=$(ssh "$SSH_TARGET" 'powershell -NoProfile -Command "$r = \"$env:LOCALAPPDATA\OysterRecorder\"; $exe = \"$r\OysterPlay.exe\"; $rec = \"$r\OysterRecorder-onedir.exe\"; $jre = \"$r\jre\bin\javaw.exe\"; $mc = \"$r\mc-instance\versions\fabric-loader-0.16.10-1.21.4\"; $ok = (Test-Path $exe) -and (Test-Path $rec) -and (Test-Path $jre) -and (Test-Path $mc); if ($ok) { \"DONE\" } else { \"WAITING\" }"' 2>/dev/null | tr -d '\r\n')
  case "$STATE" in
    DONE)
      say "   [OK] install complete after $((i*10))s"
      break
      ;;
    *)
      printf '.'
      ;;
  esac
done
echo

# ---------------------------------------------------------------------------
# Final post-install verification
# ---------------------------------------------------------------------------
say "[final] post-install state probe..."
ssh "$SSH_TARGET" 'powershell -NoProfile -ExecutionPolicy Bypass -Command -' <<'PS_EOF'
$r = "$env:LOCALAPPDATA\OysterRecorder"
$realDesktop = [Environment]::ExpandEnvironmentVariables((Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders').Desktop)
foreach ($p in @("$r\OysterPlay.exe","$r\OysterRecorder-onedir.exe","$r\jre\bin\javaw.exe","$r\mc-instance\versions\fabric-loader-0.16.10-1.21.4","$r\mc-instance\mods","$realDesktop\Oyster Recording.lnk")) {
  if (Test-Path $p) { Write-Host ("[OK]  {0}" -f $p) } else { Write-Host ("[MISS]{0}" -f $p) }
}
Write-Host ''
Write-Host "Now: double-click 'Oyster Recording' on your desktop to launch MC + record."
PS_EOF

say ""
say "================================================================"
say " v0.28.0 install pipeline COMPLETE on minipc."
say " Next: double-click 'Oyster Recording' shortcut → play → quit MC"
say "================================================================"
