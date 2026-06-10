---
task_id: S111-pii-redaction-screen
priority: 2
estimated_minutes: 30
modifies:
  - bin/pii_redactor.py
  - tests/test_pii_redactor.py
executor: qwen3.6-plus
---

## 目标

`bin/pii_redactor.py` — post-recording redact PII from screen captures (frames containing Discord names, emails, phone numbers).

OCR + regex on rgb/ frames:
1. extract text using pytesseract (lazy import; degrade gracefully if missing)
2. regex match emails / phone / Discord names (pattern: `@\w+#\d{4}` or `@\w+`)
3. blur matched regions in rgb/ frames (PIL ImageDraw)
4. log redactions count per session

## 验收

- [ ] OCR detects email in mock image
- [ ] redact replaces region with black box
- [ ] degrades gracefully if pytesseract missing
- [ ] `pytest tests/test_pii_redactor.py` 全绿
- [ ] Black + ruff

## 不要做

- 不在 recording 时 redact (post-only)
- 不上传未 redact 数据
- 直接 commit `feat/S111-pii-redaction`
