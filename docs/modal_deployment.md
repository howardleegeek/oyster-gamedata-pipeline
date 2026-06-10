# Modal Deployment Guide — DA-V2 Depth Endpoint

## Overview

This guide covers deploying the DA-V2 monocular depth model as a Modal serverless endpoint.
This allows contributors to compute depth without any local ML stack — they simply upload
video frames and receive depth EXRs.

## Why Modal?

| Option | Cost | Pros | Cons |
|--------|------|------|------|
| **Modal** | ~$0.01/session | $0 fixed, autoscales 0→N, GPU available | Cold start ~10s |
| Aliyun ECS GN6i | ~$300/mo | Full control | Expensive when idle |
| Self-host RTX 4070 | $600 one-time | No per-session cost | Single point of failure |
| Replicate | $0.02/session | No infra | Slower cold start |

**Modal is optimal for the first 6 months** — cheapest for bursty load patterns.

---

## 1. Set Up Modal Account

### 1.1 Create Account

1. Go to [modal.com](https://modal.com)
2. Sign up with GitHub or email
3. Navigate to **Settings → Tokens**

### 1.2 Install Modal CLI

```bash
pip install modal
```

### 1.3 Authenticate

```bash
modal token new
```

This will prompt you to open a browser and authorize the CLI. Alternatively, set
environment variables:

```bash
export MODAL_TOKEN_ID="your-token-id"
export MODAL_TOKEN_SECRET="your-token-secret"
```

### 1.4 Verify Setup

```bash
modal app list
```

Should show your apps (or empty list if none deployed yet).

---

## 2. Deploy the Endpoint

### 2.1 Deploy

```bash
bash server/deploy.sh
```

Or manually:

```bash
modal deploy server/modal_depth_app.py
```

### 2.2 Get Endpoint URL

After deployment, Modal will output the endpoint URL. It typically looks like:

```
https://oyster-depth--depth-endpoint.modal.run
```

Or for the async version:

```
https://oyster-depth--depth-endpoint-async.modal.run
```

### 2.3 Test the Endpoint

```bash
curl -X POST https://oyster-depth--depth-endpoint.modal.run \
     -F "video=@test_video.mp4" \
     -F "fps=6" \
     -o depth_output.tar.gz
```

### 2.4 Use the Client

```bash
python3 bin/run_da_v2_depth_remote.py \
    --frames-dir ./input_frames \
    --depth-dir ./output_depth \
    --endpoint https://oyster-depth--depth-endpoint.modal.run \
    --auth-token $MODAL_TOKEN
```

---

## 3. Rotate Credentials

### 3.1 Generate New Token

1. Go to [modal.com/settings](https://modal.com/settings)
2. Click **Create Token**
3. Copy the new token ID and secret

### 3.2 Update Environment

```bash
export MODAL_TOKEN_ID="new-token-id"
export MODAL_TOKEN_SECRET="new-token-secret"
```

### 3.3 Revoke Old Token

1. Go to [modal.com/settings](https://modal.com/settings)
2. Find the old token
3. Click **Revoke**

### 3.4 Redeploy (if token is embedded)

If you've baked credentials into the deployment, redeploy:

```bash
modal deploy server/modal_depth_app.py
```

---

## 4. Cost Monitoring

### 4.1 Check Usage

```bash
modal app list
```

Shows active apps and their status.

### 4.2 Dashboard

Visit [modal.com/dashboard](https://modal.com/dashboard) for:
- Request count
- GPU hours used
- Cost breakdown

### 4.3 Cost Estimates

| Metric | Estimate |
|--------|----------|
| Per session (1800 frames) | ~$0.01–$0.02 |
| 100 sessions/day | ~$1–$2/day |
| 1000 sessions/day | ~$10–$20/day |

### 4.4 Set Budget Alerts

1. Go to **Settings → Billing**
2. Set monthly budget limit
3. Configure email alerts at thresholds

### 4.5 Optimize Costs

- Use `Depth-Anything-V2-Small` (not Large) for cost efficiency
- Process at 6fps (not higher) to reduce frame count
- Batch multiple videos in one request if possible

---

## 5. Migration Path to Self-Host

Once volume justifies it (100+ sessions/day consistently), consider migrating:

### 5.1 When to Migrate

- Monthly Modal cost exceeds $300
- Need sub-second cold starts
- Require custom GPU configuration
- Data sovereignty requirements

### 5.2 Migration Steps

1. **Provision GPU server** (RTX 4070 or A10G equivalent)
2. **Install dependencies**:
   ```bash
   pip install transformers torch accelerate safetensors Pillow numpy OpenEXR
   apt install ffmpeg
   ```
3. **Download model**:
   ```python
   from huggingface_hub import snapshot_download
   snapshot_download("depth-anything/Depth-Anything-V2-Small-hf", local_dir="./model")
   ```
4. **Deploy FastAPI server** (similar to Modal endpoint):
   ```python
   from fastapi import FastAPI, UploadFile, File
   app = FastAPI()
   # ... same logic as modal_depth_app.py
   ```
5. **Update client endpoint**:
   ```bash
   python3 bin/run_da_v2_depth_remote.py \
       --frames-dir ./frames \
       --depth-dir ./depth \
       --endpoint https://your-server.com/depth
   ```
6. **Monitor and compare costs**

### 5.3 Hybrid Approach

Keep Modal as fallback:

```python
# In client code
try:
    result = upload_to_primary_endpoint(...)
except ConnectionError:
    result = upload_to_modal_fallback(...)
```

---

## 6. Troubleshooting

### Cold Start Too Slow

- Modal cold starts are ~10-30s for GPU containers
- Use `keep_warm=1` to maintain a warm container:
  ```python
  @stub.function(gpu="A10G", keep_warm=1)
  ```
- This costs ~$0.50/hr but eliminates cold starts

### Out of Memory

- Reduce batch size
- Use `Depth-Anything-V2-Small` instead of `Large`
- Process at lower fps

### Endpoint Unreachable

The client automatically falls back to `--skip-depth` mode:

```bash
python3 bin/run_da_v2_depth_remote.py --frames-dir X --depth-dir Y
# If endpoint fails, creates depth dir with .source marker
```

### Model Download Fails

The model is baked into the image during deployment. If it fails:
1. Check HuggingFace connectivity
2. Verify model repo exists: `depth-anything/Depth-Anything-V2-Small-hf`
3. Try alternative model: `depth-anything/Depth-Anything-V2-Base-hf`

---

## 7. Architecture

```
┌─────────────┐     mp4 upload      ┌──────────────────┐
│   Client    │ ──────────────────► │  Modal Endpoint  │
│ (no PyTorch)│                     │  (A10G GPU)      │
└─────────────┘                     └────────┬─────────┘
                                            │
                                     DA-V2 inference
                                     (1800 frames ~3min)
                                            │
                                            ▼
                              ┌─────────────────────────┐
                              │  tar.gz of depth/*.exr  │
                              │  kind: server_da_v2     │
                              └────────────┬────────────┘
                                           │
                                    download & extract
                                           │
                                           ▼
                              ┌─────────────────────────┐
                              │  depth/ directory       │
                              │  + .source marker       │
                              └─────────────────────────┘
```
