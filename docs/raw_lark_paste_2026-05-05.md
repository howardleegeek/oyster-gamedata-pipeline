# Raw Lark Paste — independent anchor (2026-05-05)

> 🟢 **INDEPENDENT GROUND TRUTH**
>
> 来源：Howard 直接从 Lark 在线版 (https://g2f5iu2p9x.sg.larksuite.com/wiki/PGgPwWrvniuF8UkIzDWlCiivgd5)
> 复制粘贴的 PRD 文字内容，2026-05-05。
>
> **本文件不经过 Claude 的 PRD 解读**，是 V₂ (independent LLM) 的唯一允许参考源。
> V₂ 实施时禁止参考：
> - `docs/PRD_DIGEST.md` (Claude 写的)
> - `docs/PRD_FORMULAS.md` (Claude 写的)
> - `bin/verify_*.py` (Claude 写的 V₁)
> - `bin/recorder_consumer_lite.py` (Claude 写的 producer)
> - `bin/sample_tarball_builder.py` (Claude 写的 producer)
>
> 这是反循环铁律 IL3。

---

## 文件 2 — action_camera.json 字段表

| key | 中文名 | 类型 | 例 |
|-----|--------|------|-----|
| `time` | 系统时间 string | `"YYYY-MM-DD HH:mm:ss.SSS"` | `"2026-03-20 16:25:29.860"` |
| `fps` | 游戏动态 fps float | `29.99895003674871` |
| `frame` | 视频第几帧 int | `0` / `95` |
| `route_type` | 路线类型 int (1/2/3) | `1` |
| `camera_position` | 摄像机位置 Vector3 | `[-28.5000, 1.6910, -7.1490]` |
| `camera_rotation_oula` | 玩家旋转欧拉角 Vector3 | `[0.0000, 76.0219, 0.0000]` |
| `camera_rotation_quaternion` | 摄像机旋转 Vector4 `[X,Y,Z,W]` | `[0.0, 0.707, 0.0, 0.707]` |
| `camera_Follow Offset` | 相机偏移量 Vector3（带空格大写F）| `[0.0000, 2.5000, -5.0000]` |
| `camera_intrinsics` | 摄像机内参 Object | `{"fx": 1080.0, "fy": 1080.0, "cx": 960.0, "cy": 540.0}` |
| `camera_speed` | 镜头移动速度 Vector3 | `[1.2500, 0.0000, -3.5000]` |
| `player_position` | 玩家世界坐标 Vector3 | `[-28.5000, 0.2510, -7.1490]` |
| `mouse_x` | 鼠标绝对移动像素 float — **最终归一化到 [0,1] 的 list** | `[0.5]` |
| `mouse_y` | 同上 | `[0.5]` |
| `mouse_dx` | 鼠标相对位置 — **归一化到 [-1, 1]，相对于上一帧** list | `[0.026]` |
| `mouse_dy` | 同上 | `[0.01]` |
| `keyCode` | 键码 int list — Windows VK code | `[87]` 单 / `[87, 65]` 多 |
| `player_rotation_oula` | 玩家旋转欧拉角 Vector3 | `[0.0000, 76.0219, 0.0000]` |
| `player_rotation_quaternion` | 玩家旋转四元数 Vector4 `[X,Y,Z,W]` | `[0.0, 0.707, 0.0, 0.707]` |
| `player_speed` | 玩家移动速度 Vector3 m/s | `[0.707, 0.0, 0.707]` |
| `metric_scale` | 物理尺度 Float | `1.0` |

> ⚠️ Lark 文档自身不一致：字段表写 `player_rotation_oula`，但 JSON 例子键名却是 `player_rotation`。
> 以字段表为准。

## VK_TO_KEY (Lark 字面)

```python
VK_TO_KEY = {
    # 功能键 F1-F12
    112: 'F1', 113: 'F2', 114: 'F3', 115: 'F4',
    116: 'F5', 117: 'F6', 118: 'F7', 119: 'F8',
    120: 'F9', 121: 'F10', 122: 'F11', 123: 'F12',
    # ESC
    27: 'ESC',
    # 数字键行
    192: '`',
    48: '0', 49: '1', 50: '2', 51: '3', 52: '4',
    53: '5', 54: '6', 55: '7', 56: '8', 57: '9',
    # 字母键
    81: 'Q', 87: 'W', 69: 'E', 82: 'R', 84: 'T',
    89: 'Y', 85: 'U', 73: 'I', 79: 'O', 80: 'P',
    65: 'A', 83: 'S', 68: 'D', 70: 'F', 71: 'G',
    72: 'H', 74: 'J', 75: 'K', 76: 'L',
    90: 'Z', 88: 'X', 67: 'C', 86: 'V', 66: 'B',
    78: 'N', 77: 'M',
    # 特殊键
    9: 'TAB',
    20: 'CAPS',
    16: 'LSHIFT', 160: 'LSHIFT', 161: 'RSHIFT',
    17: 'LCTRL', 162: 'LCTRL', 163: 'RCTRL',
    18: 'LALT', 164: 'LALT', 165: 'RALT',
    32: 'SPACE',
}
```

> ⚠️ Lark 验收 #5 写"按 ASCII 码表（如 W 键对应 87）"。但 LSHIFT=16, LCTRL=17 在 ASCII
> 中是 DLE/DC1 控制字符——**实际是 Windows VK code**，W=87 巧合等于 ASCII。

## 文件 1 — systeminfo.json (5 字段)

```json
{
  "gameProcessName": "ds",
  "x": 0, "y": 0,
  "width": 1920, "height": 1080,
  "recordDpi": 1.5
}
```

## 文件 4 — 深度图

- 类型：View-space linear depth（相机坐标系沿光轴方向 Z 距离）
- 数据精度：float32
- 单位：米
- 文件格式：EXR (OpenEXR, 单通道)
- 无效像素：天空 / 超出远平面 / 无效深度 → 统一标记 0
- 抽帧：每秒 6 张均匀（不连续帧），命名与视频时间戳对应

## 验收 8 项（json 文件验收）

1. 是否存在字段缺失及格式不正确
2. 坐标系对齐：camera 与 player 的 location/rotation 与内部一致
3. 输入映射正确性：mouse dx/dy 方向是否弄反
4. 帧率连续性：frame 是否重复或跳帧
5. 键盘事件一致性：keyCode 触发时机与画面动作一致，按 ASCII（W=87）
6. 四元数顺序：严格 `[x, y, z, w]` 排列
7. 物理阈值限制：speed 数值符合物理逻辑
8. `camera_intrinsics` 中 `fx == fy`

## 视频文件验收

- 时长 > 300s
- 分辨率 1920×1080
- fps 30
- 单条时长 5–6 min（30 min 单场景上限，240 条/人/游戏 ≈ 20 hr）
- 不要：游戏 logo / 弹窗 / 系统通知 / 水印 / 模态对话框 / 黑边 / 马赛克 / 闪屏 / 切桌面 / Alt-Tab

## 路线类型

| route_type | 名称 | 描述 |
|---|---|---|
| 1 | 常规漫游 | 模拟正常玩家路线 |
| 2 | 特殊路线 | 贴墙、贴地、极端视角 |
| 3 | 循环录制 | 绕圈，前 10s 与最后 10s 重复同一场景 |

## 坐标系（PDF 第 3 页，未在 Lark 粘贴中重述但 PDF 字面）

- 左手坐标系
- right=x, up=y, front=z
- Pitch (绕 X 轴, z=0°): 抬头+/低头-, [-180, 180]
- Yaw (绕 Y 轴, x=0°): 左转-/右转+, [-180, 180]
- Roll (绕 Z 轴, y=0°): 左倾+/右倾-, [-180, 180]
- 速度单位 m/s
- 30 fps 录制

## 四元数公式（PDF 第 4 / 第 6 页嵌入图）

```
轴-角 → 四元数（标准 Hamilton）：
  w = cos(θ / 2)
  x = v_x · sin(θ / 2)
  y = v_y · sin(θ / 2)
  z = v_z · sin(θ / 2)
```

PDF 字面承诺："如果统一了轴朝向，四元数的换算公式在每个引擎里（unity、ue、Isaac sim 等）都一样"。

---

## END OF INDEPENDENT ANCHOR

任何 V₂ 实施必须只参考本文件 + 自己读 Lark URL（如能访问）。
不允许继承 V₁ 的字段命名 / 公式实现 / 结构假设。
