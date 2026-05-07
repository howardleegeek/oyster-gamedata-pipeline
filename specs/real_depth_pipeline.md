# Real depth/ pipeline — DepthAnything V2 inference, replace empty placeholder

## Goal
Replace the empty `depth/` directory placeholder in
`bin/recorder_consumer_lite.py:~1796-1810` (currently writes literally
zero EXR files and an empty manifest `{}`) with a real DepthAnything V2
inference pipeline.

After the recorder produces `clip-*/video.mp4`, run depth inference and
populate `clip-*/depth/` with per-frame EXR files matching the buyer
spec.

Output: `bin/recorder_depth_pipeline_real.py` providing:

```python
def generate_depth_directory(video_path: Path, depth_dir: Path,
                             *, fps: int = 30) -> dict:
    """Run DepthAnything V2 on every frame of video_path.

    Args:
        video_path: input mp4 (CFR 30fps).
        depth_dir: output directory (will be created); per-frame EXR
                   written as 0000.exr, 0001.exr, ...
        fps: must match recording fps (PRD: 30).

    Returns:
        manifest dict {frame_index: sha256_of_exr_file_bytes}.
        Same shape used by R22 depth_hash residual.

    Raises:
        FileNotFoundError: if model weights or video missing.
        RuntimeError: if any frame's depth inference fails — NEVER write
                      a partial directory.
    """
```

## Hard requirements

1. Use **DepthAnything V2** (vits or vitb depending on perf — start with
   vits ~25M params for CPU). Reference: https://github.com/DepthAnything/Depth-Anything-V2
2. Model weights ship with the .exe via PyInstaller `--add-data`. NO
   download-on-first-run (testers will be offline / blocked).
3. EXR format: 1920×1080 single-channel float32 (matches PRD spec p4).
4. NO 16×16 all-zeros placeholder. NO hardlinking. Every frame is a
   real inference output.
5. If any frame fails (OOM / weight load fail / video corrupt), **abort
   the whole clip** — do not ship partial depth.
6. CPU-only inference path is fine for stop-gap (slower but always
   works); GPU acceleration via CUDA/CoreML is a follow-up.

## Constraints

- Use PyTorch (already needed for the model).
- Must work on Windows (the recorder's primary platform).
- Memory budget: 2 GB RAM for inference (testers may have 8 GB total).
- Time budget: <10 min for a 6-min clip (~30fps × 360s = 10800 frames)
  on a 4-core CPU. If too slow, downsample to 15 fps depth and document.

## Acceptance

- [ ] `bin/recorder_depth_pipeline_real.py` created.
- [ ] `bin/recorder_consumer_lite.py` imports + calls it; the
  empty-directory placeholder at lines ~1796-1810 is DELETED.
- [ ] PyInstaller spec includes the model weights file.
- [ ] Unit test `tests/test_recorder_depth_pipeline_real.py`:
  - happy path: 5-second test clip → 150 EXR files, all valid float32 1920×1080
  - corrupt video: raises RuntimeError, depth_dir is left clean
  - missing weights: raises FileNotFoundError before any work
- [ ] `python3 -m py_compile` clean.

## Don't do

- Don't ship the empty-directory placeholder as a fallback.
- Don't hardlink one EXR to fake the count.
- Don't synthesize depth from optical flow if the model fails. Abort.
- Don't include the model weights in the git repo (use Git LFS or
  download-at-build-time in CI).
