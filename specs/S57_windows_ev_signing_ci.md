---
task_id: S57-windows-ev-signing-ci
project: gamedata-pipeline
priority: 2
estimated_minutes: 30
depends_on:
  - S14-windows-inno-installer
modifies:
  - .github/workflows/build-windows-installer.yml
  - installer/sign_installer.ps1
  - tests/test_sign_script.py
executor: qwen3.6-plus
---

## 目标

GitHub Actions workflow `.github/workflows/build-windows-installer.yml`：
1. trigger: push tag `v*` matching `recorder-v*`
2. checkout + cargo build --release (Rust recorder)
3. Inno Setup compile (using actions/iscc-action 或类似)
4. (optional) sign EXE with EV cert from secrets (env var EV_CERT_PFX base64)
5. upload setup.exe as release asset

`installer/sign_installer.ps1` — local script for signing (用 signtool.exe + EV cert from cert store)。

## 约束

- 不要求 EV cert 存在才能 release（unsigned 也能发，warning）
- secrets 走 GitHub Actions secrets，不进 git
- workflow 在 self-hosted Windows runner OR `windows-latest`
- 测试 mock signtool.exe call

## 验收

- [ ] workflow YAML valid (yamllint)
- [ ] sign_installer.ps1 PowerShell syntax valid
- [ ] graceful skip when EV_CERT_PFX missing
- [ ] `pytest tests/test_sign_script.py -v` 全绿
- [ ] shellcheck-equivalent for PowerShell (PSScriptAnalyzer optional)

## 不要做

- 不真签 release (CI 自己跑)
- 不绑定具体 EV vendor (Sectigo/DigiCert 都接受)
- 直接 commit 到 branch `feat/S57-windows-ev-signing-ci`
