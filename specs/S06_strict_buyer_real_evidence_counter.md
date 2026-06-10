---
task_id: S06-strict-buyer-real-evidence-counter
project: gamedata-pipeline
priority: 1
estimated_minutes: 40
depends_on:
  - S05-h8-pass-strict-tier
modifies:
  - bin/end_to_end_gate_smoke.py
  - tests/test_end_to_end_gate_smoke_strict_buyer.py
executor: qwen3.6-plus
---

## 目标

`--strict-buyer` 模式现在不区分 synthetic vs real evidence — 合成 fixture session 也能 PASS。改成：
- `--strict-buyer` 单独检查每个 strict-required gate 的 evidence 是不是 real
- 输出 JSON 加 `evidence_provenance` 字段：`real` | `synthetic` | `unknown`
- 顶层 verdict：`BUYER_READY` 只在 **所有 strict-required gate 都是 `real` 且 PASS_STRICT/PASS** 才返回 exit 0

判定 real 的规则（按优先级）：
1. H8 marker 有 `kind: engine_zbuffer` 且 EXR 文件大小总和 > 1MB → real
2. 视频文件 ffprobe 出的 duration 非整数（合成都是整数秒）→ real
3. session_dir 路径含 `OysterClips/finalized/` → real
4. session_dir 路径含 `tests/fixtures/` 或 `/tmp/` → synthetic
5. 其他 → unknown（视为 synthetic 处理）

## 约束

- 只改 `bin/end_to_end_gate_smoke.py` + 新加测试文件
- 保留旧 `--strict-buyer` 行为（gate 状态判定不变），只新加 evidence_provenance 判定
- 顶层 verdict 三档：
  - `BUYER_READY` (real evidence + all strict gates PASS_STRICT/PASS) → exit 0
  - `STRICT_GATES_PASS_SYNTHETIC` (所有 strict gate PASS 但 evidence synthetic) → exit 2（新增）
  - `STRICT_VIOLATIONS` (任何 strict gate FAIL/SKIP) → exit 1（已有）
- 测试覆盖：合成 fixture → exit 2；真 session 全 PASS → exit 0；FAIL → exit 1

## 验收标准

- [ ] `--strict-buyer` JSON 含 `evidence_provenance` 字段
- [ ] 合成 fixture exit 2
- [ ] 真 session（满足 rule 1+2 或 rule 3）exit 0
- [ ] STRICT_VIOLATIONS 保持 exit 1 不变
- [ ] `pytest tests/test_end_to_end_gate_smoke_strict_buyer.py -v` 全绿
- [ ] Black + ruff 全绿

## 不要做

- 不动 `_evaluate_h8` 或任何 audit gate 内部逻辑
- 不重构 verdict 函数 — 在已有 `_compute_verdict` 里加分支即可
- 不改 CLI 参数语义（`--strict-buyer` 仍是 flag）
- 不要询问是否部署 — 直接 commit + push 到 branch `feat/S06-strict-buyer-evidence-provenance`
