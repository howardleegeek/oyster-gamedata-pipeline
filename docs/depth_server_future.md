# Future Depth Server Specification

## Overview

This document specifies the future production depth server that will handle depth estimation
when we reach the trigger point (10+ active contributors OR first paid customer).

## Current State

Currently, contributors use `--skip-depth` mode which achieves **89/104** audit score.
The missing 15 points are depth-related items that require DA-V2 monocular depth estimation,
which has complex dependencies (PyTorch, CUDA, etc.) that are difficult to set up on
contributor machines, especially Windows.

## Server Options

| Option | Setup cost | Per-inference cost | Latency | Best for |
|---|---|---|---|---|
| Aliyun ECS GN6i (T4 GPU) | ~$300/mo fixed | included | <1min | Steady-state production |
| Modal serverless | $0 fixed | ~$0.01/session | <30s after cold start | Bursty / unpredictable load |
| Self-host NVIDIA RTX 4070 box | $600 one-time | electricity | <30s | <1000 sessions/mo |
| Replicate API (DA-V2 pre-deployed) | $0 fixed | ~$0.02/session | <1min | Spike traffic |

## Architecture

### Current Architecture (Contributor Mode)
```
┌─────────────────────────────────────────────────────────────┐
│ CONTRIBUTOR MODE (immediate, this week)                     │
│   canonical_pipeline.py --skip-depth --target-score 89      │
│                                                             │
│   - Recorder: ffmpeg + ssh + tar (no ML)                    │
│   - Transform: pure Python                                  │
│   - Audit: 89/104 (no depth/, 9 H-group items + A4 + SS4   │
│             remain SKIP/FAIL — honest, documented)          │
│   - Time to onboard new contributor: ~30 min                │
└─────────────────────────────────────────────────────────────┘
```

### Future Architecture (Production Server Mode)
```
┌─────────────────────────────────────────────────────────────┐
│ PRODUCTION SERVER MODE (future, post-commercial-traction)   │
│   canonical_pipeline.py --depth-backend remote --target 98  │
│                                                             │
│   - Dedicated server (Aliyun ECS GPU / Modal / self-host)   │
│   - All contributor + customer sessions hit 98/104          │
│   - Triggered by: 10+ contributors OR first paid customer   │
└─────────────────────────────────────────────────────────────┘
```

## Technical Implementation

### API Design

```python
# depth_server/client.py
class DepthClient:
    def __init__(self, endpoint: str, api_key: str = None):
        self.endpoint = endpoint
        self.api_key = api_key
    
    def estimate_depth(self, frame: np.ndarray) -> np.ndarray:
        """Send frame to depth server, return depth map."""
        # Implementation depends on chosen provider
        pass
    
    def batch_estimate(self, frames: List[np.ndarray]) -> List[np.ndarray]:
        """Batch processing for efficiency."""
        pass
```

### Integration Points

1. **Pipeline Integration**:
   ```python
   # In canonical_pipeline.py
   if args.depth_backend == 'remote':
       client = DepthClient(os.environ['DEPTH_SERVER_ENDPOINT'])
       depth_maps = client.batch_estimate(frames)
   elif args.skip_depth:
       depth_maps = None  # Skip depth estimation
   else:
       depth_maps = local_depth_estimation(frames)  # Howard's Mac
   ```

2. **Configuration**:
   ```bash
   # Environment variables for production
   export DEPTH_SERVER_ENDPOINT="https://depth.example.com/api/v1"
   export DEPTH_SERVER_API_KEY="your-api-key"
   
   # Or use config file
   # ~/.config/ourproject/depth_server.yaml
   ```

### Migration Path

1. **Phase 1 (Current)**: `--skip-depth` mode for all contributors
2. **Phase 2 (Trigger met)**: Deploy depth server, update documentation
3. **Phase 3 (Rollout)**: Contributors can opt-in with `--depth-backend remote`
4. **Phase 4 (Default)**: Make remote depth the default for all users

## Trigger Conditions

Build the production server when **ONE** of:
- ✅ **10+ active contributors** making PRs
- ✅ **First paid customer** signs contract
- ✅ **Synthetic-data half-life pressure** (per Grok 18-30 mo clock) demands full-spec output

## Cost Analysis

### Option 1: Aliyun ECS GN6i (T4 GPU)
- **Monthly cost**: ~$300
- **Inference time**: <1 minute per session
- **Concurrent sessions**: 5-10 (depending on frame count)
- **Setup time**: 2-3 days
- **Maintenance**: Medium (need to monitor GPU utilization, updates)

### Option 2: Modal Serverless
- **Monthly cost**: $0 fixed + ~$0.01/session
- **Inference time**: <30s after cold start
- **Concurrent sessions**: Unlimited (auto-scales)
- **Setup time**: 1-2 days
- **Maintenance**: Low (serverless, auto-scaling)

### Option 3: Self-hosted RTX 4070
- **Upfront cost**: ~$600 (GPU) + ~$400 (rest of system)
- **Monthly cost**: Electricity (~$20)
- **Inference time**: <30s
- **Concurrent sessions**: 3-5
- **Setup time**: 3-5 days (hardware procurement, setup)
- **Maintenance**: High (hardware failures, updates, networking)

### Option 4: Replicate API
- **Monthly cost**: $0 fixed + ~$0.02/session
- **Inference time**: <1 minute
- **Concurrent sessions**: Rate limited
- **Setup time**: 1 day
- **Maintenance**: Very low (managed service)

## Recommendation

Based on our current scale and projected growth:

1. **Immediate (now)**: Continue with `--skip-depth` mode
2. **When trigger hits**: Start with **Modal serverless** (lowest barrier to entry)
3. **At 100+ sessions/day**: Migrate to **Aliyun ECS** (better cost control)
4. **At enterprise scale**: Consider **self-hosted cluster** (full control)

## Implementation Timeline

Once triggered:

- **Week 1**: Set up basic depth server (Modal or Replicate)
- **Week 2**: Integrate with pipeline, update tests
- **Week 3**: Beta test with select contributors
- **Week 4**: Full rollout, update documentation

## Testing Strategy

1. **Unit tests**: Mock depth server responses
2. **Integration tests**: Test with real depth server (staging)
3. **Performance tests**: Measure latency, cost per session
4. **Fallback tests**: Ensure pipeline works when depth server is down

## Monitoring

Once deployed, monitor:
- **Uptime**: Server availability
- **Latency**: Inference time per frame
- **Cost**: Monthly spend
- **Accuracy**: Depth estimation quality vs ground truth
- **Usage**: Number of sessions processed

## Conclusion

The `--skip-depth` mode is a strategic choice, not a lazy one. It allows contributors to
be productive immediately while we defer the infrastructure investment until we have
clear signals (10+ contributors or first paid customer). When the time comes, we'll
implement a production-grade depth server that seamlessly integrates with the existing
pipeline via the `--depth-backend remote` flag.

## Related Documents

- [ONBOARDING.md](../ONBOARDING.md) - Contributor onboarding guide
- [bin/canonical_pipeline.py](../bin/canonical_pipeline.py) - Main pipeline implementation
- [tests/test_canonical_pipeline_score.py](../tests/test_canonical_pipeline_score.py) - Audit score tests