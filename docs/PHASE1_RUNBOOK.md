# Minecraft Trajectory — Phase 1 Operator Runbook

**Status:** Phase 1 (LIVE).
**Scope:** Generate a single CoT + metadata + inputs trajectory for the
`MC-tutorial-001` task. Video stream lands in Phase 2.
**Target wall-clock per trajectory:** 5 minutes (50 LLM steps).
**Owner:** Howard Li (`howard@oysterlabs.ai`).
**Source spec:** [`MINECRAFT_TRAJECTORY_SPEC.md`](MINECRAFT_TRAJECTORY_SPEC.md).

---

## 0. What Phase 1 produces

```
trajectories/run-001/
├── manifest.json     ← session metadata + alignment proof (Phase 1 leaves video fields null)
├── cot.jsonl         ← LLM thinking + reasoning + actions
├── metadata.jsonl    ← per-step Minecraft world state from the Mineflayer bot
├── inputs.jsonl      ← per-step bot actions (move / dig / look / chat)
└── trajectory.jsonl  ← combined event log (the one the runner writes natively;
                        the three .jsonl files above are demuxes of this one)
```

Phase 1 explicitly **does NOT produce**:
- `video.mp4` — Phase 2 (OBS + spectator client)
- `frames.jsonl` — Phase 2

## 1. Host prerequisites (one time per machine)

### 1.1 Node.js 18+

```bash
node --version    # must report >= v18.0.0
```

If absent, install via `nvm` (`brew install nvm` on macOS) and `nvm install 18`.

### 1.2 npm install of the Mineflayer bot dependencies

```bash
cd /Users/howardli/Downloads/oyster-agent-runner/mineflayer
npm install
```

This installs `mineflayer`, `mineflayer-pathfinder`, `vec3`, and `minecraft-data`.
Disk: ~70 MB. Fast on first install (~30s on a clean cache).

The Python coordinator refuses to spawn the bot if `mineflayer/node_modules/`
doesn't exist — it will surface a `MineflayerProcessError` with the
remediation hint pointing back to this command. Don't skip this step.

### 1.3 Java 21 (for the Paper server)

Paper 1.20.6+ requires Java 21. macOS:

```bash
brew install openjdk@21
java -version    # should report 21.x
```

### 1.4 Paper 1.20.x server

Download a Paper 1.20.x build (we recommend `paper-1.20.6-NNN.jar`):

```bash
mkdir -p ~/minecraft-server && cd ~/minecraft-server
# Pick a build from https://api.papermc.io/v2/projects/paper
curl -o paper.jar "https://api.papermc.io/v2/projects/paper/versions/1.20.6/builds/151/downloads/paper-1.20.6-151.jar"

# Accept the EULA (single-player / private-server use is permitted by Mojang).
echo "eula=true" > eula.txt

# First-run boot to materialize server.properties etc.
java -Xms2G -Xmx2G -jar paper.jar nogui
# Wait for "Done" then Ctrl-C.
```

Edit `server.properties` for trajectory work:

```properties
online-mode=false       # required — the bot connects without Mojang auth
gamemode=survival
difficulty=easy         # spec calls for non-combat tutorial
spawn-protection=0      # allow the bot to dig at spawn
view-distance=8
simulation-distance=6
level-seed=42           # match the task's world_seed for reproducibility
motd=Oyster L4 trajectory generator
```

Then start the server in the background:

```bash
java -Xms4G -Xmx4G -jar paper.jar nogui
```

Verify it's listening:

```bash
nc -z localhost 25565 && echo "server reachable"
```

> **Why Paper instead of vanilla?** Paper supports the same protocol with
> better performance under bot load and faster startup. Vanilla works
> too — just download `server.jar` from minecraft.net and follow the
> same EULA / properties steps.

## 2. Python prerequisites

```bash
cd /Users/howardli/Downloads/oyster-agent-runner
source .venv/bin/activate
pip install -e '.[dev]'
```

Then export your Anthropic API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## 3. Run a Phase 1 trajectory

With the Paper server running on `localhost:25565` and the Mineflayer
deps installed:

```bash
cd /Users/howardli/Downloads/oyster-agent-runner
source .venv/bin/activate

python -m oyster_agent_runner.cli run-mc \
  --task-file tasks/MC-tutorial-001.json \
  --output-dir trajectories/run-001/ \
  --provider claude-thinking \
  --max-steps 50
```

Default `--minecraft-server` is `localhost:25565`. Override with
`--minecraft-server <host:port>` if your Paper server is elsewhere.

Expected wall-clock: 5–8 minutes for a 50-step run with thinking-budget
16K. Token cost: ~$1–2 per run on Sonnet 4.5.

### What you should see

1. **Server console** (Paper): `oyster_bot joined the game`
2. **CLI panel** with `task=MC-tutorial-001`, `provider=claude-thinking`
3. Periodic `[bot] login OK` / `[bot] pathfinder setup warning` lines on
   the bot's stderr (these are diagnostic only — don't redirect them
   into stdout, that would break the protocol).
4. After each step, the runner picks up an observation and emits to the
   trajectory.

### Expected output sizes (for a 50-step run)

