# PRD 公式 / 物理约束清单（PDF 抽取，2026-05-05）

> ✅ **ANCHOR UPDATED (2026-05-05)** — Howard 直接粘贴 Lark 在线版完整文本，
> 替换 PDF（PDF 公式截图被打印过程截断）。本次更新发现 5 处之前 PRD_DIGEST
> 抄错的循环 bug 已修复：
>
> | # | 旧（错） | 新（PRD 字面） |
> |---|---------|---------------|
> | 1 | mouse_x/y: float | **list[float]**（例 `{"mouse_x": [0.5]}`） |
> | 2 | mouse_dx/dy ∈ [0, 1] | **mouse_dx/dy ∈ [-1, 1]**（带方向） |
> | 3 | Cx/Cy 大写 | **cx/cy 小写**（JSON wire 例：`{"cx": 960.0}`） |
> | 4 | keyCode = ASCII | **keyCode = Windows VK code**（W=87 巧合等于 ASCII） |
> | 5 | player_rotation_oula 字段 | 字段表写 `player_rotation_oula`，例子键名 `player_rotation`（**PRD 自身不一致**——flag 给产品方） |
>
> 仍待 Howard 截图的：camera_rotation_quaternion 那栏的 [Image] 公式截图，
> 用于交叉验证 PINNs A1 公式（轴-角→四元数）。
>
> ---
>
> 来源：`/Users/howardli/Downloads/video+action+camera数据收集需求文档 - Lark Docs.pdf`
>       (PARTIAL — 公式截图被打印过程截断)
>
> Howard 的反循环要求："如果俩都有问题，会有一模一样的问题"
>
> **本文件为破循环的唯一锚点**：所有公式都是数学定律或物理常量，与字段
> 命名无关。`bin/verify_formulas.py` **必须** 全部从这里加载，禁止把数值
> 硬编码到 verifier 源码里——只有这样，verifier 失败时才能一眼看出是
> "公式抄错"还是"数据违反公式"，而不是 verifier 与 producer 共享盲点。

---

## A. 数学公式（PDF 第 4-6 页**嵌入截图**抄录）

### A1. 轴-角 → 四元数（PDF page 4 + page 6 right-bottom inline image）

```
给定旋转轴 v = (v_x, v_y, v_z)（已归一化）和旋转角 θ（弧度）：
    w = cos(θ / 2)
    x = v_x · sin(θ / 2)
    y = v_y · sin(θ / 2)
    z = v_z · sin(θ / 2)
```

**含义**：标准 Hamilton 四元数（与 Unity / Unreal / glTF 一致）。

**应用**：把 `*_rotation_oula` 字段（pitch, yaw, roll 度）按 ZYX 内旋复合
转换得到的 quaternion **必须** 在容差内等于 `*_rotation_quaternion` 字段
（数组 `[x, y, z, w]`）。

### A2. 四元数模长（PDF 验收 #6 暗含 unit quaternion）

```
‖q‖ = √(x² + y² + z² + w²) = 1
```

容差：`|‖q‖ − 1| < 0.01`。

### A3. 速度 = 位置差分 × 帧率（PDF page 3 单位 m/s + page 11 验收 #7）

```
speed[n] ≈ (position[n+1] − position[n]) · fps
```

物理上限：步行 < 5 m/s, 跑步 < 10 m/s, 飞行 < 50 m/s（buyer 验收 #7
"物理阈值限制"）。我们用 `‖speed‖ < 50 m/s` 作为通用上限。

### A4. 鼠标差分（PDF page 5 黄色高亮"于上一帧"）

```
mouse_dx[n] = mouse_x[n] − mouse_x[n−1]
mouse_dy[n] = mouse_y[n] − mouse_y[n−1]
```

第 0 帧：`mouse_dx[0] = mouse_dy[0] = 0`。

### A5. 帧间时间差（PDF page 3 "30 fps 录制"）

```
frame[n+1].time − frame[n].time ≈ 1 / 30 ≈ 33.33 ms
```

容差：±5 ms（允许采样抖动）。

---

## B. 物理常量与数值范围（PDF 字面）

