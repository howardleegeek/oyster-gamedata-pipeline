# PRD 数据要求 Checklist（版本无关）

> 来源：`docs/PRD.md` v1.0 + `bin/lint_v3_prd_grounded.py` 32 criteria
> 用途：每个 session 录完后逐项对照。**必须 100% PASS 才能交付客户。**

---

## 一、交付 4 件套（per session）

### 1️⃣ `recording.mp4`（视频）

| 项 | 硬指标 |
|---|---|
| 时长 | **5 ≤ x ≤ 6 分钟**（过短/过长拒收） |
| 分辨率 | **1920 × 1080** |
| 帧率 | **30 fps 稳定**（不能动态 FPS） |
| 编码 | H.264（默认）或 H.265，**CRF ≤ 23** |
| 音频 | AAC，**必须存在 + 连续 + 无外界噪声 + 无 NPC 刷屏** |
| 延迟 | 玩家动作 → 画面响应 **≤ 20 ms** |
| 系统/游戏 | **系统全屏 + 游戏窗口都 1920×1080** |
| 禁止 | UI 弹窗 / 任务面板 / 背包 / 第一↔第三人称切换 / 死亡重生 |

### 2️⃣ `action_camera.json`（每帧 20 字段，共 ~9000 帧 / 5 min @ 30fps）

每帧 entry 必须包含：

| 字段 | 类型 | 约束 |
|---|---|---|
| `frame` | int | **连续** 0, 1, 2, ..., N-1 无间断 |
| `time` | string | `YYYY-MM-DD HH:MM:SS.fff` |
| `fps` | float | 30.0 |
| `route_type` | int | ∈ {1, 2, 3} |
| `mouse_x` / `mouse_y` | float | 归一化 0-1 屏幕坐标 |
| `mouse_dx` / `mouse_dy` | float | 每帧 delta |
| `keyCode` | int array | 当前按下的 key codes（如 `[87]` for W） |
| `camera_position` | `{x,y,z}` | 米，**左手系**（右 +X / 上 +Y / 前 +Z） |
| `camera_rotation_oula` | `{x,y,z}` | 欧拉角；pitch [-90,90], yaw/roll [-180,180] |
| `camera_rotation_quaternion` | `{x,y,z,w}` | **顺序 xyzw**，**模长 ≈ 1** |
| `camera_Follow Offset` | `{x,y,z}` | 第三人称偏移；第一人称合理为 0 |
| `camera_intrinsics` | `{fx,fy,cx,cy}` | **必须 fx == fy**（针孔模型） |
| `camera_speed` | `{x,y,z}` | m/s 每轴 |
| `player_position` | `{x,y,z}` | 米，左手系 |
| `player_rotation_oula` | `{x,y,z}` | 同 camera 角度规则 |
| `player_rotation_quaternion` | `{x,y,z,w}` | 同 camera quat 规则 |
| `player_speed` | `{x,y,z}` | m/s |
| `metric_scale` | float | 通常 1.0 |

### 3️⃣ `gameinfo.xlsx`（单 sheet 14 字段）

| 字段 | 类型 | 示例 |
|---|---|---|
| `game_name` | string | Minecraft |
| `game_version` | string | 1.20.4 |
| `platform` | string | Java Edition |
| `scene_name` | string | flat-overworld |
| `weather` | string | clear |
| `time_of_day` | string | day |
| `character_name` | string | DataPilot |
| `character_class` | string | spectator |
| `operator_id` | string | vendor-001-op-A |
| `recording_date` | string | 2026-05-02 |
| `total_frames` | int | 9000 |
| `video_duration_sec` | float | 300.0 |
| `route_type` | int | 1 (主路径) |
| `notes` | string | 操作员备注 |

### 4️⃣ `depth/*.exr`（深度图）

| 项 | 规范 |
|---|---|
| **采样率** | **6 fps**（5 min × 6 = **1800 帧**，不是 30 fps 也不是 1 Hz） |
| 格式 | OpenEXR 单通道命名 `Z` |
| 数据类型 | float32 |
| 单位 | **米**（线性深度沿光轴 Z） |
| 无效像素 | **0**（天空 / 透明 / 裁剪外） |
| 文件名 | 时间戳对齐 `000000.exr` (t=0) → `000005.exr` (t=0.83s) → ... |
| 分辨率 | 1920 × 1080（与 mp4 一致） |

---

## 二、单值约束（跨文件）

| 约束 | 说明 |
|---|---|
| **坐标系** | 全部左手系 |
| **单位** | 长度=米，速度=m/s，角度=度 |
| **四元数** | `[x, y, z, w]` 顺序，模长 ≈ 1.0 |
| **角度范围** | pitch [-90, 90]，yaw/roll [-180, 180] |
| **帧连续性** | action_camera.json frame 0..N-1 无跳号 |
| **fx == fy** | camera_intrinsics 必须针孔模型 |
| **player_speed / camera_speed** | 任意分量 magnitude ≤ 100 m/s |

---

## 三、路径多样性（per session）

| 项 | 要求 |
|---|---|
| `route_type` | 每条 session 主路径 ∈ {1, 2, 3}，分布要平衡 |
| WASD 平衡 | 每个 session 中 W/A/S/D 按键比例不能严重失衡（防 `wasd_balance` lint #18 fail） |
| 输入分布 | 不能连续 1 分钟无任何按键 / 无任何鼠标移动 |
| 路径类型 1 | 平地探索 |
| 路径类型 2 | 战斗 / 复杂动作 |
| 路径类型 3 | 上下移动 / 建造 |

