# SELF_HEAL_v2 — Auto-Resolve Framework (2026-05-09)

> **Howard 2026-05-09**: "针对这些问题解决, 然后写自愈方案. 长期如何解决, 自动解决."
>
> rc15.7's 3-tier device fallback caught the AMD 780M × torch_directml × DepthAnything op-compat issue cleanly, but punted to server. v2 adds **automatic backend retries** so the client can self-heal across N×M (backend × device) combinations before giving up.

## Problem evidence

`/tmp/howard_test_rc15x/clip-20260509-190832/depth_manifest.json` shows rc15.8 reaching DML successfully, then `torch_directml` failing inside DepthAnything V2 with:

```
ValueError: infer_schema(func): Parameter input has unsupported type torch.Tensor.
Got func with signature (input, weight, offs)
```

This is an op-dispatcher gap in `torch_directml` (not a our-code bug). The whole rc15.7 fallback chain caught it correctly (`fallback_from: model_compat`, `tried_device: dml`, `user_hint: ...`), but server_pending means the user gets no depth until backend is up. **For depth specifically, we have alternative inference paths that bypass torch_directml.**

## v1 → v2 architecture

### v1 (rc15.7): Device chain
```
cuda → dml → cpu → server_pending
```
One framework (torch). One framework's bug breaks all GPU tiers.

### v2 (rc15.9): Backend × Device matrix

```
                  cuda      dml       cpu
torch (pytorch)   tier 1    tier 2    tier 5
onnxruntime       tier 3    tier 4    tier 6
openvino (Intel)  -         tier 7    tier 8
server-side       always available, final fallback
```

Each cell is a (backend, device) pair. On exception in cell N, recorder tries cell N+1 (same model weights, different runtime). Final fallback = `server_pending`.

### Concrete tier order for DepthAnything V2 on Windows

1. **torch + cuda** — best: NVIDIA dGPU users
2. **torch + dml** — current rc15.7 path (Howard's 780M FAILED here)
3. **onnxruntime + dml** — bypass torch_directml entirely; ONNX op coverage on DML is wider
4. **onnxruntime + cuda** — for users with both
5. **torch + cpu** — slow, only if user opts in (OYSTER_LOCAL_DEPTH=1)
6. **onnxruntime + cpu** — slow alternative
7. **server_pending** — final

Each tier transition emits `heal_event(depth_dual_track, "heal_attempt")` with `tried_tier` so backend telemetry sees which path succeeded.

## Phase rollout

### Phase B (rc15.9 — IMMEDIATE)
- Add tier 3: `onnxruntime-directml` + DepthAnything ONNX export
- Convert HF model to ONNX at first run (lazy, cached locally)
- Wire into rc15.7's fallback chain at the `model_compat` exception classifier
- ~50 MB bundle add (onnxruntime-directml package)

### Phase C (concurrent — 2 weeks)
- Backend SM ingest endpoint accepts `depth_manifest.fallback_from` field
- Backend learns mapping: `hardware_fingerprint → tier_that_worked`
- Backend emits `recommended_tier` config on next session start
- Client hot-loads config via `/v1/config/hardware_pinning`

### Phase D (Q3)
- Backend ML model: features = (gpu_vendor, ram, cpu, OS_version, driver_version), label = first_successful_tier
- Auto-retrains nightly from heal_events aggregation
- Recorder downloads pinning config at startup, **skips known-failing tiers** for that hardware
- Net result: 780M users never wait 13s for torch_directml fail; jump direct to onnxruntime tier 3

### Phase E (Q4)
- Contributor-pluggable backends: anyone can register a (model, runtime) pair
- Recorder runs A/B between pinned config and exploratory tier on 5% of sessions to discover better paths
- Telemetry feeds back to backend, ML model adapts

## Auto-resolve guarantees

**v2 contract**:
1. **Detection**: every (backend, device) failure is classified into one of 5 buckets (oom / driver / op_compat / timeout / unknown)
2. **Retry**: on any bucket except `oom` (which means hardware can't), recorder advances to next tier within 60s
3. **Reporting**: every retry path stamped in `depth_manifest.fallback_history: [...]`
4. **Stop conditions**: 3 consecutive failures, 30 min total time budget, OR all tiers exhausted
5. **Convergence**: backend telemetry guarantees within 7 days of first user with new hardware, future users with same hardware skip known-bad tiers

## Why this isn't over-engineering

- 11 rounds of audit found 42 client-side bugs, but rc15.8 still scores 21/24 on lint **because the bug isn't in our code** — it's in the third-party (torch_directml) ↔ third-party (DepthAnything model arch) interaction.
- Self-heal v1 (rc15.7) detected the failure cleanly but couldn't fix it, only punted.
- Self-heal v2 makes the recorder **ACTUALLY HEAL**: try next tier, report success path back to backend, future users benefit from your data.
- Telemetry-driven backend pinning (Phase C+D) means the system gets SMARTER over time without code changes — exactly the long-term auto-resolve Howard asked for.

## Acceptance criteria for rc15.9 (Phase B)

- [ ] `bin/depth_runtimes/torch_runtime.py` and `bin/depth_runtimes/onnx_runtime.py` exist with shared interface `infer(video_path, output_dir, ...)`.
- [ ] Recorder tries torch+dml → onnx+dml → server_pending in order, with each transition emitting heal_event.
- [ ] depth_manifest.json includes `fallback_history: [{tier, exception_class, exception_msg}, ...]` so backend can replay the failure chain.
- [ ] Bundle includes `onnxruntime-directml` package + DepthAnything ONNX export script (lazy first-run).
- [ ] Howard's AMD 780M test produces depth/*.exr files via tier 3 (ONNX+DML).
- [ ] lint v3 = 24/24 on the resulting tarball.

## Cross-feature applicability

This pattern (multi-backend × multi-device fallback matrix + telemetry-driven config) generalizes beyond depth:

- **OBS encoder fallback**: HEVC NVENC → HEVC AMF → HEVC QuickSync → HEVC software → MP4-H264 (already implicit in OBS, but heal-event coverage missing)
- **Audio capture**: WASAPI → DirectSound → null (silence stub)
- **Window detection**: title-substring → process-name → DXGI capture probe
- **Mod handshake**: HTTP → file watch → process injection (future)

Each adopts the same tier-list + heal-event protocol → eventually all features have telemetry-driven hardware pinning.
