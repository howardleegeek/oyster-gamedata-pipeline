# RFC A21+A22: Minecraft Mod IPC for Real 6DoF Camera Position & Rotation

> Cluster RFC — autonomous build via Aliyun (`minimax_agent_simple.py`).
> Closes MECE A21 (real camera_position) + A22 (real camera_rotation_quaternion).

## Iron contract

This RFC must pass an objective `gate.sh` (no human judgment):

```bash
#!/bin/bash
# gate.sh — A21+A22 acceptance
set -e
# 1. Java mod compiles
(cd mc-mod && ./gradlew build) || exit 1
# 2. Recorder side IPC client compiles
(cd recorder-side && cargo build) || exit 1
# 3. Roundtrip test: start mod in headless MC, recorder consumes 100 frames
test/roundtrip_test.sh || exit 1
# 4. Resulting action_camera.json frames have:
#    - camera_position with non-zero magnitude
#    - camera_rotation_quaternion with norm in [0.99, 1.01]
#    - varies over time (not constant)
python3 test/verify_real_data.py || exit 1
echo "A21+A22 PASS"
```

## Architecture

```
┌─────────────────────────┐                      ┌─────────────────────────┐
│  Minecraft Java (Fabric)│                      │  Rust Recorder Process  │
│  ┌─────────────────────┐│                      │  ┌─────────────────────┐│
│  │ oyster-data-mod     ││ ─── named pipe ───▶  │  │ record/mc_ipc.rs    ││
│  │   Tick hook:        ││    \\.\pipe\oyster   │  │   tokio task reads  ││
│  │     player.pos      ││    OR localhost:9876 │  │   line-delimited    ││
│  │     player.rotation ││    OR shmem ring     │  │   JSON frames       ││
│  │     write JSON      ││                      │  │                     ││
│  └─────────────────────┘│                      │  └─────────────────────┘│
└─────────────────────────┘                      └─────────────────────────┘
```

## Component 1 — Java Fabric mod (`mc-mod/`)

```java
// FabricInitializer entrypoint
public class OysterDataMod implements ClientModInitializer {
    private PipeWriter pipeWriter;
    @Override public void onInitializeClient() {
        pipeWriter = new PipeWriter("\\\\.\\pipe\\oyster-data");
        ClientTickEvents.END_CLIENT_TICK.register(this::onTick);
    }
    private void onTick(MinecraftClient mc) {
        if (mc.player == null) return;
        Vec3d pos = mc.player.getPos();
        float yaw = mc.player.getYaw();
        float pitch = mc.player.getPitch();
        // Convert to PRD left-handed: negate yaw, swap Y/Z, etc.
        // (Refer to oyster-gamedata-pipeline/docs/COORD_SYSTEM.md)
        double[] quat = eulerToQuat(yaw, pitch, 0);
        String json = String.format(
            "{\"t_ms\":%d,\"px\":%.4f,\"py\":%.4f,\"pz\":%.4f,\"qx\":%.6f,\"qy\":%.6f,\"qz\":%.6f,\"qw\":%.6f}",
            System.currentTimeMillis(), pos.x, pos.y, pos.z, quat[0], quat[1], quat[2], quat[3]
        );
        pipeWriter.writeLine(json);
    }
}
```

Build: `gradle build` produces `oyster-data-mod-X.Y.Z.jar`.

## Component 2 — Recorder IPC client (`src/record/mc_ipc.rs`)

```rust
pub struct McIpcClient { rx: mpsc::Receiver<McTickData> }
impl McIpcClient {
    pub fn spawn() -> Self {
        let (tx, rx) = mpsc::channel(1024);
        std::thread::spawn(move || {
            let pipe = open_named_pipe("\\\\.\\pipe\\oyster-data");
            let reader = BufReader::new(pipe);
            for line in reader.lines() {
                if let Ok(data) = serde_json::from_str::<McTickData>(&line.unwrap()) {
                    let _ = tx.blocking_send(data);
                }
            }
        });
        Self { rx }
    }
    pub async fn latest(&mut self) -> Option<McTickData> { self.rx.try_recv().ok() }
}
```

## Component 3 — Wiring (`src/record/action_camera_writer.rs`)

Currently writes placeholder `camera_position: [0.0, 0.0, 0.0]` and identity
quaternion. Replace with `mc_ipc.latest()` poll at frame write time.

## Acceptance tests

1. **Headless roundtrip**: start `java -jar mc-test-server.jar` + `cargo run --bin recorder -- --headless --duration 10`. Assert at least 100 frames have non-placeholder camera_position.
2. **Quaternion sanity**: norm in `[0.99, 1.01]` on every frame.
3. **Coord system**: yaw rotation positive → camera moves right (left-handed PRD convention).
4. **Latency**: tick-to-write < 100ms (real-time recording).

## Estimated effort

| Component | Hours |
|---|---|
| Java mod skeleton (Fabric template fork) | 4 |
| Pipe writer + tick hook | 4 |
| Rust IPC reader | 4 |
| Wire to action_camera_writer | 2 |
| Roundtrip test harness | 6 |
| Coord system conversion (left/right hand, axis swap) | 4 |
| Debug + integration | 16 |
| **Total** | **40 hours** ≈ 1 week |

## Non-goals / out of scope

- NOT replacing OBS video recording
- NOT changing PRD field schema (already done in rc19.0.4)
- NOT supporting non-Fabric mod loaders (Forge / Quilt later)
- NOT including audio capture (separate from mod)

## Dispatch command

```bash
SPEC_FILE=oyster-audit/cluster-rfcs/RFC-A21-A22-mc-mod-ipc.md \
  WORKING_DIR=/tmp/cluster-a21-a22 \
  TASK_ID=A21-A22-mc-mod \
  AGENT_MODEL=deepseek-v3.2 \
  python3 ~/Downloads/oyster/infra/dispatch/temporal-poc/minimax_agent_simple.py
```
