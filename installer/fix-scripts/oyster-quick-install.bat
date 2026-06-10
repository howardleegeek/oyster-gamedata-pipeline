@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================
REM  Oyster Recorder 一键安装 (v0.18.0)
REM
REM  作用: bingd 双击此文件即可:
REM        1. 关闭老版本运行的进程
REM        2. 彻底删除老安装目录 (lite 残留)
REM        3. 自动下载真正 v0.18.0 (738 MB bundled)
REM        4. 静默安装
REM        5. 启动 OysterPlay
REM
REM  使用: 双击此 .bat (不需要管理员)
REM ============================================================

title Oyster Recorder 一键安装 v0.18.0
color 0B
echo.
echo ============================================================
echo   Oyster Recorder 一键安装 v0.18.0 (738 MB bundled 版)
echo ============================================================
echo.

set "INSTALL_DIR=%LOCALAPPDATA%\OysterRecorder"
set "DOWNLOAD_URL=https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/v0.18.0/OysterRecorder-Setup-v0.18.0.exe"
set "INSTALLER_PATH=%TEMP%\OysterRecorder-Setup-v0.18.0.exe"

REM ---------- Step 1: kill running processes ----------
echo [1/5] 关闭旧版本运行的进程 (如果有)...
taskkill /F /IM OysterRecorder.exe /T  2>nul
taskkill /F /IM OysterRecorder-onedir.exe /T  2>nul
taskkill /F /IM OysterPlay.exe /T  2>nul
taskkill /F /IM javaw.exe /T  2>nul
taskkill /F /IM obs-ffmpeg-mux.exe /T  2>nul
taskkill /F /IM ffmpeg.exe /T  2>nul
timeout /t 2 /nobreak >nul
echo       OK
echo.

REM ---------- Step 2: nuke old install ----------
echo [2/5] 清理旧 lite 安装 (%INSTALL_DIR%)...
if exist "%INSTALL_DIR%" (
    rmdir /S /Q "%INSTALL_DIR%"
    if exist "%INSTALL_DIR%" (
        echo       警告: 部分文件无法删除 (可能在用中), 继续安装会覆盖
    ) else (
        echo       OK 已删除
    )
) else (
    echo       OK (没有旧安装)
)
echo.

REM ---------- Step 3: download v0.18.0 ----------
echo [3/5] 下载 v0.18.0 (738 MB, 取决于网速 ~2-10 分钟)...
echo       从: %DOWNLOAD_URL%
echo       到: %INSTALLER_PATH%
echo.
if exist "%INSTALLER_PATH%" del /F /Q "%INSTALLER_PATH%"
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%DOWNLOAD_URL%' -OutFile '%INSTALLER_PATH%' -UseBasicParsing"
if not exist "%INSTALLER_PATH%" (
    echo.
    echo [错误] 下载失败. 检查网络后重试.
    echo.
    pause
    exit /b 1
)

for %%I in ("%INSTALLER_PATH%") do set "SIZE_MB=%%~zI"
set /a SIZE_MB_INT=%SIZE_MB:~0,-6%
echo       OK 下载完成 (大约 %SIZE_MB_INT% MB)
echo.

REM ---------- Step 4: silent install ----------
echo [4/5] 静默安装 v0.18.0 (可能需要 1-3 分钟解压 800MB)...
"%INSTALLER_PATH%" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
if errorlevel 1 (
    echo.
    echo [错误] 安装失败, 错误码 %errorlevel%.
    echo        请双击 %INSTALLER_PATH% 手动安装.
    echo.
    pause
    exit /b 1
)
echo       OK 安装完成
echo.

REM ---------- Step 5: launch OysterPlay ----------
echo [5/5] 启动 OysterPlay...
set "OYSTER_PLAY=%INSTALL_DIR%\OysterPlay.exe"
if exist "%OYSTER_PLAY%" (
    start "" "%OYSTER_PLAY%"
    echo       OK 已启动. 等几秒会自动开 Minecraft + 录制.
) else (
    echo       [警告] %OYSTER_PLAY% 不存在, 安装可能没完整.
    echo                到桌面找 "Oyster Recording" 快捷方式.
)
echo.

echo ============================================================
echo   完成! 现在:
echo     1. Minecraft 会自动启动
echo     2. 录制会自动开始
echo     3. 玩 ^>=10 分钟 (WASD + E + F5)
echo     4. 退出 MC -^> session 自动保存到:
echo        %USERPROFILE%\Documents\OysterClips\session_xxx\
echo     5. 把 session 文件夹打包发回
echo ============================================================
echo.
pause
