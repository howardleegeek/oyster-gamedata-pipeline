# VENDOR ONBOARDING GUIDE
## 8-Step SOP for Data Collection

---

## 🚀 一键验证(minipc Windows 11 + WSL2 Ubuntu 22.04 实测通过)

**先确认环境能跑通,再继续下面 8 步:**

```bash
git clone --depth 1 https://github.com/howardleegeek/oyster-gamedata-pipeline.git
cd oyster-gamedata-pipeline
sudo apt-get install -y ffmpeg openjdk-21-jdk libopenexr-dev
pip install -e . OpenEXR Imath openpyxl numpy
python3 bin/sample_tarball_builder.py --output sample.tar.gz
# 期望: 27 MB · lint PASS · 0 issues · < 5 秒
```

**Cross-platform 验证**:

| 环境 | Python | 结果 |
|---|---|---|
| Windows 11 + WSL2 Ubuntu 22.04 (minipc 真测) | 3.10.12 | ✅ 0 issues PASS |
| macOS 26.3 (mac-1 真测) | 3.14 | ✅ 0 issues PASS |

跑不通 → `bash bin/doctor.sh` 自检,常见 fix:
- ffmpeg 缺 → `sudo apt-get install -y ffmpeg`
- OpenEXR 缺 → `pip install OpenEXR Imath`
- Java 缺 → `sudo apt-get install -y openjdk-21-jdk`

---

## STEP 0: PRE-FLIGHT 硬件清单

**必须满足以下硬件要求才能开始采集:**

### GPU 要求
- **1080p 采集**: NVIDIA GeForce RTX 3060 或更高 (1080p 3060+)
- **2K 采集**: NVIDIA GeForce RTX 4060 Ti 或更高 (2k 4060Ti+)
- **4K 采集**: NVIDIA GeForce RTX 4070 或更高 (4k 4070+)

### 系统配置
- **RAM**: 16GB 或更高 (16G+)
- **CPU**: Intel i5-12400F 或 AMD Ryzen 5 5600+ 或更高
- **存储**: NVMe SSD 1TB+ (确保有足够空间存放原始数据)

### 鼠标设置 (关键!)
- **鼠标品牌**: 罗技(Logitech)、雷蛇(Razer)、戴尔外星人(Dell Alienware)
- **DPI 设置**: 固定 1800 DPI (鼠标 dpi 1800)
- **系统鼠标指针速度**:
  - **Windows 10**: 设置为 6 (默认值不要改) (win10=6)
  - **Windows 11**: 设置为 10 (默认值不要改) (win11=10)
  - **注意**: 不要修改系统鼠标加速设置，保持默认

### 显示设置
- **显示分辨率**: 1920×1080 (必须系统设置和游戏内设置一致) (显示分辨率: 1920×1080)
- **刷新率**: 60Hz 或更高
- **全屏模式**: 必须使用独占全屏(Exclusive Fullscreen)，不要用无边框窗口

### 网络要求
- **上行带宽**: 50 Mbps 或更高 (网络上行: 50 Mbps+)
- **网络延迟**: < 20ms (网络延迟: < 20ms)
- **建议**: 使用有线网络连接，避免WiFi

---

## STEP 1: 环境准备与安装

### 1.1 软件安装
```bash
# 安装必要的依赖
pip install -r requirements.txt
# 安装游戏客户端
./install_game_client.sh
```

### 1.2 配置文件设置
```bash
# 复制配置文件模板
cp config_template.yaml config.yaml
# 编辑配置文件
vim config.yaml
```

### 1.3 权限验证
```bash
# 运行权限测试脚本
python test_permissions.py
# 验证硬件访问
python test_hardware_access.py
```

---

## STEP 2: 账号配置与登录

### 2.1 账号准备
- 使用提供的测试账号
- 确保账号有所有必要区域的访问权限
- 验证账号无封禁记录

### 2.2 登录流程
```python
# 自动登录脚本示例
from game_client import GameClient
client = GameClient()
client.login(username="vendor_test", password="******")
```

