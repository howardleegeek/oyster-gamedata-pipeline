# PRD Digest — Read From The PDF (2026-05-05)

> 来源：`/Users/howardli/Downloads/video+action+camera数据收集需求文档 - Lark Docs.pdf`
>
> Howard's reviewer: "你需要对项目和 bug 有足够的了解 不然咱俩对话效率会非常低"
>
> 这份 digest 是我（Claude）逐字读完 14 页 PDF 后对项目的真实认知凭证。
> 任何 sample / recorder / lint / verify 修改前必须 grep 这份 digest 取证。

---

## 1. 项目核心目标（page 1）

- 用于训练**交互式世界模型**（user-driven，不是 bot-driven）
- 数据 = "人在世界中走动和观察" 的轨迹（**不是** 人在系统中点击和操作）
- 关键词：**干净、连贯、以环境探索行为为主的 character+camera 轨迹**
- 4 交付物：
  1. Game Video (mp4)
  2. Action+camera JSON
  3. 游戏元信息 (excel = gameinfo)
  4. 深度图 (EXR 文件夹)

## 2. 路线规划（page 2）

| route_type | 名称 | 描述 |
|---|---|---|
| 1 | 常规漫游 | 模拟正常玩家玩游戏的路线 |
| 2 | 特殊路线 | 刻意贴近墙壁、地面、道具，极端视角进行移动或转动 |
| 3 | 循环录制 | 在场景中绕圈，revisiting，**同一场景在前 10s 和最后 10s 都出现** |

每批次提交：50% route_type=1 (正常游览) + 50% 均匀分配 wasd（W=40%，A/S/D 各 20%）。

## 3. 坐标系（page 3）

- **左手坐标系**
- Character: right=x, up=y, front=z
- Camera 同样左手坐标系
- **Pitch (俯仰)**: 绕 X 轴, 以 z 轴 0°, 抬头+/低头-, 区间 [-180, 180]
- **Yaw (偏航)**: 绕 Y 轴, 以 x 轴 0°, 左转-/右转+, 区间 [-180, 180]
- **Roll (翻滚)**: 绕 Z 轴, 以 y 轴 0°, 左倾+/右倾-, 区间 [-180, 180]
- 速度单位：m/s
- 世界坐标单位：m
- 录制规范：**按帧序列录制 30 fps**

## 4. 文件 1 — systeminfo.json（page 3-4）

仅 5 字段（不是我们 sample 多写的 7 字段）：

```json
{
  "gameProcessName": "ds",
  "x": 0, "y": 0,
  "width": 1920, "height": 1080,
  "recordDpi": 1.5
}
```

❌ **不要** `map_scale` 和 `map_bounds` — PDF 完全没提（sample 多加是错的）

## 5. 文件 2 — action_camera.json 字段权威清单（page 4-5）

**严格按 PDF 字面字段名**（不要"修正"任何拼音/空格/大写）：

```yaml
time:                       string ("YYYY-MM-DD HH:MM:SS.fff")
fps:                        float
frame:                      int
route_type:                 int (1/2/3)

# camera section
camera_position:            Vector3
camera_rotation_oula:       Vector3   ← 拼音 oula 不是 euler
camera_rotation_quaternion: Vector4   ← 数组 [x, y, z, w] 顺序 (验收 #6)
camera_Follow Offset:       Vector3   ← 字面带空格 + 大写 F
camera_intrinsics:          Object {fx, fy, Cx, Cy}  ← Cx/Cy 大写 (per PDF)
camera_speed:               Vector3 m/s

# character section
player_position:            Vector3
mouse_x, mouse_y:           float (归一化 [0, 1])
mouse_dx, mouse_dy:         float (归一化, 相对位置)
keyCode:                    int (按 ASCII 码表，W=87)
player_rotation_oula:       Vector3   ← 拼音 oula
player_rotation_quaternion: Vector4   ← 数组 [x, y, z, w]
player_speed:               Vector3 m/s
metric_scale:               Float (物理尺度)
```

**注意 keyCode**：PDF page 5 写 type=int（标量），但实际多键并按怎么处理 PRD 没明说。
现实选 list[int]（合理扩展）— 但需要和买家确认。Sample 当前用 `[87]` 单元素 list 是稳妥折中。

## 6. 文件 3 — gameinfo.xlsx（page 6）

PDF 在 page 12 才写 gameinfo 模板细节（场景资源 + 爬坡表 + 路线规划等），不是简单 14 字段表格。
我们的 14 字段（game_name/version/platform/scene/...）是**简化的近似**——按 page 12-14 真实模板还要加资产爬坡表 / 路线规划。

## 7. 文件 4 — 深度图 (page 7)

- 类型：**View-space linear depth**（相机坐标系下沿光轴方向的 Z 距离）
- 数据精度：float32
- 单位：米 (meters)
- 文件格式：EXR (OpenEXR, **单通道**)
- 无效像素标记：天空、超出远平面、无效深度的像素 → **统一标记为 0**（整个数据集一致）
- 抽帧要求：**每秒 6 张均匀抽帧**（不要连续帧）
- 与 video 同步导出，命名规则与视频时间戳对应

## 8. 验收金标准（page 8 + 11-12）

### Video 文件
- 时长 > 300s
- 分辨率 1920×1080
- fps 30
- 不要：游戏 logo / 弹窗 / 系统通知 / 水印 / 模态对话框 / 黑边 / 马赛克 / 闪屏

### Action_camera 8 项 check（PDF page 11-12）
1. 是否存在字段缺失及格式不正确
2. **坐标系对齐**：camera 与 player 的 location/rotation 与我方内部定义完全一致
3. **输入映射正确性**：鼠标 dx, dy 方向是否弄反，数值变化与镜头移动精准对齐
4. **帧率连续性**：排查 frame 是否重复或跳帧
5. **键盘事件一致性**：keyCode 触发时机与画面动作完全一致，按 ASCII 码表（W=87）
6. **四元数顺序**：导出数组顺序严格 `[x, y, z, w]`
7. **物理阈值限制**：speed 数值符合物理逻辑
8. **camera_intrinsics fx=fy**

### 验收策略（page 7）
- 每天每场景**随机抽 2 条做 video+json 校验**
- 2 条均不通过 → 整个场景包打回
- video 校验抽 2-5%，通过率 < 90% → 整场景打回
- 90-100% → 补足缺失部分（120 条 95% 通过 = 缺 5%×120 = 6 条 → 补 6 条）

## 9. Iron laws (我从 PDF 提取的死线)

1. **PDF 字面字段名是权威** — `camera_Follow Offset` 必须带空格+大写 F；`*_oula` 不是 `*_euler`
2. **Quaternion 数组 [x,y,z,w]** 不是 dict（验收 #6 明文）
3. **Vector3 数组**（PDF page 11 JSON snippet 用数组）
4. **左手坐标系** right=x, up=y, front=z
5. **欧拉角范围 [-180, 180]**
6. **30 fps 视频，6 fps 深度抽帧**
7. **systeminfo 5 字段** — 多的 (map_scale/map_bounds) 是 sample 错加
8. **路线 50/50 分配** — 50% 常规 + 50% wasd 均匀（W=40, A/S/D=20）
9. **画面禁忌列表** — 任何 logo/弹窗/水印/UI overlay/Alt-Tab/黑边都打回
10. **keyCode = ASCII** — W=87 必须对得上
