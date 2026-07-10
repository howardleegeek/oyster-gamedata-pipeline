"""
Modal serverless depth endpoint — DA-V2 monocular depth inference.

Deploy with: modal deploy server/modal_depth_app.py
"""

import io
import os
import subprocess
import tarfile
import tempfile

import modal
import numpy as np

# ---------------------------------------------------------------------------
# Stub / App definition
# ---------------------------------------------------------------------------
stub = modal.Stub("oyster-depth")

# ---------------------------------------------------------------------------
# Image definition
# ---------------------------------------------------------------------------
def download_model():
    """Pre-download DA-V2 model weights into the image layer."""
    from huggingface_hub import snapshot_download

    model_dir = "/root/.cache/da_v2_model"
    os.makedirs(model_dir, exist_ok=True)
    # DA-V2 monocular depth model from HuggingFace
    snapshot_download(
        repo_id="depth-anything/Depth-Anything-V2-Small-hf",
        local_dir=model_dir,
        local_dir_use_symlinks=False,
    )
    print(f"Model downloaded to {model_dir}")


image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "transformers>=4.35",
        "torch>=2.1",
        "accelerate",
        "safetensors",
        "huggingface_hub",
        "Pillow",
        "numpy",
        "OpenEXR",
        "imageio",
    )
    .apt_install("ffmpeg")
    .run_function(download_model)
)

# ---------------------------------------------------------------------------
# Core depth computation
# ---------------------------------------------------------------------------
@stub.function(
    image=image,
    gpu="A10G",
    timeout=600,
    concurrency_limit=10,
)
def compute_depth(video_bytes: bytes, fps: int = 6) -> bytes:
    """
    Receive mp4 bytes, return tar.gz of depth/*.exr.

    Pipeline:
      1. Write video to /tmp/in.mp4
      2. ffmpeg extract frames at `fps`
      3. DA-V2 inference on each frame
      4. Write EXRs with kind: server_da_v2
      5. tar.gz the depth/ dir, return bytes
    """
    import glob

    import numpy as np
    import torch
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "in.mp4")
        frames_dir = os.path.join(tmpdir, "frames")
        depth_dir = os.path.join(tmpdir, "depth")
        os.makedirs(frames_dir, exist_ok=True)
        os.makedirs(depth_dir, exist_ok=True)

        # 1. Write video
        with open(video_path, "wb") as f:
            f.write(video_bytes)

        # 2. Extract frames with ffmpeg
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i", video_path,
                "-vf", f"fps={fps}",
                "-q:v", "2",
                os.path.join(frames_dir, "frame_%06d.jpg"),
            ],
            check=True,
            capture_output=True,
        )

        frame_paths = sorted(glob.glob(os.path.join(frames_dir, "frame_*.jpg")))
        if not frame_paths:
            raise RuntimeError("No frames extracted from video")

        print(f"Extracted {len(frame_paths)} frames at {fps} fps")

        # 3. Load DA-V2 model
        from transformers import pipeline

        device = 0 if torch.cuda.is_available() else -1
        depth_pipe = pipeline(
            task="depth-estimation",
            model="depth-anything/Depth-Anything-V2-Small-hf",
            device=device,
        )

        # 4. Run inference on each frame
        for idx, frame_path in enumerate(frame_paths):
            frame = Image.open(frame_path).convert("RGB")
            result = depth_pipe(frame)
            depth_map = result["depth"]  # PIL Image or numpy array

            if isinstance(depth_map, Image.Image):
                depth_array = np.array(depth_map).astype(np.float32)
            else:
                depth_array = np.array(depth_map).astype(np.float32)

            # Normalize to 0-1 range for EXR storage
            d_min = depth_array.min()
            d_max = depth_array.max()
            if d_max - d_min > 0:
                depth_normalized = (depth_array - d_min) / (d_max - d_min)
            else:
                depth_normalized = depth_array

            # 5. Write EXR
            exr_path = os.path.join(depth_dir, f"frame_{idx:06d}.exr")
            write_exr(exr_path, depth_normalized)

            if (idx + 1) % 100 == 0:
                print(f"Processed {idx + 1}/{len(frame_paths)} frames")

        # 6. tar.gz the depth directory
        tar_bytes = io.BytesIO()
        with tarfile.open(fileobj=tar_bytes, mode="w:gz") as tar:
            tar.add(depth_dir, arcname="depth")
        tar_bytes.seek(0)

        print(f"Returning tar.gz with {len(frame_paths)} EXR files")
        return tar_bytes.getvalue()


