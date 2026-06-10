---
task_id: S55-uninstaller-polish
project: gamedata-pipeline
priority: 2
estimated_minutes: 25
depends_on:
  - S14-windows-inno-installer
modifies:
  - installer/uninstall_cleanup.bat
  - installer/oyster-recorder.iss
  - tests/test_uninstall_cleanup.py
executor: qwen3.6-plus
---

## 目标

让 uninstall 干净清除所有 Oyster data + ask 用户 OAuth token 留不留。

1. 修改 `installer/oyster-recorder.iss` 在 [UninstallRun] 加：
   - `Filename: "{app}\uninstall_cleanup.bat"; Parameters: "/SILENT"; Flags: runhidden`
2. 新 `installer/uninstall_cleanup.bat`：
   - 弹 prompt "Keep your OAuth token and history?" (Yes default)
   - No → delete `%LOCALAPPDATA%\OysterRecorder\` (全部)
   - Yes → keep `~/.oyster/auth.json` + `consent.json`，删 logs/sessions/cache
   - 删 Windows registry auto-start key
   - delete Start Menu shortcut
3. 测试 Python script mock cmd.exe + verify proper cleanup

## 约束

- 不删 user 私人文件（仅 OysterRecorder 目录）
- 默认 keep OAuth (user said "I might come back")
- /SILENT 模式跳过 prompt → 全删
- Idempotent（重复跑不报错）

## 验收

- [ ] uninstall_cleanup.bat 语法 valid (cmd /c 解析)
- [ ] `--silent` 模式无 prompt 全删
- [ ] keep mode 保留 auth.json + consent.json
- [ ] registry key cleanup verified
- [ ] `pytest tests/test_uninstall_cleanup.py -v` 全绿（mock filesystem + registry）
- [ ] shellcheck

## 不要做

- 不删非 Oyster 文件
- 不强制全删
- 不写 system32 操作
- 直接 commit 到 branch `feat/S55-uninstaller-polish`
