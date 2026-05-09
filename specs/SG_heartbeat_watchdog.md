# SG — Phase A.2: health.json 心跳 + 外部 watchdog

## 文件 1: `bin/recorder_consumer_lite.py` 心跳写入

`_watch_loop` 每次循环 + 录制中每 30s + finalize 各阶段都写 `health.json`:

```python
HEALTH_PATH = Path(_real_documents_dir()) / "OysterRecorder" / "runtime" / "health.json"

def _write_health(state: str, frame_count: int = 0, game_pid_alive: bool = False) -> None:
    HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": time.time(),
        "ts_iso": datetime.now().isoformat(),
        "pid": os.getpid(),
        "state": state,  # idle | armed | recording | finalizing | crashed
        "frame_count": frame_count,
        "game_pid_alive": game_pid_alive,
        "recorder_version": RECORDER_VERSION,
    }
    try:
        HEALTH_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass  # never block on health write
```

最少调用点 (确保 30s 内 ≥ 1 次):
1. `_watch_loop` 顶部 (`state="idle"`)
2. arm 后 ("armed")
3. 录制 5s settle 期间每秒 ("armed")
4. 录制主循环每 30s ("recording" + frame_count)
5. finalize 开始 ("finalizing")
6. session 结束 (写完 terminator 之后) ("idle")
7. `sys.excepthook` 写一次 ("crashed") + raise

## 文件 2: `bin/oyster_play.py` 外部 watchdog

OysterPlay.exe 是 consumer launcher. 启动 OysterRecorder.exe 后启动一个 watchdog 后台 daemon thread:

```python
def _watchdog_loop():
    HEALTH_PATH = Path(install_root) / "runtime" / "health.json"
    LAST_KNOWN_TS = 0.0
    while True:
        time.sleep(30)
        try:
            payload = json.loads(HEALTH_PATH.read_text())
            ts = payload.get("ts", 0)
            if ts <= LAST_KNOWN_TS and (time.time() - ts) > 90:
                if _confirm_dialog(
                    "Oyster Recorder 无响应",
                    "录制器 90 秒没更新心跳了。重启录制器? (会丢失当前未保存的录像)"
                ):
                    _kill_recorder_pid(payload.get("pid"))
                    _relaunch_recorder()
                    LAST_KNOWN_TS = 0.0
                else:
                    return
            else:
                LAST_KNOWN_TS = ts
        except FileNotFoundError:
            pass
        except Exception:
            pass

threading.Thread(target=_watchdog_loop, daemon=True).start()
```

## 约束
- health.json 路径在 `OysterRecorder/runtime/` 不在 sessions/ (避免被打包进 tarball)
- watchdog 用 stdlib tkinter 弹框, 不引入新依赖
- 不在 recorder 进程内自检 (它如果挂了自检也挂)
- 重启时 recorder 从 idle 状态, 不试图恢复中断 session (Phase B)
- watchdog 失败 (file not found / parse error) 不告警, 只在确认 recorder 真挂时弹框

## 验收
- [ ] `health.json` 在录制中 frame_count 30s 跳一次
- [ ] 模拟 freeze (Stop-Process -Id <pid> 强 kill) — watchdog 90s 内弹框
- [ ] 重启后 recorder.exe 新 PID 写入 health.json
- [ ] OysterPlay.exe 关闭时 watchdog 跟着退 (daemon=True)
- [ ] `python3 -c "import ast; ast.parse(open('bin/oyster_play.py').read())"` 通过
