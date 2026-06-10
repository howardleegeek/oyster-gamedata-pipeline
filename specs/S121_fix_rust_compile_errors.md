---
task_id: S121-fix-rust-compile-errors
priority: 1
estimated_minutes: 30
target_repo: gamedata-recorder
modifies:
  - vendor/recorder/Cargo.toml
  - vendor/recorder/src/tray/mod.rs
  - vendor/recorder/src/auth/oauth_pkce.rs
  - vendor/recorder/src/notify/mod.rs
  - vendor/recorder/src/notify/income_poller.rs
  - vendor/recorder/src/updater/mod.rs
executor: qwen3.6-plus
---

## 目标 — 修真 CI 暴露的 8 个 Rust 编译错误

Real CI cargo build --release shows:

```
error[E0599]: no method named `menu` found for struct `TrayIcon`
error[E0433]: cannot find module or crate `rand`
error[E0433]: cannot find module or crate `urlencoding`
error[E0433]: cannot find module or crate `hex`
error[E0433]: cannot find module or crate `fastrand`
error[E0599]: no method named `timeout_ms` for `notify_rust::Notification`
```

**Step 1**: read Cargo.toml + the 5 src/ files listed above

**Step 2**: write_file `Cargo.toml` — ADD to `[dependencies]`:
```toml
rand = "0.8"
urlencoding = "2.1"
hex = "0.4"
fastrand = "2.1"
```
KEEP all existing deps unchanged.

**Step 3**: write_file `src/tray/mod.rs` — fix `TrayIcon::menu` usage. The `tray-icon` 0.21 crate uses `TrayIconBuilder::with_menu(menu)` at build time, NOT `tray.menu(...)` post-build. Restructure init to pass menu via builder.

Pattern:
```rust
use tray_icon::{TrayIcon, TrayIconBuilder, menu::Menu};

pub fn start() -> Result<TrayIcon> {
    let menu = Menu::new();
    // ... add menu items ...
    let icon = TrayIconBuilder::new()
        .with_menu(Box::new(menu))
        .with_tooltip("Oyster Recorder")
        .build()?;
    Ok(icon)
}
```

**Step 4**: write_file `src/notify/income_poller.rs` and `src/notify/mod.rs` — replace `Notification::timeout_ms(N)` with `Notification::timeout(notify_rust::Timeout::Milliseconds(N))`. The notify-rust 4.11 API renamed.

**Step 5**: run_cmd("cd vendor/recorder && cargo check 2>&1 | head -20") to verify error count goes down.

## 验收

- [ ] grep "no method named" in cargo check output → 0 hits
- [ ] grep "cannot find module" → 0 hits
- [ ] cargo check exits non-fatally on remaining warnings only

## 约束

- 不删除模块，只 fix API usage
- 不动 main.rs (separate spec)
- ≤ 20 turns
- 直接 commit 到 branch `fix/S121-rust-compile-errors`
