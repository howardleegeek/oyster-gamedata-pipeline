---
task_id: S27-recorder-local-smoke
project: gamedata-pipeline
priority: 1
estimated_minutes: 45
depends_on:
  - S25-backend-stub-fastapi
modifies:
  - bin/recorder_local_smoke.py
  - bin/mock_obs_recorder.py
  - bin/mock_game_detector.py
  - tests/test_recorder_local_smoke.py
executor: qwen3.6-plus
---

## 目标

让 recorder 在 Mac/Linux **不装 OBS、不开 MC** 也能端到端跑通 — 用于 CI + 开发机验证。

1. `bin/mock_obs_recorder.py`：fake recorder 30s 内"录" mp4 (实际写假数据 + 真 metadata.json)
2. `bin/mock_game_detector.py`：fake detection 返回 {"game": "minecraft", "pid": 12345, "window_title": "MC 1.21.4"}
3. `bin/recorder_local_smoke.py`：orchestrator 调 mock_game_detector → mock_obs_recorder → upload (用 S25 backend_stub) → verify session 在 backend_stub 收到

跑完输出 `BUYER_READY` if 全 step 过，`FAIL: <step>` if 任何 step 报错。

## 约束

- 不真启 OBS 或 MC
- 不真连云端
- 假数据用 zero-byte placeholder (mp4 header + 1KB zeros)
- 不写 metadata.json 之外的真 session 文件

## 验收标准

- [ ] `python3 bin/recorder_local_smoke.py --backend-url http://localhost:8500` 30s 内 exit 0 当 backend stub 在跑
- [ ] backend 不在时 exit 1 with error
- [ ] `pytest tests/test_recorder_local_smoke.py -v` 全绿（mock backend HTTP）
- [ ] 输出包含 `BUYER_READY` line
- [ ] Black + ruff

## 不要做

- 不真录视频
- 不真启 game 进程
- 不上传到云
- 直接 commit 到 branch `feat/S27-recorder-local-smoke`
