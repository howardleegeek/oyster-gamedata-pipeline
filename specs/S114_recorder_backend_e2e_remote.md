---
task_id: S114-recorder-backend-e2e-remote
priority: 1
estimated_minutes: 30
modifies:
  - bin/remote_recorder_backend_e2e.py
  - tests/test_remote_recorder_backend_e2e.py
executor: qwen3.6-plus
---

## 目标

Extension of S71 (local e2e) for REAL deployed backend. Once Howard `fly deploy` done.

`bin/remote_recorder_backend_e2e.py --backend-url https://oyster-backend-stub.fly.dev`:
1. healthz check
2. apply as tester
3. mock OAuth exchange (use backend's mock endpoints)
4. record fake session (S29 fixture)
5. upload via signed URL
6. verify session received
7. fetch income today → 应该有 \$0.50 (after 1 BUYER_READY upload)

Exit 0 if all pass.

## 验收

- [ ] CLI accepts --backend-url
- [ ] all 7 steps executed in order
- [ ] mock backend pass (test env)
- [ ] `pytest tests/test_remote_recorder_backend_e2e.py` 全绿
- [ ] Black + ruff

## 不要做

- 不真 hit prod (除非 --url 指定)
- 不真录 video
- 直接 commit `feat/S114-remote-e2e`
