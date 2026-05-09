---
task_id: R05-zero-knowledge-consumer-launcher
project: recorder
priority: 1
estimated_minutes: 90
depends_on: []
modifies:
  - bin/recorder_consumer_lite.py              # add launch-MC-with-fabric path
  - bin/recorder_mc_launcher.py                # NEW — owns the entire MC launch chain
  - bin/recorder_jre_bootstrap.py              # NEW — bundles + verifies portable JRE
  - bin/recorder_fabric_bootstrap.py           # NEW — installs Fabric loader silently
  - bin/recorder_consumer_installer.iss        # NEW — Inno Setup installer that bundles everything
  - .github/workflows/build-recorder-installer.yml  # NEW — CI builds .exe installer
executor: glm-aliyun
---

## 目标 (Howard 2026-05-08, 用户铁律)

**"用户什么都不懂"** —— B 类用户 (玩家) zero-knowledge 体验：

```
1. 浏览器打开 oyster.so/recorder/download
2. 点 "Download for Windows" → 拿到 OysterRecorder-Setup.exe (~250MB)
3. 双击 .exe → 点 Next → 装完
4. 桌面出现 "Oyster Recorder" 图标
5. 双击图标 → MC 自动启动 (Fabric 已配好) → 进世界 → 玩
6. 退出 MC → recorder 自动打包 + 上传 → 弹通知"今天录了 23 分钟，预计 $0.45"
7. 钱自动到账
```

**用户从来不需要知道**: profile / Fabric loader / mod jar / mods folder / classpath / Java version / launcher_profiles.json / SHA / 日志路径

## 上下文

