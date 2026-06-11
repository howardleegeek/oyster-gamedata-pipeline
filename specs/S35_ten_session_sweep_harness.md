---
task_id: S35-ten-session-sweep-harness
project: gamedata-pipeline
priority: 1
estimated_minutes: 40
depends_on:
  - S29-session-fixture-generator
modifies:
  - bin/ten_session_sweep.py
  - tests/test_ten_session_sweep.py
executor: qwen3.6-plus
---

## 目标

`bin/ten_session_sweep.py` — 用 S29 fixture generator 生成 10 个变化 session → 每个跑 `bin/end_to_end_gate_smoke.py --strict-buyer` → 聚合 verdict → 报 BUYER_READY @ X/10 (ISC-1 measurer)。

Variation matrix (10 sessions):
- session 1-3: PASS_STRICT (gap_miss 0.001, 0.005, 0.008)
- session 4-6: PASS (gap_miss 0.05, 0.08, 0.10)
- session 7: PASS_DEGRADED (gap_miss 0.15)
- session 8: monocular fallback (kind=monocular_da_v2) → SKIP in strict-buyer
- session 9: missing depth/.source marker → FAIL
- session 10: corrupt manifest.json → FAIL

输出 markdown table + JSON summary到 `dashboard/sweep_summary.json`。

## 约束

- 用 S29 `bin/generate_session_fixture.py` 而不是从头写
- 顺序跑（不需要并行）
- 总耗时 ≤ 60s
- `--quick` flag：只跑 3 个 session 快速冒烟
- 不真传到 backend

## 验收标准

- [ ] `python3 bin/ten_session_sweep.py` 60s 内 exit code 表示 buyer-ready ratio (0=10/10, 1<10/10)
- [ ] 输出 `BUYER_READY @ 6/10 (60%)` 类似 line
- [ ] `--quick` 模式 ≤ 15s
- [ ] `pytest tests/test_ten_session_sweep.py -v` 全绿
- [ ] Black + ruff

## 不要做

- 不真跑 RSV01（用 end_to_end_gate_smoke 而不是 real_session_validator）
- 不依赖外部 backend
- 不修复 session 8/9/10 — 它们故意是失败 case
- 直接 commit 到 branch `feat/S35-ten-session-sweep`
