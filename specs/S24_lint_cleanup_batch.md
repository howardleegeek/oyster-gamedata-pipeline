---
task_id: S24-lint-cleanup-batch
project: gamedata-pipeline
priority: 2
estimated_minutes: 15
depends_on: []
modifies:
  - bin/prd_compliance_audit.py
  - bin/end_to_end_gate_smoke.py
  - bin/provenance_verify.py
  - bin/provenance_bundle.py
  - bin/real_session_validator.py
  - daemon/iter_watcher.py
  - daemon/rsv_feeder.py
  - daemon/cluster_dispatcher.py
  - scripts/auto_release.sh
  - scripts/iron_law_check.sh
  - tests/test_prd_compliance_h8.py
  - tests/test_end_to_end_gate_smoke_strict_buyer.py
  - tests/test_real_session_validator_hardening.py
  - tests/test_provenance_offline_bundle.py
  - tests/test_iter_watcher.py
  - tests/test_rsv_feeder.py
  - tests/test_cluster_dispatcher.py
  - tests/test_auto_release_script.py
  - tests/test_gen_quickstart.py
  - tests/test_iron_law_check.py
executor: qwen3.6-plus
---

## 目标

Engineer agent 验证：Wave 1 + Wave 2 产出的 7 个 PR 都有 cosmetic lint 问题（unsorted imports, unused `import os`, trailing whitespace, missing newline）但 pytest 全过。本 spec 跑 black + ruff --fix 把全部 cluster-generated 文件清干净。

具体步骤：
1. `black bin/ tests/ daemon/ scripts/ docs/`
2. `ruff check --fix bin/ tests/ daemon/ scripts/`
3. 再跑一次 pytest 确认没把测试搞坏
4. 一个 commit 收尾："chore(lint): batch cleanup after Wave 1+2 cluster output"

## 约束

- 不改逻辑、不动 control flow
- 只允许 black + ruff 自动 fix 的改动
- 不动 vendor/recorder/* (那是 Rust)
- 不动 specs/*.md
- 不删测试

## 验收标准

- [ ] `black --check bin/ tests/ daemon/ scripts/ docs/` exit 0
- [ ] `ruff check bin/ tests/ daemon/ scripts/` exit 0
- [ ] `pytest tests/test_prd_compliance_h8.py tests/test_end_to_end_gate_smoke_strict_buyer.py tests/test_iter_watcher.py tests/test_provenance_offline_bundle.py tests/test_gen_quickstart.py tests/test_iron_law_check.py tests/test_cluster_dispatcher.py tests/test_auto_release_script.py -v` 全绿（不含 S07/S21 的 buggy 测试，那是 v2 修）

## 不要做

- 不引新依赖
- 不重构 function signature
- 不改注释（除非 trailing whitespace）
- 直接 commit 到 branch `chore/S24-lint-cleanup`