当前 v0.27.0-rc1 失败模式 (Howard 2026-05-08 实测):
- 用户装好 MC 1.21.4 和 mod jar (`.minecraft\mods\`)
- 用户装好 Fabric loader 0.16.10
- 用户开 Mojang Launcher → 点 Play
- **结果**: 启动 vanilla 1.21.4 (Mojang Launcher 默认 profile 是 vanilla, Fabric profile 没自动注册到 launcher_profiles.json)
- Recorder 录了 video → 打包时 iron-law 拒绝 (no game-state JSONL)
- **用户看到 "packaging failed" — 不知道是因为 Fabric profile 没选**

修补 launcher_profiles.json 的方案太脆弱:
- Launcher 在运行中改 JSON 不生效 (cache)
- 用户不知道有 "profile" 概念
- 需要"杀 launcher → 重开 → 选 Fabric profile"三步

唯一 robust 方案: **recorder 完全 own MC 启动链, 不碰 Mojang Launcher**.

## 数据准确铁律

- 绝不假设 Mojang Launcher 存在 / 装了 / 配对了 (用户 PC 可能新装、可能用 MS Store、可能 launcher 损坏)
- 绝不假设系统有 Java (新装 PC 没有)
- 绝不假设系统有 .minecraft folder (首次玩 MC 的用户没有)
- 绝不假设 Fabric loader 装了 (用户从来没听过 Fabric)
- **每一个依赖必须 recorder 自带 + 自验证 + 自启动**

## 约束

- 安装包 < 500 MB (含 JRE + MC libs + Fabric + mod jar)
- 装好后能离线运行 (除了上传)
- 不污染用户系统的 PATH / Java version / .minecraft (使用 portable 沙箱目录: `%LOCALAPPDATA%\OysterRecorder\mc-instance\`)
- 必须能装在标准用户 (non-admin) 账号 (大多数家用 PC)
- 所有外部资源 (JRE / Fabric / MC libs) 必须从 manifest 验 SHA-256

## 验收标准

### A. `bin/recorder_jre_bootstrap.py` (portable JRE)

- [ ] 检测 `%LOCALAPPDATA%\OysterRecorder\jre\bin\javaw.exe` 是否存在
- [ ] 缺失则下载 Eclipse Temurin OpenJDK 21 LTS (jdk-21.0.4+7-jre.zip from adoptium.net)
- [ ] 验 SHA-256 (manifest 内嵌)
- [ ] 解压到 `%LOCALAPPDATA%\OysterRecorder\jre\`
- [ ] 返回 javaw.exe path
- [ ] 不污染系统 JAVA_HOME / PATH

### B. `bin/recorder_fabric_bootstrap.py`

- [ ] 检测 `%LOCALAPPDATA%\OysterRecorder\mc-instance\versions\fabric-loader-<X>-<MC_VER>\` 是否存在
- [ ] 缺失则跑 Fabric installer headless: `java -jar fabric-installer.jar client -mcversion 1.21.4 -dir <path> -noprofile`
- [ ] 验证 fabric-loader JSON profile 文件生成
- [ ] 拷贝 `oyster-recorder-mod-*-mc<VER>.jar` 到 `<instance>\mods\`
- [ ] 验 mod jar SHA-256 against recorder 内嵌 manifest

### C. `bin/recorder_mc_launcher.py` (核心)

- [ ] 直接组装 MC 启动命令 (不调 Mojang Launcher):
  - JVM args (heap, GC tuning, Fabric agent)
  - Classpath (从 fabric-loader JSON 解析)
  - Main class (`net.fabricmc.loader.impl.launch.knot.KnotClient`)
  - Game args (`--gameDir <portable-instance> --assetsDir ... --version fabric-loader-X-Y --accessToken <TOKEN>`)
- [ ] **Microsoft / Mojang authentication**: 优先 device-code OAuth (用户在浏览器里登一次, refresh token 存本地)
- [ ] 若 OAuth 不可用 → fallback 到 offline mode (用户名 = system username, MC 仍可玩单机)
- [ ] subprocess.Popen javaw.exe with full args
- [ ] 监控子进程: 退出 code 0 = 正常退出, ≠0 = 崩溃 → 弹 errorbox + 上传日志
- [ ] 所有 path 都 portable (`%LOCALAPPDATA%\OysterRecorder\mc-instance\`)

### D. `recorder_consumer_lite.py` 改动

- [ ] 主窗口替换大按钮: "▶ 开始录制" → "🎮 启动 Minecraft (Fabric Mode)"
- [ ] 点击该按钮:
  1. JRE bootstrap (后台, 进度条)
  2. Fabric bootstrap (后台)
  3. Mod jar 验证
  4. Auth OAuth (浏览器弹, 1 次/月)
  5. MC subprocess 启动
  6. recorder 自动 arm (无需用户再点)
- [ ] MC 退出时自动 disarm + finalize + package + upload

### E. `recorder_consumer_installer.iss` (Inno Setup)

- [ ] 单文件 .exe installer (NSIS 或 Inno Setup)
- [ ] 安装目录: `%LOCALAPPDATA%\OysterRecorder\` (per-user, no admin needed)
- [ ] Bundle 内容:
  - OysterRecorder onedir (recorder 主体)
  - manifest.json (SHA + 版本号)
  - 9 个 mod jars (1.20.1...1.21.5, 共 ~135 KB)
  - Fabric installer (~3 MB)
- [ ] 不 bundle JRE (运行时下载, 节省 install 包大小)
- [ ] Desktop shortcut "Oyster Recorder" + Start Menu entry
- [ ] 静默卸载: 删除 `%LOCALAPPDATA%\OysterRecorder\` (保留 user data)

### F. CI

- [ ] `.github/workflows/build-recorder-installer.yml`: tag push → build .exe installer → upload to GitHub Release
- [ ] Installer 自带 SHA verification (拒绝 tampered downloads)

### G. 错误处理

- [ ] **No internet on first run**: 弹 dialog "请连网再启动 (首次运行需下载 Java + Fabric)"
- [ ] **Disk full**: 弹 dialog "需要 800MB 空间, 当前 X MB"
- [ ] **Antivirus blocking JRE download**: 弹 dialog with curl-equivalent fallback URL
- [ ] **OAuth 失败**: fallback offline mode + 弹通知"已离线模式 — 录制可用, 但 Microsoft 登录失败, 请重试"
- [ ] **MC crash**: 上传 crash report 到 backend (不依赖 0x0.st)

### H. 多版本支持 (Howard "多兼容不同版本")

- [ ] Recorder 启动时探测哪些 MC 版本被 Fabric 支持 (从 Fabric meta API)
- [ ] 用户首次启动 → 弹下拉框 "选 MC 版本" → 列出所有支持的版本 (默认最新 stable)
- [ ] 用户选定后 → 装对应 mod jar + Fabric loader for that MC version
- [ ] 后续启动直接用上次选择
- [ ] 用户可在 tray menu 切换版本 (重新走 bootstrap)
- [ ] **R02 watcher 决定支持版本列表** — 不写死 `[1.20.1...1.21.5]`, 而是动态读 manifest

## 不要做

- ❌ 不要碰 Mojang Launcher (用户系统的) — 完全绕开
- ❌ 不要污染系统 PATH / JAVA_HOME / .minecraft
- ❌ 不要要求 admin 权限 (家用 PC 大多 non-admin)
- ❌ 不要 bundle JRE 进 installer (节省下载, 运行时下)
- ❌ 不要写自己的 Fabric loader (调官方 fabric-installer.jar)
- ❌ 不要假设用户懂任何技术概念 — 所有 dialog 用 plain English/Chinese, 一句话解释 + 一个按钮

## Release path

R05 实施后:
1. CI build `recorder-v0.28.0-rc1` 含 onedir + installer .exe
2. Howard 在 minipc 全新 Windows VM (or 清掉现有 .minecraft) 测试一键装
3. 录 30s session → packaging 成功 → 上传成功
4. → graduate v0.28.0
5. → 发给 alpha tester (10 个外部用户) 验证 zero-knowledge UX

— Howard Li, Oysterworld Inc, 2026-05-08
