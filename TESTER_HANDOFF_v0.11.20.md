# 🦪 Oyster GameData Recorder — 内测说明书 v0.11.20

*目标读者：测试员 · 完成时间：≤30 分钟（含 5 分钟录制）*

---

## 一句话

下载一个文件 → 双击装 → 进游戏玩 5 分钟 → 跑一行命令自检 → 把 session 发回来。

---

## 第 1 步：下载

唯一下载链接（738 MB，**单文件**，零依赖）：

```
https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/v0.11.20/OysterRecorder-Setup-recorder-v0.28.0-rc19.0.6.exe
```

SHA256 校验（PowerShell 可选）：
```
57c1f7efa38a76fdaa2fe44bdcb8d9746488932bd8728bc3d7238df1c701a397
```

---

## 第 2 步：安装

1. 双击下载好的 `.exe`
2. Windows SmartScreen 弹窗 → 点 **"More info"** → **"Run anyway"**
   - 我们暂时没买 EV 证书，所以会有这个警告，无害
3. 安装包**自动静默装** VC++ Redist 2015-2022 x64（只装一次）
4. 安装包把 Recorder + Minecraft 1.21.4 + Java 21 解压到 `%LOCALAPPDATA%\OysterRecorder\`
5. 完成页点 **"Launch OysterPlay"**

---

## 第 3 步：录制（**关键 — 最少 5 分钟**）

1. OysterPlay 会自动启动我们内置的 Minecraft（不是官方启动器）
2. **录制器会等真游戏窗口出现才开始**（在启动器上不会误触发）
3. 进游戏后**至少玩 5 分钟**，最好做这些事：
   - **WASD 走动**：≥10 秒（满足 Q6/Q10 检查）
   - **鼠标视角**：随便转
   - **按 E 开背包**：1-2 次（满足 Q1）
   - **按 F5 切视角**：1-2 次（满足 Q2）
   - **走出生点 ≥5 米**：相机位置 bbox 要 >5m（A21）
4. 玩完直接退游戏（关 Minecraft 窗口）
5. 录制器自动收尾、写 metadata、保存到 `Documents\OysterClips\session_<timestamp>\`

---

## 第 4 步：自检（**这是最重要的一步**）

打开命令行（cmd 或 PowerShell），运行：

```cmd
cd %LOCALAPPDATA%\OysterRecorder\tools
python tester_preflight.py
```

**自动找最新 session，跑 104+ 项 PRD 检查，给一行红黄绿判决。**

> **注**：如果你的版本是 v0.11.20（最早一批），`tools\` 目录不存在。
> 单独下载这两个脚本到任意目录运行：
>
> https://raw.githubusercontent.com/howardleegeek/oyster-gamedata-pipeline/main/bin/tester_preflight.py
> https://raw.githubusercontent.com/howardleegeek/oyster-gamedata-pipeline/main/bin/prd_compliance_audit.py
> https://raw.githubusercontent.com/howardleegeek/oyster-gamedata-pipeline/main/bin/audit_quality_metrics.py
>
> v0.11.21+ 已经把这些工具打进 `.exe`，不再需要单独下载。

| 判决 | 含义 | 你做什么 |
|------|------|---------|
| ✅ **GREEN (≥95/105)** | 数据完美，可以发货 | 打包发回（见第 5 步） |
| ⚠️ **YELLOW (80-94/105)** | 数据能用，有遗憾 | 看脚本里列的 FAIL 项；如果是 H8/B8 这种结构性问题，发回让我们看 |
| ❌ **RED (<80/105)** | 数据有问题，重录 | 看脚本的"建议"，按提示重录 |

**注意**：YELLOW 的常见原因有 3 个，**不是你做错了**：
- **H8 monocular_da_v2** — 我们用的是 DA-V2 单目深度，不是 metric Z-buffer，这是 architecture 限制，**永远拿不到 H8 满分**
- **B8/QM** — ffprobe/sox 路径问题，跟你录的内容无关
- **SS5** — session 7 天没传回会扣分，所以**当天传回最好**

实际经验：5 分钟好好玩的 session 通常能拿 **95-101/105**，这已经达标。

---

## 第 5 步：把 session 发回

自检脚本会在末尾打印一行：

```cmd
tar -czf my_session.tar.gz -C <session 父目录> <session 名字>
```

Windows 10/11 自带 `tar` 命令，直接复制粘贴跑就行。然后把 `my_session.tar.gz`（通常 100-500 MB）通过以下任一方式发回：

- **百度网盘**：上传后把分享链接 + 提取码发给 Howard 微信
- **微信文件传输**：直接拖给 Howard
- **GitHub Issue 附件**（如果 <100MB）：在 https://github.com/howardleegeek/oyster-gamedata-pipeline/issues 新建 issue

---

## 出了问题怎么办？

### 装不上 / 双击没反应
- 90% 是 SmartScreen 没让运行 — 看第 2 步
- 10% 是 VC++ Redist 装失败 — 手动装：
  https://aka.ms/vs/17/release/vc_redist.x64.exe

### 一秒开一个 / 录了一堆空文件
- 这是老版本 bug（v0.11.18 之前）— v0.11.20 已修复
- 如果你装的是 v0.11.20 还有这个问题，**立刻停掉**：
  1. 看任务管理器，结束所有 `OysterRecorder.exe`
  2. 删 `Documents\OysterClips\` 里所有 session
  3. 重新跑 OysterPlay

### 录制窗口跟到了启动器上
- v0.11.20 已修：处理名白名单 + 1280×720 窗口稳定门
- 如果还触发，截图发回 — 我们要看这个 bug

### MC 进不去 / 黑屏
- 检查显卡驱动（要支持 OpenGL 4.6）
- 检查 `%LOCALAPPDATA%\OysterRecorder\logs\javaw_*.log` 最新一份

### 自检脚本报"找不到 Python"
- 安装 Python 3.11+: https://www.python.org/downloads/
- 装的时候**勾选** "Add python.exe to PATH"

---

## 数据保密

- 你录的内容只发给 Howard（howard.li@berkeley.edu）
- 我们承诺：内测数据只用于模型训练，**不外传**、**不商业化**
- 如果有隐私顾虑（比如不小心录到了私人聊天窗口），告诉 Howard，整段删除

---

## 直接联系

- Howard 微信 / 邮箱 / GitHub: connecthoward
- 紧急联系电话：见私信

---

🦪 *谢谢你帮我们验证 v0.11.20 — 这一份数据是模型训练的第一公里。*
*2026-05-26 PT*
