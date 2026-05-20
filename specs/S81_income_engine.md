---
task_id: S81-income-engine
priority: 1
estimated_minutes: 35
modifies:
  - backend_stub/income_engine.py
  - backend_stub/main.py
  - tests/test_income_engine.py
executor: qwen3.6-plus
---

## 目标

`backend_stub/income_engine.py` — 从 session uploads 计算每日 income。

rate card (mock):
- BUYER_READY session: \$0.50 each
- STRICT_GATES_PASS_SYNTHETIC: \$0.10 (training data, lower tier)
- FAIL: \$0
- max 10 sessions/day count toward income

Endpoint `GET /api/v1/income/today` (already exists) 现在调用 income_engine 而非返回 mock。

## 验收

- [ ] 1 BUYER_READY upload → income = \$0.50
- [ ] 10 BUYER_READY → \$5.00
- [ ] 15 BUYER_READY → \$5.00 (cap)
- [ ] 1 FAIL → \$0
- [ ] `pytest tests/test_income_engine.py` 全绿

## 不要做

- 不连真 db
- 不实际转钱
- 直接 commit 到 branch `feat/S81-income-engine`
