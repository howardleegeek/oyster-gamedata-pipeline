---
task_id: S05-h8-pass-strict-tier
project: gamedata-pipeline
priority: 1
estimated_minutes: 30
depends_on: []
modifies:
  - bin/prd_compliance_audit.py
  - tests/test_prd_compliance_h8.py
executor: qwen3.6-plus
---

## 目标

给 H8 加 `PASS_STRICT` 档。当 `kind=engine_zbuffer` 且 `gap_miss_ratio < 0.01`（1%），返回 `status="PASS_STRICT"`，evidence 加 "strict tier (≥99% engine truth)"。其他现有行为不变。

## 约束

- 只改 `bin/prd_compliance_audit.py::_evaluate_h8`
- 已有 `PASS` (≤10% gap) 和 `PASS_DEGRADED` (>10% gap) 档保留不动
- 新增的 `PASS_STRICT` 是更严格的子集（gap < 1%）
- 测试加 3 个 case：gap=0.005 → PASS_STRICT, gap=0.05 → PASS, gap=0.15 → PASS_DEGRADED
- 不动 H1-H7, X*
- 不改 UI/CLI 输出格式以外的任何东西

## 验收标准

- [ ] `_evaluate_h8` 三档：PASS_STRICT (<1%) / PASS (1-10%) / PASS_DEGRADED (>10%)
- [ ] `pytest tests/test_prd_compliance_h8.py -v` 全绿
- [ ] `python3 bin/prd_compliance_audit.py <synthetic_session>` 输出含 `[PASS_STRICT] H8:` 行
- [ ] Black + ruff 全绿

## 不要做

- 不要重构 prd_compliance_audit.py 其他函数
- 不要改 end_to_end_gate_smoke.py（那是 S06 的事）
- 不要询问是否部署 — 直接 commit + push 到 branch `feat/S05-h8-pass-strict`
