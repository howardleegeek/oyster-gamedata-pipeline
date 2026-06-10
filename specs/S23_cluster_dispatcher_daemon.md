---
task_id: S23-cluster-dispatcher-daemon
project: gamedata-pipeline
priority: 2
estimated_minutes: 50
depends_on:
  - S08-iter-watcher-daemon
modifies:
  - daemon/cluster_dispatcher.py
  - tests/test_cluster_dispatcher.py
  - daemon/__init__.py
executor: qwen3.6-plus
---

## 目标

`daemon/cluster_dispatcher.py` — 每 15 分钟 cron 跑：
1. 扫 `specs/auto/*.md`（iter-watcher S08 产出的草稿）和 `specs/S*.md` 标记为 ready
2. 跟踪已 dispatched spec：state file `~/.oyster/cluster_dispatcher_state.json`
3. 对未 dispatched spec：
   - 准备 working dir `/tmp/cluster-<date>/<task_id>-output/`
   - 拷源代码（bin/, tests/）
   - 调 `subprocess.run` on `minimax_agent_simple.py`（env: SPEC_FILE, WORKING_DIR, TASK_ID, AGENT_MODEL=qwen3.6-plus）
   - 限制并发 ≤ 4（避免 rate limit）
   - 跑完后 git diff working_dir vs source — 若有 diff，开 PR by `gh pr create`
4. 失败处理：超时 / cluster 报错 → state 标 `failed`，3 次后放弃

## 约束

- 不真改 spec 文件（只读）
- working dir 永远 /tmp，不动 main repo
- PR 自动开但 **不 merge**（iron-law-gate 没过不会 merge）
- 加 `--once` `--dry-run` `--max-concurrent N` flags
- 不调用 cluster API 以外的外部网络（除 `gh` CLI）

## 验收标准

- [ ] `python3 daemon/cluster_dispatcher.py --once --dry-run` 列出会 dispatch 的 spec
- [ ] state file 正确 dedupe
- [ ] 并发上限生效
- [ ] failed spec 3 次后 mark dead
- [ ] `pytest tests/test_cluster_dispatcher.py -v` 全绿（mock subprocess + gh CLI）
- [ ] Black + ruff

## 不要做

- 不真调 cluster API（测试 mock）
- 不 merge PR（只开）
- 不删 working dir（保留供 debug）
- 直接 commit 到 branch `feat/S23-cluster-dispatcher`
