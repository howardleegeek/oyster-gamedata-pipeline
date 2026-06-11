---
task_id: S26-cluster-cost-tracker
project: gamedata-pipeline
priority: 2
estimated_minutes: 30
depends_on: []
modifies:
  - daemon/cluster_cost_tracker.py
  - tests/test_cluster_cost_tracker.py
executor: qwen3.6-plus
---

## 目标

新建 `daemon/cluster_cost_tracker.py` — 扫所有 cluster dispatch logs `/tmp/cluster-*/SXX-dispatch.log`，提取：
- TASK RESULT line → turns count
- HTTP 429 events → retry count
- spec_id
- model used (extract from log header)
- wall clock duration (mtime first→last)

输出 `dashboard/cluster_cost.json`：
```json
{
  "updated_at": "ISO8601",
  "per_spec": [
    { "spec": "S05", "model": "qwen3.6-plus", "turns": 40, "retries": 0, "wall_s": 312, "estimated_tokens": 12345, "estimated_usd": 0.038 }
  ],
  "totals": { "specs": 12, "turns": 451, "retries": 17, "wall_s": 4523, "estimated_usd": 0.42 }
}
```

token估算: turns × 800 (rough avg per turn input+output)
USD 估算: tokens × $3/1M (qwen3.6-plus rate)

## 约束

- 不删 log files
- idempotent — 多次跑结果一致
- `--once` `--dry-run` flags
- 不调外部 API

## 验收标准

- [ ] `python3 daemon/cluster_cost_tracker.py --once` 扫 `/tmp/cluster-*/` 出 JSON
- [ ] JSON 含所有已 dispatched spec
- [ ] `pytest tests/test_cluster_cost_tracker.py -v` 全绿（mock log files）
- [ ] Black + ruff

## 不要做

- 不连真 LLM API 算精确成本
- 不删 logs
- 直接 commit 到 branch `feat/S26-cluster-cost-tracker`
