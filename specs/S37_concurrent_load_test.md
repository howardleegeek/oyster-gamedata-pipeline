---
task_id: S37-concurrent-load-test
project: gamedata-pipeline
priority: 2
estimated_minutes: 40
depends_on:
  - S25-backend-stub-fastapi
  - S27-recorder-local-smoke
modifies:
  - bin/load_test_100_recorders.py
  - tests/test_load_test_harness.py
executor: qwen3.6-plus
---

## 目标

`bin/load_test_100_recorders.py` — 模拟 100 个 recorder 并发，每个跑 30s "session"，上传到 backend stub。测 ISC-4 (100 concurrent < 5% CPU/proc 7天)。

1. 起 N（默认 100）个 async tasks，每个：
   - 调 mock_obs_recorder 录 30s 假 session
   - 上传到 backend_stub (S25)
   - 报 (latency, throughput, success)
2. 收集 metrics：
   - p50/p95/p99 latency
   - CPU usage per recorder (psutil)
   - mem per recorder
   - error rate
3. 输出 `dashboard/load_test_results.json` + markdown 表

## 约束

- 用 asyncio + aiohttp（不真起 100 进程）
- 不真录 30s — 测试 sleep 1s 模拟
- backend stub 需先启 (--backend-url http://localhost:8500)
- 总耗时 ≤ 90s for default 100

## 验收标准

- [ ] `python3 bin/load_test_100_recorders.py --backend-url http://localhost:8500 -n 100` 90s 内出 metrics
- [ ] JSON 含 `p95_latency_ms` < 1000 (with stub)
- [ ] error rate < 1%
- [ ] `pytest tests/test_load_test_harness.py -v` 全绿（mock async backend）
- [ ] Black + ruff

## 不要做

- 不真测 7 天 (跑 sample 测得 metrics 即可)
- 不真测 < 5% CPU (mock 不可能精确)
- 不依赖真生产 backend
- 直接 commit 到 branch `feat/S37-load-test-harness`
