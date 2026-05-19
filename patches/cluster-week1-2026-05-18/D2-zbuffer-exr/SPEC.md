---
task_id: D2-zbuffer-to-exr-aligner
project: oyster-gamedata-pipeline
priority: 1
estimated_minutes: 50
depends_on: [D1-mc-mod-zbuffer-capture]
modifies:
  - bin/zbuffer_to_exr.py
  - bin/canonical_pipeline.py
  - tests/test_zbuffer_to_exr.py
executor: codex-aliyun
---

## 目标 (Howard 2026-05-18, Week 1 Gap #1, depends on D1)

把 D1 写出来的 `zbuffer/tick_<N>.bin` 文件转成 OpenEXR 格式，
按 camera frame 时间戳对齐 (nearest-tick interpolation)，
落在 `depth/frame_<N>.exr`。同时写 `depth/.source` honest marker。

## 上下文

D1 在 active_session/zbuffer/ 下写了一堆 tick_<N>.bin (server tick rate = 20 Hz)。
finalize 阶段拿到的 video 是 60 fps camera frames。需要 align：
每个 camera frame → 找最近的 tick (max gap 50ms) → 用该 tick 的 zbuffer。
没有 tick within 50ms → 标 .source.calibrated=false 那帧 fallback DA-V2。

## 约束

- 输入：`<session>/zbuffer/tick_<N>.bin` (D1 输出) + `<session>/game_state.jsonl`
  (含 tick_id ↔ timestamp_ms) + `<session>/action_camera_*.jsonl`
  (含 frame_id ↔ timestamp_ns)
- 输出：`<session>/depth/frame_<N>.exr` (16-bit float) + `<session>/depth/.source`
- 使用 `OpenEXR` Python binding (pip install OpenEXR) — 已在 pyproject.toml
  optional 'exr' extra
- max alignment gap = 50ms (server tick = 50ms 间隔；若超过说明丢 tick)
- 失败模式：如果 zbuffer/ 不存在或为空 → 整个 step 跳过 (不报错)，让 DA-V2 fallback 接管
- 性能：单 session 1000 frames，转换时间 <30s on M1 mac

## 验收标准

- [ ] `bin/zbuffer_to_exr.py` 独立可调：
      ```
      python3 bin/zbuffer_to_exr.py <session_dir>
        --max-gap-ms 50
        --fallback-on-miss true
      ```
- [ ] 读取 `tick_<N>.bin` header (12 bytes) 解析 width/height/tick_id
- [ ] 读取 game_state.jsonl 构造 tick_id → timestamp_ms 表
- [ ] 读取 action_camera_*.jsonl 拿到 camera frame 时间戳
- [ ] 对每个 camera frame：二分搜索 nearest tick；gap ≤ 50ms → 写 EXR；
      gap > 50ms → 跳过 (留给 DA-V2)
- [ ] EXR 写成 16-bit half-float，单 channel "Z"，meters 单位
- [ ] 写 `depth/.source` json:
      ```json
      {
        "kind": "engine_zbuffer",
        "framerate": 60,
        "max_depth_m": 256.0,
        "calibrated": true,
        "frame_count": <N>,
        "alignment_method": "nearest_tick_50ms",
        "gap_misses": <M>,
        "gap_miss_ratio": "<M>/<N>"
      }
      ```
- [ ] 集成到 `bin/canonical_pipeline.py` 作为 `step13_zbuffer_to_exr()`
      在 step12 (upload gate) 之前运行。step13 失败 = WARN 不 fatal。
- [ ] 单元测试 `tests/test_zbuffer_to_exr.py`：
      - 合成 fixture: 5 个 tick_<N>.bin (8×8 depth) + game_state.jsonl + camera frames
      - assert：5 个 EXR 写出来，depth/.source.kind = "engine_zbuffer"
      - 边界 case: 没有 zbuffer/ dir → step 安静跳过，无 EXR 输出
      - 边界 case: gap > 50ms → 该 frame 跳过，gap_misses 计数正确

## 不要做

- ❌ 不要假设 OpenEXR 一定可用 — pip 失败时 graceful skip + WARN log
- ❌ 不要修改 D1 的 .bin 格式
- ❌ 不要碰 DA-V2 fallback 代码 (run_da_v2_depth.py 保持原样)
- ❌ 不要把 H8 audit check 改成 "PASS only if engine_zbuffer" — H8 已经在
  prd_compliance_audit.py 里写好了，D3 spec 会处理它
