---
task_id: S43-beamng-game-adapter
project: gamedata-pipeline
priority: 1
estimated_minutes: 35
depends_on:
  - S32-roblox-game-adapter
modifies:
  - bin/games/beamng_adapter.py
  - tests/test_beamng_adapter.py
executor: qwen3.6-plus
---

## 目标

BeamNG adapter — 第 3 个游戏，关 ISC-6 (3 games)。继承 S32 的 base_adapter。

1. detect: `BeamNG.drive.exe` (Windows) / `BeamNG.drive` (Linux Steam)
2. metadata: 提取 vehicle + map + game_mode from `~/AppData/Local/BeamNG.drive/0.x/settings.json`
3. hooks: 录制 prefer driving missions（filter out menu time）

## 约束

- 继承 `bin.games.base_adapter.BaseAdapter`
- mock psutil + os.path (跨平台)
- 不真启 BeamNG

## 验收

- [ ] `from bin.games import detect_running_game` 在 mock BeamNG process 下返回 BeamNGAdapter
- [ ] extract_metadata 返回 game_name='beamng'
- [ ] `pytest tests/test_beamng_adapter.py -v` 全绿
- [ ] Black + ruff

## 不要做

- 不写真 OBS hook
- 直接 commit 到 branch `feat/S43-beamng-adapter`
