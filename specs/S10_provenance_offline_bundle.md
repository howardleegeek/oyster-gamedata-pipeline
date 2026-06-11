---
task_id: S10-provenance-offline-bundle
project: gamedata-pipeline
priority: 1
estimated_minutes: 40
depends_on: []
modifies:
  - bin/provenance_verify.py
  - bin/provenance_bundle.py
  - tests/test_provenance_offline_bundle.py
executor: qwen3.6-plus
---

## 目标

让买方 verify 真的"零依赖"。

1. 新加 `bin/provenance_bundle.py`：take 一个 session_dir → 输出单一 .tar.gz 内含：
   - `manifest.signed.json`
   - `session.tar.gz`（数据本体）
   - `pubkey-fingerprint.txt`
   - `verify.sh`（独立的 bash 脚本，不需 Python）
   - `README.md`（30 秒上手指南）
2. `bin/provenance_verify.py` 加 `--offline-bundle <bundle.tar.gz>` mode：
   - 解开 bundle 到临时目录
   - 跑 ed25519 verify
   - exit 0 = 数据完整 + 来自指定 pubkey
   - exit 1 = 失败（详细错误到 stderr）
3. verify.sh 用 openssl + sha256sum（POSIX 工具），不依赖 Python

## 约束

- 不动现有 `provenance_verify.py` 的其他 mode
- bundle 文件大小 ≤ 1.2× session 原始大小（加 metadata + sig 不太多）
- verify.sh 在 macOS bash 3.2 + Linux bash 5 + WSL bash 都能跑
- 测试用真的 ed25519 keypair（test fixture），不用 mock

## 验收标准

- [ ] `bin/provenance_bundle.py <session>` 输出 `<session>.bundle.tar.gz`
- [ ] `bin/provenance_verify.py --offline-bundle <bundle>` exit 0 on valid bundle
- [ ] tampered bundle exit 1，stderr 写出 "signature mismatch" 或 "merkle root mismatch"
- [ ] verify.sh 独立跑也行（不调 Python）
- [ ] `pytest tests/test_provenance_offline_bundle.py -v` 全绿
- [ ] Black + ruff + shellcheck

## 不要做

- 不改 ed25519 key 格式（用现有的）
- 不依赖外部 Python 包（除已有 cryptography）
- 不上传 / 不发邮件
- 直接 commit 到 branch `feat/S10-provenance-offline-bundle`
