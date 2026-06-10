---
task_id: S29-session-fixture-generator
project: gamedata-pipeline
priority: 2
estimated_minutes: 30
depends_on: []
modifies:
  - bin/generate_session_fixture.py
  - tests/test_session_fixture_generator.py
  - tests/fixtures/.gitkeep
executor: qwen3.6-plus
---

## 目标

`bin/generate_session_fixture.py` — 生成结构完整的合成 session（用于 RSV01 + audit 测试），不需要真 MC。

输出目录结构：
```
<session_dir>/
  manifest.json (含 session_id, start/end ts, frame_count, sha256)
  rgb/frame_0001.png .. frame_0900.png (900 frames @ 30fps × 30s, 1 byte placeholder)
  depth/.source (kind=engine_zbuffer, gap_miss_ratio=0.005, frame_count=900)
  depth/frame_0001.exr .. (900 placeholder EXRs)
  segmentation/frame_0001.png ..
  normals/frame_0001.exr ..
  camera_params.json (含 intrinsics)
  poses.json (900 poses)
  video.mp4 (placeholder 30s h264 30fps 6Mbps minimum metadata)
  audio.wav
  controls.csv
  inputs.jsonl
```

CLI flags:
- `--output <dir>` 必填
- `--duration 30` 秒
- `--gap-miss-ratio 0.005` (≤0.01 → H8 PASS_STRICT)
- `--video-duration-bias 320` (≥300 ≤360 buyer pdf 区间)

## 约束

- 所有文件都是 placeholder（minimum valid header + zero padding）
- mp4 用 `ffmpeg -f lavfi -i nullsrc=...` 或纯 binary header
- 生成 ≤ 5 秒
- 不依赖 OBS、MC、真录制工具

## 验收标准

- [ ] `python3 bin/generate_session_fixture.py --output /tmp/fixture_001` 5s 内 exit 0
- [ ] `python3 bin/end_to_end_gate_smoke.py /tmp/fixture_001` BUYER_READY（合成 evidence 但 PASS_STRICT 都过）
- [ ] `pytest tests/test_session_fixture_generator.py -v` 全绿
- [ ] Black + ruff

## 不要做

- 不真录 mp4
- 不需要 ffmpeg 装在测试环境
- 不上传 fixture 到 git（用 .gitkeep）
- 直接 commit 到 branch `feat/S29-session-fixture-generator`
