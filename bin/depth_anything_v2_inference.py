import hashlib
import shutil
import warnings
from pathlib import Path
from typing import Optional

import imageio.v3 as iio
import numpy as np
import torch
import torch.nn.functional as F
from huggingface_hub import hf_hub_download
from PIL import Image
from torchvision import transforms

warnings.filterwarnings("ignore", category=UserWarning, module="torchvision")


_MODEL_CACHE: Optional[torch.nn.Module] = None
_MODEL_VARIANT_MAP = {
    "vits": "LiheYoung/depth-anything-v2-small",
    "vitb": "LiheYoung/depth-anything-v2-base",
    "vitl": "LiheYoung/depth-anything-v2-large",
}


def load_model(variant: str = "vits", device: str = "cpu"):
    """Load and cache the DepthAnything V2 model. Idempotent."""
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE

    repo_id = _MODEL_VARIANT_MAP.get(variant)
    if repo_id is None:
        raise ValueError(f"Invalid variant {variant}. Must be one of {list(_MODEL_VARIANT_MAP.keys())}")

    cache_dir = Path.home() / ".cache" / "oyster_depth"
    cache_dir.mkdir(parents=True, exist_ok=True)

    model_file = hf_hub_download(
        repo_id=repo_id,
        filename="pytorch_model.bin",
        cache_dir=cache_dir,
    )

    from depth_anything_v2.dpt import DepthAnythingV2

    model = DepthAnythingV2(encoder=variant.replace("vit", "vits"), features=64, out_channels=[48, 96, 192, 384])
    state_dict = torch.load(model_file, map_location="cpu", weights_only=True)
    model.load_state_dict(state_dict)
    model.eval()
    model.to(device)
    _MODEL_CACHE = model
    return model


def infer_depth_for_video(
    video_path: Path,
    output_dir: Path,
    *,
    model_variant: str = "vits",
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
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    output_dir = Path(output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    try:
        model = load_model(variant=model_variant, device=device)
    except Exception as e:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise RuntimeError(f"Failed to load model: {e}")

    try:
        frames = iio.imread(video_path, plugin="FFMPEG", format="mp4")
    except Exception as e:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise RuntimeError(f"Failed to read video: {e}")

    if frames.ndim == 3:
        frames = frames[np.newaxis, ...]
    elif frames.ndim != 4:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise RuntimeError(f"Unexpected video shape: {frames.shape}")

    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])

    manifest = {}
    try:
        for idx, frame in enumerate(frames):
            try:
                pil_image = Image.fromarray(frame)
                input_tensor = transform(pil_image).unsqueeze(0).to(device)

                with torch.no_grad():
                    depth = model(input_tensor)
                    depth = F.interpolate(
                        depth.unsqueeze(1),
                        size=pil_image.size[::-1],
                        mode="bilinear",
                        align_corners=False,
                    ).squeeze()

                depth_np = depth.cpu().numpy().astype(np.float32)

                import OpenEXR
                import Imath

                exr_path = output_dir / f"frame_{idx:06d}.exr"
                header = OpenEXR.Header(pil_image.width, pil_image.height)
                channel = Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))
                header["channels"] = {"Z": channel}
                exr = OpenEXR.OutputFile(str(exr_path), header)
                exr.writePixels({"Z": depth_np.tobytes()})
                exr.close()

                with open(exr_path, "rb") as f:
                    sha = hashlib.sha256(f.read()).hexdigest()
                manifest[idx] = sha

            except Exception as e:
                shutil.rmtree(output_dir, ignore_errors=True)
                raise RuntimeError(f"Inference failed for frame {idx}: {e}")

    except Exception:
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        raise

    return manifest