---
title: Oyster GameData Recorder rc18 — 专家交接说明
author: Howard Li / Oyster Labs
date: 2026-05-12
---

# Oyster GameData Recorder rc18 — 专家交接说明

**日期**：2026 年 5 月 12 日
**当前出货版本**：`recorder-v0.28.0-rc18.0.5`（CI 构建中；submodule SHA `7bd4d8c`，与已验证良好的 rc18.0.3 一致）
**Howard 实测会话 PRD lint 分数**：rc18.0.5 finalizer 跑完后 **26 / 33 = 78.8 %**

---

## 60 秒产品概述

一个 Windows 桌面应用，录制 Minecraft 玩家游戏录像供 AI 世界模型训练。按 PRD §3 要求输出：

- `recording.mp4` — 1920×1080，30 fps，10 Mbps CBR
- `action_camera.json` — 每帧约 29 字段（相机/玩家位置、四元数、鼠标、手柄、keyCode……）× N 帧
- `gameinfo.xlsx` — 14 字段（game_name、scene、weather、time_of_day、operator_id、duration、route_type……）
- `depth/*.exr` — 6 fps 深度图（目前已禁用）

架构组成：

- **Rust + OBS 录制器**（`vendor/recorder/`，子模块）— egui 托盘应用，通过 `libobs-wrapper` 接入 OBS 做 game-capture，输出 mp4 + action_camera + inputs
- **Fabric mc-mod**（`mc-mod/`）— 加载进 bundled Minecraft 1.21.4 的 Java mod，每 tick 写一行 `game_state.jsonl`（玩家位置、yaw / pitch、dimension、game_mode）
- **Python 工具链**（`bin/`）— 录后 finalize、PRD lint（32 准则）、acceptance 汇总
- **安装包** — Inno Setup，把 JRE + MC + Fabric + mod + recorder.exe 全部打进一个 859 MB 的 .exe

---

## 三个已确认的 Bug（按优先级排序）

### Bug 1 — 输入捕获管道丢弃 100 % 的事件 🔴 高优先级

**文件**：`vendor/recorder/crates/input-capture/src/kbm_capture.rs`

**真实测试会话证据**（`session_20260512_182328_e610fdd6/metadata.json`）：

```json
input_capture_diagnostics: {
    registration_tier: "hook",      // hooks 注册成功
    wm_input_total: 957,            // 145 秒内 OS 投递了 957 个事件
    get_raw_input_data_failures: 0  // GetRawInputData 全部成功
}
input_stats: {
    total_keyboard_events: 0,       // 但 inputs.jsonl 里 0 条键鼠数据
    mouse_movement_std: 0.0,
    wasd_apm: 0.0,
    ...
}
```

所以事件**到达** Win32 WM_INPUT 层，`GetRawInputData` **成功**返回，但永远进不了 `inputs.jsonl`。LL hook 回调下游的管道把全部事件吞掉了。

**假设（未验证）**：`kbm_capture.rs` 里的 LL hook 回调可能按 foreground HWND 过滤，而 debug log 显示 recorder 主循环看到的游戏进程是 `Minecraft.exe pid=10492, hwnd=HWND(0x0)`（空窗口句柄）—— 基于 HWND 的过滤就会把所有事件丢掉。Minecraft 启动方式是 javaw.exe 拉起后 spawn 一个独立的 Minecraft.exe（实际游戏进程），后者拿不到 hwnd。

**给专家的问题**：`kbm_capture.rs` 里有没有 HWND 过滤逻辑需要在「检测到游戏但 hwnd == 0」时退路到「接受任何非 recorder 自身的前台窗口」？或者是不是 message pump 在 hook 安装线程上没正常 pump（WH_KEYBOARD_LL 强依赖 message pump）？

---

### Bug 2 — mc-mod IPC 路径错位 🟡 中优先级（rc18.0.5 已 workaround）

**文件**：

- `mc-mod/src/main/java/world/oyster/recorder/SessionDir.java`（第 45 行：环境变量 `OYSTER_SESSION_DIR`）
- `bin/build_bundled_installer/` 下的 `launch_mc.bat` 模板（**未设置该环境变量**）

**症状**：录完的 session 目录里缺 `gameinfo.xlsx` 和 `game_state.jsonl`。

**根因（已确认）**：mc-mod 读 `System.getenv("OYSTER_SESSION_DIR")`；如果没设，退路到 `~/Documents/OysterClips/active_session/game_state.jsonl`。bundled `launch_mc.bat` 没设这个环境变量，所以 mc-mod 写到 fallback 路径上，而 recorder 不从那里读。**mc-mod 本身工作完全正常 —— Howard 那次测试它写了 3 079 条 game state 记录 —— 只是落到了「错误」的路径。**

**rc18.0.5 的 workaround**：`bin/finalize_session.py` 录完后把文件从 fallback 路径同步到 session dir + 用其中的 yaw / pitch 反推 quaternion 回填 `action_camera.json`。

**还没出的「真正的」修法**：在录制开始那一刻把 `OYSTER_SESSION_DIR=<recorder-active-session-path>` 从 recorder → launch_mc.bat → JVM 透传过去。三条可能路径：(a) recorder 写一个 marker 文件标记当前 session 路径，launch_mc.bat 读它；(b) recorder 直接 spawn MC（目前没有，用户从托盘图标启动 MC）；(c) launch_mc.bat 解析 recorder 的日志。