### 2.3 会话管理
- 每次采集前重新登录
- 保持会话活跃时间不超过4小时
- 定期检查连接状态

---

## STEP 3: 场景选择与准备

### 3.1 场景清单
- 从提供的场景列表中按顺序采集
- 每个场景需要采集1800帧数据
- 确保场景加载完整后再开始采集

### 3.2 环境检查
```python
# 场景检查脚本
def check_scene_ready(scene_id):
    # 检查地形加载
    # 检查NPC生成
    # 检查光照条件
    # 检查碰撞体
    return is_ready
```

### 3.3 起始位置设置
- 使用标准起始坐标
- 确保视野范围内无遮挡
- 验证初始相机角度

---

## STEP 4: 采集参数配置

### 4.1 视频设置
```yaml
video_settings:
  resolution: 1920x1080
  fps: 30
  codec: h264
  bitrate: 15000k
  format: mp4
```

### 4.2 数据采集设置
```yaml
data_collection:
  frame_count: 1800
  capture_interval: 33ms  # 对应30fps
  depth_format: exr
  metadata_format: json
```

### 4.3 输入记录设置
```yaml
input_recording:
  mouse_sensitivity: 1.0
  keyboard_debounce: 50ms
  action_sampling_rate: 60Hz
```

---

## STEP 5: 自动化脚本执行

### 5.1 启动采集
```bash
# 启动数据采集
python collect_data.py --scene SCENE_ID --output OUTPUT_DIR
```

### 5.2 监控进度
```bash
# 实时监控采集状态
tail -f logs/collection.log
# 检查帧数
python check_progress.py --dir OUTPUT_DIR
```

### 5.3 异常处理
- 如果采集中断，从最近检查点恢复
- 记录所有错误到error.log
- 超过3次失败需要人工干预

---

## STEP 6: 数据验证与质量控制

### 6.1 实时验证
```python
# 实时数据验证
def validate_frame_data(frame_data):
    # 检查帧完整性
    # 验证时间戳连续性
    # 检查输入数据有效性
    return validation_result
```

### 6.2 质量检查点
- 每300帧进行一次质量检查
- 检查画面是否卡顿
- 验证输入响应时间
- 检查内存使用情况

### 6.3 问题记录
- 记录所有警告和错误
- 标记需要重新采集的片段
- 生成质量报告

---

## STEP 7: 真采集与数据导出

### 7.1 完整采集流程
```bash
# 执行完整采集
python full_collection.py \
  --scene all \
  --output ./data_raw \
  --validate true
```

### 7.2 数据打包
```bash
# 打包数据
tar -czf scene_001.tar.gz \
  video.mp4 \
  systeminfo.json \
  action_camera.json \
  gameinfo.xlsx \
  depth/
```

### 7.3 VERIFY 8 项 action_camera

**必须通过以下8项检查才能提交 (来自PDF第11-12页):**

1. **字段缺失 / 格式不正确**
   - 检查所有必需字段是否存在
   - 验证JSON格式正确性
   - 确保数据类型匹配规范

2. **坐标系对齐 (camera + player 同 left-hand)**
   - camera坐标系必须与player坐标系对齐
   - 使用左手坐标系系统
   - 验证旋转方向一致性

3. **输入映射 (mouse_dx/dy 方向匹配画面)**
   - mouse_dx/dy必须与画面移动方向匹配
   - 验证输入到输出的映射关系
   - 检查鼠标灵敏度设置正确

4. **帧率连续性 (frame 无重复 / 跳帧)**
   - 确保30fps连续无跳帧
   - 检查时间戳单调递增
   - 验证无重复帧或丢失帧

5. **键盘事件一致性 (keyCode ASCII, W=87)**
   - 键盘事件必须使用标准ASCII keyCode
   - W键必须对应keyCode 87
   - 验证按键释放事件完整

6. **四元数 [x, y, z, w]**
   - 四元数必须规范化(单位长度)
   - 格式必须为[x, y, z, w]
   - 验证旋转表示正确性

