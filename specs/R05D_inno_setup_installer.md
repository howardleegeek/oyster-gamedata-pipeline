---
task_id: R05D-inno-setup-installer
project: recorder
priority: 1
estimated_minutes: 60
depends_on: [R05A, R05B, R05C]
modifies:
  - bin/build_bundled_installer/installer.iss   # NEW (Inno Setup script)
  - bin/build_bundled_installer/build_all.ps1   # NEW (orchestrator)
executor: glm-aliyun
---

## 目标
将所有 bundle 内容 (JRE + MC + Fabric + recorder + launcher) 打包成单个
`OysterRecorder-Setup-vX.Y.Z.exe`. 用户双击装好.

## 数据准确铁律
- Installer 必须 sign code (有证书时) 减少 SmartScreen 警告
- 装好后所有文件必须 SHA verify (manifest 内嵌 + post-install check)
- 必须 per-user install (`HKCU` 不要 `HKLM`, 不需要 admin)

## 验收
- [ ] Inno Setup script 编译产生 ~460MB `.exe` installer
- [ ] 安装目录: `%LOCALAPPDATA%\OysterRecorder\` (per-user)
- [ ] 装完产生:
  - `%LOCALAPPDATA%\OysterRecorder\jre\bin\javaw.exe`
  - `%LOCALAPPDATA%\OysterRecorder\mc-instance\versions\1.21.4\1.21.4.jar`
  - `%LOCALAPPDATA%\OysterRecorder\mc-instance\versions\fabric-loader-...\`
  - `%LOCALAPPDATA%\OysterRecorder\mc-instance\mods\oyster-recorder-mod-...mc1.21.4.jar`
  - `%LOCALAPPDATA%\OysterRecorder\OysterRecorder-onedir.exe`
  - `%LOCALAPPDATA%\OysterRecorder\OysterPlay.exe`
- [ ] 桌面 shortcut "Oyster Recording" 指向 `OysterPlay.exe`, 用注册表 Desktop 路径
- [ ] 开始菜单 entry "Oyster Recording"
- [ ] 卸载: 走标准 Add/Remove Programs, 删除 `%LOCALAPPDATA%\OysterRecorder\`
- [ ] 不污染 PATH / JAVA_HOME / 其他系统配置

## 不要做
- ❌ 不要装到 `%PROGRAMFILES%` (需要 admin)
- ❌ 不要修改用户的 `%APPDATA%\.minecraft\` (那是 Mojang 私产)
- ❌ 不要 install 时下任何东西 (全部已 bundled)
- ❌ 不要 silent install 默认 (用户需要看到 progress)
