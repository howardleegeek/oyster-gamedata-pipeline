# SF — Phase A.1: terminator.json (game-agnostic 故障归因)

修改 `bin/recorder_consumer_lite.py` `_run_one_session()` 和 finalize 路径, 增加每个 session 写 `terminator.json` 到 `clip_dir/`.

## game-agnostic 设计 (Howard 2026-05-09: 未来加入更多不同游戏)

不要写 `reason = "mc_died"`, 写 `reason = "game_died"`. game-specific 信息单独放 `game_specific` dict:

```python
TERMINATOR_REASONS = (
    "clean_exit",          # 录满 5-6 min 玩家正常退游戏
    "game_died",           # 游戏进程退出 (mc / roblox / fortnite ...)
    "game_crashed",        # 游戏 process 异常退 (returncode != 0)
    "ffmpeg_died",         # 录像编码进程崩
    "ffmpeg_dirty_close",  # rc10 B2: 编码进程关闭脏 (mp4 trailer 未写)
    "disk_full",           # rc10 B5 触发
    "user_close_kill_game",   # 用户关录制器同时杀游戏
    "user_close_keep_game",   # 用户关录制器保留游戏
    "orphan_resumed",      # 启动时检到上次未清理的 tmp_dir
    "duration_too_short",  # < 5 min, PRD reject
    "crash",               # recorder 自身异常退出 (excepthook 兜底)
    "mod_handshake_failed",  # game-side mod 没回握手
    "preflight_blocked",   # Layer 0 preflight 阻断了 (Win版本/admin/etc)
)
```

## terminator.json schema

```json
{
  "schema_version": "1.0",
  "session_id": "<uuid>",
  "reason": "clean_exit",
  "last_recorded_at": "2026-05-09T16:30:12.345Z",
  "duration_sec": 367.2,
  "frame_count": 11016,
  "disk_free_mb_at_end": 2451,
  "ffmpeg_clean_close": true,
  "game_alive_at_terminate": false,
  "recorder_version": "lite-v0.28.0-rc11",
  "game_specific": {
    "game_id": "minecraft_java",
    "game_version": "1.21.4",
    "process_name": "javaw.exe",
    "mod_version": "0.1.0",
    "mod_handshake_ok": true
  }
}
```

`game_specific.game_id` 是核心抽象 — 未来加 roblox 就是 `game_id: "roblox_studio"`. 当前 hardcode `"minecraft_java"` 但放在 `game_specific` 子树里, 不放顶层.

## 实现要点

1. `__init__` 加 `self._terminator_reason: Optional[str] = None`
2. 每个 early return 前 set 一次 reason: `self._terminator_reason = "disk_full"` 等
3. 一个新方法 `_write_terminator(self, clip_dir: Path) -> None` 在 finalize 里调用 + 在 early return 路径里也调用 (即使没 clip_dir 就跳过)
4. `_run_one_session` 末尾 (clean exit) 之前 set `self._terminator_reason = "clean_exit"`
5. `_on_close` (B1 路径) 的两个分支分别 set `user_close_kill_game` / `user_close_keep_game`
6. 在 disk-full 路径 (rc10 SE 加的) set `disk_full` reason

## 约束
- 不动 systeminfo.json / metadata.json schema, terminator.json 是新文件
- bundle 进 tarball (现有 tarfile.add(clip_dir) 自动包括)
- 不引入新依赖, 全 stdlib
- `game_specific` 当前 hardcode minecraft 字段是 OK 的, 但结构必须留好 future game

## 验收
- [ ] 文件中存在常量 `TERMINATOR_REASONS` (tuple, 13 项)
- [ ] `_write_terminator` 方法存在
- [ ] terminator.json 在至少 3 种 reason (clean_exit / game_died / disk_full) 都能写到 disk
- [ ] `python3 -c "import ast; ast.parse(...)"` 通过
- [ ] 解 tarball 看到 terminator.json 跟 systeminfo.json 同级
