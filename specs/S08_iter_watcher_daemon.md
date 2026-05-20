---
task_id: S08-iter-watcher-daemon
project: gamedata-pipeline
priority: 2
estimated_minutes: 45
depends_on: []
modifies:
  - daemon/iter_watcher.py
  - tests/test_iter_watcher.py
  - daemon/__init__.py
executor: qwen3.6-plus
---

## 目标

新建 `daemon/iter_watcher.py` — 每小时 cron 跑一次：
1. 跑 `bin/prd_compliance_audit.py` against 最新 finalized session（若无则 against 合成 fixture）
2. 找出 FAIL/SKIP 项
3. 对每个 FAIL 自动生成一个 spec 草稿写到 `specs/auto/auto-YYYYMMDD-HHMM-<gate_id>.md`
4. 每个 spec 草稿格式参考 `specs/S05_*.md` (YAML frontmatter + 4 节)
5. 已有的 `specs/auto/*.md` 不覆盖（去重 by gate_id + day）

## 约束

- 新建 `daemon/` 目录（含 `__init__.py`）
- 不直接 dispatch — 只写 spec 草稿；dispatch 由 S23 cluster-dispatcher 接管
- 不修改 `bin/*` 或 `specs/S*.md`（只动 `specs/auto/`）
- 加 `--dry-run` flag（只 print 不写文件，便于 cron test）
- 加 `--once` flag（cron 一次性跑）vs 长跑（无 flag = while True sleep 3600）

## 验收标准

- [ ] `python3 daemon/iter_watcher.py --once --dry-run` 在合成 fixture 上输出准 spec 草稿
- [ ] `--once` 真写 `specs/auto/auto-*.md`
- [ ] 去重：相同 gate_id 同一天只生成一份
- [ ] `pytest tests/test_iter_watcher.py -v` 全绿
- [ ] Black + ruff

## 不要做

- 不改 audit gate
- 不写 dispatcher 逻辑
- 不打 git commit / 不调 cluster API
- 直接 commit 到 branch `feat/S08-iter-watcher`
