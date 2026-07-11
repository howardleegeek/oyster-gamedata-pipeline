#!/usr/bin/env python3
"""Export Depth Anything V2 to ONNX format.

Promoted from /tmp/poc_onnx_export.py with clean CLI, manifest generation,
and optional Aliyun OSS upload.

Usage:
    python3 bin/export_da_v2_to_onnx.py \
        --model-id depth-anything/Depth-Anything-V2-Small-hf \
        --output-dir models/da-v2-small-onnx/v1/ \
        --opset 17 \
        --upload-to-oss
"""
import argparse
import hashlib
import json
import pathlib
import sys
import time
import warnings
from datetime import datetime, timezone

import numpy as np
import torch
from PIL import Image

warnings.filterwarnings("ignore")


def sha256_file(path: pathlib.Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upload_to_oss(local_dir: pathlib.Path, bucket: str, prefix: str) -> None:
    """Upload ONNX model files to Aliyun OSS.

    Requires oss2 package and OSS credentials via environment variables:
        OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET
    """
    try:
        import oss2
    except ImportError:
        print("  oss2 not installed. Run: pip install oss2")
        sys.exit(1)

    auth = oss2.Auth(
        _env_or_die("OSS_ACCESS_KEY_ID"),
        _env_or_die("OSS_ACCESS_KEY_SECRET"),
    )
    endpoint = "https://oss-cn-hangzhou.aliyuncs.com"
    bucket_obj = oss2.Bucket(auth, endpoint, bucket)

    for fpath in local_dir.iterdir():
        if fpath.is_file():
            key = f"{prefix}/{fpath.name}"
            print(f"  uploading {fpath.name} -> {key}")
            bucket_obj.put_object_from_file(key, str(fpath))
    print("  upload complete")


def _env_or_die(name: str) -> str:
    import os
    val = os.environ.get(name)
    if not val:
        print(f"  ERROR: environment variable {name} not set")
        sys.exit(1)
    return val


def main():
    parser = argparse.ArgumentParser(
        description="Export Depth Anything V2 to ONNX format"
    )
    parser.add_argument(
        "--model-id",
        default="depth-anything/Depth-Anything-V2-Small-hf",
        help="HuggingFace model ID (default: depth-anything/Depth-Anything-V2-Small-hf)",
    )
    parser.add_argument(
        "--output-dir",
        default="models/da-v2-small-onnx/v1",
        help="Directory to write ONNX files (default: models/da-v2-small-onnx/v1)",
    )
    parser.add_argument(
        "--opset",
        type=int,
        default=17,
        help="ONNX opset version (default: 17)",
    )
    parser.add_argument(
        "--upload-to-oss",
        action="store_true",
        help="Upload exported model to Aliyun OSS after export",
    )
    parser.add_argument(
        "--oss-bucket",
        default="oyster-models",
        help="Aliyun OSS bucket name (default: oyster-models)",
    )
    parser.add_argument(
        "--oss-prefix",
        default="da-v2-small/v1",
        help="OSS key prefix (default: da-v2-small/v1)",
    )
    args = parser.parse_args()

    out_dir = pathlib.Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = out_dir / "depth_anything_v2_small.onnx"

    # 1. Load model in PyTorch
    print(f"[1/4] Loading {args.model_id} in PyTorch...")
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    processor = AutoImageProcessor.from_pretrained(args.model_id)
    model = AutoModelForDepthEstimation.from_pretrained(args.model_id).to(device)
    model.eval()
    print(f"  loaded on {device}")

    # Use a real frame if available, otherwise synthesize
    test_frame = pathlib.Path(
        "/tmp/real-session-howardplay-1778993817/session_20260516_213817_d137a341/frames_for_depth/000000.png"
    )
    if not test_frame.exists():
        print("  test frame missing, synthesizing dummy 518x518")
        dummy = np.random.randint(0, 255, (518, 518, 3), dtype=np.uint8)
        test_frame = out_dir / "_dummy.png"
        Image.fromarray(dummy).save(test_frame)

    img = Image.open(test_frame).convert("RGB")
    inputs = processor(images=img, return_tensors="pt").to(device)
    print(f"  input tensor shape: {tuple(inputs['pixel_values'].shape)}")

    # 2. Run PyTorch inference (baseline)
    print("[2/4] PyTorch baseline inference (10 runs for timing)...")
    with torch.inference_mode():
        _ = model(**inputs)
        t0 = time.time()
        for _ in range(10):
            out = model(**inputs)
        pt_time = (time.time() - t0) / 10
    pt_depth = out.predicted_depth.squeeze().cpu().float().numpy()
    print(
        f"  PyTorch ({device}): {pt_time * 1000:.1f}ms/frame, output shape {pt_depth.shape}"
    )

    # 3. Export to ONNX
    print("[3/4] Exporting to ONNX...")
    model_cpu = model.to("cpu")
    inputs_cpu = {k: v.to("cpu") for k, v in inputs.items()}

    t0 = time.time()
    torch.onnx.export(
        model_cpu,
        (inputs_cpu["pixel_values"],),
        str(onnx_path),
        input_names=["pixel_values"],
        output_names=["predicted_depth"],
        dynamic_axes={
            "pixel_values": {0: "batch_size"},
            "predicted_depth": {0: "batch_size"},
        },
        opset_version=args.opset,
        do_constant_folding=True,
    )
    export_time = time.time() - t0
    size_mb = onnx_path.stat().st_size / 1e6
    print(f"  exported in {export_time:.1f}s, graph size: {size_mb:.1f} MB")

    # Check for external data file
    data_path = onnx_path.with_suffix(".onnx.data")
    if data_path.exists():
        data_mb = data_path.stat().st_size / 1e6
        print(f"  external weights: {data_mb:.1f} MB")

    # 4. Verify with ONNX Runtime
    print("[4/4] ONNX Runtime verification...")
    try:
        import onnxruntime as ort

        sess = ort.InferenceSession(
            str(onnx_path), providers=["CPUExecutionProvider"]
        )
        pv = inputs_cpu["pixel_values"].numpy()
        _ = sess.run(None, {"pixel_values": pv})
        t0 = time.time()
        for _ in range(10):
            ort_out = sess.run(None, {"pixel_values": pv})
        ort_time = (time.time() - t0) / 10
        ort_depth = ort_out[0].squeeze()
        print(
            f"  ONNX Runtime (CPU): {ort_time * 1000:.1f}ms/frame, output shape {ort_depth.shape}"
        )

        diff = np.abs(pt_depth - ort_depth)
        max_diff = diff.max()
        mean_diff = diff.mean()
        rel_diff = mean_diff / (np.abs(pt_depth).mean() + 1e-9)
        print(
            f"  Output diff vs PyTorch: max={max_diff:.4f}, mean={mean_diff:.4f}, "
            f"rel={rel_diff * 100:.2f}%"
        )
        status = "PASS" if rel_diff < 0.01 else "WARN" if rel_diff < 0.05 else "FAIL"
        print(f"  EQUIVALENCE: {status}")
    except ImportError:
        print("  onnxruntime not installed (skipping verification)")
        max_diff = None
        rel_diff = None

    # 5. Write manifest.json
    manifest = {
        "model_id": args.model_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "opset": args.opset,
        "files": {},
    }
    for fpath in [onnx_path, data_path] if data_path.exists() else [onnx_path]:
        manifest["files"][fpath.name] = {
            "sha256": sha256_file(fpath),
            "size_bytes": fpath.stat().st_size,
        }
    if max_diff is not None:
        manifest["verification"] = {
            "max_diff": float(max_diff),
            "rel_diff": float(rel_diff),
            "status": status,
        }

    manifest_path = out_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\n  manifest written to {manifest_path}")

    # 6. Optional OSS upload
    if args.upload_to_oss:
        print(f"\n[5/5] Uploading to Aliyun OSS ({args.oss_bucket}/{args.oss_prefix})...")
        upload_to_oss(out_dir, args.oss_bucket, args.oss_prefix)

    print("\n=== EXPORT COMPLETE ===")
    print(f"ONNX graph:  {onnx_path} ({size_mb:.1f} MB)")
    if data_path.exists():
        print(f"ONNX data:   {data_path} ({data_mb:.1f} MB)")
    print(f"Manifest:    {manifest_path}")


if __name__ == "__main__":
    main()
