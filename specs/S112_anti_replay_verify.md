---
task_id: S112-anti-replay-verify
priority: 1
estimated_minutes: 30
modifies:
  - bin/anti_replay_check.py
  - tests/test_anti_replay_check.py
executor: qwen3.6-plus
---

## 目标

`bin/anti_replay_check.py` — detect duplicate / replay-attack sessions (tester re-submits same recording or near-identical).

checks per uploaded session:
1. session_id duplicate check (memory dedup against last 100 sessions)
2. video_hash sha256 first/last 1MB → reject if matches prior
3. frame_0001.png perceptual hash → reject if >0.95 similarity to prior
4. input event sequence hash → reject duplicate input streams

Log rejections to `dashboard/replay_attacks.json`.

## 验收

- [ ] duplicate session_id → reject (exit 1)
- [ ] new session → accept (exit 0)
- [ ] perceptual hash near-match → flag (exit 2)
- [ ] `pytest tests/test_anti_replay_check.py` 全绿
- [ ] Black + ruff

## 不要做

- 不需要 imagehash 装 (用 PIL hash)
- 不阻塞 upload (post-process)
- 直接 commit `feat/S112-anti-replay`
