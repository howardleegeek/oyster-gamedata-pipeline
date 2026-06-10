# ONNX + DirectML Deployment Guide

## Overview

This guide covers deploying Depth Anything V2 using **ONNX Runtime + DirectML** on Windows clients — an alternative to the server-side Modal approach (SPEC #17) and the local PyTorch approach (SPEC #16).

## Why DirectML?

DirectML is Microsoft's cross-vendor GPU compute API built into Windows 10/11. Unlike CUDA (NVIDIA-only) or Metal (Apple-only), DirectML works on:

| GPU Vendor | Supported | Notes |
|---|---|---|
| NVIDIA | ✅ | Works alongside CUDA, no driver conflicts |
| AMD | ✅ | Full support via DirectML |
| Intel (Arc, UHD, Iris) | ✅ | Integrated and discrete GPUs |
| Qualcomm (Snapdragon X) | ✅ | ARM64 Windows laptops |

**Key advantage**: A single ONNX model runs on *any* Windows GPU without installing vendor-specific SDKs.

## Installation

### Windows (DirectML)

```bash
pip install onnxruntime-directml transformers pillow numpy
```

That's it. No CUDA toolkit, no cuDNN, no PyTorch. Total install size: **~146 MB** vs PyTorch+CUDA's **~6 GB** (40× smaller).

### macOS (CoreML fallback)

```bash
pip install onnxruntime-silicon transformers pillow numpy
```

### Linux (CPU or CUDA)

```bash
pip install onnxruntime-gpu transformers pillow numpy  # CUDA
# or
pip install onnxruntime transformers pillow numpy     # CPU only
```

## Quick Start

### 1. Download the ONNX model

```bash
python3 bin/download_da_v2_onnx.py
```

This tries **Aliyun OSS** first (fast for China contributors), then falls back to **HuggingFace**. The model is cached at `~/.cache/oyster/da-v2-small-onnx/`.

### 2. Run depth estimation

```bash
# Single image
python3 bin/run_da_v2_depth_onnx.py --input photo.jpg --output depth/

# Batch processing
python3 bin/run_da_v2_depth_onnx.py --input-dir frames/ --output depth/
```

### 3. Use the canonical pipeline

```bash
# Auto-detect best backend
python3 canonical_pipeline.py --input-dir frames/ --output depth/

# Force ONNX backend
python3 canonical_pipeline.py --input-dir frames/ --output depth/ --depth-backend local-onnx-directml

# Skip depth entirely
python3 canonical_pipeline.py --input-dir frames/ --output depth/ --depth-backend skip
```

## Aliyun OSS Mirror (China Contributors)

For contributors in mainland China, HuggingFace can be slow or unreachable. We mirror the ONNX model on Aliyun OSS:

- **Primary**: `https://oyster-models.oss-cn-hangzhou.aliyuncs.com/da-v2-small/v1/`
- **Fallback**: `https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf`

The download script automatically tries Aliyun first with a 3-second timeout, then falls back to HuggingFace.

### Uploading to OSS (maintainers only)

```bash
python3 bin/export_da_v2_to_onnx.py \
    --model-id depth-anything/Depth-Anything-V2-Small-hf \
    --output-dir models/da-v2-small-onnx/v1/ \
    --upload-to-oss
```

Requires `OSS_ACCESS_KEY_ID` and `OSS_ACCESS_KEY_SECRET` environment variables.

## Comparison: Deployment Options

| Feature | ONNX-DirectML | Modal Server | PyTorch-CUDA |
|---|---|---|---|
| **GPU Support** | Any Windows GPU | Server GPU (NVIDIA) | NVIDIA only |
| **Install Size** | ~146 MB | ~50 MB (client) | ~6 GB |
| **Network Required** | No (local) | Yes (API calls) | No (local) |
| **Latency** | ~50-200ms/frame | ~200-500ms + network | ~30-100ms/frame |
| **Cost** | Free (local GPU) | Pay per inference | Free (local GPU) |
| **Privacy** | 100% local | Images sent to server | 100% local |
| **China Access** | ✅ Aliyun mirror | ❌ Server may be blocked | ❌ CUDA download issues |
| **Best For** | Windows desktop apps | Cross-platform, no GPU | Linux dev, NVIDIA users |

## Exporting Your Own Model

```bash
python3 bin/export_da_v2_to_onnx.py \
    --model-id depth-anything/Depth-Anything-V2-Small-hf \
    --output-dir models/da-v2-small-onnx/v1/ \
    --opset 17
```

This produces:
- `depth_anything_v2_small.onnx` (~1.7 MB graph)
- `depth_anything_v2_small.onnx.data` (~94 MB weights)
- `manifest.json` (SHA-256 checksums, metadata)

## Troubleshooting

### "DmlExecutionProvider not available"

This means ONNX Runtime was installed without DirectML support. Fix:

```bash
pip uninstall onnxruntime onnxruntime-gpu onnxruntime-directml
pip install onnxruntime-directml
```

### Slow inference on CPU

DirectML requires a GPU. If no GPU is detected, ONNX Runtime falls back to CPU which is significantly slower. Check:

```python
import onnxruntime as ort
print(ort.get_available_providers())
# Should include "DmlExecutionProvider" on Windows with GPU
```

### Checksum mismatch after download

The model files may have been corrupted. Force re-download:

```bash
python3 bin/download_da_v2_onnx.py --force
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  Client Machine                  │
│                                                  │
│  ┌──────────┐    ┌──────────────┐    ┌────────┐ │
│  │  Image   │───▶│  ONNX Runtime│───▶│ Depth  │ │
│  │  Input   │    │  + DirectML  │    │  Map   │ │
│  └──────────┘    └──────────────┘    └────────┘ │
│                       │                          │
│              ┌────────▼────────┐                 │
│              │ depth_anything_ │                 │
│              │ v2_small.onnx   │                 │
│              │ + .onnx.data    │                 │
│              └─────────────────┘                 │
└─────────────────────────────────────────────────┘
```

No network calls during inference. No CUDA. No PyTorch. Just ONNX Runtime + DirectML.
