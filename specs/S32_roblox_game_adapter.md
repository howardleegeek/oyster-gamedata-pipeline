---
task_id: S32-roblox-game-adapter
project: gamedata-pipeline
priority: 1
estimated_minutes: 45
depends_on:
  - S27-recorder-local-smoke
modifies:
  - bin/games/__init__.py
  - bin/games/roblox_adapter.py
  - bin/games/base_adapter.py
  - tests/test_roblox_adapter.py
executor: qwen3.6-plus
---

## 目标

新建 `bin/games/` package + 抽象 base adapter + Roblox 第一个实现（朝 ISC-6 推进 1/3 → 2/3）。

1. `base_adapter.py` 抽象基类:
   - `detect()` → Optional[GameSession] (PID, window, exe path)
   - `extract_metadata(pid)` → GameMetadata (game_name, version, current_world, etc.)
   - `pre_record_hook()`, `post_record_hook()` — opcional
2. `roblox_adapter.py`：
   - detect Roblox by exe name `RobloxPlayerBeta.exe` (Windows) / `RobloxPlayer.app` (mac)
   - extract place_id + universe_id from Roblox local logs
   - hook: 在 record 前 inject 一个 small overlay marker ("Recording for Oyster")
3. `__init__.py` 注册表 — adapter discovery

## 约束

- 抽象基类用 Python ABC
- 不真启 Roblox（测试用 mock process detection）
- detect() 返回 None 时不报错（游戏没开）
- macOS + Windows 跨平台（dispatch 节点是 mac，需 mock）

## 验收标准

- [ ] `from bin.games import detect_running_game` 返回 adapter instance or None
- [ ] mock RobloxPlayerBeta.exe in PATH → adapter.detect() 返回 non-None
- [ ] adapter.extract_metadata 返回 dict 含 game_name='roblox'
- [ ] `pytest tests/test_roblox_adapter.py -v` 全绿（mock psutil + os.path）
- [ ] Black + ruff

## 不要做

- 不真跑 Roblox client
- 不实现游戏内 mod injection（只 metadata 抽取）
- 不写 BeamNG / VRChat（后面 spec）
- 直接 commit 到 branch `feat/S32-roblox-adapter`
