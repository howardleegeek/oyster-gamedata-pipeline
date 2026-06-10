---
task_id: S93-auto-tag-bot
priority: 2
estimated_minutes: 25
modifies:
  - .github/workflows/auto-tag-on-merge.yml
  - scripts/auto_tag_bot.sh
  - tests/test_auto_tag_bot.py
executor: qwen3.6-plus
---

## 目标

`.github/workflows/auto-tag-on-merge.yml` — after every 3 commits to main since last tag, auto-bump patch version and tag + release.

Steps:
1. trigger: `push` to main
2. count commits since last tag (`git rev-list --count <tag>..HEAD`)
3. if >= 3, bump patch (v0.6.2 → v0.6.3) and tag + push + gh release create
4. body: include git log range + spec IDs (parse commit msgs for "S\d+")

`scripts/auto_tag_bot.sh` — local equivalent for manual run.

## 验收

- [ ] YAML valid
- [ ] commit count threshold configurable via env
- [ ] dry-run mode does not push
- [ ] `pytest tests/test_auto_tag_bot.py` 全绿
- [ ] shellcheck

## 不要做

- 不 auto-tag major/minor (only patch)
- 不真 push tag (CI 跑时才)
- 直接 commit 到 branch `feat/S93-auto-tag-bot`
