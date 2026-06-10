---
task_id: D1-mc-mod-zbuffer-capture
project: gamedata-recorder-mod
priority: 1
estimated_minutes: 45
depends_on: []
modifies:
  - vendor/recorder/mc-mod/src/main/kotlin/ai/oyster/recorder/mod/ZBufferCapture.kt
  - vendor/recorder/mc-mod/src/main/kotlin/ai/oyster/recorder/mod/OysterRecorderMod.kt
  - vendor/recorder/mc-mod/src/main/resources/fabric.mod.json
executor: codex-aliyun
iron_law_waived: "New file create — touches mod source, no banned terms expected but flagging to be safe."
---

## 目标 (Howard 2026-05-18, Week 1 Gap #1)

让 Fabric mod 抓 GL depth buffer 写到磁盘，给 recorder finalize 用来生成
ground-truth EXR depth — 替代当前的 DA-V2 monocular fallback。

## 上下文

当前 v0.3.0：depth/.source.kind = "monocular_da_v2"，audit H8 = SKIP_honest。
买方反馈要 engine ground-truth depth（绝对距离 / cm 级精度）。已有 mod scaffold
在 `vendor/recorder/mc-mod/`，需要加 ZBufferCapture 模块。

## 约束

- 用 Fabric Loader 0.15+ + Fabric API 0.91+ (MC 1.21.1 lane 优先)
- 不破坏现有 mod entry points (camera/player/world state writers 保持工作)
- 不引入 OpenEXR Java 依赖 — mod 只写原始 f32 bytes，EXR 转换在 Python 端 (D2)
- 文件大小目标：1920×1080×4B = 8.3MB / tick × 20tps = 165MB/s 持续写入，
  必须 async / buffered 否则掉帧

## 验收标准

- [ ] `ZBufferCapture.kt` 实现 `onWorldRenderAfter` hook (WorldRenderEvents.AFTER_TRANSLUCENT)
- [ ] 读取当前 framebuffer 的 depth attachment via `GL11.glReadPixels(GL_DEPTH_COMPONENT, GL_FLOAT)`
- [ ] Reverse-projection: depth-buffer value → world-space meters using
      `Camera#getNear`, `Camera#getFar`, projection matrix (linearize z)
- [ ] 写入 `~/Documents/OysterClips/active_session/zbuffer/tick_<N>.bin`
      格式：header (12 bytes: u32 width, u32 height, u32 tick_id) + raw f32 LE
- [ ] Async write via `CompletableFuture.runAsync()` — 不阻塞 render thread
- [ ] Bounded queue (max 60 pending) — 满了就 drop 最老的，log WARN
- [ ] `fabric.mod.json` 加 entry point `client: ai.oyster.recorder.mod.ZBufferCapture::init`
- [ ] Gradle build 通过：`./gradlew build` 在 mc-mod/ 内不报错
- [ ] 添加 toggle env var：`OYSTER_ZBUFFER_CAPTURE=1` 才启用（默认 off，避免破坏现有 user）

## 测试

- [ ] 写 `vendor/recorder/mc-mod/src/test/kotlin/ZBufferCaptureTest.kt`：
      mock LWJGL `GL11.glReadPixels` → 喂 known projection matrix → assert
      linearized depth values 在 0.1–100m 范围内 (sanity)
- [ ] `./gradlew test` 全绿

## 不要做

- ❌ 不要写 EXR （那是 D2 Python 端的事）
- ❌ 不要 sync write 阻塞 render thread (game 会卡)
- ❌ 不要碰已有 camera/player writers
- ❌ 不要假设 32-bit depth buffer — 有些 MC 配置是 24-bit + 8-bit stencil