---

## 四、禁止行为（angle of attack）

| 禁止 | 原因 |
|---|---|
| 自动按键 macro / WASD 定时器 | Lint 检测出"机器人"模式拒收 |
| 长时间静止 | Lint #stationary_threshold fail |
| 第一↔第三人称切换 | 视角变化 → camera intrinsics 不一致 |
| UI 弹窗（暂停 / 背包 / 设置） | 屏幕上有 UI = 数据污染 |
| 死亡重生 | 帧不连续 |
| 60 fps 降采样到 30 | 必须**原生 30 fps** |
| 边录边切窗口 | 必须**全屏** |

---

## 五、容量限制（pacing）

| 项 | 限制 |
|---|---|
| 同一场景 | ≤ 30 分钟（≤ 6 条 5min session） |
| 每人每游戏 | ≤ 240 条 = 20 小时（超过转新操作员） |
| 同一操作员每天 | 上限取决于真实游戏时间，不允许重复录制刷量 |

---

## 六、Lint v3 32 criteria 速查（自动验收）

> `bin/lint_v3_prd_grounded.py` 是 PRD 的代码化。每条 criterion 都对应上方某个要求。

| # | 名称 | 检查什么 |
|---|---|---|
| 1 | Video Resolution | mp4 1920×1080 |
| 2 | Video Duration | 5 ≤ x ≤ 6 min |
| 3 | Video FPS | 30 fps 稳定 |
| 4-10 | 视频内容健康 | bitrate / codec / audio / no-ui |
| 11 | route_type distribution | ∈ {1,2,3} per frame |
| 12 | camera_intrinsics fx == fy | 针孔模型 |
| 13 | quaternion xyzw order | 顺序正确 |
| 14 | quaternion normalization | 模长 ≈ 1 |
| 15 | mouse-camera alignment | mouse_dx 和 yaw delta 方向一致 |
| 16 | speed bounds | ≤ 100 m/s |
| 17 | input_stats sanity | 按键事件数 > 0 |
| 18 | wasd balance | 各方向分布合理 |
| 19-21 | 输入分布健康 | 静止检测 / unique keys / mouse std |
| 22 | metadata.json 完整 | game_exe / resolution / recordDpi / etc |
| 23 | 文件命名规范 | 无空格 / 无前导点 |
| 24 | 5 件套交付齐全 | mp4 + action_camera + gameinfo + depth + metadata |
| 25 | Video Content Health | ffmpeg probe sanity |
| 26-27 | 路径合理性 | 不全程 AFK |
| 28 | Video codec | h264 or hevc |
| 29 | Duration upper bound | ≤ 360s |
| 30 | Frame indices | 连续无跳号 |
| 31 | mouse_dx vs yaw-delta sign correlation | 鼠标和视角方向匹配 |
| 32 | speed magnitude | ≤ 100 m/s |

**目标**：每个 session `lint_result.json` 显示 `overall_status: PASS` + `passed: 32`。

---

## 七、交付前自检流程

录完一个 session 后**逐项 ✅**：

- [ ] **文件齐**：mp4 / metadata.json / action_camera.json / gameinfo.xlsx / depth/ 目录（5 件套 + metadata）
- [ ] **视频**：1920×1080 / 30fps / 5-6 min / H.264 or H.265
- [ ] **action_camera.json**：每帧 20 字段全部非 null
- [ ] **frame 连续**：0...N-1，N ≈ 9000
- [ ] **quaternion 正确**：xyzw 顺序，模长 ≈ 1
- [ ] **intrinsics**：fx == fy
- [ ] **gameinfo.xlsx**：14 字段全填
- [ ] **depth EXR**：~1800 个文件（6 fps × 5 min），float32 米
- [ ] **lint_result.json**：`overall_status: PASS`，`passed: 32/32`
- [ ] **无 UI / 无切人称 / 无死亡** 肉眼检视视频

ALL ✅ → 发客户。任一 ❌ → 重录或修复后重录。

---

## 八、当前已知偏差（rc17.3 截至 2026-05-12）

| 偏差 | rc17.3 实际 | PRD 要求 | 影响 |
|---|---|---|---|
| Depth EXR 采样率 | **1 Hz** (BJ-rescue) | **6 fps** | ⚠️ ~300 帧 vs 1800 帧；客户可能拒收 → 需 rc17.4 修 |
| `recording_date` in gameinfo.xlsx | 未验证 | 必须 YYYY-MM-DD | 待 Howard 验 |
| `operator_id` | 未填写机制 | 必填 | 需添加配置项 |
| `notes` | 未填写机制 | 操作员备注 | UI 缺失，rc17.4 加 |
| `route_type` 主路径 | 默认 1 | 应该由操作员选择 | UI 缺失 |
| `wasd_balance` PASS | 取决于 Howard 玩法 | 需平衡 | 操作员注意事项 |

---

## 九、操作员守则（每次录前）

1. 关掉所有 OS 通知 / Discord / 输入法弹框
2. 全屏 Minecraft（不要窗口模式）
3. 第一人称（不要切第三人称）
4. 不要打开背包 / 暂停 / 设置
5. WASD + 鼠标自然探索，**不要 macro**
6. 5 分钟左右按 Esc → Save and Quit
7. 等 1-2 秒右下角 toast：**绿色 PASS** → 这条数据可发客户
8. **红色 FAIL** → 看失败 criteria，决定重录还是修代码

