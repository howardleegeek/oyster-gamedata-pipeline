---
task_id: S40-pr-conflict-resolver
project: gamedata-pipeline
priority: 2
estimated_minutes: 35
depends_on: []
modifies:
  - scripts/pr_conflict_resolver.py
  - tests/test_pr_conflict_resolver.py
executor: qwen3.6-plus
---

## 目标

`scripts/pr_conflict_resolver.py` — 当 main 合了一个 PR 后，自动 rebase 所有受影响的 sibling PRs (其他 feat/SXX-cluster 分支)。

Flow:
1. `gh pr list --state open --json number,headRefName` 拿 open PRs
2. 对每个 PR：`git fetch + checkout + git rebase origin/main`
3. 冲突时：dump conflict 到 `dashboard/pr_conflicts/<PR>.diff` + `gh pr comment <PR> --body "auto-rebase failed: see attached"`
4. 无冲突：`git push --force-with-lease origin <branch>`

`--dry-run` 列要 rebase 的，不真动。
`--only <branch_pattern>` 限定范围。

## 约束

- 用 `--force-with-lease` 不裸 `--force`
- 不 merge — 只 rebase
- 失败的不阻塞其他
- Black + ruff

## 验收

- [ ] `--dry-run` 列对 PRs
- [ ] 真 rebase 没冲突的 PR 成功
- [ ] 冲突写到 `dashboard/pr_conflicts/`
- [ ] `pytest tests/test_pr_conflict_resolver.py -v` 全绿 (mock git/gh)

## 不要做

- 不真 merge
- 不 force-push 不安全
- 直接 commit 到 branch `feat/S40-pr-conflict-resolver`
