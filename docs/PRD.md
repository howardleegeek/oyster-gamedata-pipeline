# 游戏画面 + 动作 + 相机数据采集 PRD v1.0

> **版本**: 1.0  **发布日期**: 2026-05-02  **甲方**: Oysterworld INC
> **状态**: ✅ 已发布,vendor 收到即可报价 + 启动第一批
> **联络人**: Howard Li · howard.linra@gmail.com · +1 (341) 250-6526
> **GitHub 参考工程**: <https://github.com/howardleegeek/oyster-gamedata-pipeline>

---

## 0. 文档说明

本文档是**乙方(数据采集 vendor)的执行手册**,而非内部技术文档。
读完即可:
- 给出量产报价(单价 / 月产能)
- 选定技术栈并启动第一批样品
- 走通"录制 → 打包 → 提交 → 验收"完整闭环

**配套技术参考**(读完 PRD 后再深入):
- [`BUYER_SPEC_V1.md`](BUYER_SPEC_V1.md) — 字段级技术规格(谁字段 / 什么类型 / 边界值)
- [`VENDOR_ONBOARDING.md`](VENDOR_ONBOARDING.md) — 一台机器从零到产出第一个样品的 8 步 SOP
- [`SUBMISSION_FORMAT.md`](SUBMISSION_FORMAT.md) — tarball 命名规则 / 上传方式 / 自助校验

---

## 1. 项目背景与目标

### 1.1 业务背景
训练**交互式世界模型(Interactive World Model)** —— 给定一段游戏画面 + 玩家动作历史,模型预测下一帧画面。这种模型可应用于:
- 游戏 AI / NPC 行为生成
- 机器人仿真训练数据
- 空间计算 / VR 场景生成

### 1.2 数据用途
本批次数据用于训练**视觉 + 动作联合编码模型**,关键质量诉求:
- 画面与动作必须**精确同步**(≤ 20ms 延迟)
- 玩家行为必须**多样**(不能全是站着不动 / 不能全是 W 前进)
- 路径必须**贴近真实玩家**(不是巡逻脚本 / 不是 demo 录像)
- 深度信息**真实**(不是 placeholder),用于训练 3D 表征

### 1.3 商业目标
| 指标 | 目标值 |
|---|---|
| 第一批交付 | 100 clip(每 clip ≈ 5 分钟) |
| 月产能 | 1000-3000 clip / 月 |
| 总目标 | 50,000 clip(2026 年内) |
| 验收通过率 | ≥ 90 %(低于 80 % 重做不计费) |

---

## 2. 甲乙方界面

### 2.1 甲方(我们)负责
- ✅ 提供完整技术规格(本 PRD)
- ✅ 提供参考工程代码(GitHub repo,MIT 许可)
- ✅ 提供自动验收脚本(乙方可在本地预跑)
- ✅ 提供测试样品(`samples/buyer-spec-v1-rc1.tar.gz`)
- ✅ 7×24 技术支持(Slack / 微信群)
- ✅ 预付 30 % 启动金,验收后结算余款

### 2.2 乙方(vendor)负责
- ✅ 招募 / 调度录制人员
- ✅ 提供采集机器(Windows / macOS / Linux 任选)
- ✅ 按 PRD 录制 + 整理 + 打包 + 提交
- ✅ 在自助验收脚本通过的情况下提交
- ✅ 失败 clip 重录(不计入扣费)

### 2.3 不在合作范围
- ❌ 我们不提供录制人员
- ❌ 我们不提供采集机器
- ❌ 我们不报销网络 / 电费 / 软件许可
- ❌ 我们不接受手工编辑 / 后期合成的画面

---

## 3. 交付物规格(4 件套)

每份交付物对应**一段 5–6 分钟的录制**,包含 4 个文件,打包成单个 `.tar.gz`:

```
<clip_id>/
├── video.mp4              # 5–6 min, 1920×1080, 30 fps, H.264/H.265
├── action_camera.json     # 每帧 20 字段动作+相机 telemetry
├── gameinfo.xlsx          # 操作员填写的元数据(场景/天气/角色等)
└── depth/                 # 深度图(6 fps 采样)
    ├── 000000.exr
    ├── 000001.exr
    └── ...
```

| # | 文件 | 必需 | 大小估计 | 说明 |
|---|---|---|---|---|
| 1 | `video.mp4` | ✅ | 200-500 MB | 真实游戏画面录制 |
| 2 | `action_camera.json` | ✅ | 5-15 MB | JSON 数组,每帧一条记录 |
| 3 | `gameinfo.xlsx` | ✅ | < 100 KB | 单 sheet,字段见 §3.3 |
| 4 | `depth/*.exr` | ✅ | 300-800 MB | OpenEXR float32 单通道 Z |

**单 clip 总大小**: 0.5–1.5 GB(取决于场景复杂度)

### 3.1 video.mp4 硬指标
- **时长**: 5 ≤ x ≤ 6 分钟(过短或过长直接拒收)
- **分辨率**: 1920×1080(系统全屏 + 游戏窗口都必须)
- **帧率**: 30 fps **稳定**(不能动态 FPS / 不能 60 fps 降采样)
- **延迟**: 玩家动作到画面响应 ≤ 20 ms
- **声音**: 必须存在 + 连续 + 无外界噪声 + 无 NPC 对话刷屏
- **编码**: H.264 (默认) 或 H.265 (允许), CRF ≤ 23, AAC audio
- **禁止**: UI 弹窗 / 任务面板 / 背包打开 / 第一↔第三人称切换 / 死亡重生

