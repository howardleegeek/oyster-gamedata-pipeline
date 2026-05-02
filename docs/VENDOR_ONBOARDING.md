# Vendor Onboarding · 8 步从零产出第一个 clip

> **配套文档**: [`PRD.md`](PRD.md) · [`BUYER_SPEC_V1.md`](BUYER_SPEC_V1.md) · [`SUBMISSION_FORMAT.md`](SUBMISSION_FORMAT.md)
> **预计耗时**: 60-90 分钟(干净机器从零)
> **适用对象**: 操作员 / 数据采集工程师 / vendor 技术负责人

---

## 目标
在一台干净机器(Windows 10+ / macOS 13+ / Ubuntu 22.04+)上,**60 分钟内产出第一个通过 lint 的合格 tarball**。

跑通这 8 步 = vendor 完成上线验收,可以开始量产。

---

## STEP 0 · 硬件 + OS 自检
**做什么**: 确认机器满足最低要求

```bash
# macOS / Linux
uname -a                            # OS 版本
sysctl -n hw.ncpu 2>/dev/null \
  || nproc                          # CPU 核数 ≥ 4
sysctl -n hw.memsize 2>/dev/null \
  || free -h                        # RAM ≥ 16 GB

# Windows (PowerShell)
Get-ComputerInfo | Select-Object OsName, OsVersion, CsTotalPhysicalMemory
Get-WmiObject Win32_Processor | Select-Object NumberOfCores
```

