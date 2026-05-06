# Research — Per-frame depth capture for Minecraft Java 1.20.4 on Windows

> Scope: depth alongside `recorder_consumer_lite.py`'s ffmpeg gdigrab path, no MC install changes.
> Buyer spec (`docs/BUYER_SPEC_V1.md` lines 129-136 + `docs/PRD_AUDIT_2026_05_04.md` bug #1):
> View-space linear depth, float32, single-channel "Z" in OpenEXR, **meters**, invalid pixels = 0,
> 6 fps sample rate, 1920×1080, 1800 frames per 5-min clip.
> The PRD's own escape hatch (PRD.md line 473) explicitly **allows DepthAnything V2 inference**
> as a substitute when a true Z-buffer is unavailable.

## TL;DR (30-second scan)

**Ship Replay Mod headless OpenEXR render (option 7) as the 5-day MVP.** Only path producing
true per-frame Z-buffer in buyer's exact format (OpenEXR float32 "Z") with no DLL injection,
no anti-cheat risk, no custom JVM mod authored by us. **Fallback: DepthAnything V2 post-pass
(option 5)** — already prototyped, buyer pre-approved on PRD p.473, runs at 50+ fps RTX 3090
/ 15-20 fps RTX 3060 (we need 6 fps). Avoid ReShade — DLL injection + anti-cheat ambiguity is
a B2B blocker.

---

## Approach scoring

Legend: 🟢 ship | 🟡 fallback | 🔴 reject

| # | Approach | Depth quality | MVP days | LOC | Risk | Verdict |
|---|---|---|---|---|---|---|
| 7 | **Replay Mod OpenEXR export** | True Z-buffer | **5** | ~250 wrapper | Low — established mod | 🟢 |
| 5 | **DepthAnything V2 post-pass** | Inferred metric | **3** | ~400 (already partial) | None — buyer pre-approved | 🟡 |
| 1 | Iris shader pack alone | True linearized depth GPU-side | n/a | n/a | **Cannot write to disk** | 🔴 |
| 2 | ReShade DLL injector | True Z-buffer | 7-10 | ~600 | Anti-cheat ambiguity, B2B liability | 🔴 |
| 3 | Windows.Graphics.Capture | None | n/a | n/a | API is color-only (BGRA8) | 🔴 |
| 4 | DXGI Desktop Duplication | None | n/a | n/a | Color-only by design | 🔴 |
| 6 | NVENC / NV Capture SDK | Color only (NvFBC) | n/a | n/a | Not a depth path | 🔴 |

---

## Per-approach detail

### 7. Replay Mod + Igrium's Replay Exporter — RECOMMENDED 🟢

Replay Mod natively records the full game state (not the rendered frames) into a `.mcpr` file,
then re-renders deterministically through the live MC engine — including the GPU depth buffer.
Render Settings supports **OpenEXR Sequence (1.14+)** with optional depth layer; depth is
**32-bit float written into the BGRA byte stream of a PNG sequence**, or as a true float layer
in the EXR. This is *exactly* the format `BUYER_SPEC_V1.md` line 132 demands.

- **Implementation:** wrap `OysterRecorder.exe` to (a) auto-install Replay Mod jar to
  `%APPDATA%\.minecraft\mods\`, (b) detect `.mcpr` under `.minecraft/replay_recordings/`,
  (c) drive headless render via `--render` to produce EXR @ 6 fps + MP4 @ 30 fps,
  (d) repack into `clip-XXXXX/depth/000000.exr` PRD layout.
- **Time-to-MVP:** 5 days. D1 jar bundling. D2-3 render harness. D4 EXR `Z`-channel repack.
  D5 GUI integration + lint validation.
- **LOC:** ~250 Python (wrapper) + 0 Java (Replay Mod is shipped binary).
- **Deps:** Replay Mod 0.4.x for Fabric 1.20.4 (already on Modrinth), `OpenEXR` Python (already
  in `pyproject.toml [exr]`).
- **Stack compatibility:** ✅ Tkinter GUI orchestrates same as today; ffmpeg is replaced by Replay
  Mod's own renderer for the depth pass; the existing gdigrab path can stay as live-preview MP4.
- **Quality:** True view-space Z from MC's depth buffer, float32, sky=0 by construction (depth
  cleared to far plane → we map far-plane samples to 0 to satisfy "invalid=0").
- **Anti-cheat risk:** zero. Replay Mod is a vanilla rendering tool, allowed on Hypixel and 2b2t,
  not gameplay-affecting.
- **Trade-off:** depth comes from session-end *re-render*, not live. ~1-2 min post-processing
  for a 5-min clip on a 3060. Acceptable since EXR delivery is batch, not live.

### 5. DepthAnything V2 metric (Outdoor Large) — FALLBACK 🟡

Already partially implemented (`bin/autoresearch_depth_quality.py` benchmarks DA-V2 vs Marigold;
`docs/OPEN_SOURCE_INTEGRATION.md` line 112 references `bin/real_depth_filler.py` 333 LOC).
The PRD literally says (line 473): *"vendor may use DepthAnything V2 ... or use the game engine's
built-in depth channel ... directly output."* It's pre-approved.

- **Implementation:** post-pass: read MP4 → sample 1/5 frames (30→6 fps) → run
  `Depth-Anything-V2-Metric-Outdoor-Large-hf` → write float32 EXR `Z` channel, zero sky via
  brightness threshold.
- **Time-to-MVP:** 3 days. D1 wire `real_depth_filler.py` into post-record hook. D2 GPU
  fallback. D3 sky-mask + lint.
- **LOC:** ~400 (≈100 new + reuse of 333 LOC).
- **Deps:** PyTorch + transformers (~3 GB) — significantly fattens the .exe, must be optional.
- **Stack compatibility:** clean — runs after ffmpeg finishes, no MC coupling.
- **Quality:** monocular metric, AbsRel ~0.45 m on outdoor benchmarks (per *Wildlife Setting*
  benchmark, 2025-10). Buyer already accepted this tier.
- **Inference speed:** ViT-S ≈50 fps on RTX 3090, ≈15-20 fps on RTX 3060 — we only need 6 fps,
  so even a 1660 Super clears it. CPU fallback adds ~10× latency (acceptable for batch).
- **Risk:** none, but file size of bundled torch is ~2 GB. Mitigation: post-install on first run
  via `pip install` from the .exe.

### 1. Iris/Sodium shader pack — INSUFFICIENT ALONE 🔴

Iris programs (`gbuffers_basic`, composite, final) write the depth texture to GPU FBOs and we
*can* author a shader pack that linearizes and writes to a custom color buffer. **But shader
packs cannot do disk I/O.** Writing each frame's depth to a `.exr` requires Java-side code
(Fabric mod), which is exactly the "modify game" we are forbidden to ship. Useful only as a
component of a custom mod approach — which is option 7's purer cousin and worse on MVP-time
because we'd be reinventing Replay Mod's renderer.

### 2. ReShade DLL injection — REJECT FOR B2B 🔴

Technically works: ReShade's *Generic Depth* add-on (5.0+) detects MC Java's OpenGL depth
buffer and gives shader-level access. Forum guidance is "tick *Copy depth buffer before clear
operations*". A custom .fx shader can copy depth → R32F target; ReShade's screenshot facility can
then dump to disk.

- **MVP-days:** 7-10 (writing the .fx, getting ReShade silent-install scripted, hooking timer
  loop, packaging the redistributable).
- **Why reject:** (1) DLL injection triggers some anti-cheats — ambiguous on Hypixel, blocked on
  most modded servers running Spark/Vulcan. We are a B2B vendor selling 1800-frame batches; one
  customer running a friend's anti-cheat server bricks the recording silently. (2) ReShade
  redistribution license needs commercial review. (3) ReShade is *generic* — fragile to MC patches.

### 3. Windows.Graphics.Capture API — NOT APPLICABLE 🔴

`Direct3D11CaptureFramePool` only emits `DXGI_FORMAT_B8G8R8A8_UNORM` (color BGRA). The API has
no surface for depth — it captures the *composited* output, which has no depth attachment. Your
hypothesis confirmed.

### 4. DXGI Desktop Duplication — NOT APPLICABLE 🔴

Same story as #3. `IDXGIOutputDuplication::AcquireNextFrame` returns the desktop's final color
texture only. ffmpeg's `ddagrab` simply wraps this. No depth path.

### 6. NVENC / NVIDIA Capture SDK — NOT APPLICABLE 🔴

NVENC encodes a color buffer you provide. NvFBC (capture SDK) returns the framebuffer color, not
depth. Nvidia exposes depth only inside their *Nsight Graphics* dev tools, which are not
redistributable for production capture.

---

## Recommendation

**Primary (5-day MVP): Replay Mod headless EXR render (option 7)** — only zero-DLL,
zero-anti-cheat-risk path producing buyer's exact EXR format from the real engine depth buffer,
on a Fabric stack the recorder already drives.

**Fallback (3-day pivot if Replay Mod proves unstable): DepthAnything V2 post-pass (option 5).**
PRD-authorized on p.473, autoresearch infrastructure already exists.

**Belt-and-suspenders:** ship both. Replay Mod is the default ground-truth path. DepthAnything
rescues clips with corrupt/missing `.mcpr` to keep the 1800-EXR contract intact.

---

## Sources

- [Replay Mod render settings — depth map and OpenEXR sequence](https://deepwiki.com/ReplayMod/ReplayMod/4.1-render-settings)
- [Replay Mod docs](https://www.replaymod.com/docs/)
- [Replay Mod forum — Z-depth multi-pass discussion](https://www.replaymod.com/forum/thread/913)
- [Iris Shader docs — DepthTex buffer reference](https://shaders.properties/current/reference/buffers/depthtex/)
- [Iris Shader docs — gbuffers reference](https://shaders.properties/current/reference/programs/gbuffers/)
- [ReShade — depth buffer detection on OpenGL/Java](https://reshade.me/forum/general-discussion/1903-java-games-and-depth-buffer)
- [ReShade — depth buffer detection modifications](https://reshade.me/forum/general-discussion/4083-depth-buffer-detection-modifications)
- [Hypixel forum — ReShade DLL allowed status](https://hypixel.net/threads/is-reshade-dll-allowed-on-hypixel.2499048/)
- [Microsoft Learn — Windows.Graphics.Capture screen capture (BGRA only)](https://learn.microsoft.com/en-us/windows/uwp/audio-video-camera/screen-capture)
- [Windows.Graphics.Capture namespace reference](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture?view=winrt-26100)
- [Depth Anything V2 — official site](https://depth-anything-v2.github.io/)
- [Depth Anything V2 metric outdoor model on HF](https://huggingface.co/depth-anything/Depth-Anything-V2-Metric-Outdoor-Large-hf)
- [Wildlife metric depth benchmark, arXiv 2510.04723](https://arxiv.org/html/2510.04723v1)