| 编号 | 量 | 范围 / 值 | 来源页 |
|------|---|-----------|--------|
| B1 | 视频分辨率 | 1920 × 1080 | page 8（验收"video"） |
| B2 | 视频 fps | 30 | page 3 + page 8 |
| B3 | 视频时长 | 5–6 min（>300s） | page 8 |
| B4 | 深度图抽帧 | 6 张/秒 | page 7 |
| B5 | 深度图格式 | EXR float32 单通道 | page 7 |
| B6 | 无效深度 | 0（统一标记） | page 7 |
| B7 | pitch 范围 | [-180°, 180°] | page 3 |
| B8 | yaw 范围 | [-180°, 180°] | page 3 |
| B9 | roll 范围 | [-180°, 180°] | page 3 |
| B10 | 速度单位 | m/s | page 3 |
| B11 | mouse_x **类型 + 范围** | **`list[float]`**, 元素 ∈ [0, 1]（归一化） | Lark page 文件2 |
| B12 | mouse_y **类型 + 范围** | **`list[float]`**, 元素 ∈ [0, 1]（归一化） | Lark page 文件2 |
| B12a | mouse_dx 类型 + 范围 | **`list[float]`**, 元素 ∈ **[-1, 1]**（带方向）| Lark page 文件2 |
| B12b | mouse_dy 类型 + 范围 | **`list[float]`**, 元素 ∈ **[-1, 1]** | Lark page 文件2 |
| B13 | keyCode 编码 | **Windows VK code**（W=87 巧合等于 ASCII；LSHIFT=16 / LCTRL=17 / LALT=18 / TAB=9 / ESC=27 / SPACE=32 / F1-F12=112-123 等）`list[int]` | Lark VK_TO_KEY 表 |
| B14 | 四元数顺序 | `[x, y, z, w]` 数组 | page 11 验收 #6 |
| B15 | camera_intrinsics | `fx == fy`，**键名小写 `fx, fy, cx, cy`** | page 12 验收 #8 + Lark JSON 例 |
| B16 | 坐标系 | 左手 (right=x, up=y, front=z) | page 3 |

---

## C. 验收 8 项（PDF page 11-12 字面）

| # | 名称 | 检查 |
|---|------|------|
| 1 | 字段缺失/格式 | schema 检查 |
| 2 | 坐标系对齐 | location/rotation 与内部一致 |
| 3 | 输入映射正确性 | mouse dx/dy 方向不反，与镜头一致 |
| 4 | 帧率连续性 | frame 不重复/不跳 |
| 5 | 键盘事件一致性 | keyCode 触发时机与画面动作一致，按 ASCII（W=87） |
| 6 | 四元数顺序 | 严格 `[x, y, z, w]` |
| 7 | 物理阈值限制 | speed 数值符合物理逻辑 |
| 8 | 内参 | `fx == fy` |

---

## D. systeminfo 5 字段（PDF page 3-4 字面）

```json
{
  "gameProcessName": "ds",
  "x": 0, "y": 0,
  "width": 1920, "height": 1080,
  "recordDpi": 1.5
}
```

❌ **不要** `map_scale` / `map_bounds`——PDF 完全没提。

---

## E. 路线规划（PDF page 2）

| route_type | 名称 | 描述 |
|---|---|---|
| 1 | 常规漫游 | 模拟正常玩家路线 |
| 2 | 特殊路线 | 贴墙、贴地、极端视角 |
| 3 | 循环录制 | 绕圈，前 10s 与最后 10s 重复同一场景 |

每批次：50% route_type=1 + 50% wasd 均匀（W=40, A/S/D=20）。

---

## F. 反循环说明（meta，给未来的 Claude / GLM 看）

如果你正在写 verifier：
1. ✅ **从这个文件加载常量 / 公式**，禁止硬编码到 verifier 源码
2. ✅ 公式 A1 (oula→quat) 必须**双向**验证：oula→quat→oula' 应得回 oula
3. ❌ 不要让 verifier 同时接受 `camera_rotation_oula` 和 `camera_rotation_euler`
   作为 alias——那样的 "alias-tolerant" verifier 给 producer 错误盖章，
   等于循环。verifier **只接受** PDF 字面命名，错的就是错的。
4. ✅ Producer 失败 ≠ verifier 失败：producer 应当 fail-loud，verifier 应当
   独立 fail-loud。两者只通过这个 PRD_FORMULAS.md 文件**间接**耦合。
5. ✅ 任何对此文件的修改必须引用 PDF 页码或截图。
