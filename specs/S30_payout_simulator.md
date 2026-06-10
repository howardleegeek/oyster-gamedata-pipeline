---
task_id: S30-payout-simulator
project: gamedata-pipeline
priority: 1
estimated_minutes: 40
depends_on:
  - S25-backend-stub-fastapi
modifies:
  - backend_stub/payout.py
  - backend_stub/main.py
  - tests/test_payout_simulator.py
executor: qwen3.6-plus
---

## 目标

`backend_stub/payout.py` — 模拟 PayPal/Stripe payout 时序，支持 24h SLA 验证（ISC-5）。

Endpoints:
1. `POST /api/v1/payouts/queue` (Bearer) → { payout_id, queued_at, est_arrival } — 入队
2. `GET /api/v1/payouts/{id}` (Bearer) → { id, status: queued|processing|paid|failed, ... }
3. `POST /api/v1/payouts/{id}/simulate` (admin token) → 立刻标 paid (测试用)

Worker thread：每 5 min 跑一遍，把 queued > 1h 的 → processing，processing > 30min → paid (with mock txn_id)。模拟真实 PayPal 时序但加速 24×。

## 约束

- 状态机：queued → processing → paid OR queued → failed
- 内存 dict 存（重启清空）
- Daily limit per user: $1000 (mock)
- 不真连 PayPal/Stripe API
- 加 `--accelerate N` flag 测试时把时间压缩 N×

## 验收标准

- [ ] `POST /payouts/queue` 入队 + JSON 返回 id
- [ ] `POST /simulate` 立刻标 paid
- [ ] worker thread 真自动推进状态
- [ ] daily limit hits → return 429 with retry-after
- [ ] `pytest tests/test_payout_simulator.py -v` 全绿
- [ ] Black + ruff

## 不要做

- 不真转钱
- 不连真 Stripe/PayPal
- 不写 chargeback / refund 流程
- 直接 commit 到 branch `feat/S30-payout-simulator`
