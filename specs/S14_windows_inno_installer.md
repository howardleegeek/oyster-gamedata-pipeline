---
task_id: S14-windows-inno-installer
project: gamedata-pipeline
priority: 2
estimated_minutes: 45
depends_on:
  - S12-recorder-egui-to-tray
modifies:
  - installer/oyster-recorder.iss
  - installer/build_installer.ps1
  - installer/postinstall_register_autostart.bat
executor: qwen3.6-plus
---

## 目标

Inno Setup 安装器把 oyster-recorder.exe + 资源打成 `OysterRecorder-setup-vX.Y.Z.exe`。安装后：
- 写 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\OysterRecorder` 注册表项（开机自启 tray daemon）
- 创建 Start Menu 快捷方式
- 写 uninstall 入口

`build_installer.ps1`：CI 跑的脚本，input `recorder.exe` + version 字符串 → 输出 setup.exe。

## 约束

- Inno Setup 6.x 语法
- 不需要 admin 权限（用户级安装）
- 安装目录: `%LOCALAPPDATA%\OysterRecorder\`
- 静默安装支持: `/SILENT /VERYSILENT` 标志（消费者批量分发用）

## 验收标准

- [ ] `iscc installer/oyster-recorder.iss` 在 Wine + Inno Setup 也能跑（CI 友好）
- [ ] 生成的 setup.exe 大小 ≤ 50MB（不含 jre/MC）
- [ ] 安装后 `reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run\OysterRecorder` 返回路径
- [ ] /SILENT 模式下不弹任何窗口
- [ ] tests/test_installer_script.py 验证脚本语法

## 不要做

- 不打包 JRE（recorder 不需要，MC 安装时已带）
- 不写 code signing 步骤（C5 单独 spec）
- 不动 recorder 源码
- 直接 commit 到 branch `feat/S14-windows-installer`
