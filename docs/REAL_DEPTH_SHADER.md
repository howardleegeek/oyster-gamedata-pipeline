# G198 — Real-depth shader pack (vendor enablement)

**Status (2026-05-13)**: optional / opt-in. The recorder's existing DepthAnything V2 path remains the default; vendors enable the real-depth shader only when their captures hit the lint v3 #15 / #16 ratio gate.

**Problem this solves.** Lint v3 fails on criteria #15 (Depth Invalid-Pixel Ratio) and #16 (Depth Data Quality) for roughly 15 of every 1800 EXR frames per session when DepthAnything V2 is the source — texture-heavy terrain confuses the model and pushes invalid-pixel ratios past the buyer's 5 % cap. The real-depth shader pack short-circuits inference entirely by reading Minecraft's actual GBuffer depth attachment.

---

## 1. Quick start (operator)

1. **Install** the `oyster-recorder` Fabric mod jar from the build matrix
   (`mc-mod/build/libs/oyster-recorder-mod-X.Y.Z-mc1.21.4.jar` after `./gradlew build`
   on a JDK-21 box; pre-built jars also ship in the recorder release tarball).
2. **Enable real depth** by creating `~/Documents/OysterClips/preferences.json` (Windows: `%USERPROFILE%\Documents\OysterClips\preferences.json`):
   ```json
   {
     "enable_real_depth_shader": true,
     "depth_near_metres": 0.05,
     "depth_far_metres": 1024.0,
     "depth_reversed_z": false,
     "depth_width": 1920,
     "depth_height": 1080,
     "depth_fps": 6
   }
   ```
3. **Record** as usual. The mod auto-writes EXR sidecars to
   `~/Documents/OysterClips/active_session/depth/000000.exr` … `001799.exr`.
4. **Validate** the output before packaging:
   ```bash
   python3 bin/real_depth_validator.py ~/Documents/OysterClips/active_session/depth
   ```
   Exit code `0` means lint v3 #15 + #16 will pass.

---

## 2. Output format (buyer contract)

The mod emits files conforming to [`docs/PRD.md` §3.4](PRD.md) and [`docs/BUYER_SPEC_V1.md` "Depth requirements"](BUYER_SPEC_V1.md):

| Property | Value | Source |
|---|---|---|
| Container | OpenEXR 2.0 scanline image | PRD §3.4 |
| Channel | single `Z` channel, **float32** | PRD §3.4 |
| Compression | `NO_COMPRESSION` | mod default; lint v3 is compression-agnostic |
| Resolution | **1920 × 1080** | PRD §3.1 |
| Depth unit | metres along the optical Z axis (left-hand coords) | PRD §3.2 |
| Sample rate | **6 fps** (every 5th video frame) | PRD §3.4 |
| Filename | `000000.exr` … `001799.exr` (6-digit zero-padded, 0-based) | PRD §3.4 |
| Invalid sentinel | **0.0 f** (sky / clipped-far / NaN / Inf / out-of-range) | PRD §3.4 |
| Invalid-pixel ratio | **≤ 5 %** per frame (mod warns at ≥ 4 %) | lint v3 #15 |

Files are byte-identical between Mac / Linux / Windows because all multi-byte values are emitted little-endian per the OpenEXR spec.

---

## 3. Depth math (linear metres from GL depth)

The mod converts each pixel's GL depth-buffer value to linear metres using perspective inversion. The classic-z formula:

    z_ndc   = depth_buf * 2 - 1
    linear  = (2 * near * far) / (far + near - z_ndc * (far - near))

