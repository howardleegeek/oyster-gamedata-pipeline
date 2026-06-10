---
task_id: S63-rust-mainrs-wire-modules
project: gamedata-recorder
priority: 1
estimated_minutes: 50
depends_on: []
modifies:
  - vendor/recorder/src/main.rs
  - vendor/recorder/Cargo.toml
executor: qwen3.6-plus
---

## 目标

`vendor/recorder/src/main.rs` — wire in the 4 modules from PR #16 (tray, auth, updater, notify) so they actually do something.

Current state: 6 module files exist (src/tray/, src/auth/, src/updater/, src/notify/) but main.rs doesn't `mod` them or call them.

After wire-in:
1. `mod tray; mod auth; mod updater; mod notify;` at top of main.rs
2. tokio runtime starts:
   - `tray::start()` (tray icon + menu loop)
   - `auth::ensure_logged_in()` (OAuth flow if no token)
   - `updater::spawn_check_loop(24.hours)` (background timer)
   - `notify::spawn_income_poller("20:00 local")` (daily 8pm)
3. Cargo.toml: add new deps if missing (tray-icon, oauth2, reqwest, notify-rust, keyring, ed25519-dalek)
4. existing record/upload loops untouched (just additions, no removal)

## 约束

- 不删除已有 record/upload/validation logic
- 添加 deps 兼容 Cargo.lock 重锁
- 不动 OBS embed
- main.rs ≤ 300 行 after wire-in

## 验收

- [ ] `cargo build --release` 全绿 in vendor/recorder
- [ ] `cargo test` 全绿
- [ ] grep "mod tray" "mod auth" "mod updater" "mod notify" in main.rs → 4 hits
- [ ] grep "tray::start" "auth::ensure_logged_in" "updater::spawn" "notify::spawn" → 4 hits

## 不要做

- 不重写 record/upload modules
- 不删 OBS code
- 不动 existing CLI flags
- 直接 commit 到 branch `feat/S63-mainrs-wire-modules` (on gamedata-recorder repo, NOT parent)