| File | Approximate size |
|---|---|
| `cot.jsonl` | 50–200 KB (depends on thinking length) |
| `metadata.jsonl` | 30–100 KB |
| `inputs.jsonl` | 8–20 KB |
| `manifest.json` | < 2 KB |
| `trajectory.jsonl` | 80–300 KB (superset of the above) |

## 4. Verify the trajectory

```bash
# 4 expected files
ls -la trajectories/run-001/
# Spot-check the first few CoT events
head -5 trajectories/run-001/cot.jsonl

# Count thinking events (should be > 0)
grep -c '"event_type":"LLM_THINKING"' trajectories/run-001/cot.jsonl

# Confirm manifest is valid JSON
python -m json.tool trajectories/run-001/manifest.json | head -30
```

The `manifest.json`'s `alignment` block lists `cot_event_count`,
`metadata_event_count`, `input_event_count`, and `max_timestamp_sec`
— these are the buyer-facing counts that prove the streams are
internally consistent. Phase 2 adds `max_observed_drift_ms` once video
lands.

## 5. Troubleshooting

### "Mineflayer dependencies not installed"

You skipped step 1.2. Run `npm install` in `mineflayer/`.

### "timeout waiting for hello_ack" / "timeout waiting for spawn"

The bot started but couldn't reach the server. Check:

```bash
nc -z localhost 25565 || echo "server not listening"
grep -i error ~/minecraft-server/logs/latest.log | tail -10
```

If the server is reachable but the bot times out, double-check
`online-mode=false` in `server.properties`. Online mode requires Mojang
auth, which the bot deliberately doesn't use.

### Bot kicked: `Outdated client`

Paper version doesn't match what `mineflayer` auto-detected. Either:
1. Upgrade `mineflayer` to a version that supports your Paper build:
   `cd mineflayer && npm install mineflayer@latest`
2. Or pin the protocol with `--bot-version` (we'd need to add this CLI
   flag — Phase 2 todo).

### `MineflayerProcessError: subprocess died mid-step`

The bot crashed. Check stderr — it's piped through to the parent process
on subprocess death. Common causes:
- Pathfinder couldn't find a path (try a smaller move target)
- The block at `dig.target` doesn't exist (LLM hallucinated coordinates)
- Server kicked the bot for spamming (rate-limit chat messages — the
  pre-Phase-2 LLM-step rate is 6–10 req/min, well under the limit, so
  this is unlikely)

### LLM action times out

Claude with thinking 16K can take 5–15 s per turn. The default
`action_timeout_sec=30` in `MineflayerProcess` is for the Mineflayer
side — it's the time budget for `move_to` etc to finish, not for the
LLM. If a `move_to` hits the 30 s wall, it's because the pathfinder
couldn't make progress; consider chunking the goal.

## 6. Manual integration smoke test

> **Automated path:** for the common case, run `bin/smoke_phase1.sh` instead
> of the steps below. The script auto-detects `java`/`node`/`npm` (gracefully
> skips with exit 0 when any are missing), downloads a pinned Paper jar to
> `bin/.cache/paper-1.20.4.jar`, runs `npm install` if `mineflayer/node_modules/`
> is absent, boots Paper, runs `run-mc` with `--max-steps 5`, validates the
> four output files, and tears Paper down. Use `--dry-run` to exercise the
> script's plumbing in CI without launching Paper, and `--no-download` to
> reuse an already-cached jar. Exit 0 = pass or skip; exit 1 = real failure.

The unit tests use a mocked subprocess. To verify the real wiring:

```bash
# 1. Server up
nc -z localhost 25565 && echo OK

# 2. Bot can connect on its own (no Python coordinator)
cd mineflayer
node bot.js --host localhost --port 25565 --username smoke_test &
BOT_PID=$!
sleep 8
echo '{"v":1,"type":"hello"}' >&3
# (this is fragile — easier to just run the full Python pipeline)
kill $BOT_PID
```

In practice, the right smoke test is the full `run-mc` command with
`--max-steps 5` against a real server. If that produces a manifest with
`cot_event_count >= 5` and `result.termination_reason in
("success", "max_steps")`, the integration is healthy.

## 7. Cost monitoring

A 50-step run with thinking-budget 16K at Sonnet 4.5 pricing
(~$3 / MTok input, ~$15 / MTok output, thinking counted as output):

- 50 steps × ~16,000 thinking tokens = 800K thinking tokens ≈ $12
- 50 steps × ~1,000 action tokens = 50K action tokens ≈ $0.75
- 50 steps × ~2,000 input tokens = 100K input tokens ≈ $0.30

**Budget per 50-step trajectory: ~$13.** A 20-hour bundle (40 × 30-min
trajectories) is in the $200–600 range as the spec predicted.

Watch the Anthropic console and pull the API once an hour during a
batch run.

## 8. License + provenance

Each `manifest.json` carries `license: "train-only"`. Howard signed off
on this for Phase 1. Bundles MUST NOT be redistributed by buyers.
Provenance details (uploader pseudonym, ToS chain) land in
`provenance.json` in Phase 4 packaging — Phase 1 omits it.

## 9. Next steps

- Phase 2: video stream (OBS + spectator client). Spec § 7.2.
- Phase 3: task library expansion + concurrency. Spec § 7.3.
- Phase 4: bundle packaging + Decart pitch. Spec § 7.4.
