@rem postinstall_register_autostart.bat
@rem ========================================================================
@rem Fallback autostart registration for OysterRecorder tray daemon.
@rem Called by the Inno Setup [Code] CurStepChanged hook after install.
@rem
@rem This is a belt-and-suspenders measure: the primary autostart mechanism
@rem is the [Registry] section in oyster-recorder.iss. This batch file
@rem exists so that if the registry write fails for any reason, the user
@rem can still manually run this script to register autostart.
@rem
@rem Usage (manual):
@rem   "%LOCALAPPDATA%\OysterRecorder\postinstall_register_autostart.bat"
@rem ========================================================================

@echo off
setlocal

set "APP_NAME=OysterRecorder"
set "APP_EXE=oyster-recorder.exe"
set "REG_KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
set "REG_VALUE=%APP_NAME%"

rem Resolve install directory (parent of this script)
set "INSTALL_DIR=%~dp0"
rem Remove trailing backslash if present
if "%INSTALL_DIR:~-1%"=="\" set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"

set "EXE_PATH=%INSTALL_DIR%\%APP_EXE%"

if not exist "%EXE_PATH%" (
    echo [ERROR] %APP_EXE% not found at %EXE_PATH%
    exit /b 1
)

rem Register autostart (overwrite existing value)
reg add "%REG_KEY%" /v "%REG_VALUE%" /t REG_SZ /d "\"%EXE_PATH%\" --tray" /f >nul 2>&1

if %errorlevel% equ 0 (
    echo [OK] Autostart registered: "%EXE_PATH%" --tray
) else (
    echo [ERROR] Failed to write registry key %REG_KEY%\%REG_VALUE%
    exit /b 1
)

endlocal
