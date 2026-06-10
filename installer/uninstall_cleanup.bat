@rem uninstall_cleanup.bat
@rem ========================================================================
@rem OysterRecorder — Uninstall Cleanup Script
@rem ========================================================================
@rem Called by Inno Setup [UninstallRun] during uninstall.
@rem
@rem Behavior:
@rem   /SILENT or /VERYSILENT  → delete everything (no prompt)
@rem   Normal uninstall        → prompt user: keep OAuth token & history?
@rem                              Yes (default) → keep auth.json + consent.json,
@rem                                              delete logs/sessions/cache
@rem                              No              → delete entire OysterRecorder dir
@rem
@rem Always:
@rem   - Delete HKCU Run registry auto-start key
@rem   - Delete Start Menu shortcut
@rem
@rem Idempotent: safe to run multiple times (no errors on missing paths).
@rem ========================================================================

@echo off
setlocal enabledelayedexpansion

set "APP_NAME=OysterRecorder"
set "DATA_DIR=%LOCALAPPDATA%\%APP_NAME%"
set "AUTH_DIR=%USERPROFILE%\.oyster"
set "REG_KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
set "REG_VALUE=%APP_NAME%"

rem --- Detect silent mode ---------------------------------------------------
set "SILENT_MODE=0"
for %%a in (%*) do (
    if /i "%%a"=="/SILENT" set "SILENT_MODE=1"
    if /i "%%a"=="/VERYSILENT" set "SILENT_MODE=1"
)

rem --- Prompt user (skip in silent mode) ------------------------------------
if %SILENT_MODE% equ 1 (
    goto FULL_DELETE
)

rem Default: keep OAuth token and history (Yes)
echo.
echo OysterRecorder is being uninstalled.
echo.
choice /C YN /N /D Y /T 10 /M "Keep your OAuth token and history? (Y/N, default Y in 10s): "
if errorlevel 2 (
    rem User chose No → full delete
    goto FULL_DELETE
)

rem --- Keep mode: preserve auth.json + consent.json, delete the rest --------
:KEEP_MODE
echo [CLEANUP] Keeping OAuth token and history...

rem Delete logs directory if it exists
if exist "%DATA_DIR%\logs" (
    rmdir /s /q "%DATA_DIR%\logs" 2>nul
    echo [CLEANUP] Deleted logs directory.
)

rem Delete sessions directory if it exists
if exist "%DATA_DIR%\sessions" (
    rmdir /s /q "%DATA_DIR%\sessions" 2>nul
    echo [CLEANUP] Deleted sessions directory.
)

rem Delete cache directory if it exists
if exist "%DATA_DIR%\cache" (
    rmdir /s /q "%DATA_DIR%\cache" 2>nul
    echo [CLEANUP] Deleted cache directory.
)

rem Delete config directory if it exists
if exist "%DATA_DIR%\config" (
    rmdir /s /q "%DATA_DIR%\config" 2>nul
    echo [CLEANUP] Deleted config directory.
)

rem Delete any remaining files in DATA_DIR except preserving .oyster reference
rem (The main app dir will be cleaned by Inno Setup's [UninstallDelete])
goto REGISTRY_CLEANUP

rem --- Full delete mode: remove everything ----------------------------------
:FULL_DELETE
echo [CLEANUP] Removing all OysterRecorder data...

rem Delete the entire LOCALAPPDATA\OysterRecorder directory
if exist "%DATA_DIR%" (
    rmdir /s /q "%DATA_DIR%" 2>nul
    echo [CLEANUP] Deleted %DATA_DIR%.
)

rem Delete the .oyster directory (contains auth.json, consent.json)
if exist "%AUTH_DIR%" (
    rmdir /s /q "%AUTH_DIR%" 2>nul
    echo [CLEANUP] Deleted %AUTH_DIR%.
)

goto REGISTRY_CLEANUP

rem --- Registry cleanup: remove auto-start key ------------------------------
:REGISTRY_CLEANUP
echo [CLEANUP] Removing auto-start registry key...

rem Delete the HKCU Run value for OysterRecorder (idempotent)
reg delete "%REG_KEY%" /v "%REG_VALUE%" /f >nul 2>&1
if %errorlevel% equ 0 (
    echo [CLEANUP] Removed registry auto-start key.
) else (
    echo [CLEANUP] Registry key was already removed or did not exist.
)

rem --- Start Menu shortcut cleanup ------------------------------------------
echo [CLEANUP] Removing Start Menu shortcut...

rem Standard per-user Start Menu Programs folder
set "START_MENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\%APP_NAME%"

if exist "%START_MENU%" (
    rmdir /s /q "%START_MENU%" 2>nul
    echo [CLEANUP] Removed Start Menu shortcut folder.
) else (
    echo [CLEANUP] Start Menu shortcut was already removed or did not exist.
)

rem Also check the common (all-users) Start Menu just in case
set "START_MENU_COMMON=%ProgramData%\Microsoft\Windows\Start Menu\Programs\%APP_NAME%"
if exist "%START_MENU_COMMON%" (
    rmdir /s /q "%START_MENU_COMMON%" 2>nul
    echo [CLEANUP] Removed common Start Menu shortcut folder.
)

echo [CLEANUP] Uninstall cleanup complete.
endlocal
exit /b 0
