---
task_id: R05C-consumer-launcher-compiled
project: recorder
priority: 1
estimated_minutes: 60
depends_on: []
modifies:
  - bin/oyster_play.py                          # NEW (compiled to .exe)
  - bin/oyster_launch_mc.py                     # NEW (replaces today's launch_mc_fabric.py)
  - .github/workflows/build-recorder-installer.yml  # NEW CI
executor: glm-aliyun
---

## 目标
单 button consumer launcher: 用户双击 desktop "Oyster Recording" → 启动
recorder + MC + Fabric + auto-arm + 监听 MC 退出 → 自动 finalize.

替换今天临时写的 `launch_mc_fabric.py` + `OysterPlay.py` 双脚本方案.

## 关键 bug 修复 (今天踩过的)
1. **inheritsFrom 链时 mainClass 只从 leaf 取, 不从父级覆盖**
   (今天 fix 之前 launch_mc_fabric.py 错把 vanilla Main 当成 KnotClient)
2. **Fabric arguments.jvm 必须传** (例如 `-DFabricMcEmu=...`)
3. **Desktop shortcut 必须放 `HKCU:Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders\Desktop` 解析的真实路径**
   (OneDrive Desktop redirection 让 `%USERPROFILE%\Desktop` 变成"看不见的桌面")
4. **subprocess 必须 RedirectStandardError to file 然后 surface 到 MessageBox**
   (今天 pythonw 静默吞了 Java stack trace, 排查时间烧光)
5. **检测 KnotClient 必须用 wmic + UTF-8 解码** (`subprocess.check_output` 默认 cp936 在中文 Windows 会乱)

## 数据准确铁律
- mainClass 只从 leaf JSON 取 (test: vanilla 1.21.4 inherits should not produce vanilla main)
- 所有 path 用 `Path.home() + '\Documents'` 等 cross-locale safe 写法
- Desktop 路径走注册表, 不走 USERPROFILE\Desktop

## 验收
- [ ] `bin/oyster_play.py` 1 按钮 entry point
- [ ] 启动时:
  1. 检测 bundle/jre/javaw.exe 存在 → 否则 弹 dialog "安装损坏, 请重装"
  2. 检测 bundle/mc-instance/versions/fabric-loader-... 存在 → 否则同上
  3. 启动 OysterRecorder.exe (如未启动)
  4. Build javaw cmd from leaf Fabric JSON only (mainClass / JVM args)
  5. subprocess.Popen javaw with stderr → 文件 + 实时 tail
  6. 等 30s for `MainClient initialized` log line in MC's `latest.log`
  7. UIA-click recorder 的 ▶开始录制 button (用今天 EnumWindows 找 PID, 不依赖 MainWindowHandle)
  8. 等 javaw 退出 → 自动 disarm recorder
  9. 通知"完成" 或 "失败 + 原因 + 上传 crash log 按钮"
- [ ] 错误时 surface 真实 Java stderr 到 MessageBox (不 silent fail)
- [ ] 编译为 `OysterPlay.exe` via PyInstaller, 单文件
- [ ] 桌面 shortcut 写到注册表解析的 Desktop 路径

## 不要做
- ❌ 不要假设 Mojang Launcher 在
- ❌ 不要假设系统 Python 在 (PyInstaller bundle 自带)
- ❌ 不要写 `pythonw -F script.py` 模式 (今天烧了 90 分钟在 silent stderr)
- ❌ 不要假设 Desktop = USERPROFILE\Desktop
