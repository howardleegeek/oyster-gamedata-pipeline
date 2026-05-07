---
task_id: D19
project: oyster-gamedata-pipeline
priority: 2
estimated_minutes: 60
depends_on: [D15 mc-mod foundation]
modifies:
  - mc-mod/build.gradle  (matrix support)
  - mc-mod/gradle.properties  (multi-version block)
  - .github/workflows/build-mc-mod.yml  (matrix strategy)
  - mc-mod/src/main/java/world/oyster/recorder/  (no API divergence)
must_not_touch:
  - JSONL schema (must stay identical across versions)
  - bin/game_state_overlay.py (consumer is version-agnostic)
executor: glm
iron_law: REAL ONLY — every version build MUST run the JsonlWriter end-to-end
---

# D19: Multi-MC-version Fabric build matrix

## 目标

Currently `mc-mod` builds for MC 1.21.4 only. Real testers run multiple
MC versions; many run 1.20.1 (latest LTS) or 1.20.4 (popular plugin
ecosystem). Build a matrix of:

- 1.20.1 (yarn 1.20.1+build.10, fabric-api 0.92.x)
- 1.20.4 (yarn 1.20.4+build.3, fabric-api 0.97.x)
- 1.21.1 (yarn 1.21.1+build.3, fabric-api 0.103.x)
- 1.21.4 (current default, yarn 1.21.4+build.1, fabric-api 0.110.x)

Each produces a separate `oyster-recorder-mod-{version}-mc{mcv}.jar`. The
.exe auto-installer (D17) picks the matching one based on which MC
version is installed.

## 验收标准 (REAL ONLY)

- [ ] `mc-mod/gradle.properties` accepts a `MC_VERSION` env var, defaults
      to 1.21.4 for local dev. Each (mc_version, yarn, fabric-api,
      loader) tuple is in a single map at top of build.gradle
- [ ] `gradle build -PMC_VERSION=1.20.1` produces `build/libs/oyster-
      recorder-mod-X.Y.Z-mc1.20.1.jar` that includes a `fabric.mod.json`
      with `"depends": {"minecraft": "~1.20.1"}`
- [ ] All 4 versions compile cleanly. Java code MUST NOT diverge — if
      yarn mappings rename a method, abstract behind a single internal
      shim file
- [ ] CI matrix: `.github/workflows/build-mc-mod.yml` runs all 4 versions
      in parallel, uploads as separate artifacts
- [ ] Smoke test per version: each .jar must contain
      `META-INF/jars/fabric-api-base-X.Y.Z.jar` (i.e. resolves the
      version-specific fabric-api dep)
- [ ] Documentation: mc-mod/README.md "Build" section lists the matrix +
      example invocation

## REAL artifact criterion

- Each matrix cell must produce a non-empty .jar that registers
  `fabric.mod.json` listing the matching MC version. CI test:
  `unzip -p oyster-recorder-mod-*-mc{V}.jar fabric.mod.json | jq .depends.minecraft`
  → matches `~{V}`
- No "skip if version unsupported" branches — every cell builds or fails
  loudly.

## 不要做

- 不要拷贝 Java source per version (use gradle source-set or runtime
  reflection if mappings diverge)
- 不要 expand schema across versions — JSONL stays the same
