# Mineflayer ↔ Coordinator JSON-Line Protocol

**Version:** `1` (Phase 1)
**Transport:** subprocess `stdin` / `stdout`, line-delimited JSON (one object per line, UTF-8, `\n` terminator).
**Owner:** Python coordinator (the `MinecraftEnvironment` adapter in `src/oyster_agent_runner/environments/minecraft.py`) is the parent process.
**Stderr:** reserved for human-readable Mineflayer logs. The coordinator does NOT parse stderr.

> Phase 1 scope: connect, spawn, observe, dispatch four actions (`move_to`, `dig`, `look`, `chat`).
> Phase 2 will add a separate spectator-client + OBS pipeline; this protocol stays unchanged.

---

## 1. Lifecycle

```
parent spawns:  node bot.js --host <h> --port <p> --username <u> [--version <v>]
                                                        │
                                                        ▼
parent → bot:   {"v":1,"type":"hello"}                  ◄── handshake
bot   → parent: {"v":1,"type":"hello_ack","bot":"<u>"}
bot   → parent: {"v":1,"type":"spawn", ...}             ◄── once the bot has spawned
parent → bot:   {"v":1,"type":"action","id":1,"action":{...}}
bot   → parent: {"v":1,"type":"observation","id":1, ...}
...
parent → bot:   {"v":1,"type":"shutdown"}
bot   → parent: {"v":1,"type":"goodbye"}
                bot exits, return code 0
```

Every message has a top-level `v` (protocol version, currently `1`) and `type`.
Unknown `type` values MUST be ignored by the receiver, not crash. The `id` field correlates an `action` request with its `observation` reply.

If the parent does not send `hello` within 10 s of subprocess start, the bot logs a warning to stderr but keeps waiting. If the bot does not emit `hello_ack` within 30 s of `hello`, the coordinator considers the bot dead and aborts.

---

## 2. Messages — parent → bot

### 2.1 `hello`

```json
{"v": 1, "type": "hello"}
```

Emitted exactly once after subprocess start. Acknowledges the parent is ready to receive observations.

### 2.2 `action`

```json
{"v": 1, "type": "action", "id": 7, "action": {"op": "move_to", "target": [124, 64, -82]}}
```

Phase 1 supports four `op` values:

| op | args | semantics |
|---|---|---|
| `move_to` | `target: [x, y, z]` (block coords) | Pathfind to coords. Replies once arrived OR after `timeout_sec` (default 10). |
| `dig` | `target: [x, y, z]` | Dig the block at the given coords. Replies once block is broken or unreachable. |
| `look` | `yaw: radians`, `pitch: radians` | Set head orientation. Replies on next tick. |
| `chat` | `message: str` | Emit a chat message. Replies on next tick. |
| `noop` | — | Wait one tick. Replies on next tick. |

Any other `op` MUST produce a reply with `{"ok": false, "error": "unknown_op"}` — never silently dropped.

### 2.3 `shutdown`

```json
{"v": 1, "type": "shutdown"}
```

The bot disconnects from the server, emits `goodbye`, and exits with code 0.

---

## 3. Messages — bot → parent

### 3.1 `hello_ack`

```json
{"v": 1, "type": "hello_ack", "bot": "<username>", "mineflayer_version": "<x.y.z>"}
```

### 3.2 `spawn`

```json
{
  "v": 1, "type": "spawn",
  "position": [123.5, 64.0, -80.2],
  "yaw": 87.3, "pitch": -5.1,
  "health": 20, "food": 20, "xp": 0,
  "game_mode": "survival", "dimension": "overworld",
  "world_seed": 42
}
```

Emitted once when the bot's `spawn` event fires. The Python coordinator's
`Environment.reset()` returns this payload as the initial `observation`.

### 3.3 `observation`

```json
{
  "v": 1, "type": "observation", "id": 7, "ok": true,
  "tick": 412,
  "bot": {
    "position": [123.5, 64.0, -80.2],
    "yaw": 87.3, "pitch": -5.1,
    "health": 20, "food": 20, "xp": 0
  },
  "inventory": [
    {"slot": 0, "name": "oak_log", "count": 4},
    {"slot": 1, "name": null, "count": 0}
  ],
  "blocks_near": [
    {"pos": [124, 64, -82], "name": "oak_log"}
  ],
  "entities_near": [
    {"id": 7, "type": "creeper", "pos": [120, 64, -80], "distance": 4.2}
  ],
  "task_state": {"active_goal": "collect_wood", "progress": {"oak_log": 4}}
}
```

If the action failed:
```json
{"v": 1, "type": "observation", "id": 7, "ok": false, "error": "<reason>", "tick": 412, "bot": {...}, "inventory": [...]}
```

The bot ALWAYS includes the latest `bot` + `inventory` snapshot even on failure so the coordinator can persist `metadata.jsonl` lines unconditionally.

### 3.4 `tick`

```json
{"v": 1, "type": "tick", "tick": 412, "bot": {...}, "inventory": [...]}
```

Phase 1 does NOT emit unsolicited `tick` events — the coordinator only sees state in `observation` replies (one per action). Phase 3 may add a streaming-tick mode for richer metadata; the message shape is reserved here so consumers can be forward-compatible.

### 3.5 `goodbye`

```json
{"v": 1, "type": "goodbye"}
```

Emitted in response to `shutdown` immediately before process exit.

### 3.6 `error` (asynchronous)

```json
{"v": 1, "type": "error", "fatal": true, "error": "kicked: server full"}
```

Emitted whenever Mineflayer's `error` / `kicked` / `end` events fire outside the request-response flow. If `fatal` is `true`, the bot will exit shortly after — the coordinator should treat the run as terminated.

---

## 4. Time

The bot does NOT timestamp messages itself. The Python coordinator stamps every received line with its monotonic clock at read time, so the four output files (`cot.jsonl`, `metadata.jsonl`, `inputs.jsonl`, manifest) all share a single wall-clock anchor.

This also means the coordinator owns drift handling — see `MINECRAFT_TRAJECTORY_SPEC.md §3.2`.

---

## 5. Backwards-compat & versioning

- `v=1` is Phase 1.
- Adding new fields to existing message types is non-breaking; receivers ignore unknown fields.
- Adding a new `type` is non-breaking; receivers ignore unknown types.
- Removing a field, renaming a field, or changing a field's semantics is breaking → bump `v`.

---

## 6. Reference Python parser

See `src/oyster_agent_runner/environments/minecraft.py::_MineflayerProcess` — that's the canonical implementation of the parent side of this protocol.