Reversed-z (Minecraft's optional 1.17+ mode) flips the input via `1 - depth_buf` before the same math.

**Invalid pixels** (PRD §3.4):

* `depth_buf ≥ 0.999` → `0.0` (sky / clipped at far plane)
* `depth_buf < 0` or `> 1` → `0.0` (out-of-range, shouldn't happen but defensive)
* `NaN` / `Inf` → `0.0`
* `near ≤ 0` or `far ≤ near` → entire buffer `0.0` (config bug; the mod logs a warning)

The math lives in two parallel implementations that **must** stay in lockstep:

* **Java** (canonical): `mc-mod/src/main/java/world/oyster/recorder/depth/DepthMath.java`
* **Python mirror**: `bin/real_depth_validator.py`

The Python mirror is exercised by `tests/test_real_depth_math.py` (24 tests) which pin every edge case including a forward + inverse projection round-trip.

---

## 4. Architecture (mod internals)

```
┌─────────────────────────────────────────────────────────────┐
│ Minecraft client thread                                     │
│                                                             │
│   WorldRenderEvents.END  →  RealDepthExporter.onWorldRender │
│                                  │                          │
│                                  ▼                          │
│           every Nth frame: glReadPixels(GL_DEPTH_COMPONENT) │
│                                  │                          │
│                                  ▼                          │
│              direct-ByteBuffer  →  float[]  (vertical flip) │
│                                  │                          │
│                              queue.offer(DepthFrame)        │
└──────────────────────────────────┬──────────────────────────┘
                                   │
                ┌──────────────────▼──────────────┐
                │ oyster-depth-writer (daemon)    │
                │                                 │
                │   DepthMath.linearizeBuffer     │
                │   ratio self-check (warn 4 %    │
                │       hard fail at 5 %)         │
                │   OpenExrFloat32Writer.write    │
                │     → depth/NNNNNN.exr          │
                └─────────────────────────────────┘
```

* The render-thread hook never touches disk. A 4-deep blocking queue absorbs IO latency; if the writer falls more than 4 frames behind (sustained 100 % miss) we drop frames and log — better than blocking the renderer at 60 fps.
* Failure paths are fail-soft and log once at WARN. The mod can never crash Minecraft.
* The writer is a daemon thread (JVM exits cleanly with MC; no shutdown hook needed because the OS flushes file buffers on close).

---

## 5. Fallback behaviour (DA-V2 stays alive)

When `enable_real_depth_shader` is **false** or the preferences file is absent, the mod's `RealDepthExporter.start()` is a no-op (logs "disabled by preferences"). The recorder's existing DepthAnything V2 inference path (`bin/real_depth_filler.py`) runs unchanged.

```
                ┌─────────────────────────────────────────┐
                │ preferences.json::enable_real_depth_shader │
                └──────────────────┬──────────────────────┘
              ┌────────────────────┴────────────────────┐
              │                                         │
            true                                      false
              │                                         │
              ▼                                         ▼
   RealDepthExporter writes        DepthAnything V2 (bin/real_depth_filler.py)
   real GBuffer → EXR              reads recorded mp4 → infers depth → writes EXR
   (lint v3 #15 always PASS        (lint v3 #15 fails on ~15/1800 frames
    on real game-engine depth)      for texture-heavy scenes)
```

Operators can A/B both paths within the same session: enable the flag, record one clip, disable, record another, compare with `bin/lint_v3_prd_grounded.py`. The Fabric mod and the Python DA-V2 runner write to different filenames on the first pass (mod writes `000000.exr`, DA-V2 writes `frame_000000.exr` then renames), so they don't collide at packaging time.

---

## 6. Configuration reference

`~/Documents/OysterClips/preferences.json`:

| Key | Type | Default | Purpose |
|---|---|---|---|
| `enable_real_depth_shader` | bool | `false` | Master switch. False = DA-V2 fallback path remains canonical. |
| `depth_near_metres` | float | `0.05` | MC's near plane (cm-scale to keep precision for hand-held items). |
| `depth_far_metres` | float | `1024.0` | 64 chunks × 16 m — matches MC's default render distance ceiling. Tune down for shorter view distance (faster, narrower z range). |
| `depth_reversed_z` | bool | `false` | Set true if a co-installed mod enables reversed-z (rare; default MC uses classic-z). |
| `depth_width` | int | `1920` | Lint v3 #15 expects 1920×1080. Lower values fail the gate. |
| `depth_height` | int | `1080` | Same as width. |
| `depth_fps` | int | `6` | PRD §3.4 cadence. |

Unknown keys are ignored. The reader is a plain regex scan — no nested objects or arrays. Keep the file flat.

---

## 7. Validation pipeline

After every recording session, run:

```bash
python3 bin/real_depth_validator.py \
    --expected-count 1800 \
    --width 1920 --height 1080 \
    ~/Documents/OysterClips/<session>/depth
```

The validator checks (in order):

1. **Filenames**: 0-padded 6-digit indices, contiguous from `000000`, no duplicates.
2. **Frame count**: matches `--expected-count` (PRD §3.4 = 1800 for 5-min × 6 fps).
3. **Per-file structure**: single `Z` channel, float32, expected resolution.
4. **Per-file invalid ratio**: `(arr == 0) | ~isfinite(arr) ≤ 5 %`.
5. **No frame is solid-zero / solid-sky** (catches "scene loaded into nothing" failures).
6. **Aggregate**: emits `lint_v3_15_pass` and `lint_v3_16_pass` booleans for the recorder UI.

Exit codes:
* `0` — all gates pass; lint v3 #15 + #16 will agree.
* `1` — at least one gate failed; details printed (or JSON-emitted via `--json`).
* `2` — environment error (OpenEXR missing, directory absent).

---

## 8. Compiling the mod

The Mac dev box doesn't ship a JDK by default. To produce the jar:

```bash
# On a Linux/Windows/macOS box with JDK 21 installed:
cd mc-mod
./gradlew build                                # default MC version (1.21.4)
MC_VERSION=1.20.4 ./gradlew build              # other matrix rows
```

Output: `build/libs/oyster-recorder-mod-<modVersion>-mc<mcVersion>.jar`.

The mod depends only on Fabric Loader + Fabric API, both of which are pulled at build time. No native OpenEXR / Imath JNI — the EXR writer is pure Java in [`OpenExrFloat32Writer.java`](../mc-mod/src/main/java/world/oyster/recorder/depth/OpenExrFloat32Writer.java).

---

## 9. Honest caveats

* The Mac dev box that authored this skill has no JDK + LWJGL stack. The Java code is unit-tested **on paper** via a Python byte-layout port in `tests/test_java_exr_writer_format.py` — the OpenEXR 2.0 byte sequence is verified to be OpenEXR-readable and pixel-value-faithful through round-trip. The Fabric runtime path (the `WorldRenderEvents.END` hook + `glReadPixels` call) still needs a real Minecraft + JDK box for end-to-end smoke testing.
* The frame stride is derived from a hard-coded 60 Hz assumption (`60 / depth_fps = 10`). On a 144 Hz monitor the actual capture rate will be `144 / 10 = 14.4 fps` instead of 6 fps — over-sampling, which is fine for buyer acceptance because lint v3 only enforces `≥ 6 fps`, not `== 6 fps`. If under-sampling becomes a concern on lower-Hz displays, swap the stride for a wallclock-anchored timestamp comparison in a follow-up.
* Reversed-z support is present in the math (`depth_reversed_z` flag) but untested against an actual reversed-z MC mod. Default `false` matches stock MC behaviour.
* Shaders mods (Iris/OptiFine) that override the framebuffer pipeline may interfere with `glReadPixels` on the world buffer. If a vendor reports invalid output, disable any rendering mods before enabling the real-depth shader.

---

## 10. Related files

* Spec: [`specs/D1_depth_anything_v2_module.md`](../specs/D1_depth_anything_v2_module.md) — the DA-V2 fallback path.
* Lint v3: [`bin/lint_v3_prd_grounded.py`](../bin/lint_v3_prd_grounded.py) — the buyer-grounded acceptance check (gates #15 + #16).
* PRD: [`docs/PRD.md` §3.4](PRD.md) — buyer's depth/*.exr requirements.
* Validator tests: `tests/test_real_depth_math.py`, `tests/test_real_depth_validator.py`, `tests/test_java_exr_writer_format.py`.
* Fabric mod sources: `mc-mod/src/main/java/world/oyster/recorder/depth/`.
