---
task_id: S12-recorder-egui-to-tray
project: gamedata-pipeline
priority: 1
estimated_minutes: 50
depends_on: []
modifies:
  - vendor/recorder/src/main.rs
  - vendor/recorder/src/tray/mod.rs
  - vendor/recorder/Cargo.toml
executor: qwen3.6-plus
---

## 目标

按 `/Users/howardli/Downloads/plans/polished-gathering-sifakis.md` Phase 0 — 删 egui，改 tray-icon。

1. 新增 `src/tray/mod.rs` — 用 `tray-icon` crate 起一个 system tray icon
2. tray 图标 3 状态：待机 (gray) / 录制 (red) / 上传 (blue)
3. 右键菜单：Open dashboard | Pause | Exit
4. 重写 `src/main.rs`：tokio runtime 启动 + tray + 录制 daemon thread
5. 删除所有 egui::App impl 但保留 record/upload/validation 等 module（仅去 UI 层）

## 约束

- Rust 2021 edition
- 不动 record/ upload/ validation 等业务模块（只动 UI 层）
- Cargo.toml 加 tray-icon 1.x; remove eframe/egui
- 不改 Cargo.lock 锁的版本 — cargo build 时自动重锁
- macOS + Windows 兼容（tray-icon 跨平台）

## 验收标准

- [ ] `cargo build --release` 全绿（cluster 节点装了 rust toolchain）
- [ ] `cargo test` 通过
- [ ] `src/main.rs` ≤ 200 行
- [ ] grep "eframe\|egui" src/ 0 hit
- [ ] tray-icon 出现并响应右键

## 不要做

- 不动 record/ upload/ validation
- 不加 OAuth（那是 S13）
- 不加自启动（S14）
- 不加更新（S15）
- 直接 commit 到 branch `feat/S12-recorder-egui-to-tray`
