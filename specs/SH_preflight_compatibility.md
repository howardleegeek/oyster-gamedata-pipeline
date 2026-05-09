# SH — Phase A.3: 启动前 preflight self-check (game-agnostic)

修改 `bin/recorder_consumer_lite.py` 在 `RecorderApp.__init__` 末尾 + UI mainloop 前, 加 `_run_preflight()` 方法.

## game-agnostic 设计

preflight 检查分两组:
1. **universal**: 所有游戏都要的
2. **game-specific**: 当前游戏特有的

```python
GAME_PROBE_REGISTRY = {
    "minecraft_java": {
        "process_names": ["javaw.exe", "java.exe", "Minecraft.exe"],
        "window_title_substrings": ["Minecraft", "我的世界"],
        "required_companion": "java",
        "min_companion_version": "21",
    },
    # 未来 roblox / fortnite ...
}
CURRENT_GAME_ID = "minecraft_java"  # config 化, 当前 hardcode
```

## 检查列表 (12 universal + 4 game-specific)

### Universal (返回 status: pass / warn / fail / fatal)
1. **Windows 10+** — `platform.version()`, fail 如果 < 10
2. **disk_free** — `shutil.disk_usage(_output_dir()).free`, warn < 5GB, fatal < 500MB
3. **admin** — `ctypes.windll.shell32.IsUserAnAdmin()`, warn 非 admin
4. **OneDrive 同步** — 检 `_output_dir()` 是否在 OneDrive 路径, warn 提示暂停同步
5. **路径长度** — `len(install_path) < 240`, warn >= 240
6. **路径含中文** — `re.search(r'[^\x00-\x7F]', str(install_path))`, info
7. **网络可达** — try urlopen api.github.com timeout 5s, warn 失败
8. **杀软白名单** — 启动 5s 后检自身 .exe 仍存在, info 报 telemetry
9. **远程桌面** — `GetSystemMetrics(SM_REMOTESESSION) != 0`, warn
10. **DPI 缩放** — `GetDpiForWindow != 96`, info
11. **GPU** — 复用 rc9 的 `_detect_gpu_available`, info
12. **中文 IME** — `GetKeyboardLayout(0) & 0xFFFF == 0x0804`, warn 提示 WASD

### Game-specific (从 GAME_PROBE_REGISTRY[CURRENT_GAME_ID] 派生)
13. **Java 21+** — `subprocess.run(["java", "-version"])` 解析, warn
14. **MC mod 在 mods 目录** — info, 不强求
15. **window title 多 locale** — registry substrings, info
16. **process name 多版本** — javaw / java, info

## UI

helpbar 加一行:
```
✅ 系统兼容性: 通过 (12/16 检查通过, 4 警告)
点击查看详情 →
```

点击展开模态:
```
✅ Windows 11
✅ 磁盘剩余 425 GB
⚠️ 用户非管理员 — 自动更新功能不可用
⚠️ 检测到中文输入法 — 录制时切英文 (WASD)
ℹ️ DPI 缩放 150%
⚠️ Java 未安装或低于 21 — 装 Temurin 21
...
```

任何 `fatal` 阻止 arm 录制按钮 (灰显). `warn`/`info` 不阻止.

## 约束
- preflight 在 main thread + 启动后 200ms 内完成
- 12 个检查并行 (`concurrent.futures.ThreadPoolExecutor(max_workers=4)`)
- 全 stdlib, ctypes 用 user32/shell32/kernel32
- 失败检查 log 到 startup.log + terminator.json `preflight_blocked` reason

## 验收
- [ ] `GAME_PROBE_REGISTRY` 字典存在, 至少 minecraft_java 一项
- [ ] `_run_preflight()` 方法存在, 返回 16 项 status list
- [ ] 任意一项 fatal → arm 按钮 disabled
- [ ] 中文 IME 检测能弹 warn
- [ ] `python3 -c "import ast; ast.parse(...)"` 通过
