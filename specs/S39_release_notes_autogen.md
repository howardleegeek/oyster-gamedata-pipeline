---
task_id: S39-release-notes-autogen
project: gamedata-pipeline
priority: 2
estimated_minutes: 30
depends_on: []
modifies:
  - scripts/gen_release_notes.py
  - tests/test_gen_release_notes.py
executor: qwen3.6-plus
---

## 目标

`scripts/gen_release_notes.py` — 从 git log + merged PRs 自动生成 release notes markdown。

输入: 两个 git ref (prev_tag, curr_tag)
输出: markdown 段：
```markdown
## v0.5.0 (2026-05-19)

### Features
- feat(audit): H8 PASS_STRICT tier (#24) — Wave 1
- feat(audit): --strict-buyer evidence_provenance (#25) — Wave 1
- ...

### Daemons
- feat(daemon): iter-watcher (#26)
- ...

### CI / Workflows
- ...

### Fixes
- fix(S07v2): bind subprocess result var (#32)
- ...

### Cluster metrics
- N specs dispatched, M PRs merged
- ~$X total cluster cost
```

从 `gh pr list --state merged` 拿 PR 数据 + commit log 拿 commit msg。按 conventional commit type 分组。

## 约束

- 不真发 release（只 print markdown）
- 用 `gh` CLI + `git log --pretty`
- 默认 prev=last tag, curr=HEAD
- 不依赖外部 API（gh CLI 用 GH_TOKEN 即可）

## 验收标准

- [ ] `python3 scripts/gen_release_notes.py --prev v0.4.1 --curr HEAD` 输出有效 markdown
- [ ] grouping by commit type 工作
- [ ] PR link 都对（#NN 转 URL）
- [ ] `pytest tests/test_gen_release_notes.py -v` 全绿（mock git + gh CLI）
- [ ] Black + ruff

## 不要做

- 不真 push tag / 创 release
- 不发送 email / 通知
- 不修改 CHANGELOG.md（独立工具）
- 直接 commit 到 branch `feat/S39-release-notes-autogen`
