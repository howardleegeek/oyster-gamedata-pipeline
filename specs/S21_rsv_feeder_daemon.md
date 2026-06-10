---
task_id: S21-rsv-feeder-daemon
project: gamedata-pipeline
priority: 2
estimated_minutes: 35
depends_on:
  - S07-rsv01-hardening
modifies:
  - daemon/rsv_feeder.py
  - daemon/__init__.py
  - tests/test_rsv_feeder.py
executor: qwen3.6-plus
---

## 目标

新建 `daemon/rsv_feeder.py` — 每 6 小时 cron 跑一次：
1. 扫 `~/Documents/OysterClips/finalized/` 下所有 session 目录
2. 跟踪已处理 session：写 state 到 `~/.oyster/rsv_feeder_state.json`（含 session_id + sha256 + 上次 verdict）
3. 对未处理的 session：调 `bin/real_session_validator.py --sample 1 --output /tmp/rsv_<id>.json`
4. 把所有 verdict 累加到 `dashboard/buyer_ready_pct.json`：`{ "total": N, "buyer_ready": M, "pct": M/N, "updated_at": "..." }`
5. exit 0 if 处理 ≥1，exit 0 if 无新 session（不算错误）

## 约束

- 新建 daemon/__init__.py 如果不存在（与 S08 共用）
- 不重复处理（state file 去重）
- 不删除 finalized session
- 不调 cluster API
- 加 `--once` 和 `--dry-run` 模式

## 验收标准

- [ ] `python3 daemon/rsv_feeder.py --once --dry-run` 在合成 finalized 目录上正确扫描
- [ ] state file 写入正确格式
- [ ] dashboard JSON 累加正确
- [ ] 已处理 session 不重跑（idempotent）
- [ ] `pytest tests/test_rsv_feeder.py -v` 全绿
- [ ] Black + ruff

## 不要做

- 不写新 audit gate
- 不发 notification（先纯 file 输出）
- 不上传到外部
- 直接 commit 到 branch `feat/S21-rsv-feeder`
