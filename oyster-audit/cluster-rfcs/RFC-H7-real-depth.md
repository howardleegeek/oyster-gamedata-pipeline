# RFC H7: Real Depth Values (not 16×16 zeros)

> Closes MECE H7 / RBGA-A1 / PRDAUD-1.

## Two paths

### Path A — Verify rc19.0.3 DA-V2 wiring (1 day, low-effort first)

rc19.0.3 added Depth-Anything-V2 inference path in `depth_exr_writer.rs`
(per commit log). Goal: confirm it produces real depth, not the 16×16
placeholder, on minipc2 hardware.

```bash
# gate-A.sh
set -e
# 1. Run rc19.0.4 install on minipc2
# 2. Record 1 min MC
# 3. Audit depth EXR
python3 bin/prd_compliance_audit.py <session> --markdown | grep H7
# Expected: H7 ✅ with first EXR dimensions 1920×1080, not 16×16
```

If Path A passes → H7 closed. Move on.

### Path B — MC mod Z-buffer pipe (1 week, only if A fails)

If DA-V2 inference is too slow or produces wrong depth on minipc2 hardware,
fall back to extracting MC's Z-buffer via mod IPC (extends RFC-A21-A22).

Java side:
```java
// In FabricInitializer, hook into MinecraftClient.framebuffer
public void onRenderEnd(MinecraftClient mc) {
    Framebuffer fb = mc.getFramebuffer();
    int depthTex = fb.getDepthAttachment();
    // glReadPixels(GL_DEPTH_COMPONENT, GL_FLOAT, ...) → 1920×1080 f32 buffer
    // → send over named pipe \\.\pipe\oyster-depth as binary frame
}
```

Recorder side: receive binary depth frame, write as EXR.

## Dispatch command (Path B fallback)

```bash
SPEC_FILE=oyster-audit/cluster-rfcs/RFC-H7-real-depth.md \
  WORKING_DIR=/tmp/cluster-h7 \
  TASK_ID=H7-depth \
  AGENT_MODEL=deepseek-v3.2 \
  python3 ~/Downloads/oyster/infra/dispatch/temporal-poc/minimax_agent_simple.py
```
