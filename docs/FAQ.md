# R012 · Vendor 常见问题文档

> 本文档解答数据采集供应商（vendor）在启动、录制、数据处理、上传及计费合作中的常见问题。

---

## 目录

- [启动与环境](#1-启动与环境)
- [录制与采集](#2-录制与采集)
- [数据格式与验收](#3-数据格式与验收)
- [上传与提交](#4-上传与提交)
- [计费与合作](#5-计费与合作)

---

## 1. 启动与环境

### Q1: 必须用 Minecraft 吗？

不一定。Minecraft 是推荐的游戏 stack，但**任何符合 PRD 规范的游戏或模拟器均可使用**。核心要求是：

- 支持 1920×1080 分辨率输出
- 可输出 depth buffer（通过 Unity RenderDoc / Unreal Engine G-buffer / DepthAnything 推断）
- 可录制 30 fps 稳定帧率

常见替代方案：Unity Demo Scenes、Unreal Engine 官方样例、Roblox 自定义地图等。

> **相关文档**：[PRD.md](./PRD.md) §2.1

---

### Q2: 必须用 OBS 吗？

不必须。OBS 是最通用的选择，但以下录制方案均可：

| 方案 | 平台 | 特点 |
|------|------|------|
| NVIDIA ShadowPlay | Windows + NVIDIA GPU | 最低延迟，硬件编码 |
| AMD ReLive | Windows + AMD GPU | 类似 ShadowPlay |
| SwitchBoard | macOS | Apple Silicon 原生支持 |
| OBS | 全平台 | 开源，可自定义场景 |

**推荐**：Windows 用户优先使用 ShadowPlay（性能开销最小），macOS 用户使用 SwitchBoard。

> **相关文档**：[RECORDING_SETUP.md](./RECORDING_SETUP.md)

---

### Q3: 没 GPU 能跑 DepthAnything 吗？

可以跑，但**性能会显著下降**（CPU 模式比 GPU 慢 5-10x）。

**可行方案**：

1. **CPU 推断**：安装 DepthAnything V2 CPU 版本，帧率约 3-5 fps
2. **Unity G-buffer**：使用 Unity 引擎直接输出 depth texture，省去后处理步骤
3. **轻量模型**：DepthAnything-Small 可降低显存需求

**建议配置**：至少 RTX 3060 或同等性能 GPU，确保 30 fps 录制流畅。

> **相关链接**：[DepthAnything GitHub](https://github.com/DepthAnything/Depth-Anything-V2)

---

### Q4: SOP.sh 报错怎么办？

按以下步骤排查：

```bash
# 1. 运行自检脚本
./bin/doctor.sh

# 2. 查看详细日志
ls -la logs/
cat logs/latest.log

# 3. 检查依赖
./bin/check_deps.sh
```

常见错误：

- `Java not found` → 确保 JAVA_HOME 指向 JDK 21
- `OBS not running` → 启动 OBS 并配置好场景
- `S3 credentials invalid` → 检查 `~/.aws/credentials`

> **相关文档**：[TROUBLESHOOTING.md](./TROUBLESHOOTING.md)

---

### Q5: Java 装哪个版本？

**必须使用 JDK 21**。

**安装方式**：

```bash
# macOS (Homebrew)
brew install openjdk@21
export JAVA_HOME=$(brew --prefix)/opt/openjdk@21

# Linux (SDKMAN!)
curl -s "https://get.sdkman.io" | bash
sdk install java 21.0.2-tem

# Windows
# 下载 OpenJDK 21 MSI 安装包
```

> **注意**：JDK 17 及以下版本不兼容部分依赖库。

---

### Q6: 没有 Mojang 账号能跑吗？

可以。**支持 offline mode（离线模式）启动**。

```bash
# 启动命令示例（offline mode）
java -jar minecraft_server.jar --offline --username VendorPlayer
```

离线模式下：

- 玩家名可自定义（不影响数据采集）
- 无需正版认证
- 同样生成 action_camera.json 和 gameinfo.xlsx

> **注意**：部分服务器可能要求正版验证，请使用单人模式或配置允许离线登录的服务器。

---

### Q7: WSL2 Linux 能跑吗？

可以。**推荐 Windows 用户使用 WSL2 + Ubuntu 22.04**。

**配置步骤**：

```bash
# 1. 安装 WSL2
wsl --install -d Ubuntu-22.04

# 2. 安装依赖
sudo apt update
sudo apt install openjdk-21 python3 ffmpeg

# 3. 挂载 Windows GPU（可选，用于 DepthAnything）
# 确保 NVIDIA 驱动支持 WSL2
nvidia-smi  # 验证 GPU 访问
```

**注意事项**：

- WSL2 性能接近原生 Linux
- OBS 可通过 `obs-linuxbrowser` 或 Windows 端 OBS + 虚拟摄像头运行
- 建议将项目文件放在 WSL2 文件系统内（`/home/`）

---

### Q8: macOS Apple Silicon 性能够吗？

**M2 及以上芯片完全足够**。

**性能参考**：

| 芯片 | DepthAnything | 录制 | 备注 |
|------|---------------|------|------|
| M1 | 可跑，稍慢 | 30 fps 稳定 | 推荐 M1 Pro+ |
| M2 | 流畅 | 30 fps 稳定 | 推荐配置 |
| M3 | 流畅 | 30 fps 稳定 | 最佳 Apple Silicon |

**推荐配置**：

- 16GB+ 统一内存（DepthAnything 占用约 4-8GB）
- SwitchBoard 录制（原生支持 Metal 加速）
- macOS 13+ 系统

---

## 2. 录制与采集

### Q9: 操作员要会编程吗？

**不需要**。SOP.sh 设计为**一行命令**启动：

```bash
# 完整录制流程
./SOP.sh --game minecraft --duration 300 --output ./output/clip_001
```

操作员只需：

1. 按下启动按钮
2. 进行自由探索操作
3. 录制结束后停止

所有数据格式转换、lint 检查、上传脚本均可自动化运行。

> **相关文档**：[SOP.md](./SOP.md)

---

### Q10: 录制中崩溃了怎么办？

**直接丢弃该 clip，重新录制**。

我们**不提供片段拼接服务**，原因：

1. 拼接处容易产生帧跳变，影响数据连贯性
2. 时间戳连续性无法保证
3. 增加数据清洗复杂度

**处理流程**：

```
崩溃检测 → 标记 clip_id 为 FAILED → 记录原因 → 重新录制新 clip
```

> **提示**：建议每次录制前保存游戏状态（save world），便于快速重开。

---

### Q11: 怎么保证 30 fps 稳定？

**核心原则**：关闭 V-Sync，关闭后台应用，使用足够显卡。

**操作步骤**：

1. **游戏内设置**
   - 关闭 V-Sync
   - 锁定 30 fps 上限（或无上限）
   - 图形质量设为中低

2. **系统设置**
   - 关闭不必要的后台进程（浏览器、聊天软件）
   - 关闭 Windows 游戏模式
   - 确保无录屏软件冲突

3. **硬件建议**
   - RTX 3060 或同等以上
   - 16GB RAM
   - SSD 存储（避免 IO 瓶颈）

**验证命令**：

```bash
# 录制中监控帧率
./bin/monitor_fps.sh
```

---

### Q12: 操作员要做什么动作？

**自由探索，保持 WASD 平衡**。

**推荐分布**：

| 按键 | 占比 | 说明 |
|------|------|------|
| W（前进） | 40% | 主要探索方向 |
| A（左移） | 20% | 侧向移动 |
| S（后退） | 20% | 调整位置 |
| D（右移） | 20% | 侧向移动 |

**禁止动作**：

- 静止不动（stationary > 10% 拒收）
- 单一方向持续移动
- 频繁视角切换
- 战斗、死亡、跳跃等特殊事件

> **相关文档**：[ACTION_GUIDE.md](./ACTION_GUIDE.md)

---

### Q13: 战斗 / 死亡 / 切换视角算违规吗？

**全部违规，会直接拒收**。

**违规类型**：

| 违规行为 | 后果 |
|----------|------|
| 战斗（攻击生物/玩家） | 拒收 |
| 死亡 | 拒收 |
| 视角快速切换（转头过快） | 拒收 |
| 使用传送/瞬移 | 拒收 |
| 切换游戏模式 | 拒收 |

**原因**：这些行为会产生异常 camera 数据，影响模型训练质量。

---

### Q14: 一个 clip 必须正好 5 分钟吗？

**5-6 分钟均可，超出范围直接拒收**。

**要求**：

- 最短：5 分钟（300 秒）
- 最长：6 分钟（360 秒）
- 目标：5 分 30 秒（330 秒）为最佳

**帧数计算**：

```
30 fps × 300 秒 = 9000 帧（最短）
30 fps × 360 秒 = 10800 帧（最长）
```

> **注意**：不足 5 分钟或多于 6 分钟的 clip 都会被 lint 标记为 FAILED。

---

### Q15: 屏幕分辨率必须 1080p 吗？

**是，必须严格 1920×1080**。

**设置步骤**：

```bash
# OBS 设置
分辨率: 1920×1080
帧率: 30 fps
输出编码: H.264
```

**常见问题**：

- 2560×1080（超宽）→ 拒收
- 1280×720（720p）→ 拒收
- 动态分辨率 → 拒收

> **注意**：部分游戏默认非 1080p，请务必在设置中强制锁定。

---

## 3. 数据格式与验收

### Q16: action_camera.json 字段顺序重要吗？

**不重要，但必须包含全部 20 个字段**。

**必需字段列表**：

```json
{
  "clip_id": "clip_001",
  "timestamp": 1699999999.123,
  "position": [x, y, z],
  "rotation": [pitch, yaw, roll],
  "quaternion": [x, y, z, w],
  "fov": 70.0,
  "velocity": [vx, vy, vz],
  "frame_index": 0,
  ...
}
```

**要求**：

- 字段可以任意顺序
- 缺失任何字段 → 拒收
- 多余字段 → 警告（但不拒收）

> **相关文档**：[DATA_SCHEMA.md](./DATA_SCHEMA.md)

---

### Q17: 四元数顺序怎么排？

**[x, y, z, w] 顺序**。

**示例**：

```json
{
  "quaternion": [0.1, 0.2, 0.3, 0.9]
}
```

**常见错误**：

- `[w, x, y, z]` 顺序 → 拒收
- `[x, y, z, w]` 但数值错误 → 拒收

**转换公式**（如使用其他库）：

```
qw = cos(θ/2)
qx = axis.x * sin(θ/2)
qy = axis.y * sin(θ/2)
qz = axis.z * sin(θ/2)
```

---

### Q18: 坐标系是左手还是右手？

**左手坐标系**。

**轴向定义**：

| 轴 | 方向 |
|----|------|
| +X | 右（Right） |
| +Y | 上（Up） |
| +Z | 前（Forward） |

**Unity/Unreal 兼容**：

- Unity：默认左手坐标系 ✓
- Unreal：默认左手坐标系 ✓
- Three.js：可选左手/右手，需配置

> **注意**：部分 Python 库默认右手坐标系，输出前需转换。

---

### Q19: depth EXR 必须 6 fps 吗？

**是，6 fps，5 分钟视频对应 1800 帧**。

**计算公式**：

```
5 分钟 = 300 秒
300 秒 × 6 fps = 1800 帧
```

**输出要求**：

- 格式：EXR（16 位浮点）
- 分辨率：1920×1080
- 帧率：6 fps（与 RGB 视频同步）
- 命名：`depth_000000.exr`, `depth_000001.exr`, ...

**常见错误**：

- 30 fps depth → 拒收（数据量过大）
- 1 fps depth → 拒收（时间插值不准）
- PNG/JPG 格式 → 拒收

---

### Q20: gameinfo.xlsx 字段不全怎么办？

**必须 14 字段全齐，缺一拒收**。

**必需字段**：

| # | 字段名 | 示例 |
|---|--------|------|
| 1 | clip_id | clip_001 |
| 2 | game_name | Minecraft |
| 3 | game_version | 1.20.4 |
| 4 | recording_date | 2024-01-15 |
| 5 | duration_sec | 330 |
| 6 | resolution | 1920x1080 |
| 7 | fps | 30 |
| 8 | depth_fps | 6 |
| 9 | operator_id | OP001 |
| 10 | hardware_spec | RTX 3060, 32GB RAM |
| 11 | os | Windows 11 |
| 12 | start_time | 10:00:00 |
| 13 | end_time | 10:05:30 |
| 14 | notes | - |

**处理方式**：

- 缺失字段 → lint 失败，标记为 FAILED
- 手动补全后重新提交

---

### Q21: lint 失败常见原因？

**四大常见原因**：

| 原因 | 说明 | 解决方案 |
|------|------|----------|
| stationary > 10% | 静止帧过多 | 增加移动频率 |
| WASD 失衡 | 某方向占比过高 | 均衡按键分布 |
| fx ≠ fy | 焦距不一致 | 检查相机参数 |
| 帧不连续 | 丢帧或跳帧 | 检查录制稳定性 |

**详细说明**：

1. **stationary > 10%**
   - 定义：位置变化 < 0.1 米的帧数占比
   - 阈值：≤ 10%
   - 解决：保持持续移动

2. **WASD 失衡**
   - 定义：W/A/S/D 按键占比偏离推荐值
   - 阈值：各 20-40% 范围内
   - 解决：均衡操作

3. **fx ≠ fy**
   - 定义：水平/垂直焦距不相等
   - 原因：非标准相机设置
   - 解决：使用标准 FOV 设置

4. **帧不连续**
   - 定义：帧号不连续或缺失
   - 原因：录制卡顿/丢帧
   - 解决：检查硬件性能

---

### Q22: 我能自己写 lint 吗？

**可以，但必须通过 oyster-buyer's lint 检查**。

**架构说明**：

```
┌─────────────────┐
│  Vendor Lint    │  ← 你可以自定义
│  (可选)         │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ oyster-buyer    │  ← 必须通过
│ lint            │
└────────┬────────┘
         │
         ▼
    验收通过
```

**要求**：

1. 你的 lint 必须检测基本格式（JSON 完整性、字段存在性）
2. oyster-buyer lint 会进行深度检查（数据分布、时序连续性）
3. 两者都 PASS 才算验收通过

**示例自定义 lint**：

```python
# custom_lint.py
import json

def check_camera_json(path):
    with open(path) as f:
        data = json.load(f)
    
    required_fields = ['clip_id', 'quaternion', 'position']
    for field in required_fields:
        if field not in data:
            return False, f"Missing field: {field}"
    
    return True, "OK"
```

---

## 4. 上传与提交

### Q23: S3 上传太慢怎么办？

**使用 bin/upload_s3.sh，支持 multipart 上传和断点续传**。

**脚本特性**：

- 自动 multipart 分片（每片 100MB）
- 失败自动重试（最多 3 次）
- 断点续传（记录已上传分片）
- 适配低带宽（限速 200 Kbps）

**使用方式**：

```bash
# 基本用法
./bin/upload_s3.sh ./output/clip_001

# 限速上传（适配 200 Kbps 带宽）
./bin/upload_s3.sh ./output/clip_001 --rate-limit 200

# 强制重试
./bin/upload_s3.sh ./output/clip_001 --retry
```

**性能优化**：

- 使用 AWS S3 Transfer Acceleration
- 选择最近区域（ap-northeast-1）
- 压缩小文件（tar + gzip）

---

### Q24: SFTP 怎么用？

**我们提供 vendor 专属账号 + chroot 隔离**。

**获取账号**：

1. 联系管理员获取 SFTP 账号
2. 接收凭据（用户名、密码、密钥）

**连接方式**：

```bash
# 命令行连接
sftp -P 2222 vendor@upload.example.com

# FileZilla 配置
主机: sftp://upload.example.com
端口: 2222
用户名: vendor_xxx
密码: ********
```

**目录结构**：

```
/home/vendor/
├── incoming/        # 上传目录
├── processed/       # 已处理
└── rejected/        # 拒收 clip
```

> **注意**：chroot 环境下无法访问上级目录，确保在 `incoming/` 内操作。

> **相关文档**：[SUBMISSION_FORMAT.md](./SUBMISSION_FORMAT.md) §3.2

---

### Q25: manifest.yaml 必须每批都生成吗？

**是，每次提交都必须生成 manifest.yaml**。

**生成方式**：

```bash
# 自动生成
./bin/generate_manifest.py --input ./output/batch_001 --output manifest.yaml
```

**manifest.yaml 内容**：

```yaml
version: "1.0"
batch_id: "batch_001"
generated_at: "2024-01-15T10:00:00Z"
clips:
  - clip_id: "clip_001"
    size_mb: 2048
    md5: "abc123..."
    status: "uploaded"
  - clip_id: "clip_002"
    size_mb: 2100
    md5: "def456..."
    status: "uploaded"
total_clips: 2
total_size_mb: 4148
```

**作用**：

- 追踪提交批次
- 校验数据完整性
- 便于问题溯源

---

### Q26: 部分上传失败怎么办？

**aws s3 sync 自动 retry，失败 clip 列表输出到 stderr**。

**处理流程**：

```bash
# 上传命令
./bin/upload_s3.sh ./output/batch_001

# 输出示例
[INFO] Uploading clip_001... OK
[INFO] Uploading clip_002... OK
[ERROR] Uploading clip_003 failed: network timeout
[ERROR] Uploading clip_005 failed: access denied

# 失败列表（stderr）
Failed clips:
- clip_003
- clip_005
```

**手动重试**：

```bash
# 仅重试失败项
./bin/upload_s3.sh ./output/batch_001 --retry-failed
```

**常见失败原因**：

- 网络超时 → 检查带宽，重试
- 权限不足 → 验证 IAM 策略
- 文件损坏 → 重新生成 clip

---

## 5. 计费与合作

### Q27: 单价怎么定？

**发送 capacity + stack capability 给邮箱，48 小时内回复 SOW**。

**需要提供的信息**：

| 信息 | 说明 |
|------|------|
| 每月产能 | 可提交的 clip 数量 |
| 硬件配置 | GPU 型号、内存、存储 |
| 游戏 stack | 使用的游戏/引擎 |
| 人员规模 | 可参与的操作员数量 |
| 地区 | 数据中心位置 |

**联系方式**：

- 邮箱：howard.linra@gmail.com
- 邮件标题：`[R012] Vendor Pricing Request - <公司名>`

**回复内容**：

- SOW（工作说明书）
- 单价报价
- 付款周期
- 合同条款

---

### Q28: 多久付款？

**30% 预付 + 70% 验收后 7 个工作日内**。

**付款流程**：

```
┌──────────────┐
│  签订合同    │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  30% 预付    │ ← 合同生效后 3 工作日内
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  交付数据    │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  验收通过    │ ← 7 工作日内完成
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  70% 尾款    │ ← 验收后 7 工作日内
└──────────────┘
```

**验收标准**：

- lint 全部通过
- 数据完整性校验
- 无违规 clip

---

### Q29: 拒收的 clip 我能重录吗？

**可以，免费重录，分配新 clip_id**。

**流程**：

1. 收到拒收通知（含原因）
2. 分析问题
3. 重新录制
4. 使用新 clip_id 提交

**示例**：

```
原 clip: clip_001 (拒收 - stationary 15%)
新 clip: clip_001_r1 (重录)
```

**免费重录规则**：

- 首次拒收 → 免费重录
- 二次拒收（同因）→ 协商处理
- 故意违规 → 终止合作

---

### Q30: 最低批量？

**单批 100-500 clip，月交付 1000+ 不限上限**。

**批量要求**：

| 批次 | 最低 | 最高 |
|------|------|------|
| 单批 | 100 clip | 500 clip |
| 月交付 | 1000 clip | 无上限 |

**说明**：

- 低于 100 clip 的批次可能不接收（视情况而定）
- 超过 500 clip 建议分批提交
- 月交付 1000+ clip 可谈批量优惠

**激励政策**：

- 连续 3 个月交付 1000+ clip → 单价折扣 5%
- 交付质量 > 95% 通过率 → 单价折扣 3%
- 提前完成年度目标 → 年度奖金

---

## 还没回答的问题？

如有本文档未涵盖的问题，请通过以下方式联络：

- **邮箱**：howard.linra@gmail.com
- **WhatsApp**：+86-xxx-xxxx-xxxx（请先邮件预约）

我们会尽快回复，并在 FAQ 中补充常见问题。

---

*文档版本：v1.0 | 最后更新：2024-01-15*
