# PRD vs 跑通 对照表

**对比**: PRD v1.0 (`docs/PRD.md`) vs rc17.3.1（最新 tag，CI 中）+ rc17.2.3（已上线）

**图例**:
- ✅ PASS — rc17.3.1 完全满足
- ⚠️ PARTIAL — 部分满足或临时方案
- ❌ FAIL — 完全没实现
- 🚫 BLOCKED — 设计错被禁用待重写
- 👤 OPERATOR — 录制员人工保证

---

## §3.1 — `recording.mp4` 硬指标（9 项）

| PRD 要求 | 实际 | 状态 |
|---|---|---|
| 时长 5 ≤ x ≤ 6 min | 操作员控制，无强制 | 👤 |
| 同地图 ≤ 30 min | 无强制（多 session 累计无追踪） | ❌ |
| 每人每游戏 ≤ 240 条 | 无统计 | ❌ |
| 分辨率 1920×1080 | rc17.0.4+ 锁定 1080p | ✅ |
| 帧率 30 fps 稳定 | rc17.0.4+ 30 fps 验证过 | ✅ |
| 系统全屏 + 游戏窗口都 1080p | OBS 录全屏 ✓ | ✅ |
| 延迟 ≤ 20 ms | 未测量 | ❓ |
| 音频存在 + 连续 + 无噪声 | OBS 录系统音频，未实证 | ⚠️ |
| 编码 H.264 / H.265 CRF ≤ 23 | rc17.0.4+ H.264 锁定 + CRF 22 | ✅ |
| AAC 音频 | OBS 默认 AAC | ✅ |
| 禁止 UI 弹窗 / 切人称 / 死亡 | 无强制，操作员守则 | 👤 |

**评分**: 5 ✅ / 2 ⚠️ / 2 ❌ / 1 ❓ / 1 👤 = **45% PASS-only**

---

## §3.2 — `action_camera.json` 20 字段（每帧）

| 字段 | PRD 要求 | rc17.3.1 实际 | 状态 |
|---|---|---|---|
| `frame` | 连续 0..N-1 | recorder 保证 | ✅ |
| `time` | ISO ms | recorder 写 | ✅ |
| `fps` | 30.0 | recorder 写 | ✅ |
| `route_type` ∈ {1,2,3} | 操作员选 | rc17.2.3+ default 1（env 可改） | ⚠️ |
| `mouse_x` / `mouse_y` 归一化 | 真鼠标位置 | rc17.2.3 = 0.5 stuck | ❌ |
| | | rc17.3+ (BG) 修了 | ✅ (rc17.3) |
| `mouse_dx` / `mouse_dy` 每帧 delta | 真 delta | rc17.2.3 = 0 stuck | ❌ |
| | | rc17.3+ (BG) | ✅ (rc17.3) |
| `keyCode` 按下的 key | 真 key codes | rc17.2.3 = [] empty | ❌ |
| | | rc17.3+ (BG) | ✅ (rc17.3) |
| `camera_position` 左手系 m | `{x, y, z}` | rc17.2.3+ BH-narrow 填值 | ✅ |
| `camera_rotation_oula` 欧拉角 | pitch [-90,90] yaw/roll [-180,180] | rc17.2.3+ ✓ | ✅ |
| `camera_rotation_quaternion` xyzw 模长 1 | 顺序 + 模长 | rc17.2.3+ BD schema | ✅ |
| `camera_Follow Offset` | 第一人称 0 | rc17.2.3+ null（第一人称合理） | ✅ |
| `camera_intrinsics` fx==fy 针孔 | `{fx,fy,cx,cy}` | rc17.2.3+ fx==fy=771.2 | ✅ |
| `camera_speed` m/s 每轴 | 真速度 | 0 stuck（mc-mod 无 velocity IPC） | ❌ |
| `player_position` 左手系 m | `{x,y,z}` | rc17.2.3+ ✓ | ✅ |
| `player_rotation_oula` | 同 camera | rc17.2.3+ ✓ | ✅ |
| `player_rotation_quaternion` xyzw | 同 camera | rc17.2.3+ ✓ | ✅ |
| `player_speed` m/s | 真速度 | 0 stuck | ❌ |
| `metric_scale` | 1.0 | recorder 1.0 | ✅ |

**评分（rc17.3+ 假设 BG 已合）**: **14 / 20 = 70% PASS**
- ✅ 14（schema + position + rotation + intrinsics + 输入捕获）
- ❌ 2（camera_speed + player_speed，速度无 IPC）
- ⚠️ 1（route_type 默认值需操作员配）
- ✅ 3（rc17.3 后）

---

## §3.3 — `gameinfo.xlsx` 14 字段（单 sheet）

