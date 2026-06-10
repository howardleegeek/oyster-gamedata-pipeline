---
task_id: S09-release-tagger-workflow
project: gamedata-pipeline
priority: 2
estimated_minutes: 25
depends_on: []
modifies:
  - .github/workflows/auto-release.yml
  - scripts/auto_release.sh
  - tests/test_auto_release_script.py
executor: qwen3.6-plus
---

## 目标

新建 GitHub Actions workflow `.github/workflows/auto-release.yml`：
- 触发：push to main
- 条件：自上次 tag 以来 ≥3 个 commit，OR 距上次 tag ≥6h
- 动作：bump patch version（v0.4.1 → v0.4.2），write CHANGELOG entry, tag + push, gh release create

辅助 shell script `scripts/auto_release.sh`：从 git log 自动 build CHANGELOG 段，bump 版本，调 gh release create。

## 约束

- 不修改现有 workflow
- 不动 main branch 保护规则
- 半角 ASCII 版本号严格 SemVer
- CHANGELOG.md 用 Keep-a-Changelog 格式
- 如果是 feat: commit 触发 → bump minor (v0.4.1 → v0.5.0)
- 如果 BREAKING CHANGE → bump major
- 否则 → bump patch

## 验收标准

- [ ] `.github/workflows/auto-release.yml` 语法 valid (yamllint)
- [ ] `scripts/auto_release.sh` shellcheck 全绿
- [ ] `pytest tests/test_auto_release_script.py -v` 全绿（mock git log 测 SemVer 计算）
- [ ] Workflow 在 dry-run mode 不真 tag

## 不要做

- 不真 push tag（只在 CI 真跑时）
- 不删除已有 release
- 不修改 main 分支保护
- 直接 commit 到 branch `feat/S09-release-tagger`
