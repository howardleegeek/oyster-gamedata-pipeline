---
task_id: S72-tester-batch-invite
project: gamedata-pipeline
priority: 2
estimated_minutes: 20
depends_on:
  - S61-tester-invite-form
modifies:
  - bin/send_batch_invites.py
  - tests/test_send_batch_invites.py
  - docs/TESTER_BATCH_TEMPLATE.md
executor: qwen3.6-plus
---

## 目标

`bin/send_batch_invites.py` — Howard 一行命令发 N 个 tester 邀请。

```bash
python3 bin/send_batch_invites.py --emails howard@x.com,bruno@y.com,foo@z.com --backend http://localhost:8500
```

per email:
1. POST `/api/v1/testers/apply` { email, discord_user: derived from email prefix, why_interested: "Internal week 1 tester" }
2. capture tester_id
3. POST `/api/v1/testers/{id}/approve` with admin token
4. capture download_url + tester_id
5. print formatted email body (`docs/TESTER_BATCH_TEMPLATE.md`) with placeholders filled

template includes:
- Hi {name},
- Quick install link: {download_url}
- Your tester ID: {tester_id}
- Steps (3 bullets)
- Support: discord channel
- Disclaimer: alpha software

## 约束

- admin token from env `OYSTER_ADMIN_TOKEN`
- email parse to derive name (prefix before @)
- print to stdout, don't真 SMTP send
- max 10 emails per batch (rate-limit safety)

## 验收

- [ ] CLI accepts comma-sep emails
- [ ] mock backend returns 200 → script prints N email bodies
- [ ] missing admin token → exit 1 with clear msg
- [ ] `pytest tests/test_send_batch_invites.py -v` 全绿
- [ ] Black + ruff

## 不要做

- 不真 SMTP send（Howard 手动 paste）
- 不存 emails to file
- 直接 commit 到 branch `feat/S72-batch-invites`
