@echo off
start "" "%LOCALAPPDATA%\OysterRecorder\gamedata-recorder.exe"
timeout /t 2 /nobreak >nul
call "%LOCALAPPDATA%\OysterRecorder\launch_mc.bat"
