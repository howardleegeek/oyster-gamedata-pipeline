---
task_id: S103-multi-game-registry
priority: 2
estimated_minutes: 25
modifies:
  - bin/games/registry.py
  - bin/games/__init__.py
  - tests/test_game_registry.py
executor: qwen3.6-plus
---

## 目标

`bin/games/registry.py` — central game adapter registry. auto-discover all adapters in `bin/games/*_adapter.py`.

1. on import, scan `bin/games/` for `*_adapter.py`
2. instantiate each, store in registry
3. `detect_running_game()` 遍历 registry → 返回 first match
4. `list_supported_games()` → list of game names

## 验收

- [ ] 4 games registered (mc, roblox, beamng, vrchat)
- [ ] detect_running_game returns correct adapter based on mock psutil
- [ ] list_supported_games returns 4 names
- [ ] `pytest tests/test_game_registry.py` 全绿

## 不要做

- 不破坏现有 adapter 接口
- 直接 commit `feat/S103-game-registry`
