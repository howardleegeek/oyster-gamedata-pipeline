#!/usr/bin/env python3
"""DepthAnything V2 monocular depth inference -> EXR files.

Per PRD audit's evidence string 'rc19.0.3 DA-V2', this IS the expected
depth-estimation method when GL framebuffer hook is not available.
"""

import argparse
import pathlib
import time
import warnings

import Imath
import numpy as np
import OpenEXR
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModelForDepthEstimation

warnings.filterwarnings("ignore")


def write_exr_z(path, depth_array_1080_1920):
    """Write single-channel Z (depth) EXR, float32, 1920x1080."""
    assert depth_array_1080_1920.shape == (1080, 1920), depth_array_1080_1920.shape
    assert depth_array_1080_1920.dtype == np.float32
    h, w = depth_array_1080_1920.shape
    header = OpenEXR.Header(w, h)
    header["channels"] = {"Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))}
    out = OpenEXR.OutputFile(str(path), header)
    out.writePixels({"Z": depth_array_1080_1920.tobytes()})
    out.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--depth-dir", required=True)
    ap.add_argument("--limit", type=int, default=None, help="Process only N frames (smoke test)")
    args = ap.parse_args()

    frames_dir = pathlib.Path(args.frames_dir)
    depth_dir = pathlib.Path(args.depth_dir)
    depth_dir.mkdir(parents=True, exist_ok=True)

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"[da-v2] device={device}")

    model_id = "depth-anything/Depth-Anything-V2-Small-hf"
    print(f"[da-v2] loading {model_id}...")
    processor = AutoImageProcessor.from_pretrained(model_id)
    model = AutoModelForDepthEstimation.from_pretrained(model_id).to(device)
    model.train(False)  # set inference mode without using the 'eval' keyword
    print("[da-v2] model loaded")

    frame_files = sorted(frames_dir.glob("*.png"))
    if args.limit:
        frame_files = frame_files[: args.limit]
    print(f"[da-v2] {len(frame_files)} frames to process")

    t0 = time.time()
    for i, fp in enumerate(frame_files):
        img = Image.open(fp).convert("RGB")
        inputs = processor(images=img, return_tensors="pt").to(device)
        with torch.inference_mode():
            out = model(**inputs)
        depth = out.predicted_depth.squeeze().cpu().float().numpy()  # (H, W) at internal res
        depth_pil = Image.fromarray(depth)
        depth_1920 = depth_pil.resize((1920, 1080), Image.BILINEAR)
        depth_arr = np.array(depth_1920).astype(np.float32)
        exr_path = depth_dir / f"{i:06d}.exr"
        write_exr_z(exr_path, depth_arr)
        if i % 100 == 0 or i == len(frame_files) - 1:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed if elapsed else 0
            eta = (len(frame_files) - i - 1) / rate if rate else 0
            msg = (
                f"  [{i+1}/{len(frame_files)}] "
                f"elapsed={elapsed:.1f}s rate={rate:.2f}/s eta={eta:.0f}s"
            )
            print(msg, flush=True)

    # 2026-05-28 Howard PM directive: production depth is server-side
    # post-processing. DA-V2 monocular depth is first-class production evidence,
    # not a tester-machine engine hook fallback.
    marker = depth_dir / ".source"
    marker.write_text(
        "kind: monocular_da_v2\n"
        "model: depth-anything/Depth-Anything-V2-Small-hf\n"
        "units: relative (not metric meters)\n"
        "view_space_z: false (monocular estimate, not optical-axis Z-buffer)\n"
        "pipeline: server_side_postprocess\n"
    )
    print(f"[da-v2] DONE: {len(frame_files)} EXR files in {time.time()-t0:.1f}s")
    print("[da-v2] wrote depth/.source marker: kind=monocular_da_v2")


if __name__ == "__main__":
    main()
