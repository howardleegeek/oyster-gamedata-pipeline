---
task_id: S91-update-server-appcast
priority: 1
estimated_minutes: 30
modifies:
  - backend_stub/appcast_server.py
  - backend_stub/main.py
  - scripts/gen_appcast.py
  - tests/test_appcast_server.py
executor: qwen3.6-plus
---

## 目标

Backend serves `appcast.xml` for S15 updater. recorder polls every 24h.

1. `backend_stub/appcast_server.py`:
   - `GET /api/v1/updates/appcast.xml` returns XML feed of latest .exe release
   - schema: `<channel><item><title>v0.6.2</title><url>https://github.com/howardleegeek/.../releases/download/v0.6.2/OysterRecorder-setup-v0.6.2.exe</url><sparkle:signature>...</sparkle:signature></item></channel>`
2. `scripts/gen_appcast.py`:
   - read git tags + .exe checksums
   - sign with ed25519 (use existing key from S10)
   - output `appcast.xml`
3. backend serves static file OR generates on-the-fly

## 验收

- [ ] `GET /api/v1/updates/appcast.xml` returns valid XML
- [ ] Contains latest tag
- [ ] ed25519 signature embedded
- [ ] `pytest tests/test_appcast_server.py` 全绿
- [ ] Black + ruff

## 不要做

- 不真签 release .exe (用 placeholder hash)
- 不连真 ed25519 keypair generator (用现有)
- 直接 commit 到 branch `feat/S91-update-server-appcast`