| 字段 | rc17.3.1 来源 | 状态 |
|---|---|---|
| game_name | env / default "Minecraft" | ✅ |
| game_version | env / default "1.21.4" | ✅ |
| platform | env / default "Java Edition" | ✅ |
| scene_name | env / default "flat-overworld" | ⚠️ 默认（需 mc-mod IPC） |
| weather | env / default "clear" | ⚠️ 默认 |
| time_of_day | env / default "day" | ⚠️ 默认 |
| character_name | env / default "DataPilot" | ⚠️ 默认 |
| character_class | env / default "survival" | ⚠️ 默认 |
| operator_id | env / default "vendor-001-op-A" | ⚠️ 默认 |
| recording_date | 从 metadata.start_timestamp 派生 | ✅ |
| total_frames | 从 frames.jsonl 计数 | ✅ |
| video_duration_sec | 从 metadata.duration | ✅ |
| route_type | env / default 1 | ⚠️ 默认 |
| notes | env / default "" | ⚠️ 默认 |

**评分**: **6 ✅ + 8 ⚠️** = 字段全填但 8 个用默认值。需启动器表单 UI（rc17.4）让操作员填实值。**Schema 100% PASS**，**值真实性 43%**。

---

## §3.4 — `depth/*.exr` 规格

| PRD 要求 | rc17.3.1 实际 | 状态 |
|---|---|---|
| 采样 6 fps（5min × 6 = 1800 帧） | **DISABLED**（rc17.4 重写） | 🚫 |
| OpenEXR 单通道 Z | DISABLED | 🚫 |
| float32 | DISABLED | 🚫 |
| 米单位 | DISABLED | 🚫 |
| 无效像素 0 | DISABLED | 🚫 |
| 文件名时间戳对齐 | DISABLED | 🚫 |

**评分**: **0 / 6 = 0%**。设计错（BJ-cluster 用 desktop 截图代 mp4 frame）已禁用 → rc17.4 重写需 cv2 读 mp4。

---

## §4 — 路径多样性 + 输入分布 + 禁止行为

### §4.1 route_type 分布（50% normal / 25% special / 25% loop）

| PRD | 实际 |
|---|---|
| 每批 50/25/25 分布 | ❌ 无 batch tracker；每 session 操作员手选（OYSTER_ROUTE_TYPE env） |

### §4.2 输入分布
| PRD | 实际 |
|---|---|
| 50% normal + 50% wasd_balanced (W=40/A=20/S=20/D=20) | 👤 操作员守则 |
| 站立时间 ≤ 10% | 👤（lint v3 #stationary_threshold 后置检测） |
| 每秒至少一动作 | 👤 操作员守则 |

### §4.3 禁止行为
| 禁止 | 实际 |
|---|---|
| 战斗 / 砍怪 | 👤 |
| 打开背包 / 菜单 | 👤 |
| 鼠标滚轮缩放 | 👤 |
| NPC 对话 | 👤 |
| 死亡 / 重生 / 切场景 | 👤 |
| 同 clip 冻结 ≥ 2 秒 | 👤（lint v3 部分覆盖） |
| 频繁切装备 | 👤 |
| 7 类自动化采集问题 | 👤 |
| 行走/跑动/视角 >90% | 👤 |
| 交互层级 ≤ 2 层 | 👤 |
| 主体原地静止 ≤ 10% | 👤 + lint |

**评分**: **0 ✅ + 14 👤** = 几乎全靠操作员守则 + 后置 lint 检测，**无代码层 enforce**。需操作员训练 + clip 自检流程。

---

## §5 — 录制方法论

| 项 | PRD | 实际 |
|---|---|---|
| MC 版本 | Java 1.20.4 | rc17.x = **1.21.4**（Howard 改的） → ⚠️ 跨版本差异 |
| Paper 服务端 | 1.20.4 localhost:25565 | 不用 Paper，直接 client | ❌ |
| Mineflayer headless bot | ScriptedProvider | 不用 bot，**真人玩家** | ❌（设计差异） |
| OBS Studio + WebSocket | OBS 30+ | ✅ libobs 嵌入 v32.0.2 |
| DepthAnything V2 Small | fp16 | 🚫 禁用待 rc17.4 |
| 鼠标 dpi 1800 | 必须 | 👤 操作员 |
| 鼠标指针速度 win10=6 / win11=10 | 必须 | 👤 |
| 月产能 100-300 clip | 估算 | 未达（仍在初步测试） |

**评分**: PRD §5 是 OUR REFERENCE STACK 描述；rc17.x 是**变体**（无 bot、不同 MC 版本）。**核心问题**：PRD 假设是 headless bot 录制，rc17.x 走的是真人玩家录制。如果客户接受真人录制 = PASS，否则 = 重大设计差异。

---

## §6 — 自动验收（lint v3 32 criteria 必须 100% PASS）

