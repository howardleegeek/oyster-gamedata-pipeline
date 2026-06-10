---
task_id: S07-rsv01-hardening
project: gamedata-pipeline
priority: 1
estimated_minutes: 35
depends_on: []
modifies:
  - bin/real_session_validator.py
  - tests/test_real_session_validator_hardening.py
executor: qwen3.6-plus
---

## 目标

`bin/real_session_validator.py` 当前在大 session 上会卡。加：
1. 每个 gate 调用 timeout 60s（subprocess.run timeout 参数），超时算 FAIL_TIMEOUT
2. 单个 session 失败不阻塞整体 sweep — 加 `--continue-on-error`
3. 总 verdict 输出 JSON 写到 `--output <path>`（除了已有的 markdown）
4. 加 `--sample N` 选项随机抽 N 个 session（10-session sweep 不必每次全跑）

## 约束

- 只改 `bin/real_session_validator.py` + 加新测试文件
- 已有 markdown 报告输出保留
- JSON schema:
  ```json
  {
    "sweep_started": "ISO8601",
    "sweep_finished": "ISO8601",
    "sessions_total": N,
    "sessions_buyer_ready": N,
    "sessions_strict_violations": N,
    "sessions_timeout": N,
    "per_session": [{ "session_id": "...", "verdict": "BUYER_READY|...", "duration_s": 12.3 }]
  }
  ```

## 验收标准

- [ ] `--continue-on-error` flag 实现
- [ ] `--output report.json` 写出符合 schema
- [ ] `--sample 3` 随机抽样
- [ ] 单 session 60s 超时 FAIL_TIMEOUT 分类
- [ ] `pytest tests/test_real_session_validator_hardening.py -v` 全绿
- [ ] Black + ruff

## 不要做

- 不改 audit gate 内部
- 不改 markdown 报告格式
- 直接 commit 到 branch `feat/S07-rsv01-hardening`
