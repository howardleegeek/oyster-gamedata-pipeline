# Minecraft Stack — Canonical Versions (2026-05-04)

> Definitive answer for the team.
> Quote this doc when anyone asks "what version are we on?"

---

## TL;DR

| Component | Pinned Version | Edition |
|---|---|---|
| **Minecraft** | **1.20.4** | **Java Edition** (NOT Bedrock) |
| **Server software** | **PaperMC 1.20.4** (build 499) | — |
| **Bot framework** | **Mineflayer ^4.20.0** | npm package |
| **Pathfinder plugin** | **mineflayer-pathfinder ^2.4.5** | npm package |
| **Node.js runtime** | **>=18.0.0** (we use 20.x) | required by mineflayer |
| **Java runtime** | **Java 21** (OpenJDK) | required by Paper 1.20.4+ |

**Why Java not Bedrock**: Mineflayer is a Node.js client that speaks the Java
Edition wire protocol. Bedrock uses a completely different protocol
(RakNet + custom NBT) and would require a different bot library entirely
(`bedrock-protocol`, not in our stack).

---

## Where each version is pinned in the repo

| Component | File | Line |
|---|---|---|
| Paper version constant | `bin/smoke_phase1.sh` | `PAPER_VERSION="1.20.4"` |
| Paper download URL | `bin/smoke_phase1.sh` | `paper-1.20.4-499.jar` |
| Paper jar on disk | `bin/.cache/paper-1.20.4.jar` | binary |
| Mineflayer dep | `mineflayer/package.json` | `"mineflayer": "^4.20.0"` |
| Pathfinder dep | `mineflayer/package.json` | `"mineflayer-pathfinder": "^2.4.5"` |
| Node engine | `mineflayer/package.json` | `"node": ">=18.0.0"` |
| Java path | `docs/QUICKSTART.md` | `/opt/homebrew/opt/openjdk@21/bin/java` |

---

## Install flow (3 steps)

### Step 1 — Java 21
```bash
# macOS:
brew install openjdk@21
# Ubuntu 22.04:
sudo apt-get install openjdk-21-jdk
# verify:
java --version  # expect "openjdk 21.x"
```

### Step 2 — Paper Minecraft server (auto-download via our script)
```bash
cd /path/to/oyster-agent-runner
bash bin/smoke_phase1.sh
# Will auto-download bin/.cache/paper-1.20.4.jar (build 499, ~50 MB) on first run
```

### Step 3 — Mineflayer bot deps
```bash
# Need Node.js 20.x first:
nvm install 20  # OR brew install node@20

cd mineflayer
npm install            # installs mineflayer 4.20.x + mineflayer-pathfinder 2.4.x
node bot.js --check    # smoke test
```

### Verification — end-to-end
```bash
bash bin/produce_real_sample_v2.sh
# Boots Paper 1.20.4 server (~16s) + connects Mineflayer bot + captures
# 5-min ScriptedProvider trajectory + writes buyer-spec tarball
```

---

## Compatibility matrix

### What works
| Paper | Mineflayer | Status |
|---|---|---|
| 1.20.4 | 4.20.x | ✅ pinned production stack |
| 1.20.4 | 4.21.x | ✅ tested, drop-in compatible |
| 1.20.6 | 4.20.x | ⚠️ Mineflayer 4.20 doesn't yet declare 1.20.6 in protocol map; use 4.21+ |
| 1.21.x | any 4.x | ❌ NOT YET supported by Mineflayer (1.21 protocol changes pending in upstream) |

### What does NOT work (don't try)
- **Bedrock Edition**: wrong protocol entirely — would need `bedrock-protocol` library
- **Forge / Fabric mods**: our bot is vanilla-protocol only; mods that change packets break it
- **MC 1.21+**: Mineflayer hasn't fully landed 1.21 protocol support (last checked 2026-05-04; check https://github.com/PrismarineJS/mineflayer/issues for updates)
- **Java 17 with Paper 1.20.4+**: Paper 1.20.5+ requires **Java 21**. 1.20.4 still allows 17 but we standardize on 21 to stay forward-compatible.

---

## Why we picked 1.20.4 specifically

1. **Stable protocol** — last truly-mature release before 1.21's combat refactor
2. **Mineflayer-mature** — 4.20.x has full coverage of 1.20.4 packets (block-place, mount, chat, etc.)
3. **Paper has Java 17 fallback** — 1.20.5+ forces Java 21, narrowing our tester pool
4. **VPT compatibility** — the OpenAI VPT pretraining dataset uses 1.16.5 base + actions are protocol-version-agnostic; 1.20.4 still produces buyer-spec-compatible action streams

**When we should bump**: when Mineflayer ships full 1.21.x support AND a buyer explicitly requests a newer feature (smithing-table 2.0, copper grates, etc.). Until then, **don't change**.

---

## Where the actual Java jar comes from

Paper auto-downloads from Paper's own CDN (PaperMC infrastructure), not Mojang directly. The script at `bin/smoke_phase1.sh` hardcodes:

```
https://api.papermc.io/v2/projects/paper/versions/1.20.4/builds/499/downloads/paper-1.20.4-499.jar
```

SHA256 (as of pin): see Paper's own checksum at the API endpoint above.
First-run download fingerprint should match; if not, our script fails closed.

---

## Bot capability summary (what 1.20.4 + Mineflayer can do)

| Action | Status |
|---|---|
| Move (WASD-equivalent) | ✅ via `bot.setControlState` |
| Look (mouse pitch/yaw) | ✅ via `bot.look(yaw, pitch)` |
| Mine block | ✅ via `bot.dig(block)` |
| Place block | ✅ via `bot.placeBlock(refBlock, faceVector)` |
| Open inventory / GUI | ✅ via `bot.openContainer` |
| Chat / commands | ✅ via `bot.chat()` |
| Pathfinding | ✅ via `mineflayer-pathfinder` |
| Spectator-mode capture | ✅ via Paper's `gamemode 3` + `spectate` |
| F1 hide-HUD | ❌ no Mineflayer API for client-side keys; we filter UI-out at video pipeline |
| Recipe book | ✅ via `bot.recipesFor` |
| Mount / dismount | ✅ via `bot.mount(entity)` |

---

## If your team hits a version mismatch

1. **`Cannot find supported version`** in mineflayer logs → server is newer than mineflayer's pinned protocol map. Either downgrade server to 1.20.4 OR upgrade mineflayer to 4.21.x.
2. **`UnsupportedClassVersionError` 65.0** when starting paper.jar → you're on Java 17. Install Java 21.
3. **`Connection lost: Outdated server!`** → you've crossed a major version boundary. Check `paper.jar` matches `1.20.4`.

---

## One-line answers for the team

> **Q: Bedrock or Java?**
> A: **Java Edition.** Bedrock would need a different bot library.

> **Q: What MC version?**
> A: **1.20.4** (PaperMC 1.20.4 build 499).

> **Q: What Mineflayer version?**
> A: **4.20.x** (npm `^4.20.0`).

> **Q: What Java?**
> A: **Java 21** (OpenJDK).

> **Q: Can the bot run on the buyer's box?**
> A: Anywhere with Java 21 + Node.js 20 + 4 GB RAM. Linux/macOS/Windows all confirmed.