7. **物理阈值 (speed 数值合理)**
   - 速度值必须在合理物理范围内
   - 检查加速度连续性
   - 验证无瞬移或异常运动

8. **camera_intrinsics fx == fy**
   - 相机内参fx必须等于fy
   - 验证无畸变参数错误
   - 检查投影矩阵正确性

---

## STEP 7.5: 检查 5 文件齐全

**每个clip压缩包必须包含以下5个文件:**

```bash
# 验证文件完整性
tar -tzf <clip>.tar.gz

# 必须看到:
video.mp4                    # 主视频文件，30fps，1920x1080
systeminfo.json              # 系统信息配置文件
action_camera.json           # 动作和相机数据
gameinfo.xlsx               # 游戏状态信息表格
depth/000000.exr            # 深度图序列（共1800个）
depth/000001.exr
...
depth/001799.exr
```

**文件验证脚本:**
```bash
#!/bin/bash
# verify_files.sh
CLIP_FILE=$1

echo "验证文件: $CLIP_FILE"
FILE_COUNT=$(tar -tzf "$CLIP_FILE" | grep -E "(video\.mp4|systeminfo\.json|action_camera\.json|gameinfo\.xlsx|depth/.*\.exr)" | wc -l)

if [ "$FILE_COUNT" -eq 1804 ]; then
    echo "✓ 文件齐全 (5个主文件 + 1800个深度图)"
else
    echo "✗ 文件缺失或数量不对: 找到 $FILE_COUNT 个文件，需要 1804 个"
    exit 1
fi
```

---

## ⚠️ 自动化采集脚本必避免的 7 类问题

**根据PDF第9-11页原文，必须避免以下7类问题:**

### 1. 穿模 + 闪退
**问题描述**: 角色穿过物体或游戏崩溃退出  
**如何避免**: 
- ScriptedProvider move_radius 1.5, 启用 pathfinder collision check
- 添加地形边界检查，避免走到地图外
- 实现异常捕获和恢复机制

### 2. 原地转圈
**问题描述**: 角色在原地不停旋转  
**如何避免**:
- 加随机 break, mouse_dx 累计绝对值监控
- 设置最大连续旋转角度限制
- 添加方向变化检测，防止无限循环

### 3. 出现闪屏
**问题描述**: 画面出现闪烁或撕裂  
**如何避免**:
- 关 V-Sync, 锁 30fps
- 使用独占全屏模式，避免窗口管理器干扰
- 确保显卡驱动更新到最新版本

### 4. 经常穿模穿树木
**问题描述**: 频繁穿过树木等环境物体  
**如何避免**:
- 增加 collision_radius 到1.2倍角色半径
- 对树木等薄物体使用 double_sided_collision
- 添加 raycast_precheck 提前检测路径障碍

### 5. 人物卡到位置后突然漂移
**问题描述**: 角色卡住后突然瞬移  
**如何避免**:
- 设置 stuck_detection_timeout: 2.0 秒
- 启用 gradual_recovery 逐步恢复位置
- 记录卡住前的状态用于平滑恢复

### 6. 边跑边模型加载
**问题描述**: 运行时模型还在加载导致卡顿  
**如何避免**:
- 添加 preload_radius: 50.0 预加载范围
- 使用 async_loading 异步加载
- 在采集开始前等待所有资源加载完成

### 7. 山峰突变
**问题描述**: 地形高度突然变化导致异常  
**如何避免**:
- 设置 terrain_smooth_threshold: 0.5
- 启用 slope_angle_limit: 45 度
- 添加 heightmap_validation 检查地形连续性

---

## STEP 8: 数据提交与验收

### 8.1 文件命名规范
```
格式: {scene_id}_{clip_id}_{timestamp}.tar.gz
示例: scene_001_clip_001_20240101_120000.tar.gz
```