def write_exr(path: str, depth_array: np.ndarray):
    """Write a single-channel float32 EXR file."""
    import Imath
    import OpenEXR

    height, width = depth_array.shape
    header = OpenEXR.Header(width, height)
    header["channels"] = {
        "Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))
    }
    exr = OpenEXR.OutputFile(path, header)
    exr.writePixels({"Z": depth_array.tobytes()})
    exr.close()


# ---------------------------------------------------------------------------
# HTTP web endpoint
# ---------------------------------------------------------------------------
@stub.function(
    image=image,
    gpu="A10G",
    timeout=600,
    concurrency_limit=10,
)
@modal.web_endpoint(method="POST")
def depth_endpoint(req):
    """
    HTTP entry: receive mp4 via multipart/form-data, return tar.gz of depth EXRs.

    Usage:
        curl -X POST https://oyster-depth.modal.run/depth-endpoint \
             -F "video=@input.mp4" \
             -F "fps=6" \
             -o depth_output.tar.gz
    """

    # req is a FastAPI Request-like object from Modal
    # Modal web_endpoint passes the request body directly
    # For multipart, we parse it manually or use form data

    # Extract video bytes and fps from the request
    # Modal's web_endpoint provides the raw request

    # Get form data
    video_bytes = None
    fps = 6

    # Get form data (for multipart/form-data uploads)
    # Note: form parsing would go here if needed; currently we accept raw bytes
    # Fallback: treat body as raw video bytes
    if hasattr(req, "form"):
        video_bytes = req.body if hasattr(req, "body") else bytes(req)
    else:
        video_bytes = req.body if hasattr(req, "body") else bytes(req)

    # For simplicity with Modal's web_endpoint, we accept raw bytes
    # The client should send the mp4 as the raw body
    if video_bytes is None:
        video_bytes = bytes(req) if isinstance(req, bytes) else req

    # Run depth computation
    result_bytes = compute_depth.remote(video_bytes, fps=fps)

    # Return tar.gz bytes
    from modal import Response

    return Response(
        content=result_bytes,
        media_type="application/gzip",
        headers={"Content-Disposition": "attachment; filename=depth.tar.gz"},
    )


# ---------------------------------------------------------------------------
# Async version for better throughput
# ---------------------------------------------------------------------------
@stub.function(
    image=image,
    gpu="A10G",
    timeout=600,
    concurrency_limit=10,
)
@modal.web_endpoint(method="POST")
async def depth_endpoint_async(req):
    """
    Async HTTP entry for depth computation.
    Accepts multipart form data with 'video' file field and optional 'fps' field.
    """
    from fastapi import UploadFile
    from fastapi.responses import Response

    form = await req.form()
    video_file: UploadFile = form.get("video")
    fps_str = form.get("fps", "6")

    if video_file is None:
        return Response(
            content='{"error": "Missing video field"}',
            status_code=400,
            media_type="application/json",
        )

    video_bytes = await video_file.read()
    fps = int(fps_str)

    # Run depth computation (this is a blocking remote call)
    result_bytes = compute_depth.remote(video_bytes, fps=fps)

    return Response(
        content=result_bytes,
        media_type="application/gzip",
        headers={"Content-Disposition": "attachment; filename=depth.tar.gz"},
    )
