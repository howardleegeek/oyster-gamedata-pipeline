---
task_id: S13-oauth-google-discord-pkce
project: gamedata-pipeline
priority: 1
estimated_minutes: 60
depends_on:
  - S12-recorder-egui-to-tray
modifies:
  - vendor/recorder/src/auth/mod.rs
  - vendor/recorder/src/auth/oauth_pkce.rs
  - vendor/recorder/Cargo.toml
executor: qwen3.6-plus
---

## 目标

OAuth login via Google + Discord，PKCE 流程，首次安装/未 logged in 时浏览器弹出。

1. 新增 `src/auth/mod.rs` — token storage (encrypted file `~/.oyster/auth.json`)
2. 新增 `src/auth/oauth_pkce.rs`:
   - 起 localhost loopback server (port 0 自动选)
   - 生成 code_verifier + code_challenge (S256)
   - 打开浏览器到 Google/Discord authorize URL
   - 接 callback，换 token
   - 存 access_token + refresh_token (atime + 7-day expiry)
3. 整合到 main：tray 启动时 if not auth → trigger OAuth flow
4. menu 加 "Logout" 项

## 约束

- Provider: Google (well-known config from accounts.google.com/.well-known/openid-configuration) + Discord (discord.com/api/oauth2)
- client_id 写到 const，但 client_secret = NONE (PKCE!)
- 不用 webview / 不嵌入 OAuth UI — 浏览器弹出
- 加密 token 用 `keyring` crate (跨平台 keychain)
- macOS + Windows 兼容

## 验收标准

- [ ] `cargo build --release` 全绿
- [ ] `cargo test --features mock-oauth` 单测过（mock loopback）
- [ ] Manual: 启动 recorder，无 token 时浏览器自动开，登录后 token 写到 keyring
- [ ] `Logout` 菜单清除 token

## 不要做

- 不存 client_secret
- 不实现 password-grant (deprecated by OAuth2.1)
- 不动 tray UI（除加 Logout 菜单项）
- 直接 commit 到 branch `feat/S13-oauth-pkce`