### 3.2 action_camera.json 字段(20 个)
完整字段表见 [`BUYER_SPEC_V1.md`](BUYER_SPEC_V1.md#action_camerajson--20-fields-per-frame)。摘要:

```json
{
  "frame": 0,
  "time": "2026-05-02 15:30:45.000",
  "fps": 30.0,
  "route_type": 1,
  "mouse_x": 0.5, "mouse_y": 0.5,
  "mouse_dx": 0.01, "mouse_dy": -0.02,
  "keyCode": [87],
  "camera_position": {"x": 100.0, "y": 64.0, "z": 200.0},
  "camera_rotation_oula": {"x": 0.0, "y": 90.0, "z": 0.0},
  "camera_rotation_quaternion": {"x": 0, "y": 0.707, "z": 0, "w": 0.707},
  "camera_Follow Offset": {"x": 0.0, "y": 1.6, "z": 0.0},
  "camera_intrinsics": {"fx": 960.0, "fy": 960.0, "cx": 960.0, "cy": 540.0},
  "camera_speed": {"x": 0.0, "y": 0.0, "z": 4.317},
  "player_position": {"x": 100.0, "y": 64.0, "z": 200.0},
  "player_rotation_oula": {"x": 0.0, "y": 90.0, "z": 0.0},
  "player_rotation_quaternion": {"x": 0, "y": 0.707, "z": 0, "w": 0.707},
  "player_speed": {"x": 0.0, "y": 0.0, "z": 4.317},
  "metric_scale": 1.0
}
```

**关键约束**:
- `fx == fy`(必须,intrinsics 必为针孔模型)
- 四元数顺序: `[x, y, z, w]`,模长 ≈ 1
- 角度范围: pitch [-90, 90], yaw/roll [-180, 180]
- 坐标系: **左手系**(右 +X / 上 +Y / 前 +Z)
- 速度单位: m/s 每轴
- frame 必须连续(0,1,2,...,N-1 无间断)

### 3.3 gameinfo.xlsx 字段(单 sheet)
| 字段 | 类型 | 示例 | 说明 |
|---|---|---|---|
| game_name | string | Minecraft | 游戏名 |
| game_version | string | 1.20.4 | 版本号 |
| platform | string | Java Edition | 平台 |
| scene_name | string | flat-overworld | 场景标识 |
| weather | string | clear | 天气 |
| time_of_day | string | day | 时段 |
| character_name | string | DataPilot | 角色名 |
| character_class | string | spectator | 职业/模式 |
| operator_id | string | vendor-001-op-A | 录制人 |
| recording_date | string | 2026-05-02 | 录制日 |
| total_frames | int | 9000 | 总帧数 |
| video_duration_sec | float | 300.0 | 视频时长 |
| route_type | int | 1 | 主路径类型 |
| notes | string | 平地探索 | 操作员备注 |

### 3.4 depth/*.exr 规格
- **采样**: 6 fps(每秒 6 帧,**不是 30**) → 5min × 6fps = **1800 帧**
- **格式**: OpenEXR,单通道命名 `Z`
- **数据类型**: float32
- **单位**: 米(线性深度,沿光轴 Z)
- **无效像素**(天空 / 透明 / 裁剪外): 0
- **文件名**: 与视频时间戳对齐 → `000000.exr`(t=0s) → `000005.exr`(t=0.83s) → ...

---

## 4. 路径多样性要求

### 4.1 路径类型(route_type)
| 类型 | 名称 | 描述 | 占比要求 |
|---|---|---|---|
| 1 | normal | 自然玩家行为(随机走 + 看周围) | 50 % |
| 2 | special | 贴墙 / 贴地 / 极端角度 / 跳跃 | 25 % |
| 3 | loop | 最后 10 秒回到起点 | 25 % |

### 4.2 输入分布要求
**每批 clip 必须满足**:
- 50 % "normal" clip:按真实玩家自由行动
- 50 % "wasd_balanced" clip:严格按 W=40 % / A=20 % / S=20 % / D=20 % 输入

**站立时间 ≤ 10 %**(超过 10 % 全帧不动直接拒收)

**每秒至少有一个动作**(包括视角转动)

### 4.3 禁止行为
- ❌ 战斗 / 砍怪 / 抓宠
- ❌ 打开背包 / 菜单 / 地图
- ❌ 鼠标滚轮缩放
- ❌ 与 NPC 对话(对话框直接拒收)
- ❌ 死亡 / 重生 / 切场景(必须在同一区域 ≤ 30 分钟)
- ❌ 同一 clip 内画面**冻结 ≥ 2 秒**(loading 都算)

---

## 5. 录制方法论

### 5.1 推荐技术栈(我们已验证)
**Minecraft Java Edition 1.20.4 + OBS Studio + DepthAnything V2** —— 我们提供完整参考工程:

| 组件 | 用途 | 说明 |
|---|---|---|
| Minecraft Java 1.20.4 | 游戏端(spectator gamemode) | offline 模式即可,不需 Mojang 付费账号 |
| Paper 1.20.4 | 服务端(localhost:25565) | 提供平地世界 + RCON 控制 |
| Mineflayer | Headless bot 行为驱动 | Node.js, 我们提供 ScriptedProvider |
| OBS Studio + WebSocket v5 | 录屏 + 录音 + H.264 编码 | obs-studio 30+, websockets 库控制 |
| DepthAnything V2 Small | 深度推理 | HuggingFace, fp16 模式 GPU/M-series 可跑 |

**全套代码已开源**: <https://github.com/howardleegeek/oyster-gamedata-pipeline>

### 5.2 替代技术栈(乙方自选)
只要满足 PRD 验收即可使用其他技术栈,例如:
- **CS2 / Valorant** spectator 模式 + 自动 demo replay
- **GTA V** 自由探索模式 + ScriptHook + 输入回放
- **BeamNG.drive** drive mode + 内置 telemetry export
- **Unity / Unreal 自建场景** + DepthAnything V2 推理

**乙方需自行验证**:
- 画面 = 真实游戏画面(非 placeholder / 非合成)
- 输入 = 真实键盘鼠标(非脚本注入到 game state)
- 深度 = 真实推理或 game engine 直接输出(非 placeholder)

### 5.3 录制硬件最低要求
| 资源 | 最低 | 推荐 |
|---|---|---|
| OS | Windows 10 / macOS 13 / Ubuntu 22.04 | Windows 11 / macOS 14+ |
| CPU | 4-core 3.0GHz | 8-core 3.5GHz+ |
| RAM | 16 GB | 32 GB |
| GPU | GTX 1660 / Apple M1 | RTX 3060 / Apple M2 Pro+ |
| 硬盘 | 1 TB SSD(每月) | 4 TB SSD |
| 网络 | 50 Mbps 上行 | 200 Mbps 上行 |

**单台机器月产能估算**: 100-300 clip(取决于场景复杂度 + 操作员熟练度)

---

## 6. 验收标准

### 6.1 自动验收(必须 100 % 通过)
乙方在提交前必须本地跑过这一步,**没通过的 tarball 不要提交**:

```bash
# 安装我方工具
git clone https://github.com/howardleegeek/oyster-gamedata-pipeline.git
cd oyster-gamedata-pipeline && bash SOP.sh

# 验收单个 clip
oyster-buyer-lint <your_clip>.tar.gz
```

输出 `PASS` 才能提交,任何 `FAIL` 自行修复重打包。

### 6.2 人工抽查(每批 5 % 抽样)
我方每批抽 5 %(最少 5 个)进行人工 review,检查:
- 画面是否真实(无 placeholder / 无 testsrc)
- 路径是否多样(无重复巡逻)
- 输入是否真实(操作员真在操作,不是脚本)
- 声音是否正常(不是死循环 BGM)

**抽查通过率 < 80 %**: 整批拒收 + 不计费

### 6.3 八项 action_camera 校验(每帧)
1. 字段无缺失,类型正确
2. 坐标对齐左手系
3. mouse_dx/dy 方向与相机运动一致
4. 帧无跳帧无重复
5. keyCode 时序与可视动作一致
6. 四元数顺序 `[x, y, z, w]`
7. 速度数值合理(无超光速 / 无负无穷)
8. `fx == fy`(camera_intrinsics)

### 6.4 视频质量验收(每 clip)
- ≥ 5 ≤ 6 分钟
- 30 fps 稳定
- 1920×1080
- 无 UI / 无 logo / 无对话框
- 可视 NPC ≤ 2
- 场景流畅(无 portal cut)
- 无 1↔3 人称切换
- 无死亡 / 重生

---

## 7. 提交方式

### 7.1 命名规范
```
<vendor_id>_<batch_id>_<clip_id>_v<spec_version>.tar.gz

例:
vendor-001_batch-2026-05-A_clip-00042_v1.tar.gz
```

### 7.2 上传方式
**默认(推荐)**: 上传到我方 S3 桶(预签名 URL,我方启动后给乙方)
```
s3://oysterworld-gamedata-vendor-uploads/<vendor_id>/<batch_id>/
```

**备选**: 我方提供 SFTP 账号,scp 上传:
```bash
scp clip-00042_v1.tar.gz vendor@upload.oysterworld.dev:/uploads/
```

**备选 2**: 阿里云 OSS bucket(适用国内 vendor)

### 7.3 提交频率
- **建议**: 每天上传(完成多少传多少)
- **要求**: 每周一次完整批次提交
- **批次大小**: 100-500 clip / 批

### 7.4 提交清单(每批)
```yaml
batch_id: vendor-001_batch-2026-05-A
vendor_id: vendor-001
total_clips: 200
upload_date: 2026-05-09
operator_list:
  - vendor-001-op-A: 80 clips
  - vendor-001-op-B: 70 clips
  - vendor-001-op-C: 50 clips
manifest_sha256: a3f5b2...c9
notes: "Minecraft 平地世界 1.20.4, day-clear weather"
```

---

## 8. 量产计划与时间表

| 阶段 | 时间窗 | 交付目标 | 单价 |
|---|---|---|---|
| **样品验收** | 启动后 7 天内 | 5-10 clip | 免费(预付金支付) |
| **小批量** | 第 2-4 周 | 100 clip | 协商单价 |
| **正式量产** | 第 5 周起 | 1000-3000 clip / 月 | 协商单价(量大优惠) |
| **专题批次** | 不定期 | 特定场景 / 特定路径 | 协商单价(+ 加价) |

**单价方向**(具体 vendor 报价后协商):
- 普通 clip(随机场景 + 普通路径): $X / clip
- 专题 clip(特定场景 + special / loop 路径): $1.5 X / clip
- 加急 clip(48 小时内交付): $2 X / clip

---

## 9. 法律与版权

### 9.1 数据所有权
- 录制内容版权归 **Oysterworld INC**
- 乙方不得对外公开 / 转售 / 用于其他训练
- 乙方录制完毕后 **30 天内**删除本地副本

### 9.2 游戏版权合规
- **Minecraft**: 我方提供 Mojang EULA 合规证明,乙方按我方 SOP 执行即可
- **其他游戏**: 乙方需自行确认 EULA 允许第三方录制(大多数游戏单机 / 玩家自录像合法)
- **禁止**: 任何破解客户端 / 未经授权服务器 / 私服 / 涉及付费内容

### 9.3 隐私
- 录制不得包含其他玩家用户名(本人除外)
- 录制不得包含真实姓名 / 邮箱 / IP 等 PII
- 操作员姓名仅以 `vendor-NNN-op-X` 编号方式记录

### 9.4 保密
- 本 PRD + 内部技术文档 **不得公开发布**
- 我方参考工程是 MIT 开源(可公开),但不要带本 PRD 一起转发
- 与外部讨论本项目时使用代号 **"GameData"**

---

## 10. 工具与示例工程

### 10.1 GitHub 参考工程
**主仓**: <https://github.com/howardleegeek/oyster-gamedata-pipeline>

**核心入口**:
- `SOP.sh` — 一键 onboarding(8 步,fresh clone 跑通)
- `bin/e2e_smoke.sh` — 端到端 smoke 测试
- `bin/produce_real_sample_v2.sh` — 真画面采集 orchestrator(全真版本)
- `samples/` — 我方放置的样品 tarball
- `docs/BUYER_SPEC_V1.md` — 字段级技术规格

### 10.2 子模块
仓库通过 git submodule 集成 3 个相关项目:
- `vendor/recorder/` — `gamedata-recorder`(Rust + OBS embedded)
- `vendor/input-logger/` — `gamedata-input-logger`(键盘鼠标 raw input)
- `vendor/enrichment/` — `oyster-enrichment`(后处理 / 元数据增强)

### 10.3 验收脚本
```bash
oyster-buyer-lint <tarball>           # 全套 lint
python3 bin/data_quality_report.py    # 质量报告
python3 bin/depth_exr_validator.py    # depth/*.exr 单独验
python3 bin/gameinfo_xlsx_validator.py # gameinfo 单独验
python3 bin/video_metadata_extractor.py # 视频元信息
```

---

## 11. Onboarding(8 步)

详细见 [`VENDOR_ONBOARDING.md`](VENDOR_ONBOARDING.md)。简版:

```bash
# 1. clone
git clone --recurse-submodules https://github.com/howardleegeek/oyster-gamedata-pipeline.git
cd oyster-gamedata-pipeline

# 2. 一键安装环境(自动装 java/python/openexr 依赖)
bash SOP.sh

# 3. 拿一个我方样品 tarball
ls samples/buyer-spec-v1-rc1.tar.gz

# 4. 跑 lint 验证装好了
oyster-buyer-lint samples/buyer-spec-v1-rc1.tar.gz

# 5. 跑 e2e smoke(产出第一个 placeholder bundle)
bash bin/e2e_smoke.sh

# 6. 跑全真采集(产出真画面 bundle)
bash bin/produce_real_sample_v2.sh

# 7. 用真采集流水线产出 1 个 clip(5 分钟,跑完拿到 .tar.gz)

# 8. 把这个 .tar.gz 上传到我方 S3 / SFTP / OSS
```

---

## 12. FAQ

**Q1: 必须用 Minecraft 吗?**
A: 不强制。任何能输出 1080p 30fps 真实画面 + 真实输入 + 真实深度的游戏 / 引擎都可以。我们只验收最终 4 件套。

**Q2: 必须用 OBS 吗?**
A: 不强制。任何录屏工具(NVIDIA ShadowPlay / AMD ReLive / FFmpeg / SwitchBoard)只要输出 1080p30 H.264 都可以。

**Q3: 没 GPU 能跑深度推理吗?**
A: DepthAnything V2 Small 在 Apple M2 / RTX 3060 都能跑。如果实在没有,我方可以提供 CPU 推理脚本(慢 5-10 倍)或允许 vendor 用游戏引擎自带深度通道(Unity G-Buffer / Unreal scene depth)直接输出。

**Q4: 操作员不会编程怎么办?**
A: SOP.sh 是一键脚本,会自动检测环境 + 装依赖 + 跑通 e2e。操作员只需会:
- 玩游戏(spectator 飞行)
- 打开终端复制粘贴一个命令
- 等录制完成上传

**Q5: 录制中断了怎么办?**
A: 中断的 clip 直接弃用,从头重录。我方不接收任何拼接 / 编辑过的 clip。

**Q6: 单价怎么定?**
A: 收到本 PRD 后请回邮件给 howard.linra@gmail.com,附上:
- 月产能估计(clip 数 / 月)
- 期望单价(USD / clip)
- 启动时间
- 团队规模(几个操作员 / 几台机器)
- 是否能跑全真技术栈(Minecraft + OBS + DepthAnything)

我方 48 小时内回复 + 签 SOW + 打 30 % 预付金。

**Q7: 出现 lint FAIL 但操作员看不出问题?**
A: 加我方 Slack / 微信群,工程师 ≤ 4 小时回复 + 远程协助。

**Q8: 数据上传速度太慢?**
A: 我方提供 S3 multipart + resume 上传脚本,适配 200 Kbps 网络也能传。

---

## 13. 联络

**Howard Li** · CEO, Oysterworld INC
- 📧 howard.linra@gmail.com(主)
- 📱 +1 (341) 250-6526(WhatsApp / iMessage)
- 💬 LinkedIn: <https://www.linkedin.com/in/connecthoward/>
- 🐙 GitHub: <https://github.com/howardleegeek>

**响应承诺**:
- 邮件: 24 小时内
- 紧急问题(production stop): 4 小时内
- 报价 + SOW: 48 小时内

---

## 附录 A: Spec 版本对照表
| 版本 | 日期 | 主要变更 |
|---|---|---|
| v1.0 | 2026-05-02 | 首发 |

## 附录 B: 文档索引
- [BUYER_SPEC_V1.md](BUYER_SPEC_V1.md) — 字段级技术规格(本 PRD §3 的展开)
- [VENDOR_ONBOARDING.md](VENDOR_ONBOARDING.md) — 8 步操作手册
- [SUBMISSION_FORMAT.md](SUBMISSION_FORMAT.md) — tarball 命名 / 上传 / 校验

## 附录 C: 验收脚本输出示例
```
$ oyster-buyer-lint vendor-001_batch-2026-05-A_clip-00042_v1.tar.gz
[1/8] Tarball structure ........... PASS
[2/8] video.mp4 1920x1080 30fps ... PASS
[3/8] video.mp4 5-6 min duration .. PASS (5m 32s)
[4/8] action_camera.json schema ... PASS (9000 frames, 20 fields)
[5/8] action_camera continuity .... PASS (no gaps, no dups)
[6/8] gameinfo.xlsx fields ........ PASS
[7/8] depth/*.exr count + format .. PASS (1980 EXRs, all float32-Z)
[8/8] Cross-file timestamp align .. PASS

✅ ACCEPTED · ready to submit
```
