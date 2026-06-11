# Spec: MC Mod Engine Z-buffer Hook (Tier 3 Metric Depth)

**Priority**: P0 — depth is our $几十/小时 vs 普通视频 $几美元/小时 差异化护城河
**Target**: H8 audit `PASS_STRICT` (engine_zbuffer source, gap_miss_ratio < 1%)
**Effort**: 1-2 weeks Java/Fabric
**Architecture alignment**: client captures raw Z-buffer; server linearizes + writes EXR

---

## Goal

Produce **真 metric depth** per frame in Minecraft, written as binary Z-buffer
dumps the server can later linearize → EXR. No more monocular DA-V2 SKIP on
H8. Strict buyers stop rejecting the data.

---

## Why client-side raw, not full EXR

Per 2026-05-26 expert meeting:
- Mod **only** captures raw — no compute, no transform.
- Server pipeline: read raw Z dump → projection-matrix linearize → EXR write.
- Keeps client lightweight, avoids per-machine GPU/driver bugs.
- One server-side fix benefits all testers retroactively.

---

## What to capture per frame

In `vendor/recorder-mod/src/main/java/.../ZBufferCapture.java` (new file):

```java
// On render-tick mixin into Minecraft's render loop (after world render,
// before HUD overlay)
public class ZBufferCapture {
    private static final int W = 1920;
    private static final int H = 1080;
    private static FloatBuffer depthBuf;
    private static long currentTick;

    @Inject(method = "render", at = @At("AFTER_WORLD_RENDER"))
    public static void captureZBuffer(GameRenderer self, float partialTicks) {
        // 1. Grab the depth attachment of the main framebuffer
        Framebuffer fb = MinecraftClient.getInstance().getFramebuffer();
        int depthTex = fb.getDepthAttachment();

        // 2. Read the depth texture via glReadPixels
        //    Format: GL_DEPTH_COMPONENT, type: GL_FLOAT
        depthBuf.clear();
        GL11.glReadPixels(0, 0, W, H, GL11.GL_DEPTH_COMPONENT,
                          GL11.GL_FLOAT, depthBuf);

        // 3. Capture the projection matrix (needed for server linearization)
        Matrix4f proj = self.getBasicProjectionMatrix(
            MinecraftClient.getInstance().options.getFov().getValue());
        float zNear = 0.05f;
        float zFar  = 1024.0f;  // MC default render distance

        // 4. Write raw blob: header + Z values
        Path out = sessionDir.resolve("zbuffer")
                              .resolve(String.format("tick_%08d.bin", currentTick));
        try (OutputStream os = Files.newOutputStream(out)) {
            // Header (32 bytes, all little-endian):
            //   4 bytes: magic "ZBUF"
            //   4 bytes: version (u32 = 1)
            //   4 bytes: width (u32 = 1920)
            //   4 bytes: height (u32 = 1080)
            //   4 bytes: zNear (f32)
            //   4 bytes: zFar (f32)
            //   4 bytes: fov_deg (f32)
            //   4 bytes: tick (u32)
            writeHeader(os, currentTick, zNear, zFar, fov);
            // Body: 1920*1080*4 = 8,294,400 bytes of f32 depth
            writeDepthBuffer(os, depthBuf);
        }
    }
}
```

**Per-frame size**: 8.3 MB raw (1920×1080×4 bytes f32). For a 5-min session at
30 fps: 5×60×30 = 9000 frames × 8.3 MB = **74.7 GB raw** ⚠️

**Compression strategy**:
- gzip the per-frame bin → typical 30-50% of float-32 raw (4-5 MB/frame)
- Or write groups of 30 frames (1 sec) into 1 gzip → ~120 MB/sec → 36 GB/5min still huge
- **Better**: store as **uint16 normalized depth** (Z mapped from [zNear,zFar] →
  [0,65535]). 2 bytes/pixel × 1920×1080 = 4.15 MB/frame. 30 fps × 5 min = 37 GB.
  Still huge.
- **Best**: keep at 480×270 (canonical_pipeline already downscales for DA-V2).
  uint16 × 480×270 = 259 KB/frame. 9000 frames = 2.3 GB. **Manageable**.

**Decision**: capture at 480×270 uint16 (or f16) by default. Optional 1920×1080
f32 mode for "premium" buyer tier.

---

## What the server does with it

`backend/workers/depth_linearize.py` (new):

