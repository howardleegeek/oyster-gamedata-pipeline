@echo off
REM ============================================================
REM  Oyster Recorder — Defender 预防脚本（v0.12.3+）
REM
REM  作用: 在安装 .exe 之前先告诉 Windows Defender 信任我们的
REM        安装目录，避免 javaw.exe 和 Minecraft .jar 被误删。
REM  使用: 右键此文件 → "以管理员身份运行"
REM ============================================================
title Oyster Recorder - Pre-install Defender Exclusion
color 0B
echo.
echo ============================================================
echo   Oyster Recorder - Defender Exclusion Setup
echo ============================================================
echo.
echo   This script adds the OysterRecorder install folder to
echo   Windows Defender exclusions BEFORE you run the installer.
echo.
echo   This prevents Defender from quarantining the bundled
echo   Java runtime (javaw.exe) and Minecraft .jar files.
echo.
echo   Path to be excluded:
echo     %%LOCALAPPDATA%%\OysterRecorder
echo.
echo ============================================================
echo.

REM Check for admin rights (Defender exclusion requires admin)
net session >/dev/null 2>&1
if %errorlevel% neq 0 (
    echo [!] ERROR: This script must be run as Administrator.
    echo.
    echo     Right-click this file and select "Run as administrator".
    echo.
    pause
    exit /b 1
)

echo [1/3] Adding Defender path exclusion...
powershell -NoProfile -Command "Add-MpPreference -ExclusionPath '%LOCALAPPDATA%\OysterRecorder' -ErrorAction SilentlyContinue; if ($?) { Write-Host '      OK: %LOCALAPPDATA%\OysterRecorder excluded' -ForegroundColor Green } else { Write-Host '      WARN: Could not add exclusion (Defender may be disabled or managed by policy)' -ForegroundColor Yellow }"

echo.
echo [2/3] Adding Defender process exclusions (javaw.exe + OysterPlay.exe)...
powershell -NoProfile -Command "Add-MpPreference -ExclusionProcess 'javaw.exe' -ErrorAction SilentlyContinue; Add-MpPreference -ExclusionProcess 'OysterPlay.exe' -ErrorAction SilentlyContinue; Add-MpPreference -ExclusionProcess 'OysterRecorder.exe' -ErrorAction SilentlyContinue; Write-Host '      OK: javaw.exe + OysterPlay.exe + OysterRecorder.exe excluded' -ForegroundColor Green"

echo.
echo [3/3] Verifying exclusions...
powershell -NoProfile -Command "$ex = Get-MpPreference; $paths = $ex.ExclusionPath; $procs = $ex.ExclusionProcess; if ($paths -contains '%LOCALAPPDATA%\OysterRecorder') { Write-Host '      OK: path exclusion confirmed' -ForegroundColor Green } else { Write-Host '      WARN: path exclusion may not be active' -ForegroundColor Yellow }; Write-Host ('      processes excluded: ' + ($procs -join ', '))"

echo.
echo ============================================================
echo   DONE. Now you can run the OysterRecorder-Setup-*.exe
echo   installer without Defender interfering.
echo ============================================================
echo.
pause
