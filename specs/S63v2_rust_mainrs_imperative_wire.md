---
task_id: S63v2-rust-mainrs-imperative-wire
project: gamedata-recorder
priority: 1
estimated_minutes: 20
depends_on: []
modifies:
  - vendor/recorder/src/main.rs
  - vendor/recorder/Cargo.toml
executor: qwen3.6-plus
---

## 目标 — 必须真写文件，不能只 analyze

S63 V1 跑了 40 turns，0 个 write_file。本 spec 给你**精确逐字**的写入指令。

### Step 1 — read `src/main.rs` 现状

read_file('src/main.rs')

### Step 2 — write_file('src/main.rs') 改动如下

在 main.rs 文件**最开头**（任何 `use` 之前）插入 4 行：

```rust
mod tray;
mod auth;
mod updater;
mod notify;
```

在 `#[tokio::main]` 或 `fn main()` 函数体内（注意现有逻辑保留），**最早**位置加 4 行：

```rust
    tray::start();
    auth::ensure_logged_in().await.ok();
    updater::spawn_check_loop(std::time::Duration::from_secs(86400));
    notify::spawn_income_poller("20:00");
```

### Step 3 — write_file('Cargo.toml')

在 `[dependencies]` section 末尾追加（若不在则查找并加）：

```toml
tray-icon = "0.21"
oauth2 = "5.0"
keyring = "3.6"
notify-rust = "4.11"
ed25519-dalek = "2.2"
reqwest = { version = "0.12", features = ["json"] }
self_update = { version = "0.41", default-features = false, features = ["rustls"] }
```

### Step 4 — verify

run_cmd("grep -c 'mod tray' src/main.rs")  必须返回 ≥1
run_cmd("grep -c 'tray::start' src/main.rs")  必须返回 ≥1

## 约束

- 必须实际调用 write_file 至少 2 次（main.rs + Cargo.toml）
- 不允许跑超过 15 turns
- 不允许只 read 不 write
- 不允许重写整个 main.rs，只 surgical insertion

## 验收

- [ ] `grep 'mod tray' src/main.rs` → 1 hit
- [ ] `grep 'mod auth' src/main.rs` → 1 hit
- [ ] `grep 'mod updater' src/main.rs` → 1 hit
- [ ] `grep 'mod notify' src/main.rs` → 1 hit
- [ ] `grep 'tray::start' src/main.rs` → 1 hit
- [ ] `grep 'tray-icon' Cargo.toml` → 1 hit

## 不要做

- 不重构 record/upload/validation
- 不删 OBS code
- 不 analyze 40 turn — surgical 写 + verify + done
- 直接 commit 到 branch `feat/S63v2-mainrs-wire`
