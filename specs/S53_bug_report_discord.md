---
task_id: S53-bug-report-discord
project: gamedata-pipeline
priority: 1
estimated_minutes: 30
depends_on:
  - S51-crash-reporter
modifies:
  - bin/bug_report.py
  - tests/test_bug_report.py
  - docs/BUG_REPORT_TEMPLATE.md
executor: qwen3.6-plus
---

## 目标

`bin/bug_report.py` — CLI tool for 内测 user to report bugs to Discord channel.

1. Prompts:
   - Severity (1-3)
   - Title (1 line)
   - Steps to reproduce
   - Expected behavior
   - Actual behavior
   - Attach: latest crash dump? (y/n)
   - Attach: last 200 lines OysterRecorder.log? (y/n)
2. Posts to Discord webhook (URL from `~/.oyster/config.json` → `bug_report_webhook`)
3. Hashes user identifier for anon attribution
4. Reply user with "Report sent, ID: <uuid>"

`docs/BUG_REPORT_TEMPLATE.md` — markdown template for 内测 user to fill out before pasting.

## 约束

- Discord webhook URL configurable (not hardcoded)
- 不上传 game session data
- 不上传 OAuth token / credentials
- max attach size 2MB (Discord limit)
- Retry once on transient HTTP error

## 验收

- [ ] `python3 bin/bug_report.py` interactive prompts → POST to mock Discord webhook
- [ ] Webhook URL missing → exit with clear error
- [ ] Attached crash dump correctly base64-encoded
- [ ] `pytest tests/test_bug_report.py -v` 全绿 (mock HTTPS POST)
- [ ] Black + ruff

## 不要做

- 不真 ping Discord
- 不上传 OAuth tokens
- 不收集 PII
- 直接 commit 到 branch `feat/S53-bug-report-discord`
