@echo off
REM Install Oyster Capture Daemon as Windows Scheduled Task
REM Run as Administrator

setlocal enabledelayedexpansion

echo Installing Oyster Continuous Capture Daemon...
echo.

REM Check if running as administrator
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Please run this script as Administrator!
    pause
    exit /b 1
)

REM Get script directory
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
set "PYTHON_EXE=python.exe"

REM Check if Python is available
where %PYTHON_EXE% >nul 2>&1
if %errorLevel% neq 0 (
    echo Python not found in PATH. Please install Python 3.8+.
    pause
    exit /b 1
)

REM Create the task
echo Creating scheduled task...
schtasks /create /tn "OysterCaptureDaemon" ^
    /tr "\"%PYTHON_EXE%\" \"%PROJECT_ROOT%\bin\continuous_capture_daemon.py\" run" ^
    /sc onlogon ^
    /ru SYSTEM ^
    /rl highest ^
    /it ^
    /f

if %errorLevel% neq 0 (
    echo Failed to create scheduled task!
    pause
    exit /b 1
)

echo.
echo Task created successfully!
echo.
echo Task details:
schtasks /query /tn "OysterCaptureDaemon" /fo list

echo.
echo To start the task immediately:
echo schtasks /run /tn "OysterCaptureDaemon"
echo.
echo To stop the task:
echo schtasks /end /tn "OysterCaptureDaemon"
echo.
echo To delete the task:
echo schtasks /delete /tn "OysterCaptureDaemon" /f
echo.
pause