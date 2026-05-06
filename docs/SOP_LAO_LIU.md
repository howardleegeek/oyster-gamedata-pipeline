# 录制 SOP（给老刘 / vendor）

## 默认 Vanilla 路径（裸奔游戏）

**装啥**：
- Minecraft Java 版（任何版本都行，不强制 1.20.4）
- 我们的 OysterRecorder.exe（已在桌面）

**MC 里设**：
- 窗口模式（**不要全屏**）— 全屏录不到
- 1920×1080
- FOV 70

**怎么录**：
1. 双击桌面 OysterRecorder 图标
2. 打开 MC 进游戏
3. 切回 OysterRecorder 点 ▶ 开始录制
4. 玩 5-6 分钟
5. 退出 MC，自动出 clip 到 Documents\OysterClips\

**不要装任何 mod。** 完。

---

## 可选升级（要真 6DoF 相机数据才装）

如果买家要"准确的相机位置 + 旋转"（不是 placeholder），需要：

- 切到 **Minecraft Java 1.20.4**（必须这个版本，Replay Mod 只支持它）
- 装 **Fabric Loader**（启动器一键）
- 装 **Fabric API**（去 https://fabricmc.net/use/ 下 jar，丢 mods 文件夹）
- 装 **Replay Mod 2.6.16**（去 https://replaymod.com/download/ 下 jar，丢 mods 文件夹）

Replay Mod 会自动把游戏内每帧的相机位置 / 旋转写到 `.mcpr` 文件，我们的 recorder 解析这个文件填进 action_camera.json 的 camera_position / quaternion 字段。

**Vanilla 路径** = 没真 6DoF（这些字段是 placeholder）
**Replay Mod 路径** = 真 6DoF
