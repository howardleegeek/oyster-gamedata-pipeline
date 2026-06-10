@echo off
REM ============================================================================
REM check_runtime.bat — Verify VC++ 2015-2022 x64 runtime is installed
REM ============================================================================
REM Exit codes:
REM   0 — VC++ runtime found, installation may proceed
REM   1 — VC++ runtime missing, user chose not to download
REM
REM Registry key checked:
REM   HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64
REM
REM If the key is missing, the user is prompted to download the runtime.
REM ============================================================================

set "REG_KEY=HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
set "DOWNLOAD_URL=https://aka.ms/vs/17/release/vc_redist.x64.exe"

REM --- Check registry key ---
reg query "%REG_KEY%" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] VC++ 2015-2022 Redistributable (x64) is installed.
    exit /b 0
)

REM --- Runtime not found ---
echo.
echo [ERROR] VC++ 2015-2022 Redistributable (x64) is NOT installed.
echo         OysterRecorder requires this runtime to function.
echo.

REM --- In silent/verysilent mode, just fail without prompting ---
for %%a in (%*) do (
    if /i "%%a"=="/SILENT" (
        echo [SILENT] Cannot prompt user in silent mode. Exiting with error.
        exit /b 1
    )
    if /i "%%a"=="/VERYSILENT" (
        echo [VERYSILENT] Cannot prompt user in verysilent mode. Exiting with error.
        exit /b 1
    )
)

REM --- Interactive prompt ---
set /p "CHOICE=VC++ runtime required. Download? (Y/n): "

REM Default to Yes if user just presses Enter
if "%CHOICE%"=="" set "CHOICE=Y"

if /i "%CHOICE%"=="Y" (
    echo Opening download page...
    start "" "%DOWNLOAD_URL%"
    echo.
    echo Please install the VC++ Redistributable, then re-run the installer.
    exit /b 1
)

if /i "%CHOICE%"=="N" (
    echo Installation cancelled. Please install the VC++ Redistributable manually.
    exit /b 1
)

REM Any other input — treat as decline
echo Installation cancelled. Please install the VC++ Redistributable manually.
exit /b 1
