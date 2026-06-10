---
task_id: S82-tos-privacy-draft
priority: 1
estimated_minutes: 25
modifies:
  - docs/TERMS_OF_SERVICE.md
  - docs/PRIVACY_POLICY.md
  - tests/test_tos_privacy_links.py
executor: qwen3.6-plus
---

## 目标

Draft minimum-viable TOS + Privacy Policy for 内测 (US jurisdiction, English).

`TERMS_OF_SERVICE.md`:
- service description (game data recorder + AI training data marketplace)
- eligibility (18+)
- payment terms (mock 内测 phase = no real payout)
- IP ownership (user owns gameplay, grants Oyster license for AI training)
- liability disclaimer (alpha software, AS-IS)
- termination
- governing law (Delaware)

`PRIVACY_POLICY.md`:
- data collected (game screen recording, input events, OAuth identity)
- data uses (AI training, payout calculation)
- data sharing (3rd party AI buyers, anonymized)
- user rights (delete account, export data)
- contact (placeholder@oyster.example)
- GDPR/CCPA acknowledgment

## 验收

- [ ] Both .md ≤ 500 lines
- [ ] TOS contains "AS-IS", "Delaware", "alpha"
- [ ] Privacy contains "OAuth", "delete", "anonymized"
- [ ] `pytest tests/test_tos_privacy_links.py` (validates references) 全绿

## 不要做

- 不咨询律师（disclaimer "draft, not legal advice"）
- 不打 PDF
- 直接 commit 到 branch `feat/S82-tos-privacy-draft`