**给专家的问题**：在「录制开始 ↔ MC 启动」这个时刻，最 Windows-friendly 的 recorder → bat → JVM 环境变量透传模式是什么？

---

### Bug 3 — Lint #13（Quaternion xyzw 顺序）启发式有 bug 🟢 低优先级

**文件**：`bin/lint_v3_prd_grounded.py` 第 651 行（`_quat_rest_state_xyzw`）

**当前逻辑**：如果 `abs(q).index(max(abs(q))) == 3`（也就是最后一个分量是绝对值最大的），投 xyzw 一票。

**为什么在真实游戏数据上失效**：第一人称 Minecraft 在 yaw > 90° 旋转时，`w = cos(yaw/2)` 是小值，不再是绝对值最大的分量。启发式就把正确的 xyzw 数据误判为 wxyz。Howard 那次 session：xyzw 90 票 vs wxyz 202 票 —— 启发式给出错误结论。

**我的 finalizer 输出的是数学上正确的 xyzw**（ZYX intrinsic Euler → 四元数，已归一化到 |q| = 1，已验证）。Lint #14（归一化）通过；#13 卡在启发式上。

**给专家的问题**：游戏域四元数有没有更好的 xyzw 顺序启发式？还是接受这是一个有缺陷的 lint，需要用面向客户的新版本取代？

---

## 两个推迟到 rc19 的事项

### 深度图 EXR（PRD §3.4）

**状态**：rc17.3.1 子模块里被禁用。集群之前 rc17.4 的尝试用 `capture_screen()` 在录制**结束之后**截桌面来生成深度（错的 —— 截到的是录制结束时桌面上的内容，不是录到的游戏画面），而且只跑 1 Hz，PRD 要 6 Hz。需要从头重写：基于 cv2 重新 decode mp4 + DepthAnything V2 Small（onnxruntime-directml）逐帧推理。

### H.265 vs H.264 编码器

**症状**：metadata 写 `encoder: "x264"`（= H.264），但 PRD §3.1 要求 H.265。测试机（minipc1）GPU 是 AMD Radeon 780M，libobs 探测 AMF HEVC 编码器没找到，回退到了 x264。修法二选一：(a) 针对 GPU 做特殊配置；(b) 让客户在 HEVC 不可用时接受 x264 回退。

---

## 当前 8 个 lint failure 的归因

| # | 失败项 | 根因 | 归属 |
|---|---|---|---|
| 2 | 录像时长 145s < 300s | 用户录得太短 | 用户操作 |
| 13 | xyzw 顺序启发式 | Lint v3 在大旋转上的缺陷 | Bug 3 |
| 14 | 四元数归一化 | ~~无数据~~ rc18.0.5 已修 | ✅ |
| 15 | 深度无效像素比 | 深度禁用 | rc19 |
| 16 | 深度数据质量 | 深度禁用 | rc19 |
| 24 | 目录结构（缺 `depth/`） | 深度禁用 | rc19 |
| 27 | inputs.jsonl 质量 | 事件空 | Bug 1（文件存在所以「通过」；但实际数据是空的） |
| 31 | 鼠标/相机一致性 | 50 个采样对全是静止（mouse_dx = 0） | Bug 1 |
| 38 | 音频连续性 | 录像中确实有 > 2s 的静音段 | 硬件侧（minipc1 音频设备） |

**不动 rc19 的现实上限**：约 29 / 33 = 88 %（修好 Bug 1 + 录够长 + 音频硬件正常）。

**真正 100 % 需要**：Bug 1 修复 + 深度 EXR 出货 + #13 启发式改进 + 客户签 RFC-001。

---

## 专家可以直接打开的文件

- **输入管道 bug**：`vendor/recorder/crates/input-capture/src/kbm_capture.rs`（264 行起的 low-level hook 回调）
- **mc-mod 环境变量**：`mc-mod/src/main/java/world/oyster/recorder/SessionDir.java:45` + `bin/build_bundled_installer/installer.iss`（搜索 `launch_mc.bat` 和 bundled bat 模板）
- **Lint 启发式**：`bin/lint_v3_prd_grounded.py:651`（`_quat_rest_state_xyzw`）
- **Recorder 主循环 / HWND 检测**：`vendor/recorder/src/record/recorder.rs`（搜 "Found running game via process scan"）
- **action_camera writer**：`vendor/recorder/src/record/action_camera_writer.rs`
- **rc18.0.5 的 finalizer**：`bin/finalize_session.py`（Bug 2 的 workaround）

## 哪些**不是**问题

- 视频捕获：✅ 工作正常（10.6 Mbps CBR，OpenGL shared-texture 钩进 javaw，174 MB / 145s）
- mc-mod 加载：✅ 工作正常（Howard 那次写了 3 079 ticks —— 只是写到了错的路径）
- OBS 嵌入：✅ 没有单独的 OBS 进程，全部在 recorder 内
- 游戏检测：✅ javaw 启动后 2.2 秒就 hook 上
- 安装包：✅ 859 MB 单文件，Inno Setup，包含 JRE + MC + Fabric + mods + recorder
- CI 管道：✅ 4 个 workflow（Rust EXE / Python EXE / MC mod / Bundler），子模块编译干净时全过

## 仓库

`https://github.com/howardleegeek/oyster-gamedata-pipeline`（parent，里面的 `vendor/recorder/` 子模块指向 `gamedata-recorder` 仓库）

rc18.x 线所在分支：`stream-rc17.4-form`（命名有历史遗留；rc18 恰好是从这条分支 fork 的）。