```python
def linearize_zbuffer(raw_bin: Path, session_meta: dict) -> np.ndarray:
    """Read raw zbuffer bin → linear metric depth (meters)."""
    header = read_zbuffer_header(raw_bin)
    z_norm = read_depth_values(raw_bin)  # shape (H, W), dtype uint16
    z01 = z_norm.astype(np.float32) / 65535.0  # [0,1]

    # Linearize using projection matrix (perspective Z)
    near = header.zNear  # 0.05
    far = header.zFar    # 1024
    z_metric = (near * far) / (far - z01 * (far - near))  # meters

    return z_metric  # shape (H,W), dtype float32, units = meters

def to_exr(depth_metric: np.ndarray, out_path: Path):
    """Write metric depth as OpenEXR with kind=engine_zbuffer marker."""
    write_exr_z(out_path, depth_metric)

def process_session(session_dir: Path):
    raw_dir = session_dir / "zbuffer"
    exr_dir = session_dir / "depth"
    exr_dir.mkdir(exist_ok=True)

    for raw in sorted(raw_dir.glob("tick_*.bin")):
        z_metric = linearize_zbuffer(raw, session_meta)
        to_exr(z_metric, exr_dir / raw.with_suffix(".exr").name)

    # Honesty marker
    (exr_dir / ".source").write_text(json.dumps({
        "kind": "engine_zbuffer",
        "frame_count": frame_count,
        "gap_miss_ratio": 0.0,  # zero if every tick has matching frame
        "source_resolution": "480x270",
        "source_bit_depth": "uint16_norm",
        "zNear": 0.05,
        "zFar": 1024.0,
    }))
```

---

## Audit gate `prd_compliance_audit.py` H8

Already supports `kind=engine_zbuffer`. With `gap_miss_ratio < 0.01` it gives
**PASS_STRICT** — exactly what strict buyers require.

---

## File layout impact

**Client side** (raw, ~2.3 GB for 5-min session at 480×270 uint16):
```
session_<ts>/
├── recording.mp4
├── game_state.jsonl
├── inputs.jsonl
├── metadata.json
├── frames.jsonl
└── zbuffer/                      # NEW
    ├── tick_00000000.bin          # 259 KB each
    ├── tick_00000001.bin
    └── ... (9000 frames)
```

**Server side** (after linearize_zbuffer worker):
```
session_<ts>/
├── ... (client raw files)
└── depth/                        # NEW
    ├── .source                   # kind=engine_zbuffer
    ├── tick_00000000.exr          # 1 MB each (compressed float32)
    └── ... (9000 frames)
```

---

## Implementation plan

| Phase | Effort | Owner |
|-------|--------|-------|
| 1. Java mixin to intercept render loop, glReadPixels | 2 days | Java engineer |
| 2. Write raw zbuffer/*.bin per tick (480×270 uint16) | 1 day | Java engineer |
| 3. Test against 5/13 + 5/16 minecraft instances | 1 day | Java engineer |
| 4. backend/workers/depth_linearize.py | 2 days | Python |
| 5. Wire to ingest hook on /api/v1/sessions | 1 day | Python |
| 6. Test with 5/16 (885MB) session end-to-end | 1 day | QA |
| 7. Audit H8 PASS_STRICT verification | 0.5 day | QA |

**Total**: ~8.5 person-days. With 1 Java + 1 Python engineer in parallel: **~5-7 days**.

---

## Acceptance criteria

- [ ] Recording with MC mod produces `zbuffer/tick_*.bin` files (size 259KB each)
- [ ] Server worker linearizes → EXR with kind=engine_zbuffer
- [ ] Audit H8 reports `PASS_STRICT` on test session
- [ ] First metric Z values manually spot-checked against known geometry (player
      at y=64 sees floor at ~64m, sky at far ~1024m)
- [ ] Compatible with v0.13 minimal client (no canonical_pipeline on client)

---

## Risk

- ⚠️ Some MC versions (1.21.4 specifically) might use Vulkan via Sodium mod →
  glReadPixels not available. Need fallback for shader-mod users.
- ⚠️ 480×270 might be too low for premium buyers — may need 1920×1080 tier later.
- ⚠️ render-tick mixin timing: `@At("AFTER_WORLD_RENDER")` may miss HUD-overlay
  occlusion. Need to verify on a flat plain (sky should read 1024.0).
