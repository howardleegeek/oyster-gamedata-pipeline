# `deploy_mod_to_cluster_RESTART.md` — Restart Procedure for the D16 Mod

**Companion to** `bin/deploy_mod_to_cluster.sh`.

`deploy_mod_to_cluster.sh` only **stages** the new mod jar into each
Paper instance's `mods/` directory. It deliberately does **not** restart
anything — Howard runs the swarm controller (`/tmp/swarm_controller.sh`,
PID 81197 with 21h+ uptime, "不停运转") and bouncing the servers is his
call. This file is the human-facing checklist for that bounce.

> **Estimated downtime**: 5–10 minutes total (≈2 min flush per Paper +
> 30 s per restart + 1–2 min for the swarm controller to settle).

> **Pre-req**: Each cluster Paper dir must be a Fabric server, not
> vanilla. If `deploy_mod_to_cluster.sh` errored with
> *"is NOT a Fabric server"*, do the [paper.jar → fabric-server-launch.jar
> swap](#one-time-vanilla-paper--fabric-server-conversion) below first.

---

## Step-by-step

### 1. Confirm the mod jars are staged

```bash
for d in /tmp/oyster_paper_d3 /tmp/oyster_paper_mac1_p2 /tmp/oyster_paper_mac1_p3; do
    echo "== $d =="
    ls -la "$d/mods/"oyster-recorder-mod-*.jar 2>/dev/null || echo "  (no oyster mod jar staged)"
done
```

Expected: each of the three dirs has exactly one
`oyster-recorder-mod-*.jar` matching the build artifact filename.

### 2. SIGTERM the swarm controller first (PID 81197 today)

The swarm controller actively manages bot connections and will throw
errors if it sees Paper restart while it's mid-connect. Stop it
**before** the Paper instances:

```bash
SWARM_PID=$(pgrep -f swarm_controller.sh | head -1)
echo "stopping swarm_controller PID $SWARM_PID"
kill -TERM "$SWARM_PID"
# Wait up to 10s for graceful shutdown, then SIGKILL if stuck:
for i in {1..10}; do kill -0 "$SWARM_PID" 2>/dev/null || break; sleep 1; done
kill -0 "$SWARM_PID" 2>/dev/null && kill -KILL "$SWARM_PID"
```

> **Note**: at time of writing, today's PID is 81197 with 21:40+ uptime.
> Always re-derive via `pgrep` — don't hardcode.

### 3. SIGTERM all 3 Paper instances and wait for them to flush worlds

Paper handles `SIGTERM` by running a clean `/stop` — it auto-saves the
world, kicks players gracefully, and exits 0. **Do not `kill -9`** unless
SIGTERM has been ignored for >2 min — that risks corrupting region files
and making world chunks unrecoverable.

```bash
for d in /tmp/oyster_paper_d3 /tmp/oyster_paper_mac1_p2 /tmp/oyster_paper_mac1_p3; do
    pid=$(cat "$d/paper.pid" 2>/dev/null)
    [[ -n "$pid" ]] || { echo "no pid for $d"; continue; }
    echo "stopping Paper at $d (PID $pid)"
    kill -TERM "$pid"
done

# Wait up to 120s for ALL three to exit. Paper's world flush takes the
# longest part of the shutdown.
deadline=$(( $(date +%s) + 120 ))
for d in /tmp/oyster_paper_d3 /tmp/oyster_paper_mac1_p2 /tmp/oyster_paper_mac1_p3; do
    pid=$(cat "$d/paper.pid" 2>/dev/null) || continue
    while kill -0 "$pid" 2>/dev/null && (( $(date +%s) < deadline )); do
        sleep 2
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo "WARNING: $d (PID $pid) still alive after 120s — investigate before SIGKILL"
    else
        echo "  $d stopped cleanly"
    fi
done
```

### 4. Restart Paper with `fabric-server-launch.jar` (NOT `paper.jar`)

`paper_server_start.sh` was written to launch vanilla Paper. For the
mod-loaded build we want the **same Aikar's flags** + same per-dir
config but pointed at `fabric-server-launch.jar`.

The flags below were copied verbatim from
`/tmp/oyster_paper_d3/paper_boot.log` for the currently-running PID
70706:

```bash
JAVA=/opt/homebrew/opt/openjdk@21/bin/java   # match the existing boot log
AIKAR_FLAGS=(
    -Xms2G -Xmx4G
    -XX:+UseG1GC
    -XX:+ParallelRefProcEnabled
    -XX:MaxGCPauseMillis=200
    -XX:+UnlockExperimentalVMOptions
    -XX:+DisableExplicitGC
    -XX:+AlwaysPreTouch
    -XX:G1NewSizePercent=30
    -XX:G1MaxNewSizePercent=40
    -XX:G1HeapRegionSize=8M
    -XX:G1ReservePercent=20
    -XX:G1HeapWastePercent=5
    -XX:G1MixedGCCountTarget=4
    -XX:InitiatingHeapOccupancyPercent=15
    -XX:G1MixedGCLiveThresholdPercent=90
    -XX:G1RSetUpdatingPauseTimePercent=5
    -XX:SurvivorRatio=32
    -XX:+PerfDisableSharedMem
    -XX:MaxTenuringThreshold=1
    -Dusing.aikars.flags=https://mcflags.emc.gs
    -Daikars.new.flags=true
)

# Restart each Paper instance.
declare -A PORTS=(
    [/tmp/oyster_paper_d3]=25565
    [/tmp/oyster_paper_mac1_p2]=25566
    [/tmp/oyster_paper_mac1_p3]=25567
)

for d in "${!PORTS[@]}"; do
    port=${PORTS[$d]}
    echo "starting Fabric server at $d on port $port"
    cd "$d" || continue
    if [[ ! -f fabric-server-launch.jar ]]; then
        echo "ERROR: $d has no fabric-server-launch.jar — see one-time conversion below"
        continue
    fi
    LOG="$d/paper_boot.log"
    nohup "$JAVA" "${AIKAR_FLAGS[@]}" -jar fabric-server-launch.jar nogui > "$LOG" 2>&1 &
    NEW_PID=$!
    echo "$NEW_PID" > "$d/paper.pid"
    echo "  $d: PID=$NEW_PID, log=$LOG"
done
```

Watch for `[Server thread/INFO]: Done (XX.XXXs)! For help, type "help"`
in each `paper_boot.log`. If you see
`[ERROR]: Mod 'oyster-recorder-mod' is not compatible with this Minecraft
version`, the build matrix produced a jar for the wrong MC version —
re-check `mc-mod/build.gradle`'s `MC_MATRIX` and re-run
`deploy_mod_to_cluster.sh` after the right CI build lands.

### 5. Verify each Paper LISTENs on its port

```bash
for port in 25565 25566 25567; do
    if (echo > /dev/tcp/localhost/$port) 2>/dev/null; then
        echo "  port $port: LISTEN ✓"
    else
        echo "  port $port: NOT LISTENING — check paper_boot.log"
    fi
done
```

### 6. Restart the swarm controller

```bash
nohup /tmp/swarm_controller.sh > /tmp/swarm_controller.log 2>&1 &
echo "swarm controller restarted, PID=$!"
```

Spot-check after ~30 s: `tail /tmp/swarm_controller.log` should show
healthy bot connects. If you see repeated reconnect-loop errors, look
back at the affected Paper's `paper_boot.log` for mod-side stack traces
— the most common cause is a `fabric-api` version mismatch, fixed by
rebuilding the mod against the right `MC_MATRIX` cell.

---

## One-time vanilla-Paper → Fabric-server conversion

If `deploy_mod_to_cluster.sh` failed with *"is NOT a Fabric server"*,
the dir is still vanilla Paper. Convert it once, then re-run the deploy
script:

1. Stop the Paper instance (step 3 above for that one dir).
2. Download the Fabric server installer for the matching MC version
   (currently `1.21.4`):
   ```bash
   curl -fLO https://meta.fabricmc.net/v2/versions/loader/1.21.4/0.16.9/1.0.1/server/jar
   mv jar "$d/fabric-server-launch.jar"
   ```
3. Move the existing `paper.jar` aside (keep it; `fabric-server-launch.jar`
   reuses Paper's `server.properties`, `bukkit.yml`, world dirs):
   ```bash
   mv "$d/paper.jar" "$d/paper.jar.bak"
   ```
4. Re-run `bin/deploy_mod_to_cluster.sh` — the dir is now Fabric-eligible.
5. Boot it via step 4 above.

Note: Fabric servers cannot load Paper plugins (Bukkit API differs).
Anything in `$d/plugins/` becomes inert. Mods drop into `$d/mods/`
instead — which is exactly what `deploy_mod_to_cluster.sh` now manages.

---

## TL;DR

| Phase | Action | Expected duration |
|-------|--------|-------------------|
| 1     | Confirm jars staged                          | <10 s |
| 2     | Stop swarm controller (SIGTERM)              | 10–30 s |
| 3     | SIGTERM 3 Papers, wait for world flush       | 30 s – 2 min each |
| 4     | Restart with fabric-server-launch.jar        | 30 s each |
| 5     | Verify ports LISTEN                          | <10 s |
| 6     | Restart swarm controller                     | 1–2 min |
| **Total** | | **5–10 min downtime** |
