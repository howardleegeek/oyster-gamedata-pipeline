---
task_id: S15-winsparkle-auto-update
project: gamedata-pipeline
priority: 2
estimated_minutes: 50
depends_on:
  - S12-recorder-egui-to-tray
  - S14-windows-inno-installer
modifies:
  - vendor/recorder/src/updater/mod.rs
  - vendor/recorder/Cargo.toml
  - update_server/appcast.xml.template
  - update_server/sign_appcast.py
executor: qwen3.6-plus
---

## 目标

WinSparkle-style 静默自动更新。recorder 每 24h 检查一次 update_server，如有新版本：
1. 下载 setup.exe 到 `%TEMP%`
2. ed25519 verify signature（从 appcast.xml 拿 sig）
3. quit current process
4. spawn setup.exe with /SILENT
5. setup 启动后会 kill 旧 process + install + restart tray

`update_server/appcast.xml.template`：jinja 模板生成 appcast feed。
`update_server/sign_appcast.py`：发版时把 appcast.xml 用 ed25519 签名。

## 约束

- Rust async 用 reqwest 拉 appcast.xml
- ed25519 verify 用 `ed25519-dalek` crate
- 不要重新发明 wheel — 用 `cargo-update` / `self_update` crate 但禁掉 GitHub releases 默认源，指向我们的 update_server
- 更新失败不阻断 recorder（log + 下次再试）

## 验收标准

- [ ] `cargo build --release` 全绿
- [ ] `cargo test` updater 单测过
- [ ] update_server/sign_appcast.py 输出符合 schema
- [ ] tampered appcast.xml 触发 ed25519 verify failure（exit log）
- [ ] 24h cron 触发更新检查（tokio timer）

## 不要做

- 不弹 "update available" 对话框（产品要求：静默）
- 不强制 user 同意（已 OAuth 时即 implicit consent）
- 不写自动 rollback（v1 不要）
- 直接 commit 到 branch `feat/S15-auto-update`