**要求**:
- ✅ OS: Windows 10/11 · macOS 13+ · Ubuntu 22.04+
- ✅ CPU: 4-core 3.0GHz+
- ✅ RAM: 16 GB+
- ✅ 硬盘空闲: ≥ 100 GB(单 batch 100 clip × 1 GB)
- ✅ 网络: 50 Mbps 上行(测试: <https://fast.com>)

**不通过**: 不要继续,加我方群求建议。

---

## STEP 1 · 装 Java 21 + Python 3.11+ + ffmpeg
**做什么**: 装运行环境

### macOS (Homebrew)
```bash
# 装 brew(若没有)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 装依赖
brew install openjdk@21 python@3.11 ffmpeg openexr git

# 配 Java 21 路径
sudo ln -sfn /opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk \
             /Library/Java/JavaVirtualMachines/openjdk-21.jdk
echo 'export JAVA_HOME=$(/usr/libexec/java_home -v 21)' >> ~/.zshrc
source ~/.zshrc
```

### Ubuntu / Debian
```bash
sudo apt update
sudo apt install -y openjdk-21-jdk python3.11 python3.11-venv \
                    ffmpeg libopenexr-dev git
```

### Windows (PowerShell as Admin)
```powershell
# 装 winget(若没有,从 Microsoft Store)
winget install -e --id EclipseAdoptium.Temurin.21.JDK
winget install -e --id Python.Python.3.11
winget install -e --id Gyan.FFmpeg
winget install -e --id Git.Git
```

**验证**:
```bash
java -version          # 应显示 21.0.x
python3 --version      # 应显示 3.11.x
ffmpeg -version | head -1
git --version
```

---

## STEP 2 · clone 参考工程
**做什么**: 拿我方代码 + 子模块

```bash
git clone --recurse-submodules \
  https://github.com/howardleegeek/oyster-gamedata-pipeline.git
cd oyster-gamedata-pipeline
```

**验证**:
```bash
ls vendor/                 # 应有 recorder/ input-logger/ enrichment/
ls bin/SOP.sh              # 应存在
```

**子模块没拉下来**:
```bash
git submodule update --init --recursive
```

---

## STEP 3 · 一键安装(SOP.sh)
**做什么**: 跑 SOP.sh,自动装 python 包 + 准备目录

```bash
bash SOP.sh
```

**预期输出**(摘):
```
[STEP 1/8] Probing Java... openjdk version "21.0.x"  ✅
[STEP 2/8] Installing Python deps... ok (test, exr, xlsx, cs2, beamng) ✅
[STEP 3/8] Downloading Paper 1.20.4... ok ✅
[STEP 4/8] Eulla agreed... ok ✅
[STEP 5/8] Probing OBS Studio... not installed (OK, skip for placeholder run) ⚠️
[STEP 6/8] Probing PyTorch... not installed (OK, will fall back) ⚠️
[STEP 7/8] Generating sample EXR + xlsx... ok ✅
[STEP 8/8] Running e2e_smoke.sh... PASS ✅

🎉 Onboarding complete. You can now run:
  bash bin/produce_real_sample_v2.sh
```

**STEP 5/6 可选**: 如果只跑 placeholder e2e 则不需 OBS / PyTorch。要产出**真画面 + 真深度**必须装(见 STEP 5)。

---

## STEP 4 · 跑 placeholder e2e(确认环境对了)
**做什么**: 用我们的 ScriptedProvider + ffmpeg testsrc + 占位 EXR 跑通完整流水线

```bash
bash bin/e2e_smoke.sh
```

**预期输出**:
```
[1/4] Capturing 5min trace... ok (9000 frames)
[2/4] Adapting to buyer-spec... ok
[3/4] Linting... PASS (8/8 checks)
[4/4] Summary:
  output: out/buyer/clip-test-001/
  tarball: out/buyer/clip-test-001.tar.gz (12 MB)
✅ e2e_smoke PASS
```

**有了!** 这就是合格 tarball 的最小演示。但是:
- ❌ video.mp4 = ffmpeg testsrc 测试条(不是真游戏)
- ❌ depth/*.exr = 占位均匀深度(不是真推理)
- ✅ action_camera.json = 真 ScriptedProvider 行为(20 字段全)
- ✅ gameinfo.xlsx = 操作员模板填写

要量产**必须**走 STEP 5-8 升级到真画面真深度。

---

## STEP 5 · 装 OBS Studio + DepthAnything V2(真画面真深度)
**做什么**: 装真采集所需的两个重组件

### OBS Studio 30+
- macOS: `brew install --cask obs`
- Ubuntu: `sudo apt install obs-studio`
- Windows: <https://obsproject.com/download>

**配置 OBS WebSocket**:
1. 打开 OBS → Tools → WebSocket Server Settings
2. 启用 "Enable WebSocket server"
3. 端口 `4455`(默认),密码 `oyster-obs-2026`(我方约定)
4. 点 "Apply" → "OK"

**配置录制**:
1. Settings → Output → Recording
2. 格式: `mp4`
3. 编码器: `Apple VT H264`(macOS) / `NVENC H264`(Windows NVIDIA)
4. CRF: `18-23`
5. 音频: 启用桌面音频(48kHz)
6. Settings → Video → 输出分辨率 `1920×1080`,FPS `30`

### PyTorch + transformers + DepthAnything V2
```bash
# 通用(CPU 也能跑,慢)
python3 -m pip install torch torchvision transformers[torch] \
                       OpenEXR pillow numpy

# Apple Silicon (推荐, MPS 加速)
python3 -m pip install --pre torch torchvision \
  --extra-index-url https://download.pytorch.org/whl/nightly/cpu

# NVIDIA GPU (CUDA 12.x)
python3 -m pip install torch torchvision \
  --index-url https://download.pytorch.org/whl/cu121
```

**首次会自动下载模型**(~115 MB):
```bash
python3 -c "
from transformers import pipeline
p = pipeline('depth-estimation', model='depth-anything/Depth-Anything-V2-Small-hf')
print('Model loaded:', p.model.config.model_type)
"
```

**验证**:
```bash
python3 -c "import OpenEXR; print('OpenEXR ok')"
python3 -c "import torch; print('Torch:', torch.__version__, 'CUDA:', torch.cuda.is_available(), 'MPS:', torch.backends.mps.is_available())"
```

---

## STEP 6 · 启动 Paper 服务器 + Mineflayer bot
**做什么**: 起本地 Minecraft 服务端 + 自动驾驶 bot

### Terminal 1 - Paper 服务器
```bash
cd ~/oyster-gamedata-pipeline/.cache/paper
java -Xmx4G -Xms2G -jar paper-1.20.4.jar nogui
```

等到日志出现 `Done (12.3s)! For help, type "help"` —— 服务起好。

**确认 RCON 开**(`server.properties`):
```
enable-rcon=true
rcon.port=25575
rcon.password=oyster-rcon-2026
```

### Terminal 2 - Mineflayer bot
```bash
cd ~/oyster-gamedata-pipeline
node vendor/recorder/bot.js \
  --host localhost --port 25565 \
  --username DataPilot \
  --mode wasd_balanced \
  --duration 300
```

**预期输出**:
```
[bot] Connected to localhost:25565
[bot] Spawned at (100, 64, 200)
[bot] ScriptedProvider: mode=wasd_balanced, seed=42
[bot] Action distribution: W=40% A=20% S=20% D=20%
[bot] Recording 300 sec (9000 ticks at 30 tps)...
```

---

## STEP 7 · 启 Minecraft 客户端 + OBS 录制
**做什么**: 启真客户端,spectator 跟随 bot,OBS 录屏

### Terminal 3 - Minecraft Java 客户端 (offline mode)
```bash
# 用我们的启动器
python3 bin/mc_launcher_real.py \
  --server localhost:25565 \
  --username Spectator01 \
  --gamemode spectator
```

启动后客户端会自动:
1. Login as `Spectator01`(offline UUID)
2. 连接 localhost:25565
3. 切到 spectator gamemode
4. 通过 RCON 发 `/spectate <bot-uuid> Spectator01` —— 视角跟随 bot

### Terminal 4 - OBS 录制 + 真深度推理 orchestrator
```bash
bash bin/produce_real_sample_v2.sh \
  --duration 300 \
  --output out/real/clip-001
```

**这一步会**:
1. 通过 WebSocket 让 OBS StartRecord
2. 等 5 分钟
3. StopRecord → 录像文件 `clip-001.mp4`
4. 同步从 mineflayer pull 真 action_camera.json
5. ffmpeg 提取 6 fps 帧
6. DepthAnything V2 跑真深度推理 → `depth/*.exr`
7. 生成 gameinfo.xlsx
8. 打包 → `clip-001.tar.gz`

**预期总耗时**: 10-15 分钟(5 分录制 + 5-10 分深度推理)

---

## STEP 8 · 验收 + 提交
**做什么**: 本地 lint → 上传

### 本地 lint
```bash
oyster-buyer-lint out/real/clip-001.tar.gz
```

**预期**:
```
[1/8] Tarball structure ........... PASS
[2/8] video.mp4 1920x1080 30fps ... PASS
[3/8] video.mp4 5-6 min duration .. PASS (5m 02s)
[4/8] action_camera.json schema ... PASS (9060 frames, 20 fields)
[5/8] action_camera continuity .... PASS
[6/8] gameinfo.xlsx fields ........ PASS
[7/8] depth/*.exr count + format .. PASS (1812 EXRs, all float32-Z)
[8/8] Cross-file timestamp align .. PASS

✅ ACCEPTED · ready to submit
```

### 重命名 + 上传
```bash
# 命名: <vendor_id>_<batch_id>_<clip_id>_v<spec_version>.tar.gz
mv out/real/clip-001.tar.gz \
   vendor-001_batch-2026-05-A_clip-00001_v1.tar.gz

# 上传(我方启动后给 SFTP/S3/OSS 凭证)
scp vendor-001_batch-2026-05-A_clip-00001_v1.tar.gz \
    vendor@upload.oysterworld.dev:/uploads/
```

---

## ✅ 8 步走完 = vendor 通过上线验收

恭喜!产出第一个真 clip,通过 lint,完成上传。

**接下来**:
1. 给我方发邮件: `第一批 onboarding 完成,第一个 clip 已上传,vendor_id=<vendor-NNN>`
2. 我方人工 review 第一 clip + 24 小时内反馈
3. 反馈通过 → 启动正式 SOW 签约 + 30% 预付金
4. 开始量产(每周提交一批)

---

## 故障排查

### "Java not found"
- macOS: `brew install openjdk@21 && brew link --force openjdk@21`
- 重启 terminal 让 PATH 生效

### "Paper 服务器起不来"
- 检查 25565 端口被占用: `lsof -i :25565`
- 检查 EULA: `cat .cache/paper/eula.txt` 应是 `eula=true`

### "Mineflayer 连接失败"
- 确认 server.properties 的 `online-mode=false`(允许 offline 客户端登录)

### "OBS 不录"
- 检查 WebSocket 设置已启用 + 端口 4455 + 密码对
- 看 OBS 日志: `Help → Log Files → View Current Log`

### "DepthAnything 报错"
- CPU 模式很慢 → 换 Apple M / NVIDIA GPU
- OOM → 降低 batch_size: `python3 -m bin.real_depth_filler --batch-size 1`
- 模型下载失败 → 设代理: `export HF_ENDPOINT=https://hf-mirror.com`

### "Lint FAIL: video duration 4m 50s"
- 录制不足 5 分钟 → bot 提前退出 / 客户端断线
- 解决: bot 加 `--duration 360`(留 buffer)

### 不确定哪步出错
- 跑诊断: `bash bin/cluster_status_check.sh`
- 加我方 Slack / 微信群,贴日志 → 工程师 ≤ 4 小时回复

---

## 一图速览

```
┌─────────────────┐
│ 0  硬件自检      │ → 不通过则放弃
├─────────────────┤
│ 1  装环境        │ → java/python/ffmpeg/openexr
├─────────────────┤
│ 2  clone 仓库    │ → --recurse-submodules
├─────────────────┤
│ 3  SOP.sh        │ → 一键安装 python 包
├─────────────────┤
│ 4  e2e_smoke     │ → placeholder bundle, 验环境
├─────────────────┤
│ 5  装 OBS+Torch  │ → 真采集需要的重组件
├─────────────────┤
│ 6  Paper+bot     │ → server + 自动驾驶
├─────────────────┤
│ 7  MC+OBS录制    │ → 真画面 + 真深度
├─────────────────┤
│ 8  lint + 上传   │ → 第一个真 clip ✅
└─────────────────┘
```

---

**问题反馈**: howard.linra@gmail.com / WhatsApp +1 (341) 250-6526
