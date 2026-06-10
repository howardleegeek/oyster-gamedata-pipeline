@echo off
setlocal EnableExtensions

set "ROOT=%LOCALAPPDATA%\OysterRecorder"
set "ROOTFWD=%LOCALAPPDATA:\=/%/OysterRecorder"
set "INSTANCE=%ROOT%\mc-instance"
set "TEMPLATE=%ROOT%\mc_args_template.txt"
set "ARGFILE=%ROOT%\mc_args.txt"
set "LOGDIR=%ROOT%\logs"
set "STDERR=%LOGDIR%\mc_stderr.log"
set "JAVA=%ROOT%\jre\bin\javaw.exe"

if not exist "%JAVA%" exit /b 2
if not exist "%TEMPLATE%" exit /b 3
if not exist "%INSTANCE%" exit /b 4
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>nul

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$p=Join-Path $env:ROOT 'mc-instance\options.txt'; [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($p)) | Out-Null; $lines=@(); if([IO.File]::Exists($p)){ $lines=[IO.File]::ReadAllLines($p) }; $seen=$false; $out=New-Object System.Collections.Generic.List[string]; foreach($line in $lines){ if($line -like 'pauseOnLostFocus:*'){ $seen=$true; $out.Add('pauseOnLostFocus:false') } else { $out.Add($line) } }; if(-not $seen){ $out.Add('pauseOnLostFocus:false') }; [IO.File]::WriteAllLines($p, [string[]]$out, (New-Object System.Text.UTF8Encoding $false))"
if errorlevel 1 exit /b 5

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$text=[IO.File]::ReadAllText($env:TEMPLATE).Replace('{ROOT}', $env:ROOTFWD); [IO.File]::WriteAllText($env:ARGFILE, $text, (New-Object System.Text.UTF8Encoding $false))"
if errorlevel 1 exit /b 6

start "" /D "%INSTANCE%" "%JAVA%" "@%ARGFILE%" 2>>"%STDERR%"
exit /b %ERRORLEVEL%
