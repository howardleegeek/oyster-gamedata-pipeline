# Raw Capture / Server Postprocess Architecture

## Decision

The consumer Windows client and Minecraft mod capture raw evidence only. They do
not generate final linear depth, OpenEXR, or buyer-specific depth encodings on the
player machine.

## Why

User machines vary too much: missing dependencies, different GPU drivers, AMD/NVIDIA
differences, low VRAM, antivirus behavior, and inconsistent Minecraft installs. The
depth deliverable is valuable precisely because it is harder than screen recording,
so the expensive part must run in a controlled server environment.

## Client Responsibilities

- launch the fixed Minecraft POC version;
- record video;
- capture camera telemetry, game state, inputs, timestamps, and manifest metadata;
- optionally dump raw non-linear depth texture/buffer data if the mod can expose it safely;
- package and upload the raw session after recording ends.

## Server Responsibilities

- depth linearization;
- OpenEXR float32 `Z` generation;
- alternate encodings such as uint16 depth PNG;
- compression and buyer-specific dataset conversion;
- quality scoring and acceptance reports.

## Minecraft POC Rule

Minecraft remains the first production POC because the mod ecosystem and game-state APIs are
tractable. The client is optimized for stability, not final-format rendering. The server turns
the raw session into buyer deliverables.

## Expansion Rule

New games must first prove a plug-and-play raw capture plan:

1. video;
2. camera or viewpoint telemetry;
3. game state or environment state;
4. input/action stream when available;
5. capture manifest;
6. uploadable raw session archive.

Only after that plan is stable should the server-side depth or buyer-format converter be added.

The executable code contract lives in `src/oyster_agent_runner/capture_architecture.py`.
