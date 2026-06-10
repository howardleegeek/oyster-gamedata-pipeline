---
task_id: S102-vrchat-adapter
priority: 2
estimated_minutes: 30
modifies:
  - bin/games/vrchat_adapter.py
  - tests/test_vrchat_adapter.py
executor: qwen3.6-plus
---

## 目标

VRChat adapter (4th game ⇒ exceed ISC-6 to 4/3).

1. detect: `VRChat.exe` (Windows) / `vrchat.app` (mac)
2. metadata: extract world_id from `~/AppData/LocalLow/VRChat/VRChat/output_log_*.txt`
3. hooks: skip recording when user in private worlds (privacy)

## 验收

- [ ] adapter inherits BaseAdapter
- [ ] detect → returns VRChatAdapter when VRChat.exe in mock psutil
- [ ] private world filter works
- [ ] `pytest tests/test_vrchat_adapter.py` 全绿

## 不要做

- 不真启 VRChat
- 不录 private 内容
- 直接 commit `feat/S102-vrchat-adapter`