### 8.2 上传流程
```bash
# 使用提供的上传脚本
python upload_data.py \
  --file scene_001_clip_001.tar.gz \
  --server upload.example.com \
  --token YOUR_UPLOAD_TOKEN
```

### 8.3 提交检查清单
- [ ] 5个必需文件齐全
- [ ] 文件命名符合规范
- [ ] 数据通过8项action_camera检查
- [ ] 无7类自动化问题
- [ ] 硬件配置符合要求

---

## 验收抽查机制

### 每日提交检查规则

**规则1: 每天提交 → 我方每场景抽 2 条做 video+json 校验,2 条都不过 → 整场景包打回**

**规则2: 每天提交 → 我方每场景抽 2-5% 做 video 校验,通过率 < 90% → 当日数据包打回**

**规则3: 通过率 90-100% → 补差额(例 95% → 补 5%)**

### 详细说明:
1. **每场景抽2条做video+json校验**
   - 每天提交后，我方会在每个场景中随机抽取2条clip
   - 进行完整的video+json校验（包括8项action_camera检查）
   - 如果2条都不过 → 整场景包打回

2. **每场景抽2-5%做video校验**
   - 每天提交后，我方会在每个场景中随机抽取2-5%的clip
   - 进行video质量校验（画面质量、连续性等）
   - 如果通过率 < 90% → 当日数据包打回

3. **通过率计算与补采**
   - 通过率 90-100% → 接受数据，但需要补差额
   - 例: 95%通过率 → 需要补采5%的数据量
   - 补采必须在24小时内完成并重新提交

### 质量评分标准
```yaml
quality_metrics:
  video_quality:          # 视频质量 (权重30%)
    - 分辨率符合1920x1080
    - 帧率稳定30fps
    - 无画面撕裂闪烁
  
  data_completeness:      # 数据完整性 (权重30%)
    - 5个文件齐全
    - 1800帧完整
    - 深度图序列连续
  
  action_camera_valid:   # action_camera校验 (权重40%)
    - 8项检查全部通过
    - 坐标系正确
    - 输入映射准确
```

### 问题反馈流程
1. **即时反馈**: 检查失败后2小时内通知
2. **问题分类**: 根据严重程度分类处理
3. **重新提交**: 修正后24小时内重新提交
4. **最终验收**: 通过所有检查后标记完成

---

## 附录

### A. 硬件验证脚本
```bash
# 验证硬件配置
python verify_hardware.py

# 输出示例:
# GPU: NVIDIA GeForce RTX 3060 ✓ (1080p 3060+)
# RAM: 32GB ✓ (16G+)
# CPU: Intel i5-12400F ✓
# Mouse DPI: 1800 ✓
# Resolution: 1920x1080 ✓
# Network Upload: 100 Mbps ✓ (50 Mbps+)
# Network Latency: 15ms ✓ (< 20ms)
```

### B. 常见问题解答
**Q: 鼠标DPI无法设置为1800怎么办？**  
A: 使用鼠标厂商软件设置，或更换支持1800DPI的鼠标。

**Q: 系统鼠标速度设置在哪里？**  
A: Windows设置 → 设备 → 鼠标 → 其他鼠标选项 → 指针选项。

**Q: 如何验证采集帧率？**  
A: 使用 `ffprobe -i video.mp4` 检查视频信息。

**Q: 深度图数量不足1800个怎么办？**  
A: 检查采集脚本是否完整运行，确保无提前中断。

**Q: 如何检查8项action_camera？**  
A: 使用提供的验证脚本: `python validate_action_camera.py action_camera.json`

**Q: 如何避免7类自动化问题？**  
A: 严格按照"自动化采集脚本必避免的7类问题"中的建议配置脚本。

### C. 技术支持
- **问题反馈**: issues@example.com
- **紧急支持**: support@example.com (24/7)
- **文档更新**: 定期检查此文档最新版本

---

**版本**: 2.0  
**更新日期**: 2024-01-01  
**生效日期**: 立即生效  

*请严格按照本指南执行，确保数据质量符合验收标准。第一次运行就能通过抽查！*