| Criterion | 名称 | rc17.2.3 | rc17.3 | rc17.3.1 |
|---|---|---|---|---|
| 1-3 | Video resolution / duration / fps | ✅ ✅ ✅ | ✅ ✅ ✅ | ✅ ✅ ✅ |
| 4-10 | Video content health (codec/audio/UI) | ⚠️ | ⚠️ | ⚠️ |
| 11 | route_type ∈ {1,2,3} | ✅ (BD) | ✅ | ✅ |
| 12 | camera_intrinsics fx==fy | ✅ (BD) | ✅ | ✅ |
| 13 | quaternion xyzw order | ✅ (BD) | ✅ | ✅ |
| 14 | quaternion 模长 ≈ 1 | ✅ (BD) | ✅ | ✅ |
| 15 | mouse-camera alignment | ❌ (mouse=0 stuck) | ✅ (BG) | ✅ |
| 16 | speed bounds ≤ 100 m/s | ⚠️ (speed=0 通过 abs check) | ⚠️ | ⚠️ |
| 17 | input_stats sanity | ❌ (0 events) | ✅ (BG) | ✅ |
| 18 | wasd_balance | ❌ | ✅ (BG) | ✅ |
| 19-21 | 输入分布 stationary / unique keys / mouse std | ❌ | ✅ | ✅ |
| 22 | metadata 完整（含 recordDpi） | ✅ (BD) | ✅ | ✅ |
| 23 | 文件命名规范 | ✅ | ✅ | ✅ |
| 24 | 5 件套交付齐 | ⚠️ depth 错 | ⚠️ | ⚠️ depth 缺 |
| 25 | Video content health (ffmpeg probe) | ✅ | ✅ | ✅ |
| 26-27 | 路径合理性 | ⚠️ | ⚠️ | ⚠️ |
| 28 | Video codec h264/hevc | ✅ | ✅ | ✅ |
| 29 | Duration upper bound ≤ 360s | ✅ | ✅ | ✅ |
| 30 | Frame indices monotonic | ✅ | ✅ | ✅ |
| 31 | mouse_dx vs yaw-delta sign correlation | ❌ | ✅ (BG) | ✅ |
| 32 | speed magnitude ≤ 100 m/s | ⚠️ | ⚠️ | ⚠️ |

**评分（理想 graceful session）**:

| Tag | PASS | FAIL | PARTIAL | 总分 |
|---|---|---|---|---|
| **rc17.2.3** | 19 | 8 | 5 | **59%** |
| **rc17.3** (估) | 26 | 1 | 5 | **81%** |
| **rc17.3.1** (估) | 25 | 2 | 5 | **78%** (depth 缺 1) |

---

## 跨章节总评

| Section | 完整度 |
|---|---|
| §3.1 mp4 硬指标 | **45%** |
| §3.2 action_camera 20 字段 | **70%** (rc17.3) |
| §3.3 gameinfo 14 字段 schema | **100%** schema, **43%** 真值 |
| §3.4 depth EXR | **0%** (disabled) |
| §4 路径多样性 + 禁止行为 | **0%** code enforce, 全靠操作员 |
| §5 录制方法论 | **不同设计**（真人 vs bot）|
| §6 lint v3 acceptance | **81%** (rc17.3) |

**加权平均** (按字段数量): ~**60% PASS**（按 PRD 字面）。

---

## 必须做（rc17.4 候选）

1. 🚫 **depth EXR 正确实现** — cv2 读 mp4 + DepthAnything V2 + 6 fps + EXR float32（PRD §3.4 整章 0%）
2. **operator_id / scene_name / character_* 表单 UI** — gameinfo 14 字段 8 个真值（PRD §3.3 字段填实化）
3. **mc-mod 速度 IPC** — camera_speed + player_speed 真值（lint #16/#32 PARTIAL → PASS）
4. **延迟 ≤ 20ms 测量 + 标注** — PRD §3.1（未测）
5. **音频连续性自检** — PRD §3.1（未实证）

## 文档（rc17.4 候选）

6. **操作员守则 + 培训文档** — PRD §4 全 14 项靠操作员
7. **批次 route_type 分布 tracker** — PRD §4.1 50/25/25 分布
8. **PRD §5 设计差异说明书** — 真人 vs headless bot 设计差，向客户解释

## 集群已派单

- ✅ OTLP telemetry (Audit B1) — cluster agent 跑中
- 未来：B2 idempotent / G4 16-hex / R1 path / R2 marker

---

## 结论

**当前 rc17.3.1 离 100% PRD 还差**：
- depth EXR 0%（最大缺口）
- 速度字段 0%（lint partial）
- 8 个 gameinfo 字段用默认值
- §4 禁止行为靠操作员

**最快达到 100% PRD 路径**：
- rc17.4 集中修 depth EXR（最大 1 块）
- rc17.5 加表单 UI + mc-mod velocity IPC
- 操作员守则 + 培训

预估额外 **2-3 个 rc** 才能 100% PRD。
