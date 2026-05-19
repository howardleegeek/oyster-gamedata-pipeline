# Tester 环境要求

> Howard 2026-05-05: "所以MC的版本 还有其他背景mod有什么要求吗"
>
> 测试人员（tester）开始录制前必须满足的环境清单。
> 缺任何一项 = 录的 clip 买家可能不收。

---

## 🟢 必装（不装无法满足 buyer-spec）

| # | 项目 | 版本 | 为什么 |
|---|---|---|---|
| 1 | **Minecraft Java Edition** | 1.20.4 | recorder 锁定的版本；其他版本 mod 兼容性可能崩 |
| 2 | **Java Runtime** | Java 21 LTS（Microsoft / Adoptium / Azul） | MC 1.20.4 强制要求 Java 17+，21 性能最好 |
| 3 | **Fabric Loader** | 0.16+ for MC 1.20.4 | Replay Mod 走 Fabric 路径（Forge 也能但 Fabric 简单） |
| 4 | **Replay Mod** | 2.6.16+ for MC 1.20.4 (Fabric build) | **唯一真 6DoF 数据来源**（A2/A3 buyer gap 唯一解）— 没装 = camera/quaternion 字段全 0 placeholder |
| 5 | **Fabric API** | matching MC 1.20.4 | Replay Mod 的依赖 |

**一键安装路径**：
1. 下载 MC 官方启动器：https://www.minecraft.net/download
2. 启动器创建新 profile：版本 = 1.20.4 Fabric
3. 启动器自动下载 Java 21（如果还没装）
4. 启动器创建 profile 后，手动放 mod jar 到 `%APPDATA%\.minecraft\mods\`：
   - `fabric-api-X.Y.Z+1.20.4.jar`
   - `replaymod-1.20.4-2.6.16.jar`
5. 启动游戏，左下角应看到"Replay Mod"+ 录制按钮

---

## 🟡 强烈推荐（影响录制质量但不 block）

| # | 项目 | 版本 | 为什么 |
|---|---|---|---|
| 6 | **Sodium** | 0.5+ for 1.20.4 (Fabric) | 渲染优化 → 录制时不丢帧；30 fps 稳定 |
| 7 | **Iris Shaders** | 1.7+ | 与 Sodium 配合；如果需要 shader 路径录深度（不通过 DA-V2） |
| 8 | **Lithium** | 0.12+ | 服务端模拟优化；MC 单人模式更流畅 |
| 9 | **MemoryLeakFix** | latest | 长录制（5+ 分钟）防内存累积导致丢帧 |

---

## 🟢 推荐 MC 设置（启动后改）

进 Settings → Video Settings：

| 设置项 | 值 | 为什么 |
|---|---|---|
| **Window Mode** | Windowed（**不要 Fullscreen**） | gdigrab 在 exclusive fullscreen 下会失败录 0 字节 |
| **Resolution** | 1920×1080（窗口尺寸） | recorder 锁定 1080p 输出；其他分辨率会被强制 scale 损失质量 |
| **FOV** | 70°（默认） | recorder 的 intrinsics.yaml 按 70° 算 fx/fy；其他值需要手动改 intrinsics |
| **GUI Scale** | 2 或 3 | 别用 Auto，避免高 DPI 缩放问题 |
| **Render Distance** | 12-16 chunks | 平衡性能；太高会丢帧 |
| **Particles** | All / Decreased | 不要 Minimal，buyer 想看完整粒子 |
| **VSync** | OFF | 可能触发 fps 不稳定 |
| **Brightness** | 50% (Default) | 别拉到 100%，影响场景真实性 |

---

## 🔴 禁止安装（会触发 lint 失败 / buyer 拒收）

| # | 类别 | 例子 | 为什么 |
|---|---|---|---|
| 1 | **HUD/UI 修改 mod** | OptiFine HD overlay, Inventory Tweaks, ToroHealth | lint criterion 19 (No UI Overlay) 会失败 |
| 2 | **Logo/Watermark mod** | 任何在画面加水印的 mod | criterion 20 (No Logo) |
| 3 | **Popup mod** | 通知 / 聊天弹窗类 | criterion 21 (No Popup) |
| 4 | **作弊 mod** | X-Ray, Killaura, AutoMine | 数据被买家识别为非真人操作 |
| 5 | **Resource pack** 改动 UI | 任何换 inventory/HUD 贴图 | UI 风格不一致 |
| 6 | **Shader pack** 加 watermark | 一些社区 shader 默认有 logo 角标 | criterion 19/20 |

---

## 🟡 其他注意事项

### 第一次启动 Replay Mod
1. 进游戏后 ESC → Replay Viewer → Recording Settings
2. **Disable**: "Show recording indicator" / "Recording status overlay"（避免 UI overlay）
3. **Enable**: "Auto-record on world join" — 一进世界自动开录

### 录制流程（tester 视角）
1. 双击桌面 OysterRecorder shortcut → 录制器窗口出现
2. 启动 MC 1.20.4 Fabric → 选 World → 进入游戏
3. Replay Mod 自动开始录 `.mcpr` 文件到 `.minecraft/replay_recordings/`
4. 切回 OysterRecorder 窗口 → 点 ▶ 开始录制
5. 玩 5-6 分钟 → 退出 MC
6. OysterRecorder 自动打包 5 文件 tarball + 后处理 .mcpr → 真 6DoF 填进 action_camera.json
7. 桌面/Documents 看到 clip-YYYYMMDD-HHMMSS.tar.gz

### Why Fabric 不 Forge?
- Replay Mod 两边都有，Fabric 体积更小、启动更快、修复 bug 更勤
- recorder 后端代码（recorder_replay_mod_postprocess.py / recorder_replay_mod_installer.py）默认假设 Fabric 路径
- Forge 也能跑，但需要手动改路径配置

### 多次录制 / 长 session
- recorder 6 分钟自动停（PRD 5-6 min 上限）
- 想录更长：每个 session 录 5-6 min 各产一个独立 clip
- 不要中途切窗口 / Alt-Tab 太久（可能触发 MC 失焦减帧）

---

## 一句话总结给 tester

**MC 1.20.4 + Java 21 + Fabric Loader + Replay Mod + Fabric API**，window 模式 1920×1080，FOV 70°，**不装其他任何 mod**，进游戏左下角看到 Replay Mod 按钮就 OK。
