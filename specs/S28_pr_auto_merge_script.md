---
task_id: S28-pr-auto-merge-script
project: gamedata-pipeline
priority: 2
estimated_minutes: 25
depends_on: []
modifies:
  - scripts/auto_merge_green_prs.sh
  - tests/test_auto_merge_script.py
executor: qwen3.6-plus
---

## 目标

`scripts/auto_merge_green_prs.sh` — 扫所有 open PRs，对满足以下条件的自动 squash-merge：
1. mergeable=MERGEABLE (per `gh pr view --json mergeable`)
2. mergeStateStatus=CLEAN
3. All required checks GREEN (iron-law-gate 等)
4. Has label `auto-merge` OR is from `feat/SXX-cluster*` pattern AND has `--auto` flag passed

`--dry-run` 列要 merge 的，不真 merge。
`--auto`: 不需要 label，按 feat/SXX-cluster 模式 batch merge。
`--max N`: 一次最多 merge N 个（防爆 main）。

## 约束

- 用 `gh pr merge --squash --delete-branch`
- 不 force, 不 admin override
- 失败的 PR 写 `dashboard/merge_failures.log`
- 不 merge 含 `WIP` 或 `DO NOT MERGE` label 的

## 验收标准

- [ ] `bash scripts/auto_merge_green_prs.sh --dry-run` 列出 PR 不动作
- [ ] `--max 1 --auto` 真 merge 1 个 ready PR
- [ ] `pytest tests/test_auto_merge_script.py -v` 全绿（mock gh CLI）
- [ ] shellcheck

## 不要做

- 不真 push/merge 到 main 当跑测试
- 不 force-merge
- 不删 PR descriptions
- 直接 commit 到 branch `feat/S28-auto-merge-script`
