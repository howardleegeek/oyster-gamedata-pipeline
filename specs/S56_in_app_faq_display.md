---
task_id: S56-in-app-faq-display
project: gamedata-pipeline
priority: 2
estimated_minutes: 25
depends_on:
  - S50-tester-onboarding-kit
modifies:
  - bin/faq_server.py
  - tests/test_faq_server.py
executor: qwen3.6-plus
---

## 目标

`bin/faq_server.py` — 起 local HTTP server (port 8765) serving FAQ markdown from `docs/TESTER_FAQ.md` as nicely formatted HTML。Tray "Help" menu opens `http://localhost:8765/`。

1. FastAPI single endpoint `GET /` → render TESTER_FAQ.md to HTML（用 markdown lib）
2. CSS：simple readable layout
3. `GET /search?q=...` → fulltext search FAQ entries
4. `GET /troubleshooting` → render TESTER_TROUBLESHOOTING.md
5. Auto-start when recorder tray daemon starts，shut down on tray exit

## 约束

- localhost only (security)
- read-only FAQ md from `docs/`
- 不需要 db
- port 8765 hardcoded (configurable via env)

## 验收

- [ ] `python3 bin/faq_server.py &` 起 port 8765
- [ ] `curl localhost:8765/` returns HTML
- [ ] search `?q=oauth` 返回相关 entry
- [ ] `pytest tests/test_faq_server.py -v` 全绿
- [ ] Black + ruff

## 不要做

- 不需要 web framework heavy lifting（FastAPI 即可）
- 不连 backend
- 不上传 search query
- 直接 commit 到 branch `feat/S56-in-app-faq`
