---
task_id: S22-iron-law-gate-workflow
project: gamedata-pipeline
priority: 1
estimated_minutes: 30
depends_on: []
modifies:
  - .github/workflows/iron-law-gate.yml
  - scripts/iron_law_check.sh
  - tests/test_iron_law_check.py
executor: qwen3.6-plus
---

## 目标

新建 `.github/workflows/iron-law-gate.yml` — PR open / push 时跑：
1. `black --check .`
2. `ruff check .`
3. `pytest tests/ -v -x`（fast-fail）
4. `bash scripts/iron_law_check.sh` — 检查：
   - 没有新增 `pytest.mark.skip` 或 `@pytest.mark.xfail` without 注释
   - 没有 `# TODO real-data` 或类似 placeholder marker
   - `collect_ignore` 列表只能 shrink 不能 grow
   - 至少 1 个 commit 在这个 PR 上（不是空 PR）
5. 任一失败 = exit 1, PR 不能 merge

辅助 `scripts/iron_law_check.sh` POSIX bash。

## 约束

- 不修改现有 GitHub Actions workflows（除新建 iron-law-gate.yml）
- 不动 branch protection rule（手动设）
- iron_law_check.sh 在 macOS/Linux 都能跑
- exit code 严格语义：0=pass, 1=violation, 2=script error

## 验收标准

- [ ] workflow yaml valid (yamllint)
- [ ] iron_law_check.sh 在干净 repo 上 exit 0
- [ ] iron_law_check.sh 在含新 `@pytest.mark.skip` 的 PR 上 exit 1
- [ ] iron_law_check.sh 在 `collect_ignore` grew 的 PR 上 exit 1
- [ ] `pytest tests/test_iron_law_check.py -v` 全绿（mock git diff）
- [ ] shellcheck + Black + ruff

## 不要做

- 不真在外部 repo merge
- 不删除现有测试
- 不修改任何 src/ 代码
- 直接 commit 到 branch `feat/S22-iron-law-gate`
