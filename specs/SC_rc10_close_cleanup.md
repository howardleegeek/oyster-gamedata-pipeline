# SC — rc10 关闭窗口清理 (B1+B2)

修改 `bin/recorder_consumer_lite.py` 的 `_on_close()` (大约 line 2462) 和 `_stop_ffmpeg()` (大约 line 2441):

## B1 — 关录制窗口时弹确认框, 同意则杀 MC

`_on_close()` 当前只 set stop_event + stop ffmpeg + destroy. 加: 在 `self.destroy()` 之前用 `tkinter.messagebox.askyesno("关闭确认", "也要关闭 Minecraft 吗?\n\n是 = 关 MC + 关录制\n否 = 仅关录制 (MC 继续运行无录像)")`. yes → Windows 上 `subprocess.run(["taskkill", "/F", "/IM", "javaw.exe"], creationflags=0x08000000, capture_output=True, timeout=5)`, 错误吞 + trace. 非 Windows 平台跳过这一步 (现在录制器只支持 Win, 但保持代码可移植).

## B2 — ffmpeg 关闭异常写进 manifest

`_stop_ffmpeg()` 当前的 `except Exception: pass` (大约 line 2450) 改成: 设 `self._ffmpeg_clean_close = False` (`__init__` 里默认 True); `proc.wait` 超时走 terminate/kill 路径时也设 False. 然后在 `_run_one_session()` 里写 manifest 的地方 (大约 line 2280–2300, 找 `meta = {...}`) 加字段 `"mp4_clean_close": getattr(self, "_ffmpeg_clean_close", True)`.

## 约束
- 不动 UI 主体, 只加确认框 + manifest 字段
- 不动录制 pipeline 主线程 / `_watch_loop`
- 不引入新依赖 (`tkinter.messagebox` 已经在 stdlib)
- 现有 trace 风格保持: `_trace(f"on_close: kill MC requested, taskkill exit={rc}")`

## 验收
- [ ] 关窗弹确认框, 是 → MC 被杀, 否 → MC 留着
- [ ] manifest.json 多 `mp4_clean_close: true|false` 字段
- [ ] `python3 -c "import ast; ast.parse(open('bin/recorder_consumer_lite.py').read())"` 通过
- [ ] 不破坏现有的 `_on_close → _upload_log_remote` 流
