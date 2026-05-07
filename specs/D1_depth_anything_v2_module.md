# D1 — DepthAnything V2 inference module (atomic, no pipeline dependencies)

## Goal
Implement a single Python module: `bin/depth_anything_v2_inference.py`

## Public API

```python
def infer_depth_for_video(
    video_path: Path,
    output_dir: Path,
    *,
    model_variant: str = "vits",  # vits | vitb | vitl
    device: str = "cpu",
) -> dict[int, str]:
    """Run DepthAnything V2 on every frame of video_path; write per-frame EXR.

    Args:
        video_path: input mp4.
        output_dir: where to write frame_NNNNNN.exr files. Will be created.
        model_variant: ViT-S (smallest, ~25M params) recommended for CPU.
        device: 'cpu' or 'cuda'.

    Returns:
        manifest: dict mapping frame_index → sha256 hex of the EXR file content.

    Raises:
        FileNotFoundError: video_path or model weights missing.
        RuntimeError: any frame's inference fails. The output_dir is left clean
                      (rmtree on failure).
    """


def load_model(variant: str = "vits", device: str = "cpu"):
    """Load and cache the DepthAnything V2 model. Idempotent."""
```

## Hard requirements

1. **Read frames**: use `imageio` + `imageio-ffmpeg`. **DO NOT use cv2.**
2. **Write EXR**: use `OpenEXR` + `Imath`. Single-channel `Z` float32, native
   resolution (do not resize input).
3. **Hash**: `hashlib.sha256(open(exr_path, 'rb').read()).hexdigest()`.
4. **Model**: download DepthAnything V2 ViT-S weights via HuggingFace
   `LiheYoung/depth-anything-v2-small` on first call. Cache to
   `~/.cache/oyster_depth/`. Subsequent calls reuse cache.
5. **NO synthetic fallback**. If model load fails, raise. If frame inference
   fails, raise. Output dir must be all-real or empty.
6. Use `torch.no_grad()` for inference.
7. Inputs are normalized per DepthAnything spec (ImageNet mean/std).
8. EXR coordinate convention: standard OpenEXR `Z` channel, float32.

## Test (must pass `pytest -q`)

```python
# tests/test_depth_anything_v2_inference.py
import math
from pathlib import Path
import tempfile
import imageio.v3 as iio
import numpy as np
import pytest

from bin.depth_anything_v2_inference import infer_depth_for_video, load_model


@pytest.fixture
def tiny_video(tmp_path):
    """5-frame synthetic video — 256x256, deterministic gradient per frame."""
    out = tmp_path / "tiny.mp4"
    frames = []
    for i in range(5):
        f = np.full((256, 256, 3), fill_value=(i * 50) % 256, dtype=np.uint8)
        frames.append(f)
    iio.imwrite(out, frames, fps=30, codec="libx264")
    return out


def test_inference_writes_one_exr_per_frame(tiny_video, tmp_path):
    out = tmp_path / "depth"
    manifest = infer_depth_for_video(tiny_video, out, model_variant="vits", device="cpu")
    assert len(manifest) == 5
    for i in range(5):
        assert (out / f"frame_{i:06d}.exr").exists()
    # All EXR hashes should be DIFFERENT (different inputs → different depths)
    sha_set = set(manifest.values())
    assert len(sha_set) >= 2, "All frames produced identical depth — inference broken"


def test_missing_video_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        infer_depth_for_video(tmp_path / "nope.mp4", tmp_path / "out")


def test_corrupt_video_raises_and_cleans(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a real mp4")
    out = tmp_path / "depth"
    with pytest.raises(RuntimeError):
        infer_depth_for_video(bad, out)
    # Output dir should not exist (cleaned on failure) or be empty
    assert not out.exists() or list(out.glob("*.exr")) == []
```

## Acceptance

- [ ] `python3 -m py_compile bin/depth_anything_v2_inference.py`
- [ ] `python3 -m pytest tests/test_depth_anything_v2_inference.py -q`
- [ ] All 3 tests pass.
- [ ] Module never imports cv2.

## Don't

- Don't fall back to synthetic gradient on model load failure.
- Don't import cv2.
- Don't modify any other file in the repo.
- Don't include the model weights in the repo.
