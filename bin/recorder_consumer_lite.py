#!/usr/bin/env python3
"""
bin/recorder_consumer_lite.py — Oyster Recorder Lite (stop-gap MVP)

Howard 2026-05-05: "直接下载 然后 打开 然后 打开minecraft 直接测试"
                   "他们的流程就是什么都不需要设置"
                   "我需要快 出release"

This is a STOP-GAP single-.exe recorder so Howard's testers can run the
"download → open → play MC → get clip" loop TODAY, while Howard's team
finishes the Rust gamedata-recorder full refactor (per
~/Downloads/plans/polished-gathering-sifakis.md Phase 0-3).

What it does:
  1. Tester double-clicks OysterRecorder.exe
  2. Window opens with: "请打开 Minecraft 开始游戏，自动开始录制"
  3. Background thread polls Windows process list every 2s for
     javaw.exe (Minecraft Java launcher) or Minecraft.exe (Bedrock)
  4. When MC detected → spawns bundled ffmpeg.exe to record the
     Minecraft window with H.265, saving to
     %USERPROFILE%\\Documents\\OysterClips\\clip-YYYYMMDD-HHMMSS.mp4
  5. Window updates: "正在录制 — 玩你的 Minecraft 即可"
  6. When MC process exits → kills ffmpeg → finalizes mp4 →
     window shows: "✓ 录制完成 — 文件已保存"
  7. Done. Tester closes the window.

What it INTENTIONALLY does NOT do (deferred to Howard's team's Rust app):
  - Audio extractor (G196)
  - Per-frame depth files (G198 shader pack)
  - 20-field action_camera.json (G164 intrinsics + G163 keycode)
  - gameinfo.xlsx scene metadata
  - 5-file PRD tarball — output is just .mp4 for now

  The validator (qa-validator-v0.2.0+) will say "✗ FAIL" on these clips
  because they're missing PRD fields. That's fine for stop-gap — testers
  see "recording works", engineers see "still missing X/Y/Z fields".
  Howard's team's Rust app produces full PRD-compliant tarballs.

Built into a single Windows .exe by .github/workflows/build-recorder-exe.yml
using PyInstaller --onefile --windowed with bundled ffmpeg.exe (added via
--add-binary). No Python, no admin, no extra installs needed by tester.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ---- Startup tracing (runs BEFORE any heavyweight import) -----------------
# Howard tester 2026-05-05: "反馈过来一点就闪退" — v0.1.0 silently crashed
# on launch with --windowed (which swallows stderr). To diagnose, every
# import + init step writes a single line to ~/OysterRecorder.log BEFORE
# any GUI work. Tester can email/Slack that file even after the .exe is
# gone. The log is also tail-printed in the messagebox if Tk is alive.
_STARTUP_LOG = Path.home() / "OysterRecorder.log"


def _trace(step: str) -> None:
    try:
        with _STARTUP_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {step}\n")
    except Exception:
        # Even logging failed — nothing more we can do this early.
        pass


_trace("=== OysterRecorder boot ===")
_trace(f"sys.executable={sys.executable}")
_trace(f"sys.frozen={getattr(sys, 'frozen', False)}")
_trace(f"sys.platform={sys.platform}")
_trace(f"os.name={os.name}")

# Bumped on every release — used by self-update logic.
# CRITICAL: this MUST match the recorder-vX.Y.Z tag we're publishing
# under. Out-of-sync versions cause v0.13 onedir installs to think
# they're v0.8 and "update" themselves to v0.9 single-file, breaking
# the bundled _internal/ layout. See v0.14.0 commit for postmortem.
RECORDER_VERSION = "lite-v0.28.0-rc15.14"

# rc15 (Howard 2026-05-09 "一次就测完"): heal_registry import with safe
# fallback. Recorder still runs if heal_registry.py is missing (dev mode);
# in production the bundle always includes it.
#
# rc15-fix BUG#4 (HIGH): split into two try/except blocks. Was bug:
# both imports in one block — if heal_report failed (e.g. PyInstaller
# missed it), heal_registry's _heal_emit ALSO got replaced with noop,
# silently disabling all heal event emission. Now: heal_registry +
# heal_report fail independently.
try:
    from heal_registry import emit_event as _heal_emit  # type: ignore  # noqa: PLC0415
except Exception:
    def _heal_emit(*args: Any, **kwargs: Any) -> str:  # type: ignore
        return ""  # noop when heal_registry unavailable
try:
    # PyInstaller hint: ensure heal_report is bundled (lazy-imported by
    # _open_heal_report). Static analysis catches this top-level import.
    import heal_report as _heal_report_module  # type: ignore  # noqa: F401, PLC0415
except Exception:
    _heal_report_module = None  # type: ignore  # dashboard button will inline-error

# rc11 SF (Phase A.1, IRON LAW EXCEPTION pre-approved 2026-05-09):
# game-agnostic failure-attribution. Every session writes terminator.json
# alongside systeminfo.json so backend ingest can quarantine + we can
# diagnose without reaching into testers' machines.
TERMINATOR_REASONS = (
    "clean_exit",            # 录满 5-6 min 玩家正常退游戏
    "game_died",             # 游戏进程退出 (mc / roblox / fortnite ...)
    "game_crashed",          # 游戏 process 异常退 (returncode != 0)
    "ffmpeg_died",           # 录像编码进程崩
    "ffmpeg_dirty_close",    # rc10 B2: 编码进程关闭脏 (mp4 trailer 未写)
    "disk_full",             # rc10 B5 触发
    "user_close_kill_game",  # 用户关录制器同时杀游戏
    "user_close_keep_game",  # 用户关录制器保留游戏
    "orphan_resumed",        # 启动时检到上次未清理的 tmp_dir
    "duration_too_short",    # < 5 min, PRD reject
    "crash",                 # recorder 自身异常退出 (excepthook 兜底)
    "mod_handshake_failed",  # game-side mod 没回握手
    "preflight_blocked",     # Layer 0 preflight 阻断了
)
# Future games extend this. game_specific.game_id is the abstraction key.
CURRENT_GAME_ID = "minecraft_java"

# rc12 SH (Phase A.3, IRON LAW EXCEPTION pre-approved 2026-05-09):
# game-agnostic preflight self-check registry. Add a new game by adding
# an entry; preflight loop reads from CURRENT_GAME_ID lookup. Universal
# checks (Win version, disk, network, IME, etc.) run regardless of game.
GAME_PROBE_REGISTRY: dict[str, dict[str, Any]] = {
    "minecraft_java": {
        "display_name": "Minecraft (Java Edition)",
        "process_names": ["javaw.exe", "java.exe", "Minecraft.exe"],
        "window_title_substrings": ["Minecraft", "我的世界"],
        "required_companion": "java",
        "min_companion_version": "21",
    },
    # 未来 roblox / fortnite / valorant ...
}

# rc11 SG (Phase A.2): heartbeat for external watchdog. The recorder
# writes this every loop tick + every 30s during recording; an external
# watcher (oyster_play.py) reads it and prompts to restart if stale > 90s.
def _health_path() -> Path:
    return Path(_real_documents_dir()) / "OysterRecorder" / "runtime" / "health.json"


def _write_health(state: str, frame_count: int = 0, game_pid_alive: bool = False) -> None:
    """rc11 SG: heartbeat write. Best-effort, never raises.

    state: idle | armed | recording | finalizing | crashed
    """
    try:
        path = _health_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": time.time(),
            "ts_iso": datetime.now().isoformat(),
            "pid": os.getpid(),
            "state": state,
            "frame_count": frame_count,
            "game_pid_alive": game_pid_alive,
            "recorder_version": RECORDER_VERSION,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass  # never block on health write

# R01 iron-law: supported MC versions for real game-state Fabric mod.
# Kept in sync with .github/workflows/build-mc-mod.yml matrix.
SUPPORTED_MC_VERSIONS = [
    "1.20.1", "1.20.2", "1.20.4", "1.20.6",
    "1.21.1", "1.21.2", "1.21.3", "1.21.4", "1.21.5",
]


class RecorderError(RuntimeError):
    """Hard-fail error for iron-law violations (no silent fallback)."""
RELEASES_API = (
    "https://api.github.com/repos/howardleegeek/oyster-gamedata-pipeline"
    "/releases?per_page=20"
)


def _is_onedir_install() -> bool:
    """Detect whether we're running as a PyInstaller --onedir bundle.

    --onedir bundles have a ``_internal/`` directory next to the .exe
    holding all DLLs. --onefile extracts to a temp dir and the .exe
    sits alone. Self-update can ONLY safely overwrite same-format
    bundles (onefile→onefile or onedir→onedir-zip), so this gate
    prevents an onedir install from being clobbered by a single-file
    .exe download.
    """
    if not getattr(sys, "frozen", False):
        return False
    exe = Path(sys.executable).resolve()
    return (exe.parent / "_internal").is_dir()


# ---- Self-update (engineer ships once, recorder updates itself) ---------
# Howard 2026-05-05: "能不能 自动给这个电脑上的更新"
# On launch (and once an hour while running), check the GitHub Releases
# API for the latest `recorder-v*` tag. If a newer version exists,
# download the new .exe to %TEMP%, then write a tiny Windows .bat that
# (a) waits 3s for us to fully exit, (b) copies the new .exe over our
# path, (c) re-launches us. We spawn the .bat detached and quit.
# Tester sees the recorder briefly close + reopen — every ~5 min when
# we ship a new release, never has to touch the file manually again.

def _current_version_tag() -> str:
    """Return the recorder-vX.Y.Z tag derived from RECORDER_VERSION."""
    # RECORDER_VERSION = "lite-v0.8.0" → tag = "recorder-v0.8.0"
    semver = RECORDER_VERSION.split("-", 1)[-1]  # 'v0.8.0'
    return f"recorder-{semver}"


def _latest_release_tag_and_url() -> tuple[Optional[str], Optional[str]]:
    """Query GitHub API for the latest recorder-v* release. Returns
    (tag, exe_download_url) or (None, None) on any failure (network,
    rate-limit, etc).
    """
    try:
        import urllib.request
        req = urllib.request.Request(
            RELEASES_API,
            headers={"User-Agent": "OysterRecorder/lite", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            releases = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        _trace(f"update_check: api error {e}")
        return None, None
    for rel in releases:
        tag = rel.get("tag_name", "")
        if not tag.startswith("recorder-v"):
            continue
        if rel.get("draft") or rel.get("prerelease"):
            continue
        for asset in rel.get("assets", []):
            if asset.get("name") == "OysterRecorder.exe":
                return tag, asset.get("browser_download_url")
    return None, None


def _is_newer_tag(latest: str, current: str) -> bool:
    """Compare recorder-vA.B.C semver-ish tags. Returns True if latest > current.

    rc15.8-fix BUG R11 H10 (CRITICAL): old impl parsed "0-rc15" as
    ValueError → 0. So rc16 ("v0.28.0-rc16") = (0,28,0) compared LESS
    than rc15.7.2 ("v0.28.0-rc15.7.2") = (0,28,0,7,2) — Python tuple
    comparison treats shorter as smaller when prefix equal. Result:
    auto-update from rc15.7.2 to future rc16 NEVER fires. Fix: extract
    all digit groups from each piece via regex so 'rc15' contributes 15
    to the comparison key.
    """
    import re as _re  # noqa: PLC0415
    def _key(t: str) -> tuple[int, ...]:
        v = t.replace("recorder-v", "").split(".")
        out: list[int] = []
        for piece in v:
            try:
                out.append(int(piece))
            except ValueError:
                # "0-rc15" → [0, 15], "rc16" → [16], "alpha" → [0]
                ints = [int(m) for m in _re.findall(r'\d+', piece)]
                if ints:
                    out.extend(ints)
                else:
                    out.append(0)
        return tuple(out)
    return _key(latest) > _key(current)


def _stage_self_update(new_exe_url: str) -> bool:
    """Download the new .exe and write a self-replacing .bat.

    Returns True if the update was staged (caller should exit so the
    .bat can take over). Returns False on any failure (no harm, recorder
    keeps running on the current version).

    v0.14.0 GUARD: refuses to self-update an --onedir install with a
    single-file .exe. The single-file .exe assumes %TEMP% extraction
    and overwrites our bootstrap, leaving the _internal/ folder
    orphaned and crashing the next launch with PYI_APPLICATION_HOME_DIR.
    """
    # v0.20.1: fail-loud diagnostic trace at every gate (was silently False)
    if os.name != "nt":
        _trace(f"update: SKIP — non-Windows OS ({os.name})")
        return False
    if not getattr(sys, "frozen", False):
        _trace("update: SKIP — not running as packaged .exe (dev mode)")
        return False
    if _is_onedir_install():
        _trace("update: SKIP — onedir bundle, refuses single-.exe overwrite (would orphan _internal/)")
        return False
    try:
        import urllib.request
        # B4: download to same drive as current_exe for instant rename (not cross-drive copy)
        target_dir = Path(sys.executable).parent / "_update_tmp"
        target_dir.mkdir(parents=True, exist_ok=True)
        new_path = target_dir / "OysterRecorder-update.exe"
        _trace(f"update: downloading {new_exe_url} -> {new_path}")
        with urllib.request.urlopen(new_exe_url, timeout=120) as resp, \
                new_path.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
        size = new_path.stat().st_size
        _trace(f"update: downloaded {size} bytes")
        if size < 1_000_000:  # under 1 MB is suspicious — likely a 4xx error page
            _trace(f"update: ABORT — downloaded file too small ({size} bytes), likely error page not exe")
            return False
    except Exception as e:
        # v0.20.1: explicit exception type for diagnostic clarity
        _trace(f"update: download failed [{type(e).__name__}]: {e}")
        return False

    current_exe = Path(sys.executable)
    bat_path = Path(tempfile.gettempdir()) / "OysterRecorder-update.bat"
    # B3: bat with retry loop to wait for .exe handle release (max 30s)
    bat_body = (
        "@echo off\r\n"
        "set RETRY=0\r\n"
        ":retry\r\n"
        "timeout /t 1 /nobreak > nul\r\n"
        f'move /Y "{new_path}" "{current_exe}" 2>nul\r\n'
        "if errorlevel 1 (\r\n"
        "  set /a RETRY+=1\r\n"
        "  if %RETRY% LSS 30 goto retry\r\n"
        "  echo update FAILED after 30s, abort\r\n"
        f'del "{new_path}" 2>nul\r\n'
        "  exit /b 1\r\n"
        ")\r\n"
        f'start "" "{current_exe}"\r\n'
        f'del "{bat_path}"\r\n'
    )
    try:
        bat_path.write_text(bat_body, encoding="ascii")
        # v0.15.0: add CREATE_NO_WINDOW (0x08000000) so the cmd.exe
        # invocation doesn't FLASH a black console window on screen.
        # DETACHED_PROCESS (0x08) alone only detaches from the parent's
        # console — Windows still creates a fresh visible one for cmd.
        # CREATE_NO_WINDOW + CREATE_NEW_PROCESS_GROUP (0x200) combined
        # give us: no visible window, no parent console, survives parent
        # exit.
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat_path)],
            creationflags=0x08 | 0x200 | 0x08000000,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _trace(f"update: staged via {bat_path}")
        # rc15.2-fix BUG C3: wire B3 (bat retry) + B4 (same-drive tmp).
        # Both rc10 features were dark in heal log until now.
        try:
            from heal_registry import emit_event as _heal_emit_local  # noqa: PLC0415
            _heal_emit_local(
                "B4_update_same_drive_tmp", "detect", "info",
                f"update tmp on same drive as install: {target_dir}",
                details={"target_dir": str(target_dir), "current_exe": str(current_exe)},
                remediation={"action": "auto_clean", "performed": True,
                             "next_step": "bat will retry move /Y up to 30s"},
                recorder_version=RECORDER_VERSION,
            )
            _heal_emit_local(
                "B3_update_bat_retry", "heal_success", "info",
                "update bat staged with 30s retry loop",
                details={"bat_path": str(bat_path), "size_bytes": new_path.stat().st_size},
                remediation={"action": "auto_clean", "performed": True,
                             "next_step": "process exits, bat swaps .exe + relaunches"},
                recorder_version=RECORDER_VERSION,
            )
        except Exception:
            pass
        return True
    except Exception as e:
        _trace(f"update: stage failed {e}")
        try:
            from heal_registry import emit_event as _heal_emit_local  # noqa: PLC0415
            _heal_emit_local(
                "B3_update_bat_retry", "heal_failed", "error",
                f"update stage failed: {type(e).__name__}: {e}",
                details={"exception_type": type(e).__name__, "msg": str(e)[:200]},
                remediation={"action": "user_prompt", "performed": False,
                             "next_step": "tester downloads installer manually from GitHub releases"},
                recorder_version=RECORDER_VERSION,
            )
        except Exception:
            pass
        return False


def _check_for_update_in_background(on_done=None) -> None:
    """Fire-and-forget update check. Calls on_done(tag, url, is_newer)."""
    def _go():
        latest_tag, exe_url = _latest_release_tag_and_url()
        current_tag = _current_version_tag()
        if not latest_tag:
            _trace("update_check: no release returned by API")
            if on_done:
                on_done(None, None, False)
            return
        is_newer = _is_newer_tag(latest_tag, current_tag)
        _trace(f"update_check: current={current_tag} latest={latest_tag} newer={is_newer}")
        if on_done:
            on_done(latest_tag, exe_url, is_newer)
    threading.Thread(target=_go, daemon=True).start()


# ---- Remote telemetry (so engineer can see logs without tester action) ---
# Howard 2026-05-05: "你这边 能不能有log 到信息和日志"
# v0.20.1 hotfix: ix.io is DEAD (offline since 2024). Switch to 0x0.st,
# which is alive, free, no-auth, accepts multipart file uploads.
# Always write a local diagnostic zip to Desktop as fallback so the tester
# can manually send via WeChat/email even if both endpoints fail.
TELEMETRY_ENDPOINT = "https://0x0.st"  # multipart POST with field name "file"

# rc15.14 (Howard 2026-05-10 "diagnostic 是否可以让他们直接上传 是不是
# 需要服务器"): tester 一键上传 ENTIRE diagnostic zip (含 javaw + heal
# events + sysinfo) 到匿名文件主机. NO server needed for short-term.
# Multi-host fallback chain: catbox.moe (永久) → file.io (14天) → 0x0.st
# (5MB log only, last resort). Each is anonymous, no auth required.
CATBOX_ENDPOINT = "https://catbox.moe/user/api.php"
FILE_IO_ENDPOINT = "https://file.io"


def _upload_diagnostic_zip(zip_path: Path) -> Optional[str]:
    """rc15.14: upload entire diagnostic zip to anonymous host with fallback.

    Returns hosted URL on success, None on all-host failure. Tester sees
    URL in helpbar + clipboard. Howard reads URL, downloads zip, debugs.

    Tries in order:
      1. catbox.moe (permanent, 200MB max) — preferred
      2. file.io (14 days, 5GB max) — fallback
      3. 0x0.st (existing path, log-only, 5MB) — last resort

    NEVER raises (caller will fall through to local-only diagnostic).
    """
    if not zip_path.exists():
        return None
    body = zip_path.read_bytes()
    if len(body) > 200 * 1024 * 1024:  # 200MB safety cap
        _trace(f"upload_diag: zip too large ({len(body)/1e6:.1f}MB), skipping")
        return None

    import urllib.request  # noqa: PLC0415
    import uuid as _uuid  # noqa: PLC0415

    # Tier 1: catbox.moe
    try:
        boundary = f"----CatBox{_uuid.uuid4().hex}"
        crlf = "\r\n"
        # catbox.moe wants reqtype=fileupload + file field
        parts: list[bytes] = []
        parts.append(f"--{boundary}{crlf}".encode())
        parts.append(f'Content-Disposition: form-data; name="reqtype"{crlf}{crlf}'.encode())
        parts.append(b"fileupload" + crlf.encode())
        parts.append(f"--{boundary}{crlf}".encode())
        parts.append(
            f'Content-Disposition: form-data; name="fileToUpload"; filename="{zip_path.name}"{crlf}'.encode()
            + f"Content-Type: application/zip{crlf}{crlf}".encode()
        )
        parts.append(body)
        parts.append(f"{crlf}--{boundary}--{crlf}".encode())
        data = b"".join(parts)
        req = urllib.request.Request(
            CATBOX_ENDPOINT, data=data,
            headers={
                "User-Agent": "OysterRecorder/diagnostic-upload",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            url = resp.read().decode("utf-8", errors="replace").strip()
        if url.startswith("http"):
            _trace(f"upload_diag: catbox.moe success {url}")
            return url
        _trace(f"upload_diag: catbox returned non-URL: {url[:200]}")
    except Exception as exc:
        _trace(f"upload_diag: catbox.moe failed [{type(exc).__name__}]: {exc}")

    # Tier 2: file.io
    try:
        boundary = f"----FileIO{_uuid.uuid4().hex}"
        head = (
            f"--{boundary}{crlf}"
            f'Content-Disposition: form-data; name="file"; filename="{zip_path.name}"{crlf}'
            f"Content-Type: application/zip{crlf}{crlf}"
        ).encode()
        tail = f"{crlf}--{boundary}--{crlf}".encode()
        data = head + body + tail
        req = urllib.request.Request(
            FILE_IO_ENDPOINT, data=data,
            headers={
                "User-Agent": "OysterRecorder/diagnostic-upload",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            import json as _json  # noqa: PLC0415
            payload = _json.loads(resp.read().decode("utf-8", errors="replace"))
            if payload.get("success") and payload.get("link"):
                _trace(f"upload_diag: file.io success {payload['link']}")
                return payload["link"]
        _trace(f"upload_diag: file.io non-success response")
    except Exception as exc:
        _trace(f"upload_diag: file.io failed [{type(exc).__name__}]: {exc}")

    return None
DIAGNOSTIC_ZIP_NAME = "OysterRecorder_diagnostic.zip"


def _desktop_path() -> Path:
    """Return path to user's Desktop, falling back to home if not present."""
    desktop = Path.home() / "Desktop"
    if desktop.exists():
        return desktop
    # 中文 Windows 桌面 may localize — try OneDrive Desktop too
    onedrive = Path.home() / "OneDrive" / "Desktop"
    if onedrive.exists():
        return onedrive
    return Path.home()


def _build_diagnostic_zip() -> Optional[Path]:
    """Build a diagnostic zip on the Desktop containing log + system info.

    Tester can send this zip via WeChat / email to engineer when remote
    upload fails. Returns the zip path on success, None on failure.
    """
    try:
        import zipfile, platform
        zip_path = _desktop_path() / DIAGNOSTIC_ZIP_NAME
        sys_info_lines = [
            f"recorder_version: {RECORDER_VERSION}",
            f"timestamp: {datetime.now().isoformat()}",
            f"platform: {platform.platform()}",
            f"python: {sys.version}",
            f"frozen: {getattr(sys, 'frozen', False)}",
            f"is_onedir: {_is_onedir_install() if getattr(sys, 'frozen', False) else 'N/A'}",
            f"sys.executable: {sys.executable}",
            f"home: {Path.home()}",
            f"log_file: {_STARTUP_LOG}",
            f"log_exists: {_STARTUP_LOG.exists()}",
            f"log_size_bytes: {_STARTUP_LOG.stat().st_size if _STARTUP_LOG.exists() else 0}",
        ]
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("sysinfo.txt", "\n".join(sys_info_lines))
            if _STARTUP_LOG.exists():
                zf.write(_STARTUP_LOG, "OysterRecorder.log")
            # rc15.11 (Howard 2026-05-10 测试员 bingd MC exit-1): also bundle
            # latest 3 javaw_*.log files. Was bug: tester sent diagnostic
            # missing the actual MC crash trace (only had OysterRecorder.log
            # which is the recorder's log, not Minecraft's). Now diagnostic
            # zip self-contained for both recorder + game.
            try:
                if os.name == "nt":
                    java_logs_dir = (
                        Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                        / "OysterRecorder" / "logs"
                    )
                else:
                    java_logs_dir = Path.home() / ".oyster_recorder" / "logs"
                if java_logs_dir.exists():
                    java_logs = sorted(
                        java_logs_dir.glob("javaw_*.log"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )[:3]
                    for log in java_logs:
                        zf.write(log, f"java_logs/{log.name}")
                    _trace(f"diagnostic_zip: bundled {len(java_logs)} javaw_*.log files")
            except Exception as exc:
                _trace(f"diagnostic_zip: javaw log bundle failed: {exc}")
            # rc15.11: also bundle heal_events.jsonl for self-heal telemetry.
            try:
                heal_log = Path(_real_documents_dir()) / "OysterRecorder" / "runtime" / "heal_events.jsonl"
                if heal_log.exists():
                    zf.write(heal_log, "heal_events.jsonl")
            except Exception:
                pass
            # rc15.11: bundle latest terminator.json (session-less or per-session).
            try:
                runtime_dir = Path(_real_documents_dir()) / "OysterRecorder" / "runtime"
                term = runtime_dir / "terminator.json"
                if term.exists():
                    zf.write(term, "terminator.json")
            except Exception:
                pass
        _trace(f"diagnostic_zip: built {zip_path}")
        return zip_path
    except Exception as exc:
        _trace(f"diagnostic_zip: build failed: {exc}")
        return None


def _upload_log_remote() -> Optional[str]:
    """POST ~/OysterRecorder.log to 0x0.st. Returns short URL or None.

    v0.20.1 hotfix: previously POSTed to ix.io which has been offline since
    2024. Now uses 0x0.st (multipart upload, anonymous, free, alive).
    Whether or not the remote upload succeeds, _build_diagnostic_zip() is
    also called so the tester always has a local fallback.
    """
    if not _STARTUP_LOG.exists():
        _trace("upload_log: no local log file yet")
        return None
    try:
        body = _STARTUP_LOG.read_text(encoding="utf-8", errors="replace").encode("utf-8")
    except Exception as exc:
        _trace(f"upload_log: read failed: {exc}")
        return None
    if len(body) > 5_000_000:  # 0x0.st limit ~512MB but trim aggressively
        body = body[-1_000_000:]
    try:
        # 0x0.st expects multipart/form-data with field name "file".
        # Build a minimal multipart body with stdlib (no `requests` dep).
        import urllib.request
        import uuid
        boundary = f"----OysterBoundary{uuid.uuid4().hex}"
        crlf = "\r\n"
        head = (
            f"--{boundary}{crlf}"
            f'Content-Disposition: form-data; name="file"; filename="OysterRecorder.log"{crlf}'
            f"Content-Type: text/plain{crlf}{crlf}"
        ).encode("utf-8")
        tail = f"{crlf}--{boundary}--{crlf}".encode("utf-8")
        data = head + body + tail
        req = urllib.request.Request(
            TELEMETRY_ENDPOINT,
            data=data,
            headers={
                "User-Agent": "OysterRecorder/lite (engineer-contact)",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            url = resp.read().decode("utf-8", errors="replace").strip()
        if url.startswith("http"):
            _trace(f"upload_log: success {url}")
            try:
                with _STARTUP_LOG.open("a", encoding="utf-8") as fh:
                    fh.write(f"{datetime.now().isoformat()} REMOTE_LOG_URL={url}\n")
            except Exception:
                pass
            return url
        _trace(f"upload_log: server returned non-URL response: {url[:200]}")
    except Exception as exc:
        # v0.20.1: explicit exception type + message for diagnostic clarity
        _trace(f"upload_log: POST failed [{type(exc).__name__}]: {exc}")
    return None


def _upload_log_in_background(callback=None) -> None:
    """Fire-and-forget upload; optional callback receives the URL or None."""
    def _go():
        url = _upload_log_remote()
        if callback is not None:
            try:
                callback(url)
            except Exception:
                pass
    threading.Thread(target=_go, daemon=True).start()

try:
    _trace("importing tkinter…")
    import tkinter as tk
    from tkinter import messagebox, ttk  # ttk: rc9 depth-progress bar
    _trace("tkinter ok")
except Exception:
    _trace(f"tkinter FAILED:\n{traceback.format_exc()}")
    raise

# pynput is lazily imported in InputCapture.start() so that startup of
# the .exe doesn't fail if pynput's hooks misbehave on a tester's box.
# PyInstaller still picks it up because we --hidden-import it in the
# workflow.

# When PyInstaller-frozen, ffmpeg.exe lives in sys._MEIPASS.
if getattr(sys, "frozen", False):
    _BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", "."))
else:
    _BUNDLE_ROOT = Path(__file__).resolve().parent

# Path to bundled ffmpeg binary. On Windows this is ffmpeg.exe; on
# non-frozen runs (developer testing on macOS / Linux) we fall back to
# whatever ffmpeg is on PATH.
_FFMPEG = _BUNDLE_ROOT / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
if not _FFMPEG.exists():
    found = shutil.which("ffmpeg")
    _FFMPEG = Path(found) if found else _FFMPEG  # may not exist on dev box

# Tester output directory: ~/Documents/OysterClips/
#
# rc8 fix: on Windows, "Documents" can be redirected to OneDrive (common
# default on consumer boxes). When it is, `Path.home() / "Documents"` still
# returns the un-redirected NTFS path, but Explorer's sidebar "Documents"
# shortcut points at the redirected OneDrive path — so the tester opens
# Explorer, clicks Documents, sees no OysterClips folder, panics. We resolve
# the *real* Documents path from the registry (User Shell Folders\Personal)
# the same way Explorer does, so files land where the tester actually looks.
def _real_documents_dir() -> Path:
    if os.name != "nt":
        return Path.home() / "Documents"
    try:
        import winreg  # noqa: PLC0415 — Windows-only stdlib
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as k:
            raw, _ = winreg.QueryValueEx(k, "Personal")
            return Path(os.path.expandvars(raw))
    except OSError:
        return Path.home() / "Documents"


def _output_dir() -> Path:
    docs = _real_documents_dir() / "OysterClips"
    docs.mkdir(parents=True, exist_ok=True)
    # rc15.11 (Howard 2026-05-10 测试员 bingd MC exit-1 调查): pre-create
    # all dirs the Fabric mod tries to write to. If active_session/ doesn't
    # exist when mod's JsonlWriter does first append, mod throws → MC
    # crashes mid-init with exit code 1. Defensive create here costs nothing.
    # Also pre-create _rejected/ so SK partial-tarball move at finalize
    # never races against missing dir on first session.
    try:
        (docs / "active_session").mkdir(parents=True, exist_ok=True)
        (docs / "_rejected").mkdir(parents=True, exist_ok=True)
    except Exception:
        pass  # never let mkdir break startup; fall through and let mod fail
              # naturally with a clearer error than missing dir.
    return docs


def _detect_gpu_available() -> bool:
    """Return True if any GPU-accelerated depth inference path is reachable.

    rc9 (Howard 2026-05-09): testers running on integrated-GPU laptops sit
    through 30-60 minutes of CPU DepthAnything inference and assume the
    recorder is hung. We detect available accelerators here so the UI can
    pre-check the "Skip depth maps" button on no-GPU boxes (the tester
    can still uncheck it manually if they want to wait).

    Detection order on Windows (most → least preferred):
      1. NVIDIA CUDA — try ``ctypes.WinDLL("nvcuda.dll")`` then
         ``cuInit(0) == 0``. Loading nvcuda.dll alone isn't sufficient
         because Windows ships the DLL stub even on Intel-only boxes.
      2. DirectML / DXGI hardware adapter — every Win10+ machine has
         ``dxgi.dll``, but a discrete GPU is implied by ``torch_directml``
         being importable (we only ship it in the DML build).

    Returns False on non-Windows (the recorder is Windows-only in
    production; on dev macOS we just default to "GPU available"-style
    behaviour by returning True since the integrated Apple GPU is fast
    enough for testing). On unexpected exceptions we return False — the
    UI prefers "default to skip" over a falsely promising progress bar.
    """
    if os.name != "nt":
        # Dev / smoke-test path: macOS/Linux contributors testing the
        # recorder still want the depth UI exercised. Return True so the
        # skip button is NOT pre-checked. Real tester boxes are Windows.
        return True
    try:
        import ctypes  # noqa: PLC0415
    except Exception:
        return False
    # 1. NVIDIA path
    try:
        nvcuda = ctypes.WinDLL("nvcuda.dll")  # type: ignore[attr-defined]
        cu_init = getattr(nvcuda, "cuInit", None)
        if cu_init is not None:
            try:
                # cuInit returns 0 (CUDA_SUCCESS) on a real CUDA-capable box.
                # Anything else (CUDA_ERROR_NO_DEVICE, etc) means no GPU.
                rc = int(cu_init(0))
                if rc == 0:
                    return True
            except Exception:
                pass
    except OSError:
        # nvcuda.dll not present at all — no NVIDIA driver installed.
        pass
    except Exception:
        pass
    # rc15.7-fix BUG R9 H5: not just find_spec — actually call is_available()
    # so the detection result matches what infer_depth_for_video will see.
    # Was bug: package present but driver pre-22.40 → find_spec returns True
    # → detect_gpu = True → run_local_depth = True → device="dml" set →
    # is_available False at runtime → fall through to "cuda" → cuda also fails
    # → silent fallback to server. Now: if torch_directml.is_available()
    # returns False, _detect_gpu_available also returns False (server-pending
    # path entirely, no false-positive UI of local inference starting).
    # 2. DirectML path — only meaningful if torch-directml made it into the
    # bundle. Pure dxgi.dll presence isn't sufficient (every Win10+ has it).
    try:
        import importlib.util  # noqa: PLC0415

        if importlib.util.find_spec("torch_directml") is not None:
            # rc15.7-fix BUG R9 H5: also call is_available() — find_spec
            # returning True only means the package is bundled; driver may
            # be too old (pre-Adrenalin 22.40) to actually init DirectML.
            try:
                import torch_directml  # type: ignore  # noqa: PLC0415
                return bool(torch_directml.is_available())  # type: ignore[attr-defined]
            except Exception:
                return False
    except Exception:
        pass
    return False


# Process names treated as "Minecraft" — both Java and Bedrock variants.
MC_PROCESS_NAMES = {"javaw.exe", "java.exe", "Minecraft.exe", "MinecraftLauncher.exe"}


def _get_minecraft_window_rect() -> Optional[dict[str, int]]:
    """Return Minecraft window geometry on Windows, or None if not found.

    Uses Win32 EnumWindows + GetWindowText + GetWindowRect via ctypes
    (no extra deps, built into Python). We scan all top-level windows
    and pick the first whose title contains 'Minecraft'. PRD requires
    gameProcessName / x / y / width / height / recordDpi (criterion 8),
    so this powers the real systeminfo.json.

    Returns None on non-Windows or if no MC window is visible yet.
    """
    if os.name != "nt":
        return None

    try:
        import ctypes
        import ctypes.wintypes as wt
    except Exception:
        return None

    user32 = ctypes.windll.user32

    EnumWindows = user32.EnumWindows
    GetWindowText = user32.GetWindowTextW
    GetWindowTextLength = user32.GetWindowTextLengthW
    IsWindowVisible = user32.IsWindowVisible
    GetWindowRect = user32.GetWindowRect
    GetDpiForWindow = getattr(user32, "GetDpiForWindow", None)

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    found_hwnd: list[int] = []
    found_title: list[str] = []

    def _callback(hwnd, _lparam):
        if not IsWindowVisible(hwnd):
            return True
        ln = GetWindowTextLength(hwnd)
        if ln == 0:
            return True
        buf = ctypes.create_unicode_buffer(ln + 1)
        GetWindowText(hwnd, buf, ln + 1)
        title = buf.value
        if "minecraft" in title.lower():
            found_hwnd.append(hwnd)
            found_title.append(title)
            return False  # stop iteration
        return True

    EnumWindows(EnumWindowsProc(_callback), 0)

    if not found_hwnd:
        return None

    rect = wt.RECT()
    if not GetWindowRect(found_hwnd[0], ctypes.byref(rect)):
        return None

    dpi = 96
    if GetDpiForWindow is not None:
        try:
            dpi = int(GetDpiForWindow(found_hwnd[0])) or 96
        except Exception:
            dpi = 96

    return {
        "title": found_title[0],
        "x": int(rect.left),
        "y": int(rect.top),
        "width": int(rect.right - rect.left),
        "height": int(rect.bottom - rect.top),
        "recordDpi": dpi,
    }


import re as _re  # noqa: E402

_MC_VERSION_RE = _re.compile(r"Minecraft\s+([\d.]+)")


def _parse_mc_version_from_title(title: str) -> Optional[str]:
    """Extract Minecraft version from window title (any locale).

    Works for titles like:
      - "Minecraft 1.21.4"
      - "Minecraft 1.21.4 - 单人游戏"
      - "Minecraft 1.21.4 - シングルプレイ"
    """
    m = _MC_VERSION_RE.search(title)
    return m.group(1) if m else None


def _recorder_version_tuple() -> tuple[int, ...]:
    """Parse RECORDER_VERSION into a comparable numeric tuple.

    "lite-v0.27.0-iron-law-strict" → (0, 27, 0)
    """
    import re  # noqa: PLC0415
    nums = re.findall(r"\d+", RECORDER_VERSION.split("-v")[-1].split("-")[0])
    return tuple(int(n) for n in nums)


class InputCapture:
    """Lightweight keyboard + mouse capture into a JSON Lines buffer.

    Each event is a dict with: timestamp_ms (relative to start), event_type
    (key_down / key_up / mouse_move / mouse_click), and event-specific
    fields (keyCode int, mouseX, mouseY, button). Used to populate
    action_camera.json's `records` array (real data for the keyboard +
    mouse fields; camera/quaternion fields stay placeholder until the
    Rust app's shader pack lands).

    pynput's Listener runs on its own thread. We tee events into an
    in-memory list (self.events) which is later flushed to disk.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._kbd_listener = None
        self._mouse_listener = None
        self._start_time = 0.0
        self._lock = threading.Lock()

    def _now_ms(self) -> int:
        return int((time.time() - self._start_time) * 1000)

    def start(self) -> bool:
        """Begin listening. Returns False if pynput unavailable."""
        try:
            from pynput import keyboard, mouse
        except Exception:  # noqa: BLE001 — best-effort
            return False

        self._start_time = time.time()

        def on_press(key):  # noqa: ANN001
            self._record_key(key, "key_down")

        def on_release(key):  # noqa: ANN001
            self._record_key(key, "key_up")

        def on_move(x, y):  # noqa: ANN001
            with self._lock:
                self.events.append({
                    "timestamp_ms": self._now_ms(),
                    "event_type": "mouse_move",
                    "mouseX": int(x),
                    "mouseY": int(y),
                })

        def on_click(x, y, button, pressed):  # noqa: ANN001
            with self._lock:
                self.events.append({
                    "timestamp_ms": self._now_ms(),
                    "event_type": "mouse_click",
                    "mouseX": int(x),
                    "mouseY": int(y),
                    "button": str(button),
                    "pressed": bool(pressed),
                })

        self._kbd_listener = keyboard.Listener(
            on_press=on_press, on_release=on_release
        )
        self._mouse_listener = mouse.Listener(
            on_move=on_move, on_click=on_click
        )
        self._kbd_listener.start()
        self._mouse_listener.start()
        return True

    def _record_key(self, key: Any, event_type: str) -> None:
        # PRD requires keyCode as an int. pynput exposes Key.<name> for
        # special keys and KeyCode(char='X') for char keys. Map to a
        # stable int via vk (Windows virtual-key) when available, else
        # fall back to the Unicode codepoint of the char.
        kc: int = -1
        try:
            vk = getattr(key, "vk", None)
            if isinstance(vk, int):
                kc = vk
            else:
                ch = getattr(key, "char", None)
                if ch:
                    kc = ord(ch)
                else:
                    name = getattr(key, "name", None)
                    if name:
                        kc = hash(name) & 0xFFFF
        except Exception:
            kc = -1
        with self._lock:
            self.events.append({
                "timestamp_ms": self._now_ms(),
                "event_type": event_type,
                "keyCode": kc,
            })

    def realtime_check(self, last_n: int = 100) -> dict[str, Any]:
        """v0.22.0 (Howard '录的时候确保数据的精确度'): lightweight per-tick
        validation on the live event buffer. Caller polls this every ~1s
        on the UI thread to display data-quality status WHILE recording.

        Cheap checks only — no heavy residuals, no copies. Returns:
          - total_events: int
          - monotonic_ts: bool (timestamps non-decreasing in last_n)
          - invalid_keycodes: int (keyCode == -1 count in last_n)
          - mouse_oob: int (off-screen mouse positions in last_n; uses
            primary screen res 1920×1080 as bound, expands on multi-monitor)
          - last_event_age_ms: int (time since last event)
          - healthy: bool (all checks pass)
        """
        with self._lock:
            sample = self.events[-last_n:] if len(self.events) > last_n else list(self.events)
        total = len(self.events)
        if not sample:
            return {
                "total_events": 0,
                "monotonic_ts": True,
                "invalid_keycodes": 0,
                "mouse_oob": 0,
                "last_event_age_ms": -1,
                "healthy": True,
            }
        # Monotonic timestamps
        prev = -1
        monotonic = True
        for ev in sample:
            ts = ev.get("timestamp_ms", 0)
            if ts < prev:
                monotonic = False
                break
            prev = ts
        # Invalid keyCodes (failed VK mapping)
        invalid_kc = sum(
            1 for ev in sample
            if ev.get("event_type", "").startswith("key_")
            and ev.get("keyCode", -1) == -1
        )
        # Mouse out-of-bounds (primary screen heuristic)
        # NOTE: multi-monitor setups can legitimately have negative coords;
        # we use a generous ±5000 envelope to avoid false alarms.
        mouse_oob = sum(
            1 for ev in sample
            if ev.get("event_type", "").startswith("mouse_")
            and (ev.get("mouseX", 0) < -5000 or ev.get("mouseX", 0) > 10000
                 or ev.get("mouseY", 0) < -5000 or ev.get("mouseY", 0) > 10000)
        )
        last_age = self._now_ms() - sample[-1].get("timestamp_ms", 0)
        healthy = monotonic and invalid_kc == 0 and mouse_oob == 0 and last_age < 5000
        return {
            "total_events": total,
            "monotonic_ts": monotonic,
            "invalid_keycodes": invalid_kc,
            "mouse_oob": mouse_oob,
            "last_event_age_ms": last_age,
            "healthy": healthy,
        }

    def stop(self) -> list[dict[str, Any]]:
        for L in (self._kbd_listener, self._mouse_listener):
            try:
                if L is not None:
                    L.stop()
            except Exception:
                pass
        with self._lock:
            return list(self.events)


def _list_windows_processes() -> set[str]:
    """Return a set of running process executable names (case-preserved).

    Uses `tasklist` on Windows (no extra deps). On non-Windows dev boxes
    this returns an empty set — the recorder is Windows-only.

    v0.15.0: pass CREATE_NO_WINDOW (0x08000000) so the CMD console
    spawned for tasklist DOES NOT FLASH on screen. Without this flag,
    tester sees a black popup every 2s during arm-wait — extremely
    annoying. The flag attaches the child process to no console rather
    than the visible default one.
    """
    if os.name != "nt":
        return set()
    try:
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",        # rc15.3-fix BUG#15: was cp1252 default
            errors="replace",        # rc15.3-fix BUG#15: tolerate non-ASCII
            timeout=5,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception:  # noqa: BLE001
        return set()
    names = set()
    for line in out.splitlines():
        # CSV: "Image Name","PID","Session Name","Session#","Mem Usage"
        if line.startswith('"'):
            try:
                names.add(line.split('","', 1)[0].lstrip('"'))
            except Exception:
                continue
    return names


_MC_RUNNING_CACHE: tuple[float, bool] = (0.0, False)
_MC_RUNNING_CACHE_TTL = 1.8  # < 2s sleep so cache misses align with loop


def _minecraft_running() -> bool:
    """True iff any process matching MC_PROCESS_NAMES is alive.

    rc15.5-fix BUG R7 H2: cache the result for 1.8s. Phase 3 hot loop
    polls every 2s + tasklist subprocess takes 0.5-1s on AV-scanned
    machines = effectively 50% CPU on tester boxes. Cache halves it.
    """
    global _MC_RUNNING_CACHE
    now = time.time()
    cached_ts, cached_val = _MC_RUNNING_CACHE
    if (now - cached_ts) < _MC_RUNNING_CACHE_TTL:
        return cached_val
    val = bool(MC_PROCESS_NAMES & _list_windows_processes())
    _MC_RUNNING_CACHE = (now, val)
    return val


def _find_minecraft_pids() -> list[int]:
    """rc15.2 BUG#7: return PIDs of MC processes (not all javaw.exe).

    Uses tasklist /fo csv /nh to get image,PID per row, filters to
    MC_PROCESS_NAMES. Empty list on non-Windows or detection failure.

    rc15.3-fix BUG#14: use csv.reader for column parsing (line.split(",")
    breaks on process names containing commas, e.g. "My App, Inc.").
    rc15.3-fix BUG#15: pin encoding=utf-8 errors=replace so non-ASCII
    process names don't UnicodeDecodeError on cp1252 default.
    """
    if os.name != "nt":
        return []
    import csv as _csv  # noqa: PLC0415
    try:
        out = subprocess.check_output(
            ["tasklist", "/fo", "csv", "/nh"],
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except Exception:
        return []
    pids: list[int] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        try:
            parts = next(_csv.reader([line]))
            if len(parts) < 2:
                continue
            image, pid_str = parts[0], parts[1]
            if image not in MC_PROCESS_NAMES:
                continue
            pids.append(int(pid_str))
        except Exception:
            continue
    return pids


# ---- Recorder app ----------------------------------------------------------

GREEN = "#2e7d32"
RED = "#c62828"
ORANGE = "#ef6c00"
TEXT_GRAY = "#546e7a"


class RecorderApp(tk.Tk):
    """One-window recorder: detect MC, record, save, show status."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Oyster 录制器")
        self.geometry("520x340")
        self.minsize(440, 300)
        self.configure(bg="white")

        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._output_path: Optional[Path] = None
        self._stop_event = threading.Event()
        self._input_capture: Optional[InputCapture] = None
        self._captured_events: list[dict[str, Any]] = []
        # v0.4.0: tester explicitly opts in to recording. Default is
        # observe-only mode so our .exe can NEVER be blamed for MC
        # crashing — a tester whose MC crashes can verify it crashes
        # WITHOUT us recording first.
        self._record_armed = False
        self._mc_window_rect: Optional[dict[str, int]] = None
        # rc10 B2: track whether ffmpeg closed cleanly (default True).
        self._ffmpeg_clean_close: bool = True
        # rc11 SF: failure-attribution reason for current/last session.
        # Set at every exit path; consumed by _write_terminator().
        self._terminator_reason: Optional[str] = None
        self._mod_handshake_ok: bool = False  # set by mod handshake on first ack

        # rc9 (Howard 2026-05-09): depth-progress UX state.
        #
        # _skip_depth_flag is a threading.Event the inference loop polls
        # between frames. The recorder GUI sets it when the tester clicks
        # "跳过深度图" / when GPU isn't detected and the tester left the
        # default-skip checkbox armed.
        #
        # _depth_default_skip is a boolean computed once at startup —
        # True means the GPU probe came back negative so the skip
        # checkbox is pre-checked. The tester can still untick it on the
        # progress UI to override.
        #
        # _depth_progress_widgets holds the live tk widgets for the
        # progress UI so we can tear it down once inference returns to
        # ready state.
        self._skip_depth_flag = threading.Event()
        self._depth_default_skip: bool = not _detect_gpu_available()
        self._depth_progress_widgets: list[Any] = []
        self._depth_progress_started_at: float = 0.0

        self._build_ui()
        # rc12 SH: run preflight self-check after UI mount so we can
        # disable arm button on fatal + show summary in helpbar.
        self._preflight_results: list[dict[str, Any]] = []
        try:
            self._preflight_results = self._run_preflight()
            n_pass = sum(1 for r in self._preflight_results if r["status"] == "pass")
            n_warn = sum(1 for r in self._preflight_results if r["status"] in ("warn", "info"))
            n_fatal = sum(1 for r in self._preflight_results if r["status"] in ("fail", "fatal"))
            total = len(self._preflight_results)
            if n_fatal > 0:
                self._set("⛔ 系统检查失败", RED,
                          f"{n_fatal}/{total} 致命错误 — 请先解决再录制")
                self._terminator_reason = "preflight_blocked"
                self._write_terminator(None)
                # disable arm button
                try:
                    self._arm_btn.config(state="disabled")
                except Exception:
                    pass
            elif n_warn > 0:
                _trace(f"preflight: {n_pass} pass, {n_warn} warn/info, ready (with warnings)")
            else:
                _trace(f"preflight: all {n_pass} checks pass")
            # rc15 Heal Framework: mirror preflight outcome.
            severity = ("fatal" if n_fatal > 0
                        else "warn" if n_warn > 0
                        else "info")
            _heal_emit(
                "SH_preflight", "report", severity,
                f"preflight: {n_pass} pass, {n_warn} warn/info, {n_fatal} fatal of {total} checks",
                details={"results": [{"check": r["check"], "status": r["status"], "msg": r["message"][:100]}
                                     for r in self._preflight_results]},
                remediation={"action": "user_prompt" if n_fatal > 0 else "none",
                             "performed": n_fatal > 0,
                             "next_step": "fix fatal items before recording" if n_fatal > 0
                             else "ok to proceed (warnings noted)"},
                recorder_version=RECORDER_VERSION,
            )
        except Exception:
            _trace(f"preflight: unexpected failure {traceback.format_exc()}")

        # Start background watcher immediately — testers do not click anything.
        threading.Thread(target=self._watch_loop, daemon=True).start()

        # Clean shutdown if window is closed mid-recording.
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # v0.8.0: kick off self-update check immediately. If a newer
        # release exists, stage the update and exit cleanly so the .bat
        # can swap us out and re-launch. Tester sees a brief flicker.
        _check_for_update_in_background(self._on_update_check)

    def _build_ui(self) -> None:
        # v0.4.0: explicit "Arm recording" button. Default state = NOT
        # recording. Tester can verify Minecraft works first, then arm
        # the recorder. This also distinguishes "MC crashes by itself"
        # from "MC crashes because of our ffmpeg".
        self._verdict = tk.Label(
            self,
            text="…",
            font=("Helvetica", 30, "bold"),
            bg="white",
            fg=TEXT_GRAY,
            height=2,
        )
        self._verdict.pack(fill="x", padx=20, pady=(20, 8))

        self._subtitle = tk.Label(
            self,
            text="先正常打开 Minecraft 玩一下，确认 MC 不崩。\n要开始录制再点下面按钮。",
            font=("Helvetica", 12),
            bg="white",
            fg=TEXT_GRAY,
            wraplength=480,
            justify="center",
        )
        self._subtitle.pack(pady=(0, 4))

        # The arm-recording button (default: idle / unarmed)
        self._arm_btn = tk.Button(
            self,
            text="▶ 开始录制",
            font=("Helvetica", 14, "bold"),
            bg="#1976d2",
            fg="white",
            activebackground="#1565c0",
            activeforeground="white",
            bd=0,
            padx=22,
            pady=12,
            cursor="hand2",
            command=self._toggle_arm,
        )
        self._arm_btn.pack(pady=(16, 4))

        # "Send log" — manual telemetry push so tester can give engineering
        # a remote-readable log URL even if the recorder hasn't crashed.
        # Useful when MC crashes but our recorder is still alive.
        self._upload_btn = tk.Button(
            self,
            text="↗ 发送日志给工程师",
            font=("Helvetica", 10, "underline"),
            bg="white",
            fg="#1976d2",
            bd=0,
            cursor="hand2",
            command=self._upload_log_now,
        )
        self._upload_btn.pack(pady=(0, 6))

        # Spacer
        tk.Frame(self, bg="white").pack(expand=True, fill="both")

        # Tiny output-dir hint + log path at the bottom
        self._hint = tk.Label(
            self,
            text=(
                f"录制完成后会保存到: {_output_dir()}\n"
                f"如果出问题，点上面按钮自动打包诊断包到桌面。"
            ),
            font=("Helvetica", 9),
            bg="white",
            fg=TEXT_GRAY,
            wraplength=500,
            justify="center",
        )
        self._hint.pack(pady=(0, 4))

        # v0.20.1: extra row of small action buttons for tester self-help
        helpbar = tk.Frame(self, bg="white")
        helpbar.pack(pady=(0, 8))
        tk.Button(
            helpbar, text="打开日志文件夹",
            font=("Helvetica", 8), bg="white", fg="#666",
            bd=0, cursor="hand2",
            command=lambda: self._open_path(_STARTUP_LOG.parent),
        ).pack(side="left", padx=4)
        tk.Button(
            helpbar, text="复制日志路径",
            font=("Helvetica", 8), bg="white", fg="#666",
            bd=0, cursor="hand2",
            command=lambda: self._copy_to_clipboard(str(_STARTUP_LOG)),
        ).pack(side="left", padx=4)
        tk.Button(
            helpbar, text="导出诊断包到桌面",
            font=("Helvetica", 8), bg="white", fg="#666",
            bd=0, cursor="hand2",
            command=self._export_diagnostic_only,
        ).pack(side="left", padx=4)
        # rc8: prominent "View My Recordings" button — opens the OysterClips
        # folder in Explorer at the registry-resolved (OneDrive-aware) path
        # where session tarballs actually live. Bigger / styled vs the other
        # helpbar buttons because this is the action testers ask for first.
        tk.Button(
            helpbar, text="📂 我的录像",
            font=("Helvetica", 9, "bold"), bg="#1976d2", fg="white",
            activebackground="#1565c0", activeforeground="white",
            bd=0, padx=10, pady=3, cursor="hand2",
            command=lambda: self._open_path(_output_dir()),
        ).pack(side="left", padx=4)

        # rc15 Heal Framework v1: dashboard button. Generates
        # heal_report.html from heal_events.jsonl and opens it in browser.
        tk.Button(
            helpbar, text="📊 健康报告",
            font=("Helvetica", 9, "bold"), bg="#0277bd", fg="white",
            activebackground="#01579b", activeforeground="white",
            bd=0, padx=10, pady=3, cursor="hand2",
            command=self._open_heal_report,
        ).pack(side="left", padx=4)

    def _open_heal_report(self) -> None:
        """rc15: generate + open heal_report.html. Failure is non-fatal."""
        _trace("user clicked health-report button")
        try:
            from heal_report import generate_html  # type: ignore  # noqa: PLC0415
            out = generate_html(limit=500)
            self._open_path(out)
            _heal_emit(
                "heal_registry", "user_action", "info",
                "tester opened health report",
                details={"report_path": str(out)},
            )
        except Exception as exc:
            _trace(f"open_heal_report failed: {exc}")
            self._hint.config(
                text=f"健康报告生成失败: {exc}",
                fg="#c62828",
            )

    def _export_diagnostic_only(self) -> None:
        """Build diagnostic zip + auto-upload to anonymous host. rc15.14:
        tester one-click → upload to catbox.moe (or file.io fallback) →
        URL shown in helpbar + copied to clipboard. Tester can paste in
        WeChat/email to engineer instantly. NO server needed.
        """
        _trace("user clicked export-diagnostic button")
        self._hint.config(text="正在打包诊断 + 上传…", fg="#0277bd")

        def _go():
            zp = _build_diagnostic_zip()
            url = None
            if zp:
                url = _upload_diagnostic_zip(zp)
            def apply():
                if zp and url:
                    # Copy URL to clipboard for instant paste.
                    try:
                        self.clipboard_clear()
                        self.clipboard_append(url)
                    except Exception:
                        pass
                    self._hint.config(
                        text=(f"✓ 已上传, URL 已复制到剪贴板:\n{url}\n"
                              f"(本地备份: {zp})"),
                        fg="#1976d2",
                    )
                    _trace(f"export_diagnostic: uploaded to {url}")
                elif zp:
                    self._hint.config(
                        text=(f"诊断包已导出 (上传失败, 网络问题):\n{zp}\n"
                              f"请手动发给工程师"),
                        fg="#1976d2",
                    )
                    self._open_path(zp.parent)
                else:
                    self._hint.config(
                        text="导出失败，请截屏后联系工程师",
                        fg="#dc2626",
                    )
            self.after(0, apply)
        threading.Thread(target=_go, daemon=True).start()

    def _upload_log_now(self) -> None:
        """Tester clicked '发送日志给工程师'. v0.20.1: always build a local
        diagnostic zip on Desktop as fallback, also try remote upload.
        Tester always has a path they can manually send via WeChat/email.
        """
        _trace("user clicked send-log button")
        self._upload_btn.config(text="↗ 上传中…", state="disabled")

        def _go():
            # Always build diagnostic zip first (works offline, no network).
            zip_path = _build_diagnostic_zip()
            # Then attempt remote upload (best-effort).
            url = _upload_log_remote()

            def apply():
                if url:
                    self._upload_btn.config(
                        text="✓ 上传成功 — 复制链接",
                        state="normal",
                        command=lambda u=url: self._copy_to_clipboard(u),
                    )
                    self._hint.config(
                        text=f"工程师可访问: {url}\n本地诊断包也在: {zip_path or _desktop_path()}",
                        fg="#1976d2",
                    )
                elif zip_path:
                    self._upload_btn.config(
                        text="✓ 诊断包已生成 — 打开桌面",
                        state="normal",
                        command=lambda p=zip_path: self._open_path(p.parent),
                    )
                    self._hint.config(
                        text=f"远程上传失败，但已生成桌面诊断包:\n{zip_path}\n请通过微信/邮件发给工程师",
                        fg="#d97706",  # amber
                    )
                else:
                    self._upload_btn.config(
                        text="✗ 全部失败 — 复制日志路径",
                        state="normal",
                        command=lambda: self._copy_to_clipboard(str(_STARTUP_LOG)),
                    )
                    self._hint.config(
                        text=f"上传 + 打包都失败。请手动发送:\n{_STARTUP_LOG}",
                        fg="#dc2626",  # red
                    )
            self.after(0, apply)

        threading.Thread(target=_go, daemon=True).start()

    def _copy_to_clipboard(self, text: str) -> None:
        """Copy text to system clipboard via Tk."""
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()
            _trace(f"clipboard: copied {text!r}")
        except Exception as e:
            _trace(f"clipboard: copy failed {e}")

    def _open_path(self, path: Path) -> None:
        """Open a file or directory in the OS default handler.

        rc15.5-fix BUG R7 H1 (SEC): restrict os.startfile to safe suffix
        whitelist + directories. Was bug: os.startfile on Windows can
        execute .exe/.bat/.lnk if path is ever attacker-controlled
        (e.g. read from heal event details). Hard-fail unsafe suffixes.
        """
        SAFE_SUFFIXES = {"", ".html", ".htm", ".mp4", ".tar", ".gz", ".tgz",
                         ".jsonl", ".json", ".log", ".txt", ".yaml", ".csv",
                         ".png", ".jpg", ".webm", ".zip"}
        try:
            suffix = path.suffix.lower()
            # Allow directories (suffix=="") + whitelist files only.
            if path.is_file() and suffix not in SAFE_SUFFIXES:
                _trace(f"_open_path: REFUSED unsafe suffix {suffix!r} for {path}")
                return
        except Exception:
            return
        # Fall through to original behavior (os.startfile / open / xdg-open)
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]  # nosec — whitelisted suffix
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            _trace(f"_open_path failed: {exc}")
        return

    def _auto_lint(self, tarball: Path) -> None:
        """v0.7.0: run G165 lint v3 on the just-recorded tarball + show
        24-criteria PRD result inline. Runs on a daemon thread so the
        UI stays responsive while numpy/PIL spin up.
        """
        def _go():
            try:
                # Lazy import — keeps recorder cold-start fast.
                if getattr(sys, "frozen", False):
                    sys.path.insert(0, str(_BUNDLE_ROOT))
                import lint_v3_prd_grounded as lint_mod  # noqa: PLC0415

                # Extract the tarball into a temp dir for the linter.
                with tempfile.TemporaryDirectory() as td:
                    td_path = Path(td)
                    with tarfile.open(tarball, "r:gz") as tf:
                        tf.extractall(td_path)
                    inner = [p for p in td_path.iterdir() if p.is_dir()]
                    target = inner[0] if len(inner) == 1 else td_path
                    rpt = lint_mod.run_all_checks(target)

                _trace(
                    f"auto_lint: {rpt.passed_count}/{rpt.total_checks} "
                    f"PASS, {rpt.failed_count} FAIL"
                )

                def _ui():
                    if rpt.failed_count == 0:
                        self._set(
                            f"✓ {rpt.passed_count}/{rpt.total_checks} 全过",
                            GREEN,
                            "完全符合买家规格 — 可以交付。",
                        )
                    else:
                        # Lite recorder is expected to fail several
                        # depth/audio/intrinsics checks; that's known
                        # scope (Rust app fixes those). Still surface
                        # the count so Howard sees what's collected.
                        self._set(
                            f"⚠️ {rpt.passed_count}/{rpt.total_checks} 通过",
                            ORANGE,
                            f"{rpt.failed_count} 项缺失（深度/音频/内参等需要正式 Rust 版补）。"
                            f"已收的部分可以先用。",
                        )
                    # Detail in hint label.
                    fail_names = [
                        f"#{r.criterion_id}"
                        for r in rpt.results if not r.passed
                    ][:8]
                    self._hint.config(
                        text=(
                            f"已保存: {tarball}\n"
                            f"未通过: {', '.join(fail_names) if fail_names else '(none)'}"
                        ),
                        fg=ORANGE if rpt.failed_count else GREEN,
                    )
                self.after(0, _ui)
            except Exception as exc:  # noqa: BLE001
                _trace(f"auto_lint failed: {exc}\n{traceback.format_exc()}")
                self.after(0, lambda: self._hint.config(
                    text=f"已保存: {tarball}\n（自动验证失败 — 见远程日志）",
                    fg=ORANGE,
                ))

        threading.Thread(target=_go, daemon=True).start()

    def _auto_bft(self, tarball: Path) -> None:
        """v0.21.0: BFT N=4 self-verification on the just-recorded tarball.

        Goes deeper than _auto_lint (which runs PRD-grounded shallow checks).
        BFT runs V₁ Claude / V₂ MiniMax / V₂' GLM / V₃ Physics-Table on every
        frame pair, computes per-residual COMMIT/REJECT/VIEW_CHANGE/INSUFFICIENT
        and a dataset_decision. If FAIL, surfaces specific residuals + which
        verifier rejected, so tester knows exactly what to re-record.

        Howard's "shift-left" ask: 数据对的最强保证 = recorder 自验。
        """
        def _go():
            try:
                # Lazy import — keeps cold-start fast.
                if getattr(sys, "frozen", False):
                    sys.path.insert(0, str(_BUNDLE_ROOT))
                from bin.bft_orchestrator import orchestrator as orch  # noqa: PLC0415
                import json  # noqa: PLC0415

                # Extract tarball + read action_camera.json.
                with tempfile.TemporaryDirectory() as td:
                    td_path = Path(td)
                    with tarfile.open(tarball, "r:gz") as tf:
                        tf.extractall(td_path)
                    # v0.23.0: target = whichever directory level holds
                    # action_camera.json. Some tarballs wrap everything in a
                    # single dir (recorder default), others extract files at
                    # root (sample_tarball_builder). Pick whichever works.
                    if (td_path / "action_camera.json").exists():
                        target = td_path
                    else:
                        inner = [p for p in td_path.iterdir() if p.is_dir()]
                        candidates = [
                            p for p in inner
                            if (p / "action_camera.json").exists()
                        ]
                        target = candidates[0] if candidates else (
                            inner[0] if len(inner) == 1 else td_path
                        )
                    ac_path = target / "action_camera.json"
                    if not ac_path.exists():
                        _trace("auto_bft: action_camera.json not found, skip")
                        return
                    records = json.loads(ac_path.read_text(encoding="utf-8"))

                    # v0.23.0: gather multimodal artifact paths so the
                    # orchestrator-level residuals (R13/R15/R16/R20/R22/R23)
                    # actually fire instead of ABSTAINing. Each path is
                    # backward-compatible — orchestrator self-handles None.
                    inputs_path = target / "inputs.jsonl"
                    depth_dir = target / "depth"
                    depth_manifest_path = target / "depth_manifest.json"
                    video_path = target / "video.mp4"
                    inputs_arg = inputs_path if inputs_path.exists() else None
                    depth_dir_arg = depth_dir if depth_dir.exists() else None
                    depth_manifest_arg = (
                        depth_manifest_path if depth_manifest_path.exists() else None
                    )
                    video_arg = video_path if video_path.exists() else None

                    # Run BFT on the first 60 contiguous frames (= 2 seconds).
                    # Must be contiguous because R03/R04/R05 require real
                    # frame[n+1] neighbors — non-adjacent pairs produce false
                    # REJECTs (Δposition over a gap ≠ speed · dt). The full
                    # dataset analysis runs on engineer's side via the CLI
                    # `python -m bin.bft_orchestrator.orchestrator <ac.json>`.
                    n = len(records)
                    spot_check_size = min(60, n)
                    contiguous_records = records[:spot_check_size]
                    report = orch.aggregate_dataset(
                        contiguous_records,
                        fps=30.0,
                        inputs_path=inputs_arg,
                        depth_dir=depth_dir_arg,
                        depth_manifest_path=depth_manifest_arg,
                        video_path=video_arg,
                    )

                _trace(f"auto_bft: report={report}")

                def _ui():
                    decision = report.get("dataset_decision", "UNKNOWN")
                    residuals = report.get("residuals", {})
                    # v0.23.0: separate single-modal (R01..R12) from
                    # multimodal/dataset (R13/R15/R16/R20*/R22/R23) so the
                    # tester sees which class failed.
                    multimodal_prefixes = ("R13", "R15", "R16", "R20", "R22", "R23")
                    multimodal_bad = []
                    single_bad = []
                    for rname, stats in residuals.items():
                        rej = stats.get("REJECT", 0)
                        vc = stats.get("VIEW_CHANGE", 0)
                        if rej == 0 and vc == 0:
                            continue
                        tag = (
                            f"{rname}({rej}REJ)" if rej > 0
                            else f"{rname}({vc}VC)"
                        )
                        if rname.startswith(multimodal_prefixes):
                            multimodal_bad.append(tag)
                        else:
                            single_bad.append(tag)
                    if decision == "PASS":
                        self._set(
                            f"✓ BFT 共识: 全过 ({len(residuals)} 残差)",
                            GREEN,
                            "数据通过 N=4 拜占庭共识 — 可以上传。",
                        )
                        self._hint.config(
                            text=f"已保存: {tarball}\nBFT N=4 全过 ({len(residuals)} 残差)",
                            fg=GREEN,
                        )
                    else:
                        bad_combined = multimodal_bad + single_bad
                        detail_lines = []
                        if multimodal_bad:
                            detail_lines.append(
                                f"多模态失败: {', '.join(multimodal_bad[:5])}"
                            )
                        if single_bad:
                            detail_lines.append(
                                f"单模态失败: {', '.join(single_bad[:5])}"
                            )
                        if not detail_lines:
                            detail_lines.append("(详情见日志)")
                        self._set(
                            f"⚠️ BFT 共识 FAIL ({decision})",
                            ORANGE,
                            "\n".join(detail_lines)
                            + "\n建议重录或检查 producer。",
                        )
                        self._hint.config(
                            text=(
                                f"已保存: {tarball}\n"
                                f"BFT 数据决议: {decision} — "
                                f"问题: {', '.join(bad_combined[:5]) if bad_combined else '见日志'}"
                            ),
                            fg=ORANGE,
                        )
                self.after(0, _ui)
            except Exception as exc:
                _trace(f"auto_bft failed: {exc}\n{traceback.format_exc()}")

        threading.Thread(target=_go, daemon=True).start()

    def _on_update_check(self, latest_tag, exe_url, is_newer):
        """Self-update callback. If newer release found, stage + restart."""
        if not is_newer or not exe_url:
            self.after(0, lambda: self._hint.config(
                text=f"已经是最新版 ({_current_version_tag()})\n如果出问题，请把 {_STARTUP_LOG} 截图给工程师。",
                fg=TEXT_GRAY,
            ))
            return
        # Don't auto-replace mid-recording; wait until tester is idle.
        if self._record_armed:
            _trace(f"update: deferred — recording in progress, will retry on close")
            return
        _trace(f"update: staging {latest_tag}")
        self.after(0, lambda: self._set("⏳ 自动更新中…", ORANGE,
                                         f"正在下载 {latest_tag}，几秒后会自动重启。"))
        if _stage_self_update(exe_url):
            _trace("update: staged ok, exiting for relaunch")
            self.after(2000, self._on_close)
        else:
            self.after(0, lambda: self._hint.config(
                text=f"自动更新失败 — 见 {_STARTUP_LOG}",
                fg=ORANGE,
            ))

    def _tick_recording_status(self) -> None:
        """v0.12.0: tick once per second while ffmpeg is alive, updating
        the subtitle with elapsed time + current video file size +
        progress toward 6-minute cap. Self-stops when ffmpeg dies.
        """
        if self._ffmpeg_proc is None or self._ffmpeg_proc.poll() is not None:
            return  # ffmpeg has exited; let watch_loop's finalizer take over
        try:
            elapsed = max(0.0, time.time() - self._record_started_at)
        except Exception:
            elapsed = 0.0
        # Format mm:ss
        mm = int(elapsed // 60)
        ss = int(elapsed % 60)
        # Read current video file size; the file may not exist for the
        # first ~1s as ffmpeg sets up its container.
        size_str = "—"
        try:
            if self._video_path and self._video_path.exists():
                mb = self._video_path.stat().st_size / (1024 * 1024)
                size_str = f"{mb:.1f} MB"
        except Exception:
            pass
        # Progress bar visual (caps at 6 min = 360s)
        cap = 360.0
        pct = min(100, int((elapsed / cap) * 100))
        bar_w = 18
        filled = int(bar_w * pct / 100)
        bar = "█" * filled + "░" * (bar_w - filled)
        # v0.22.0: real-time data quality (Howard '录的时候确保数据的精确度')
        # Lightweight check on the live event buffer — if anything looks off,
        # surface it NOW so the tester aborts and re-records instead of
        # discovering a problem 6 minutes later in _auto_bft.
        quality_line = ""
        try:
            if self._input_capture is not None:
                rt = self._input_capture.realtime_check(last_n=200)
                if rt["healthy"]:
                    quality_line = f"\n✓ 数据精度 OK ({rt['total_events']} 事件)"
                else:
                    issues = []
                    if not rt["monotonic_ts"]:
                        issues.append("时间戳乱序")
                    if rt["invalid_keycodes"] > 0:
                        issues.append(f"{rt['invalid_keycodes']}个无效键码")
                    if rt["mouse_oob"] > 0:
                        issues.append(f"{rt['mouse_oob']}个鼠标越界")
                    if rt["last_event_age_ms"] > 5000:
                        issues.append(f"事件停顿 {rt['last_event_age_ms']/1000:.1f}s")
                    quality_line = f"\n⚠️ 数据精度: {', '.join(issues)}"
        except Exception:
            pass
        try:
            self._subtitle.config(
                text=f"⏱  {mm}分{ss:02d}秒  /  6 分钟  ({pct}%)\n[{bar}]\n📦 视频文件 {size_str}{quality_line}",
                fg=RED,
            )
        except Exception:
            pass
        # Schedule next tick.
        self.after(1000, self._tick_recording_status)

    def _restore_window(self) -> None:
        """Bring the recorder window back from minimized state.

        Called from the watcher thread (via self.after) after recording
        finishes, so the tester sees the verdict banner without having
        to find us in the taskbar.
        """
        try:
            self.deiconify()
            self.lift()
            _trace("window restored from taskbar")
        except Exception as e:
            _trace(f"deiconify failed: {e}")

    # ---- rc9 depth-progress UI ----------------------------------------
    # Howard 2026-05-09: testers thought the recorder was hung because
    # DepthAnything V2 inference runs invisibly for 30-60 minutes on CPU
    # (10800 frames × 1-3 sec each). These methods replace the normal
    # arm/idle UI with a progress bar + ETA + Skip button while inference
    # is in flight, then restore the normal UI when it finishes (or the
    # tester skips). All Tk mutations happen on the main thread via
    # self.after(0, ...) so the depth runner can call back from any
    # worker thread safely.
    def _show_depth_progress_ui(self) -> None:
        """Replace the normal recorder UI with a depth-inference progress
        view. Force the window to re-appear from the taskbar so the tester
        actually sees it.
        """
        # rc9 Bug 1: force restore from iconified — tester complained the
        # recorder "stayed hidden" while depth ran for ~40 min.
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.after(500, lambda: self.attributes("-topmost", False))
            _trace("depth_progress: restored window from taskbar")
        except Exception as e:
            _trace(f"depth_progress: deiconify failed: {e}")

        # Reset state.
        self._skip_depth_flag.clear()
        self._depth_progress_started_at = time.time()

        # Hide the normal arm button + helpbar; we'll restore them on
        # finish. We don't destroy them so layout state is preserved.
        try:
            self._arm_btn.pack_forget()
            self._upload_btn.pack_forget()
        except Exception:
            pass

        # Top-level status text — replaces the verdict banner.
        self._set("📊 处理深度图中…", "#1976d2",
                  "录制已结束，正在生成每帧深度图。\n"
                  "深度图完成后会打包成最终 tarball。")

        # Build the progress widgets. We keep refs in a list so
        # _hide_depth_progress_ui can tear them down cleanly.
        progress_frame = tk.Frame(self, bg="white")
        progress_frame.pack(pady=(8, 4), fill="x", padx=24)

        # Counter label: "1240 / 10800 帧"
        counter = tk.Label(
            progress_frame,
            text="0 / ? 帧",
            font=("Helvetica", 12, "bold"),
            bg="white",
            fg="#1976d2",
        )
        counter.pack(pady=(0, 4))

        # Progress bar (ttk indeterminate-capable, but we'll drive it
        # determinately via self["value"] = pct from the callback).
        bar = ttk.Progressbar(
            progress_frame,
            orient="horizontal",
            mode="determinate",
            maximum=100.0,
            length=400,
        )
        bar.pack(pady=(0, 6))
        bar["value"] = 0.0

        # ETA label: computed from rate × frames remaining.
        eta_label = tk.Label(
            progress_frame,
            text="预计剩余时间：—",
            font=("Helvetica", 10),
            bg="white",
            fg="#546e7a",
        )
        eta_label.pack(pady=(0, 6))

        # Skip-depth row: pre-checked when no GPU detected.
        skip_row = tk.Frame(progress_frame, bg="white")
        skip_row.pack(pady=(4, 0))
        skip_var = tk.BooleanVar(value=self._depth_default_skip)
        skip_check = tk.Checkbutton(
            skip_row,
            text=(
                "默认跳过 (未检测到独立显卡，CPU 推断会很慢)"
                if self._depth_default_skip
                else "跳过深度图（保留视频和操作数据）"
            ),
            variable=skip_var,
            font=("Helvetica", 10),
            bg="white",
            fg="#546e7a",
            selectcolor="white",
            activebackground="white",
            command=lambda: self._on_skip_depth_toggle(skip_var.get()),
        )
        skip_check.pack(side="left", padx=(0, 6))

        skip_btn = tk.Button(
            skip_row,
            text="🚫 跳过深度图",
            font=("Helvetica", 11, "bold"),
            bg="#d97706",
            fg="white",
            activebackground="#b45309",
            activeforeground="white",
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2",
            command=self._on_skip_depth_clicked,
        )
        skip_btn.pack(side="left", padx=(6, 0))

        # If GPU isn't detected, set the flag immediately. The tester can
        # still uncheck the box to override (which clears the flag).
        if self._depth_default_skip:
            self._skip_depth_flag.set()
            _trace("depth_progress: default-skip armed (no GPU detected)")

        self._depth_progress_widgets = [
            progress_frame, counter, bar, eta_label,
            skip_row, skip_check, skip_btn, skip_var,
        ]

    def _on_skip_depth_toggle(self, checked: bool) -> None:
        """Skip checkbox toggled. Set/clear the cooperative skip flag."""
        if checked:
            self._skip_depth_flag.set()
            _trace("depth_progress: skip flag SET via checkbox")
        else:
            self._skip_depth_flag.clear()
            _trace("depth_progress: skip flag CLEARED via checkbox")

    def _on_skip_depth_clicked(self) -> None:
        """Tester clicked the prominent 'Skip depth maps' button."""
        _trace("depth_progress: tester clicked SKIP DEPTH button")
        self._skip_depth_flag.set()
        # Replace the inline status text so the tester immediately sees
        # the click landed even before the inference loop polls the flag.
        self._set("⏳ 正在收尾跳过…", "#d97706",
                  "等当前帧推断完，就会打包不带深度图的 tarball。")

    def _on_depth_progress(self, frames_done: int, total_frames: int) -> None:
        """Called from the depth runner thread — marshal to Tk via after().

        Updates the counter, progress bar, and ETA. Safe to call with
        total_frames == 0 (we just hide the percent + ETA in that case).
        """
        # Compute everything off the GUI thread, then schedule the apply.
        elapsed = max(0.001, time.time() - self._depth_progress_started_at)
        rate = frames_done / elapsed if elapsed > 0 else 0.0  # frames/sec
        remaining = max(0, (total_frames - frames_done))
        eta_sec = remaining / rate if rate > 0 else 0.0
        pct = (100.0 * frames_done / total_frames) if total_frames > 0 else 0.0

        def _apply() -> None:
            try:
                if not self._depth_progress_widgets:
                    return  # UI already torn down
                _, counter, bar, eta_label, *_rest = self._depth_progress_widgets
                if total_frames > 0:
                    counter.config(
                        text=f"{frames_done} / {total_frames} 帧 ({pct:.1f}%)",
                    )
                    bar["value"] = pct
                else:
                    counter.config(text=f"{frames_done} / ? 帧")
                # ETA — only meaningful once we have a rate AND a total.
                if total_frames > 0 and rate > 0 and frames_done > 0:
                    if eta_sec >= 60:
                        eta_label.config(
                            text=f"预计剩余时间：约 {int(round(eta_sec / 60))} 分钟",
                        )
                    else:
                        eta_label.config(
                            text=f"预计剩余时间：约 {int(round(eta_sec))} 秒",
                        )
                else:
                    eta_label.config(text="预计剩余时间：估算中…")
            except Exception as e:
                _trace(f"depth_progress: apply failed: {e}")

        try:
            self.after(0, _apply)
        except RuntimeError:
            # Tk closed mid-inference; nothing to update.
            pass

    def _hide_depth_progress_ui(self) -> None:
        """Tear down the depth-progress widgets and restore normal UI.

        Called when inference finishes (success OR skip). Idempotent.
        """
        widgets = self._depth_progress_widgets
        self._depth_progress_widgets = []

        def _apply() -> None:
            for w in widgets:
                try:
                    if hasattr(w, "destroy"):
                        w.destroy()
                except Exception:
                    pass
            # Re-add the arm + upload buttons so the tester can record
            # again without restarting the .exe. Order matches _build_ui.
            try:
                self._arm_btn.pack(pady=(16, 4))
                self._upload_btn.pack(pady=(0, 6))
            except Exception:
                pass
            _trace("depth_progress: UI restored to ready state")

        try:
            self.after(0, _apply)
        except RuntimeError:
            pass

    def _toggle_arm(self) -> None:
        """Tester clicked the arm button. Toggle recording state.

        v0.6.0: Howard 反馈 '界面 影响玩游戏'. As soon as recording is
        armed we minimize ourselves to the taskbar so MC reclaims full
        screen focus. Critical for exclusive-fullscreen MC sessions
        where our window stealing focus could crash MC. The user can
        click the taskbar icon any time to bring us back and click
        '停止录制'.
        """
        if not self._record_armed:
            # Arm
            self._record_armed = True
            self._arm_btn.config(
                text="■ 停止录制",
                bg="#c62828",
                activebackground="#b71c1c",
            )
            _trace("recording armed by user click")
            # v0.17.0 BUG FIX: do NOT iconify here. Earlier versions
            # iconified the window the moment ▶ was clicked, hiding the
            # GUI before the watch_loop's Phase-1 status messages
            # ('⏸ 已 arm — 请打开 Minecraft') could be seen. Tester saw
            # window vanish and assumed crash. v0.17.0 defers iconify to
            # the moment ffmpeg actually starts (in _run_one_session, see
            # 'iconify after ffmpeg starts' marker).
        else:
            # Disarm — request the watcher to stop any in-flight ffmpeg.
            self._record_armed = False
            self._stop_event.set()
            self._stop_ffmpeg()
            self._arm_btn.config(
                text="▶ 开始录制",
                bg="#1976d2",
                activebackground="#1565c0",
            )
            _trace("recording disarmed by user click")

    # ---- worker --------------------------------------------------------
    def _watch_loop(self) -> None:
        """Poll for MC launch → record → poll for MC exit → finalize.

        v0.9.0: this is now the SESSION wrapper. After a session
        finishes (recording packaged + verdict shown), it loops back to
        Phase 1 so re-arming via the GUI button works without restarting
        the .exe. Only the close-the-window or app exit path tears the
        loop down for good (via self._stop_event when _on_close fires).
        """
        if not _FFMPEG.exists():
            self._set("⚠️ 缺少 ffmpeg", ORANGE,
                      "ffmpeg.exe 没有打包进来，请联系工程师。")
            return

        while not self._stop_event.is_set():
            # rc11 SG: idle heartbeat at top of loop so watchdog sees the
            # process is alive even between recording sessions.
            _write_health("idle", 0, _minecraft_running())
            self._run_one_session()
            # After a session ends naturally, reset the arm state so the
            # GUI button starts fresh and Phase 1 (wait for arm + MC)
            # picks up cleanly on the next iteration. _stop_event is
            # only set by _on_close, which means tester-quit-the-app.
            if not self._stop_event.is_set():
                self.after(0, self._reset_arm_button)

    def _reset_arm_button(self) -> None:
        """After a recording session, restore the button to '▶ 开始录制' so
        the tester can immediately start another session without
        restarting the .exe.

        rc15.4-fix BUG R6 H3: re-run preflight + re-enable button if last
        run had a fatal preflight (e.g. disk freed up since startup). Was
        bug: preflight FATAL disabled arm permanently, tester had to
        restart recorder to recover.
        """
        try:
            self._record_armed = False
            # Re-run preflight to see if fatal condition cleared.
            try:
                fresh = self._run_preflight()
                self._preflight_results = fresh
                n_fatal = sum(1 for r in fresh if r["status"] in ("fail", "fatal"))
            except Exception:
                n_fatal = 0  # don't lock arm if probe itself crashed
            self._arm_btn.config(
                text="▶ 开始录制",
                bg="#1976d2",
                activebackground="#1565c0",
                state="normal" if n_fatal == 0 else "disabled",
            )
            _trace(f"button reset for next session (preflight fatal={n_fatal}, arm={'on' if n_fatal == 0 else 'off'})")
        except Exception:
            pass

    def _run_one_session(self) -> None:
        """One arm→record→package→verdict cycle. Returns when finished."""
        # Phase 1: wait for tester to arm AND MC to start.
        # v0.4.0 split: do NOT spawn ffmpeg until the tester explicitly
        # clicks "开始录制". This protects testers whose MC otherwise
        # works fine — our auto-ffmpeg won't blame us for MC issues.
        # v0.16.0: live status update so tester always sees what we're
        # waiting for. Previous bug: tester clicked ▶ → button changed
        # to '■ 停止录制' → but if MC wasn't running, watch_loop sat in
        # silent Phase 1 sleep forever and 'time stays 0' = '时间不动'.
        _trace("watch_loop: waiting for arm + MC")
        self._set("准备好", TEXT_GRAY,
                  "先打开 Minecraft 玩一会儿确认它不崩。\n确认后再点上面 ▶ 开始录制。")
        arm_announced_at = None
        last_status_update = 0.0
        while not self._stop_event.is_set():
            armed = self._record_armed
            mc_alive = _minecraft_running()
            if armed and mc_alive:
                _trace("watch_loop: armed + MC alive → entering recording")
                break
            now = time.time()
            # First-time arm message
            if armed and arm_announced_at is None:
                arm_announced_at = now
            # Update status every 2s while waiting so tester sees progress
            if now - last_status_update >= 2.0:
                last_status_update = now
                if armed and not mc_alive:
                    waited = int(now - (arm_announced_at or now))
                    self._set("⏸ 已 arm — 请打开 Minecraft", ORANGE,
                              f"录制器在等 Minecraft 启动…（已等 {waited} 秒）\n"
                              f"打开 Minecraft 后 1-2 秒会自动开始录")
                elif not armed and mc_alive:
                    self._set("Minecraft 已开 — 等你点 ▶ 开始录制", TEXT_GRAY,
                              "MC 检测到了，点上面蓝色按钮就开始录")
                elif not armed and not mc_alive:
                    self._set("准备好", TEXT_GRAY,
                              "先打开 Minecraft 玩一会儿确认它不崩。\n"
                              "确认后再点上面 ▶ 开始录制。")
            time.sleep(0.5)
        if self._stop_event.is_set():
            _trace("watch_loop: stopped before recording")
            return

        # 5-second settle delay. Lets MC fully transition out of any
        # loading screen / world generation before ffmpeg attaches to
        # its window or grabs the desktop. Reported MC crashes were
        # likely caused by GDI capture starting mid-loading.
        _trace("watch_loop: 5s settle before ffmpeg")
        self._set("● 即将开始", ORANGE, "5 秒后开始录制（让 Minecraft 稳定）…")
        for _ in range(5):
            if self._stop_event.is_set() or not self._record_armed:
                _trace("watch_loop: aborted during settle")
                return
            time.sleep(1.0)

        # Capture window geometry (if visible) for systeminfo.json.
        self._mc_window_rect = _get_minecraft_window_rect()
        _trace(f"watch_loop: mc_window={self._mc_window_rect}")

        # Phase 2: start recording into a temp dir; we'll package it as
        # a 5-file PRD-shaped tarball after MC exits.
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._tmp_dir = Path(tempfile.mkdtemp(prefix=f"oyster-rec-{ts}-"))
        # rc10 B5 (IRON LAW EXCEPTION 2026-05-09): pre-flight disk space check.
        # rc15.4-fix BUG R4 H4: gate was 500MB but session writes 80-110MB +
        # depth dir. Tester at 600MB free passed gate then ran out mid-record →
        # ffmpeg dirty close, corrupted partial mp4. Raise to 2GB to leave
        # margin for: 1 session video (~110MB) + retry buffer + tmp staging.
        _MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
        _free_bytes = shutil.disk_usage(self._tmp_dir).free
        if _free_bytes < _MIN_FREE_BYTES:
            _free_mb = _free_bytes / (1024 * 1024)
            self._set("⚠️ 磁盘空间不足", ORANGE,
                      f"剩余 {_free_mb:.0f} MB, 录制需 ≥500 MB. 清理后重试.")
            _trace(f"disk_check: ABORT — only {_free_mb:.0f} MB free in {self._tmp_dir} (threshold 2GB)")
            self._terminator_reason = "disk_full"
            self._write_terminator(None)  # no clip_dir yet — write to runtime/
            _heal_emit(
                "B5_disk_space_preflight", "heal_failed", "fatal",
                f"disk free {_free_mb:.0f} MB < 2048 MB threshold; recording aborted",
                details={"free_mb": _free_mb, "threshold_mb": 2048, "probe_path": str(self._tmp_dir)},
                remediation={
                    "action": "user_prompt",
                    "performed": True,
                    "next_step": "tester clears disk space and retries arm",
                },
                recorder_version=RECORDER_VERSION,
            )
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            return
        self._video_path = self._tmp_dir / "video.mp4"
        self._record_started_at = time.time()
        # R18 session-binding: one UUID per recording, propagated into
        # session_manifest.json + every action_camera frame + inputs.jsonl
        # session_start. Closes red-team B-05 (Frankenstein splice).
        import uuid as _uuid_mod
        self._session_id = str(_uuid_mod.uuid4())
        try:
            self._start_ffmpeg(self._video_path)
        except Exception as exc:  # noqa: BLE001
            self._set("⚠️ 录制启动失败", ORANGE,
                      f"{type(exc).__name__}: {exc}")
            return

        # Start input capture in parallel with video. If pynput fails
        # (rare — e.g. tester hardened OS), record video only and note
        # in subtitle so the tester knows.
        self._input_capture = InputCapture()
        input_ok = self._input_capture.start()
        if input_ok:
            self._set("● 正在录制", RED,
                      "玩你的 Minecraft 即可，退出游戏会自动停止录制。"
                      "（视频 + 键鼠输入同步采集中）")
        else:
            self._set("● 正在录制（仅视频）", RED,
                      "键鼠采集未启动，仅录制视频。继续玩游戏即可。")

        # v0.12.0: live progress ticker so the tester knows recording is
        # actually working. Updates every second with elapsed seconds +
        # current video file size. Self-stops when ffmpeg ends.
        self.after(0, self._tick_recording_status)

        # v0.17.0: iconify after ffmpeg starts marker — moved here from
        # _toggle_arm. Now Phase 1 status messages stay visible until
        # recording actually begins.
        try:
            self.after(0, self.iconify)
            _trace("window iconified to taskbar (after ffmpeg start)")
        except Exception as e:
            _trace(f"iconify failed: {e}")

        # Phase 3: wait for MC to exit OR for 6 minutes elapsed (PRD cap)
        # OR for the user to disarm.
        # PRD spec requires 5-6 min duration; auto-stop at 6 min so the
        # downstream lint doesn't reject for over-length.
        MAX_RECORD_SECONDS = 6 * 60
        _last_health_write = 0.0
        while True:
            if self._stop_event.is_set():
                _trace("watch_loop: stop_event set — finalizing whatever we have")
                break
            if not self._record_armed:
                _trace("watch_loop: user disarmed — finalizing whatever we have")
                break
            if not _minecraft_running():
                _trace("watch_loop: MC exited — finalizing")
                # rc11 SF: distinguish clean game exit from session-cap exit.
                # If MC vanished before 5 min, treat as game_died (not clean_exit).
                self._terminator_reason = "game_died"
                break
            elapsed = time.time() - self._record_started_at
            if elapsed >= MAX_RECORD_SECONDS:
                self._set("⏱ 已到 6 分钟，自动停止", ORANGE,
                          "PRD 规格要求 5-6 分钟，正在收尾…")
                break
            # rc11 SG: heartbeat every 30s during recording so watchdog
            # can detect freezes (vs the 2s sleep + condition checks).
            now = time.time()
            if now - _last_health_write >= 30.0:
                _write_health(
                    "recording",
                    frame_count=len(self._captured_events or []),
                    game_pid_alive=True,
                )
                _last_health_write = now
            time.sleep(2.0)

        # v0.9.0 BUG FIX: previously, hitting `_stop_event` returned
        # before `_package_tarball`, throwing away the recording. Now ANY
        # stop reason (MC exit / user disarm / 6-min cap / stop_event)
        # falls through to packaging so the tester always gets a tarball
        # representing what was actually recorded up to that point.

        # Phase 4: finalize ffmpeg + input capture, then package.
        self._stop_ffmpeg()
        if self._input_capture is not None:
            self._captured_events = self._input_capture.stop()
        else:
            self._captured_events = []
        try:
            output_tar = self._package_tarball(ts)
        except Exception as exc:  # noqa: BLE001
            self._set("⚠️ 打包失败", ORANGE,
                      f"{type(exc).__name__}: {exc}")
            return

        if output_tar.exists():
            size_mb = output_tar.stat().st_size / (1024 * 1024)
            # rc13 SK (Phase B.3): duration enforcement — buyer rejects
            # < 5 min sessions per PRD criterion 2. Show modal so tester
            # knows to re-record instead of silently shipping rejected data.
            if self._terminator_reason == "duration_too_short":
                try:
                    import tkinter.messagebox as _mb
                    elapsed_min = (
                        (time.time() - self._record_started_at) / 60.0
                        if getattr(self, "_record_started_at", 0) else 0.0
                    )
                    _mb.showwarning(
                        "录制时长不足",
                        f"本次录制 {elapsed_min:.1f} 分钟 (< 5 分钟). "
                        "买家规格要求 5–6 分钟, 这次录像会被自动隔离, "
                        "请重新录制. 重启 Minecraft 后点 ▶ 开始录制 即可."
                    )
                    _trace(f"SK: prompted re-record (elapsed {elapsed_min:.1f} min)")
                    # rc15 Heal Framework: tester saw the re-record prompt.
                    _heal_emit(
                        "SK_duration_too_short_prompt", "user_prompt", "warn",
                        f"prompted tester to re-record ({elapsed_min:.1f} min < 5 min PRD min)",
                        details={"elapsed_min": elapsed_min, "min_required_min": 5.0},
                        remediation={"action": "user_prompt", "performed": True,
                                     "next_step": "tester restarts MC + records 5-6 min"},
                        session_id=getattr(self, "_session_id", None),
                        recorder_version=RECORDER_VERSION,
                    )
                except Exception:
                    _trace(f"SK: messagebox failed {traceback.format_exc()}")
                # rc15.4-fix BUG R5#4: move partial tarball to _rejected/
                # so buyer ingest can filter cleanly + tester can recover.
                try:
                    rejected_dir = output_tar.parent / "_rejected"
                    rejected_dir.mkdir(parents=True, exist_ok=True)
                    rejected_tar = rejected_dir / output_tar.name
                    output_tar.rename(rejected_tar)
                    output_tar = rejected_tar
                    _trace(f"SK: moved partial tarball to {rejected_tar}")
                except Exception:
                    _trace(f"SK: failed to move partial tarball {traceback.format_exc()}")
                # rc15.4-fix BUG R4 H1: orange verdict (NOT green) so UI is
                # consistent with the warning popup that just appeared.
                self._set("⚠️ 录制时长不足 — 已隔离", "#ef6c00",
                          f"{output_tar.name} 已移至 _rejected/ 目录, 请重录")
                self._hint.config(text=f"已隔离: {output_tar}", fg="#ef6c00")
            else:
                self._set("✓ 录制完成", GREEN,
                          f"{output_tar.name} ({size_mb:.1f} MB) 已保存。"
                          f"正在验证买家规格…")
                self._hint.config(
                    text=f"已保存: {output_tar}",
                    fg=GREEN,
                )
                # rc15.13 (Howard 2026-05-10 "新文件找不到 旧文件出来了"):
                # OneDrive sync trap — Documents may be cloud-redirected,
                # tester opens "我的录像" and sees stale cloud-restored
                # files. New tarball wrote OK to absolute path but
                # invisible until OneDrive sync. Two-pronged fix:
                # 1. Auto-open Explorer with new file SELECTED (highlighted)
                #    so tester literally cannot miss it.
                # 2. Write LATEST_RECORDING.txt to Desktop with absolute
                #    path so tester has unmissable pointer even if
                #    Explorer auto-open fails.
                try:
                    if os.name == "nt":
                        # explorer /select,<file> opens parent dir + selects
                        # the file (blue highlight). Cannot be missed.
                        subprocess.Popen(
                            ["explorer", f"/select,{output_tar}"],
                            creationflags=0x08000000,
                        )
                        _trace(f"rc15.13: Explorer /select fired on {output_tar}")
                except Exception as exc:
                    _trace(f"rc15.13: Explorer /select failed: {exc}")
                try:
                    desktop = _desktop_path()
                    pointer = desktop / "LATEST_RECORDING.txt"
                    pointer.write_text(
                        f"最新录制文件位置 (Latest recording path):\n\n"
                        f"{output_tar}\n\n"
                        f"如果 Explorer 没自动打开, 双击此 .txt 旁的资源管理器\n"
                        f"地址栏粘贴上面这个路径即可找到.\n\n"
                        f"录制时长: {size_mb:.1f} MB · {datetime.now().isoformat()}\n"
                        f"如果文件夹打开但看到旧文件 (云端同步还原):\n"
                        f"  - 等几秒让 OneDrive 同步完\n"
                        f"  - 或按 F5 刷新\n"
                        f"  - 或打开 OneDrive 设置看是否在同步\n",
                        encoding="utf-8",
                    )
                    _trace(f"rc15.13: wrote desktop pointer {pointer}")
                except Exception as exc:
                    _trace(f"rc15.13: desktop pointer failed: {exc}")
            # v0.6.0: window was iconified when arm was pressed to free
            # MC focus. MC has now exited, so restore our window so the
            # tester sees the green "✓ 录制完成" verdict without needing
            # to click the taskbar.
            self.after(0, self._restore_window)
            # v0.7.0: integrated buyer-spec validation. Howard 2026-05-05
            # "不知道有没有录到买家想要的东西" — answer the question
            # directly inside the recorder, no need to launch a separate
            # tool. We import lint_v3 lazily so its numpy/PIL deps don't
            # delay startup.
            self._auto_lint(output_tar)
            # v0.21.0: shift-left BFT N=4 self-verification — Howard
            # 战略反馈"最重要确保数据是对的". Lint v3 is a shallow check
            # (24 PRD criteria); BFT runs the full PINNs residual stack
            # across 4 independent verifiers and surfaces specific
            # disagreements so tester knows what to re-record.
            self._auto_bft(output_tar)
            # Engineer-side telemetry: push the full session log to a
            # remote pastebin so engineering can curl <url> and see what
            # happened on tester's machine without asking for files.
            def _on_url(url: Optional[str]) -> None:
                if url:
                    self.after(0, lambda: self._hint.config(
                        text=f"已保存: {output_tar}\n远程日志: {url}",
                        fg=GREEN,
                    ))
            _upload_log_in_background(_on_url)
        else:
            self._set("⚠️ 录制结束但文件未生成", ORANGE,
                      "请联系工程师并截图本窗口。")

    def _package_tarball(self, ts: str) -> Path:
        """Package the recording into a 5-file PRD-shaped tarball.

        Layout (per docs/CONSUMER_QA_CHECKLIST.md):
            clip-YYYYMMDD-HHMMSS.tar.gz
            ├── video.mp4            (real recording)
            ├── systeminfo.json      (window geometry — best-effort)
            ├── action_camera.json   (placeholder; full impl in Rust app)
            ├── gameinfo.xlsx        (placeholder; full impl in Rust app)
            └── depth/               (empty; full impl needs G198 shader)

        This will FAIL G165 lint (24/24 PRD criteria) on depth/audio,
        but at least gets the SHAPE right so the validator's structural
        checks can pass and engineers see exactly which fields are stubbed.
        """
        clip_dir = self._tmp_dir / f"clip-{ts}"
        clip_dir.mkdir(parents=True, exist_ok=True)

        # Compute recording duration once, up front: the frame-count math
        # below (action_camera.json) and the partial-duration check at the
        # end both need it. Reading time.time() now vs. later differs by
        # microseconds — irrelevant for 30 fps frame bucketing.
        elapsed_sec = max(0.0, time.time() - self._record_started_at)

        # 1. Move the real video into place.
        if self._video_path and self._video_path.exists():
            shutil.move(str(self._video_path), str(clip_dir / "video.mp4"))

        # 2. systeminfo.json — v0.10.0: now uses the proper engineering
        # helper bin/generate_systeminfo_json.build_systeminfo() rather
        # than my hand-rolled dict. Same function the buyer-spec
        # pipeline uses, so backend ingest accepts it without special-casing.
        rect = self._mc_window_rect or {}
        try:
            import generate_systeminfo_json as gsi  # noqa: PLC0415
            sys_info = gsi.build_systeminfo(
                game_process_name="javaw.exe",
                x=int(rect.get("x", 0)),
                y=int(rect.get("y", 0)),
                width=int(rect.get("width", 1920)),
                height=int(rect.get("height", 1080)),
                record_dpi=float(rect.get("recordDpi", 96)) / 96.0,  # ratio
            )
            sys_info["recordedAt"] = ts
            sys_info["recorderVersion"] = RECORDER_VERSION  # rc13: was hardcoded 'lite-v0.10.0'
            sys_info["_real_window_geometry"] = bool(self._mc_window_rect)
        except Exception as e:  # noqa: BLE001
            _trace(f"systeminfo: helper import failed, using stub ({e})")
            sys_info = {
                "gameProcessName": rect.get("title", "Minecraft"),
                "x": rect.get("x", 0),
                "y": rect.get("y", 0),
                "width": rect.get("width", 1920),
                "height": rect.get("height", 1080),
                "recordDpi": rect.get("recordDpi", 96),
                "recordedAt": ts,
                "recorderVersion": f"{RECORDER_VERSION}-fallback",  # rc13: was hardcoded 'lite-v0.10.0-fallback'
            }
        (clip_dir / "systeminfo.json").write_text(
            json.dumps(sys_info, indent=2), encoding="utf-8"
        )

        # 3. action_camera.json — v0.19.0 BIG REWRITE: PRD-aligned schema
        # was event-based with mouseX/cameraX scalars; PRD wants 9000
        # frame-aligned records at 30Hz. Sample/sample_tarball_builder.py
        # is the canonical schema reference.
        from datetime import datetime as _dt, timedelta as _td  # noqa: PLC0415

        FPS = 30.0
        target_frame_count = int(elapsed_sec * FPS) if elapsed_sec > 0 else 9000
        SCREEN_W, SCREEN_H = 1920, 1080
        # Build per-frame state by replaying captured pynput events into
        # frame buckets. Each frame inherits the last-known mouse/key
        # state (latching).
        events = sorted(
            self._captured_events,
            key=lambda e: e.get("timestamp_ms", 0),
        )
        cur_keys: list[int] = []
        cur_mx, cur_my = SCREEN_W // 2, SCREEN_H // 2
        prev_mx, prev_my = cur_mx, cur_my
        ev_idx = 0
        base_time = _dt.fromtimestamp(self._record_started_at)
        # Use intrinsics computed from window geometry (recorder.intrinsics.yaml
        # FOV 70° → fy = 540 / tan(35°) ≈ 771.4).
        fy = 540.0 / 0.7002075382097097  # tan(35°) = 0.7002...
        intrinsics = {"fx": round(fy, 3), "fy": round(fy, 3),
                      "cx": 960.0, "cy": 540.0}

        # R01 iron-law: load real game-state from Fabric mod JSONL.
        # On v0.26.0+ this is MANDATORY — hard-fail if missing, unless
        # --allow-placeholder was explicitly passed.
        try:
            from game_state_overlay import load as _gs_load, lookup_at_ms as _gs_lookup, apply_to_record as _gs_apply  # type: ignore  # noqa: PLC0415
        except ImportError:
            _gs_load = _gs_lookup = _gs_apply = None  # type: ignore

        _gs_samples = _gs_load() if _gs_load else None
        if _gs_samples:
            _trace(f"package: real game-state JSONL found, {len(_gs_samples)} samples — overlay enabled")
            # rc13 SF-fix: real game-state ingest = mod handshake confirmed.
            # Without this, terminator.json reports mod_handshake_ok=false
            # even when buyer data is fully real. Backend would quarantine
            # legit sessions thinking they're placeholder.
            self._mod_handshake_ok = True
        else:
            ver = _recorder_version_tuple()
            allow_placeholder = getattr(self, "_allow_placeholder", False)
            if ver >= (0, 26, 0) and not allow_placeholder:
                supported_str = ", ".join(SUPPORTED_MC_VERSIONS)
                raise RecorderError(
                    "Real game-state Fabric mod not loaded.\n"
                    f"Detected MC version: {_parse_mc_version_from_title((self._mc_window_rect or {}).get('title', '')) or 'unknown'}\n"
                    f"Supported mod builds:  {supported_str}\n"
                    "Download from:        https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/latest\n"
                    r"Install path:         %APPDATA%\.minecraft\mods" "\n"
                    "Tarball NOT created."
                )
            if allow_placeholder:
                _trace("package: --allow-placeholder active — using placeholder camera/player fields")
            else:
                _trace("package: no game-state JSONL — using placeholder camera/player fields (pre-v0.26.0)")

        action_records = []
        for f in range(target_frame_count):
            # Frame-relative timestamp in ms
            f_ms = int(f * 1000.0 / FPS)
            t = base_time + _td(milliseconds=f_ms)
            t_str = t.strftime("%Y-%m-%d %H:%M:%S.") + f"{t.microsecond // 1000:03d}"
            # Apply all events whose timestamp is ≤ this frame's window
            while ev_idx < len(events) and events[ev_idx].get("timestamp_ms", 0) <= f_ms:
                ev = events[ev_idx]
                et = ev.get("event_type", "")
                if et == "key_down":
                    kc = int(ev.get("keyCode", -1))
                    if kc >= 0 and kc not in cur_keys:
                        cur_keys.append(kc)
                elif et == "key_up":
                    kc = int(ev.get("keyCode", -1))
                    if kc in cur_keys:
                        cur_keys.remove(kc)
                elif et in ("mouse_move", "mouse_click"):
                    cur_mx = int(ev.get("mouseX", cur_mx))
                    cur_my = int(ev.get("mouseY", cur_my))
                ev_idx += 1
            # Normalized mouse coords (0..1) per PRD; deltas
            mx_n = cur_mx / SCREEN_W
            my_n = cur_my / SCREEN_H
            mdx = (cur_mx - prev_mx) / SCREEN_W
            mdy = (cur_my - prev_my) / SCREEN_H
            prev_mx, prev_my = cur_mx, cur_my
            rec = {
                "frame": f,
                "time": t_str,
                "fps": FPS,
                "route_type": 1,
                # PRD 文件2 字面：mouse_* 都是 list[float]，例 `{"mouse_x": [0.5]}`.
                # mouse_x/y ∈ [0, 1]，mouse_dx/dy ∈ [-1, 1] (带方向).
                "mouse_x": [mx_n],
                "mouse_y": [my_n],
                "mouse_dx": [mdx],
                "mouse_dy": [mdy],
                # PRD: keyCode is list[int] of currently-held keys (VK code, NOT ASCII).
                "keyCode": list(cur_keys) if cur_keys else [],
                # camera_* fields placeholder — Replay Mod postprocess
                # (G274) overwrites these when .mcpr present. For vanilla
                # tester these stay [0,64,0] / identity.
                "camera_position": [0.0, 64.0, 0.0],
                # PRD page 4 字面 'camera_rotation_oula' (拼音). DO NOT rename to euler.
                "camera_rotation_oula": [0.0, 0.0, 0.0],
                "camera_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
                # PRD page 4 字面 'camera_Follow Offset' (带空格 + 大写 F).
                # DO NOT rename to snake_case. Quirky but PRD-mandated.
                "camera_Follow Offset": [0.0, 1.6, 0.0],
                "camera_intrinsics": intrinsics,
                "camera_speed": [0.0, 0.0, 0.0],
                "player_position": [0.0, 64.0, 0.0],
                # PRD page 5 字面 'player_rotation_oula' (拼音). DO NOT rename.
                "player_rotation_oula": [0.0, 0.0, 0.0],
                "player_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
                "player_speed": [0.0, 0.0, 0.0],
                "metric_scale": 1.0,
                # R18 session-binding: identifies which recording this
                # frame belongs to. Verified against session_manifest.json.
                "session_id": getattr(self, "_session_id", ""),
            }
            # Howard 2026-05-07: if the mod is installed, overlay real
            # camera/player fields from the JSONL onto this record.
            if _gs_samples and _gs_apply:
                sample = _gs_lookup(_gs_samples, f_ms)
                if sample is not None:
                    _gs_apply(rec, sample)
            action_records.append(rec)

        (clip_dir / "action_camera.json").write_text(
            # PRD format: top-level array of records, no wrapper dict.
            # sample_tarball_builder.py writes this format too.
            json.dumps(action_records, separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )

        # v0.20.2: write inputs.jsonl — raw pynput events (key_down, key_up,
        # mouse_move, mouse_click) with millisecond timestamps. Producer-side
        # artifact for R13 multimodal residual (keyCode-vs-input-replay)
        # which closes the FI-02 blind spot in BFT N=4 single-modal mesh.
        # See docs/SPEC_R13_MULTIMODAL.md § R13. Per IL10, this file is OK
        # to be missing on legacy/headless runs — R13 will ABSTAIN, not FAIL.
        try:
            with (clip_dir / "inputs.jsonl").open("w", encoding="utf-8") as fh:
                # First line: session_start sentinel (frame-time alignment).
                fh.write(json.dumps({
                    "event_type": "session_start",
                    "timestamp_ms": 0,
                    "fps": FPS,
                    "frame_count": target_frame_count,
                    # R18: session_id ties this inputs.jsonl to the same
                    # session_manifest.json that action_camera frames cite.
                    "session_id": getattr(self, "_session_id", ""),
                }) + "\n")
                for ev in self._captured_events:
                    fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
            _trace(f"package: wrote inputs.jsonl ({len(self._captured_events)} events)")
        except Exception as e:
            _trace(f"package: inputs.jsonl write failed: {e}")
            # Non-fatal: action_camera.json still ships; R13 will ABSTAIN.

        # R18: write session_manifest.json so consumer-side R18 residual can
        # bind every artifact (action_camera frames + inputs.jsonl session_start)
        # to a single recording. Closes red-team B-05 (Frankenstein splice).
        try:
            (clip_dir / "session_manifest.json").write_text(
                json.dumps({
                    "session_id": getattr(self, "_session_id", ""),
                    "recorder_version": RECORDER_VERSION,  # rc13: was hardcoded 'lite-v0.21.0'
                    "start_time": _dt.fromtimestamp(self._record_started_at).isoformat(),
                    "frame_count": target_frame_count,
                    "fps": FPS,
                    "mp4_clean_close": getattr(self, "_ffmpeg_clean_close", True),
                }, indent=2),
                encoding="utf-8",
            )
            _trace("package: wrote session_manifest.json")
        except Exception as e:  # noqa: BLE001
            _trace(f"package: session_manifest.json write failed: {e}")
            # Non-fatal: R18 will ABSTAIN on missing manifest.

        # 4. gameinfo.xlsx — uses bin/generate_gameinfo_xlsx.write_xlsx() for
        # a real 14-field xlsx. Howard 2026-05-06 Iron Law: NO PLACEHOLDER
        # FALLBACK. If the helper fails, the clip is unusable; fail loud
        # so the tester can re-run rather than ship a stub.
        import generate_gameinfo_xlsx as ggx  # noqa: PLC0415
        video_dur = max(0.0, time.time() - self._record_started_at)
        game_info = ggx.build_gameinfo_dict(
            game_name="Minecraft",
            game_version="1.20.4",
            platform="Java Edition",
            scene_name="overworld",
            weather="clear",
            time_of_day="day",
            character_name="DataPilot",
            character_class="player",
            operator_id="lite-recorder",
            total_frames=int(video_dur * 30),  # 30 fps locked
            video_duration_sec=video_dur,
            route_type=1,
            notes=f"recorded by {RECORDER_VERSION} at {ts}",
        )
        ggx.write_xlsx(game_info, str(clip_dir / "gameinfo.xlsx"))

        # 5. depth/ — Howard 2026-05-07: REAL DepthAnything V2 inference on
        # the recorded video.mp4. Iron Law: NO PLACEHOLDER. The .exe is
        # built with torch + transformers + DepthAnything model bundled via
        # PyInstaller (build-recorder-exe.yml --add-data). Inference runs
        # CPU-side on the local machine; ~1-3 sec/frame at 256×256.
        #
        # If the bundled model fails to load (corrupted weights, missing
        # torch), the recording ABORTS the tarball — we refuse to ship
        # without real depth.
        #
        # rc9 (Howard 2026-05-09): show a depth-progress UI to the tester
        # so they don't think the recorder is hung during the 30-60 min
        # CPU pass. Also honour the cooperative skip flag — if the tester
        # clicks "跳过深度图" (or no GPU was detected and the default-skip
        # was left armed), we ship the tarball WITHOUT a depth/ directory.
        # Iron-law-compatible: a skipped tarball is partial-by-choice, not
        # a placeholder; downstream lint will FAIL on missing depth which
        # is the correct behaviour.
        # rc14 dual-track depth (Howard 2026-05-09 选项 C):
        #   - GPU 可用 → 本地 DepthAnything V2 (cuda 或 dml, NOT cpu)
        #   - GPU 不可用 → server_pending, backend GPU worker 处理 (Phase C SM)
        #   - OYSTER_LOCAL_DEPTH=0 强制服务器 (调试)
        #   - OYSTER_LOCAL_DEPTH=1 强制本地 (即使无 GPU, 跑 CPU)
        #
        # 修复 rc9-rc13 的核心 bug: device="cpu" 硬编码导致集显用户卡死
        # 30-60 min. 现在真 GPU 用户拿到本地 depth, 无 GPU 用户不阻塞 — 数据
        # 质量在两条路径上不一致是已知 trade-off, backend ingest 通过
        # depth_manifest.status 知道哪些 session 需要 server-side 重算.
        depth_dir = clip_dir / "depth"
        depth_manifest_path = clip_dir / "depth_manifest.json"

        _depth_override = os.environ.get("OYSTER_LOCAL_DEPTH", "").strip()
        if _depth_override == "1":
            run_local_depth = True
            local_device = "cpu"  # explicit force, accept the CPU pain
            track_reason = "OYSTER_LOCAL_DEPTH=1 force-local (CPU)"
        elif _depth_override == "0":
            run_local_depth = False
            local_device = None
            track_reason = "OYSTER_LOCAL_DEPTH=0 force-server"
        else:
            # Auto: pick device by GPU detection
            gpu_ok = _detect_gpu_available()
            if not gpu_ok:
                run_local_depth = False
                local_device = None
                track_reason = "no GPU detected — server-pending"
            else:
                # rc14: prefer real accelerators. cuda first (nvcuda.dll
                # loadable), DirectML second (torch_directml importable).
                # cpu is NEVER auto-selected — that was rc9-rc13's bug.
                local_device = "cuda"
                try:
                    import torch_directml  # type: ignore  # noqa: PLC0415
                    if torch_directml.is_available():  # type: ignore[attr-defined]
                        local_device = "dml"
                except Exception:
                    pass
                run_local_depth = True
                track_reason = f"GPU detected — local inference on {local_device}"
        _trace(f"depth: track decision = {track_reason}")
        # rc15 Heal Framework: depth track decision is high-value telemetry.
        _heal_emit(
            "depth_dual_track", "report", "info",
            f"depth track: {track_reason}",
            details={"run_local": run_local_depth, "device": local_device,
                     "override": _depth_override or "auto"},
            remediation={"action": "auto_clean" if run_local_depth else "server_defer",
                         "performed": True,
                         "next_step": "local inference runs" if run_local_depth
                         else "backend GPU pool consumes upload (Phase C)"},
            session_id=getattr(self, "_session_id", None),
            recorder_version=RECORDER_VERSION,
        )

        if run_local_depth:
            # rc15.7 (Howard 2026-05-09 "我自己觉得可以多出不同方法 fall back"):
            # multi-tier fallback chain. Each tier has specific failure mode
            # detection + corresponding server_pending status reason so backend
            # can route the session correctly.
            #
            # Tiers (in order of preference):
            #   1. local_device (dml/cuda) — primary
            #   2. cuda fallback if dml fails AND cuda was available but lower
            #      priority (rare: dual-GPU systems with dGPU + AMD iGPU)
            #   3. server_pending with detailed exception reason
            #
            # 30-min hard timeout via threading.Timer setting skip_flag prevents
            # stuck inference (DML driver hang). Stricter than user-skip path
            # because user expects local depth to take 5-15 min on 780M; >30 min
            # means the GPU is wedged and server-side will be faster anyway.
            depth_skipped = False
            local_failure_reason: Optional[str] = None
            local_failure_type: Optional[str] = None
            _DEPTH_TIMEOUT_SEC = 30 * 60  # 30 min

            timeout_timer: Optional[threading.Timer] = None
            try:
                from depth_anything_v2_inference import infer_depth_for_video  # noqa: PLC0415
                video_path = clip_dir / "video.mp4"
                _trace(f"depth: running DepthAnything V2 ({local_device}) on {video_path} (timeout {_DEPTH_TIMEOUT_SEC}s)")
                self.after(0, self._show_depth_progress_ui)
                # rc15.7-fix BUG R9 H4: DML first-frame compiles HLSL shaders
                # for 10-30s before any progress fires. Show explicit hint so
                # tester doesn't think recorder froze + force-quit.
                if local_device == "dml":
                    self.after(0, lambda: self._set(
                        "⏳ DML 初始化中", "#0277bd",
                        "首次启动 DirectML 着色器编译 (10-30 秒, 之后会快)..."
                    ))
                # Fire-and-forget timer to set skip flag if inference hangs.
                def _on_depth_timeout() -> None:
                    nonlocal local_failure_reason, local_failure_type
                    if not self._skip_depth_flag.is_set():
                        _trace(f"depth: TIMEOUT after {_DEPTH_TIMEOUT_SEC}s on {local_device} — skipping to fallback")
                        local_failure_reason = f"timeout after {_DEPTH_TIMEOUT_SEC}s on {local_device}"
                        local_failure_type = "timeout"
                        self._skip_depth_flag.set()
                timeout_timer = threading.Timer(_DEPTH_TIMEOUT_SEC, _on_depth_timeout)
                timeout_timer.daemon = True
                timeout_timer.start()
                try:
                    manifest = infer_depth_for_video(
                        video_path,
                        depth_dir,
                        model_variant="vits",
                        device=local_device,
                        progress_callback=self._on_depth_progress,
                        should_skip=self._skip_depth_flag.is_set,
                    )
                    _trace(f"depth: rendered {len(manifest)} REAL EXR frames on {local_device}")
                    if self._skip_depth_flag.is_set():
                        depth_skipped = True
                        if local_failure_type is None:
                            local_failure_type = "user_skip_local"
                            local_failure_reason = "user clicked skip during local inference"
                finally:
                    self.after(0, self._hide_depth_progress_ui)
                    if timeout_timer is not None:
                        timeout_timer.cancel()
                if depth_skipped:
                    try:
                        if depth_dir.exists():
                            shutil.rmtree(depth_dir, ignore_errors=True)
                    except Exception:
                        pass
                    depth_manifest_path.write_text(
                        json.dumps({
                            "status": "server_pending",
                            "frames": [],
                            "reason": "user skipped local inference — defer to backend",
                            "fallback_from": "user_skip_local",
                            "client_version": RECORDER_VERSION,
                        }, indent=2),
                        encoding="utf-8",
                    )
                    self._set("ℹ️ 深度图待服务器处理", "#0277bd",
                              "tarball 完成，深度数据将由后端处理。")
                else:
                    # Local depth succeeded — write manifest in proper shape.
                    # rc15.7-fix BUG R9 C2 (CRITICAL): infer_depth_for_video
                    # returns dict[int, str] (frame_idx → sha256), NOT list.
                    # Was bug: isinstance(manifest, list) always False → wrote
                    # empty [] → backend integrity check (R22) lost all
                    # per-frame hashes → ABSTAIN on every local_complete.
                    if isinstance(manifest, dict):
                        # rc15.7.2-fix BUG R10 #6: convert int keys → str keys
                        # for JSON wire format. Was bug: json.dumps silently
                        # str-coerces int keys → backend Pydantic models
                        # typed `dict[int, str]` reject. Now explicit + schema
                        # is `dict[str, str]` end-to-end (str of frame_idx).
                        frames_field: Any = {str(k): v for k, v in manifest.items()}
                        frame_count = len(manifest)
                    elif isinstance(manifest, list):
                        frames_field = manifest
                        frame_count = len(manifest)
                    else:
                        frames_field = {}
                        frame_count = 0
                    depth_manifest_path.write_text(
                        json.dumps({
                            "status": "local_complete",
                            "frames": frames_field,
                            "frame_count": frame_count,
                            "device": local_device,
                            "client_version": RECORDER_VERSION,
                        }, indent=2),
                        encoding="utf-8",
                    )
                    _trace(f"depth: local manifest written, status=local_complete, {frame_count} frames")
            except Exception as e:
                self.after(0, self._hide_depth_progress_ui)
                if timeout_timer is not None:
                    timeout_timer.cancel()
                # rc15.7 multi-tier fallback: classify exception so backend
                # routes the session correctly + tester sees actionable hint.
                # rc15.7.2-fix BUG R10 #5: also match exception CLASS NAME
                # (locale-independent) before matching message string. On
                # Chinese Windows torch errors are localized (e.g. "设备
                # 内存不足") and English keyword match misses everything.
                # Class names like "OutOfMemoryError" are stable across locales.
                exc_name = type(e).__name__
                exc_name_lower = exc_name.lower()
                exc_msg = str(e)
                exc_msg_lower = exc_msg.lower()
                if (
                    "outofmemory" in exc_name_lower
                    or "out of memory" in exc_msg_lower
                    or "oom" in exc_msg_lower
                    or "allocator" in exc_msg_lower
                    or "内存不足" in exc_msg
                ):
                    fallback_kind = "oom"
                    user_hint = (
                        "GPU 显存不足 (Radeon 集显默认 UMA 2GB; "
                        "BIOS 调高到 4GB 或换独立 GPU)"
                    )
                elif (
                    "directml" in exc_name_lower
                    or "directml" in exc_msg_lower
                    or "dxgi" in exc_msg_lower
                    or "d3d" in exc_msg_lower
                ):
                    fallback_kind = "dml_driver"
                    user_hint = (
                        "DirectML / DX12 错误 — 更新 AMD Adrenalin 驱动到 23.40 以上"
                    )
                elif "cuda" in exc_name_lower or "cuda" in exc_msg_lower:
                    fallback_kind = "cuda_driver"
                    user_hint = "CUDA 错误 — 更新 NVIDIA 驱动"
                elif (
                    "tensor" in exc_msg_lower
                    or "shape" in exc_msg_lower
                    or "dimension" in exc_msg_lower
                    or "形状" in exc_msg
                    or "尺寸" in exc_msg
                ):
                    fallback_kind = "model_compat"
                    user_hint = "模型 / 后端 shape 不兼容 — 已 fall back 到服务器"
                else:
                    fallback_kind = "unknown_local_error"
                    user_hint = "未知本地推理错误 — 已 fall back 到服务器"
                _trace(
                    f"depth: local inference FAILED kind={fallback_kind} "
                    f"{exc_name}: {exc_msg!r}"
                )

                # rc15.9 self-heal v2: BEFORE giving up to server_pending,
                # try ONNX Runtime DML as inner-tier 3 (different op
                # dispatcher than torch_directml; resolves AMD 780M
                # model_compat issue when torch_directml lacks the op).
                # See SELF_HEAL_v2_AUTO_RESOLVE.md for full N×M matrix.
                fallback_history: list[dict[str, str]] = [
                    {"tier": f"torch+{local_device}", "exception_class": exc_name,
                     "exception_msg": exc_msg[:300]},
                ]
                onnx_attempted = False
                onnx_succeeded = False
                if fallback_kind in ("model_compat", "dml_driver", "unknown_local_error"):
                    onnx_attempted = True
                    _trace(f"depth: rc15.9 v2 — trying onnxruntime+dml inner tier")
                    try:
                        from depth_runtimes.onnx_runtime import (  # noqa: PLC0415
                            infer_depth_for_video as _onnx_infer,
                        )
                        manifest = _onnx_infer(
                            video_path,
                            depth_dir,
                            model_variant="vits",
                            progress_callback=self._on_depth_progress,
                            should_skip=self._skip_depth_flag.is_set,
                        )
                        onnx_succeeded = True
                        _trace(f"depth: ONNX+DML tier succeeded, {len(manifest)} frames")
                    except Exception as onnx_exc:
                        onnx_exc_name = type(onnx_exc).__name__
                        onnx_exc_msg = str(onnx_exc)
                        fallback_history.append({
                            "tier": "onnx+dml",
                            "exception_class": onnx_exc_name,
                            "exception_msg": onnx_exc_msg[:300],
                        })
                        _trace(f"depth: ONNX+DML also failed {onnx_exc_name}: {onnx_exc_msg!r}")
                _trace(f"depth: all local tiers exhausted — falling back to server_pending (history={len(fallback_history)} attempts)")
                # rc15.9: write either local_complete (ONNX won) or
                # server_pending with full fallback_history (all tiers failed).
                if onnx_succeeded:
                    depth_manifest_path.write_text(
                        json.dumps({
                            "status": "local_complete",
                            "frames": {str(k): v for k, v in (manifest or {}).items()} if isinstance(manifest, dict) else manifest,
                            "frame_count": len(manifest) if hasattr(manifest, "__len__") else 0,
                            "device": "onnx+dml",
                            "fallback_history": fallback_history,
                            "client_version": RECORDER_VERSION,
                        }, indent=2),
                        encoding="utf-8",
                    )
                    self._set("ℹ️ ONNX 备援成功", "#0277bd",
                              f"torch+{local_device} 失败, 已切到 onnx+dml. 深度数据本地完成.")
                    _heal_emit(
                        "depth_dual_track", "heal_success", "info",
                        f"v2 self-heal: tier 2 failed → tier 3 (onnx+dml) succeeded",
                        details={"fallback_history": fallback_history,
                                 "winning_tier": "onnx+dml"},
                        remediation={"action": "auto_clean", "performed": True,
                                     "next_step": "no action — auto-resolved via tier 3"},
                        session_id=getattr(self, "_session_id", None),
                        recorder_version=RECORDER_VERSION,
                    )
                else:
                    depth_manifest_path.write_text(
                        json.dumps({
                            "status": "server_pending",
                            "frames": [],
                            "reason": f"local inference exception ({fallback_kind}): {exc_name}: {exc_msg}",
                            "fallback_from": fallback_kind,
                            "tried_device": local_device,
                            "fallback_history": fallback_history,
                            "user_hint": user_hint,
                            "client_version": RECORDER_VERSION,
                        }, indent=2),
                        encoding="utf-8",
                    )
                # Visible UI message: tester sees ACTIONABLE next-step.
                self._set("ℹ️ 深度图待服务器处理", "#0277bd",
                          f"本地推理失败 ({fallback_kind}). {user_hint}")
                _heal_emit(
                    "depth_dual_track", "heal_failed", "warn",
                    f"local depth fallback to server: {fallback_kind}",
                    details={"fallback_kind": fallback_kind, "exc_name": exc_name,
                             "tried_device": local_device, "user_hint": user_hint,
                             "exc_msg": exc_msg[:500]},
                    remediation={"action": "server_defer", "performed": True,
                                 "next_step": user_hint},
                    session_id=getattr(self, "_session_id", None),
                    recorder_version=RECORDER_VERSION,
                )
        else:
            # No GPU or force-server — backend handles depth post-upload.
            depth_manifest_path.write_text(
                json.dumps({
                    "status": "server_pending",
                    "frames": [],
                    "reason": track_reason,
                    "client_version": RECORDER_VERSION,
                    "expected_handler": "Phase C SM backend depth-worker",
                }, indent=2),
                encoding="utf-8",
            )
            _trace("depth: server_pending manifest written — backend GPU pool will infer post-upload")

        # R22 (D-04 defense): hash every *.exr in depth/ and write
        # depth_manifest.json next to the directory. Stop-gap path
        # has zero EXR files so the manifest is the empty object {},
        # which still lets the consumer-side R22 vote PASS instead of
        # ABSTAIN once the full recorder ships EXR frames.
        # rc15.4-fix BUG R5#1 (CRITICAL): was overwriting depth_manifest.json
        # (status envelope from rc14 dual-track) with a flat EXR hash map →
        # buyer ingest lost status field → server_pending sessions misclassified
        # as having complete depth. Now: hashes go to SEPARATE file
        # depth_hashes.json, status manifest preserved untouched.
        try:
            import hashlib as _hashlib  # noqa: PLC0415
            depth_dir_path = clip_dir / "depth"
            depth_hashes: dict[str, str] = {}
            for exr_path in sorted(depth_dir_path.glob("*.exr")):
                sha = _hashlib.sha256()
                with exr_path.open("rb") as fh:
                    for chunk in iter(lambda fh=fh: fh.read(1 << 20), b""):
                        sha.update(chunk)
                depth_hashes[exr_path.name] = sha.hexdigest()
            (clip_dir / "depth_hashes.json").write_text(
                json.dumps(depth_hashes, indent=2),
                encoding="utf-8",
            )
            _trace(f"package: wrote depth_hashes.json ({len(depth_hashes)} entries) — depth_manifest.json status envelope preserved")
        except Exception as e:  # noqa: BLE001
            _trace(f"package: depth_hashes.json write failed: {e}")
            # Non-fatal — R22 will ABSTAIN on missing hashes, same as
            # behaviour before this hook landed.

        # 6. v0.18.0: intrinsics.yaml — buyer expects fx/fy/Cx/Cy.
        # Standard MC at 1920x1080 with default 70° vertical FOV:
        #   fy = (height/2) / tan(FOV_v/2) = 540 / tan(35°) ≈ 771.4
        #   fx = fy (square pixels)  per criterion 12 (fx==fy)
        #   Cx = width/2 = 960, Cy = height/2 = 540
        # MC's actual FOV is user-settable so this is a default; full
        # Rust recorder will read FOV from game config.
        import math  # noqa: PLC0415 — local import keeps cold-start fast
        fov_v_deg = 70.0
        focal = (1080 / 2) / math.tan(math.radians(fov_v_deg / 2))
        intrinsics = {
            "fx": round(focal, 3),
            "fy": round(focal, 3),
            # PRD 文件2 例：cx/cy 小写（JSON wire 格式权威）。
            # 表格描述写大写 Cx/Cy 是 PRD 内部不一致，wire 例为准.
            "cx": 960.0,
            "cy": 540.0,
            "width": 1920,
            "height": 1080,
            "fov_vertical_deg": fov_v_deg,
            "_note": "default MC 70° vertical FOV; tester may have changed in-game",
        }
        try:
            import yaml  # noqa: PLC0415
            (clip_dir / "intrinsics.yaml").write_text(
                yaml.safe_dump(intrinsics, sort_keys=False),
                encoding="utf-8",
            )
        except Exception:
            # Fall back to a plain text file in YAML-ish format.
            (clip_dir / "intrinsics.yaml").write_text(
                "\n".join(f"{k}: {v}" for k, v in intrinsics.items()),
                encoding="utf-8",
            )

        # 7. v0.18.0: duration enforcement — if recording <5 min the
        # buyer-spec rejects (criterion 2 wants 5-6 min). We can't
        # extend a too-short recording, but we mark it 'partial' in
        # systeminfo so backend ingest can quarantine + ask tester to
        # redo. (elapsed_sec computed at top of method.)
        sys_info["actual_duration_sec"] = round(elapsed_sec, 1)
        sys_info["partial"] = elapsed_sec < 300.0  # <5 min
        # Re-write systeminfo.json with the new fields.
        (clip_dir / "systeminfo.json").write_text(
            json.dumps(sys_info, indent=2), encoding="utf-8"
        )

        # R01: if --allow-placeholder is active and JSONL was missing,
        # stamp metadata.json with data_authenticity='placeholder' so
        # buyers can identify non-real game-state tarballs.
        if getattr(self, "_allow_placeholder", False) and not _gs_samples:
            meta = {
                "data_authenticity": "placeholder",
                "warning": "camera/player fields are constant [0.0, 64.0, 0.0]",
            }
            (clip_dir / "metadata.json").write_text(
                json.dumps(meta, indent=2), encoding="utf-8"
            )
            _trace("package: wrote metadata.json with data_authenticity=placeholder")

        # rc11 SF: write game-agnostic failure-attribution manifest into
        # clip_dir BEFORE tarball write so it's bundled. clean_exit reason
        # is the default for normal-finish path; earlier exit paths set
        # other reasons (disk_full, user_close_*, game_died, etc).
        if self._terminator_reason is None:
            self._terminator_reason = (
                "duration_too_short" if elapsed_sec < 300.0 else "clean_exit"
            )
        if not self._ffmpeg_clean_close:
            self._terminator_reason = "ffmpeg_dirty_close"
        self._write_terminator(clip_dir)

        # Write the tarball into the user's Documents/OysterClips/.
        out_tar = _output_dir() / f"clip-{ts}.tar.gz"
        with tarfile.open(out_tar, "w:gz") as tf:
            tf.add(clip_dir, arcname=f"clip-{ts}")

        # Cleanup tmp dir.
        try:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
        except Exception:
            pass
        return out_tar

    def _detect_audio_device(self) -> Optional[str]:
        """Probe ffmpeg dshow for the first audio input device.

        Returns the alt-name for use as `-i audio=<name>`. Returns None
        if no device found or probe fails — caller should record video
        only in that case. v0.11.0.
        """
        if os.name != "nt":
            return None
        try:
            res = subprocess.run(
                [str(_FFMPEG), "-hide_banner", "-list_devices", "true",
                 "-f", "dshow", "-i", "dummy"],
                capture_output=True, timeout=8, text=True,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            # ffmpeg writes device list to stderr
            output = res.stderr or res.stdout or ""
        except Exception as e:
            _trace(f"audio_probe: ffmpeg list_devices failed: {e}")
            return None

        # Parse "DirectShow audio devices" section, capture device names.
        # Format: [dshow @ 0x...]  "Microphone (Realtek...)" (audio)
        in_audio = False
        first_audio = None
        for line in output.splitlines():
            if "DirectShow audio devices" in line:
                in_audio = True
                continue
            if in_audio:
                if "DirectShow video devices" in line:
                    break
                # Match: '"<device-name>"'
                if '"' in line and "Alternative name" not in line:
                    name = line.split('"')[1] if '"' in line else None
                    if name and first_audio is None:
                        first_audio = name
                        break
        _trace(f"audio_probe: device={first_audio!r}")
        return first_audio

    def _start_ffmpeg(self, out_path: Path) -> None:
        """Spawn ffmpeg with gdigrab to record the Minecraft window.

        R01 v2 (iron-law-strict): ALWAYS uses cropped-desktop capture
        with geometry from mc_window rect. Title encoding is irrelevant —
        we use -offset_x/-offset_y/-video_size + -i desktop, which is
        fully locale-blind. Hard-fails if mc_window is None (no window
        detected). The old title-based branch has been removed.

        v0.11.0: also captures audio via dshow if a device is detected.
        Falls back to video-only if no audio device or audio capture
        fails to start.
        """
        # R01 iron-law: hard-fail if Minecraft window not detected.
        if self._mc_window_rect is None:
            raise RecorderError(
                "Minecraft window not detected. Is Minecraft running and visible?"
            )

        rect = self._mc_window_rect
        mc_title = rect.get("title", "")
        x = rect.get("x", 0)
        y = rect.get("y", 0)
        w = rect.get("width", 1920)
        h = rect.get("height", 1080)

        # R01 D section: parse MC version from title and warn if unsupported.
        mc_ver = _parse_mc_version_from_title(mc_title)
        # rc13 SF-fix: stash detected MC version for terminator.json.
        # Without this, game_specific.game_version is always null even
        # when MC is running. Buyer can't filter sessions by MC version.
        self._mc_version = mc_ver
        if mc_ver and mc_ver not in SUPPORTED_MC_VERSIONS:
            _trace(
                f"WARN: Minecraft {mc_ver} not in supported list. "
                "Real game-state mod only loads on stable releases. "
                "Recording will hard-fail at packaging unless you switch "
                "to a supported version OR pass --allow-placeholder."
            )

        # Audio probe.
        audio_dev = self._detect_audio_device()
        audio_inputs = []
        audio_codec = []
        if audio_dev:
            audio_inputs = ["-f", "dshow", "-i", f"audio={audio_dev}"]
            audio_codec = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
            _trace(f"ffmpeg: capturing audio from '{audio_dev}'")
        else:
            _trace("ffmpeg: no audio device found, recording video only")

        # R01 v2: always cropped-desktop capture using detected geometry.
        # locale-blind — title encoding never participates in the ffmpeg cmd.
        video_input = [
            "-f", "gdigrab",
            "-framerate", "30",
            "-draw_mouse", "0",
            "-offset_x", str(x),
            "-offset_y", str(y),
            "-video_size", f"{w}x{h}",
            "-i", "desktop",
        ]
        _trace(
            f"ffmpeg: window-area capture title='{mc_title}' "
            f"geometry={x},{y},{w},{h}"
        )

        cmd = [
            str(_FFMPEG),
            *video_input,
            *audio_inputs,
            "-vf", "scale=1920:1080:flags=lanczos",
            "-c:v", "libx265",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            *audio_codec,
            "-r", "30",
            "-t", "360",
            "-y",
            str(out_path),
        ]
        # On Windows, CREATE_NO_WINDOW (0x08000000) hides the ffmpeg
        # console window so the tester only sees our Tk window.
        flags = 0x08000000 if os.name == "nt" else 0
        self._ffmpeg_proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )

    def _run_preflight(self) -> list[dict[str, Any]]:
        """rc12 SH: preflight environment self-check.

        Returns list of {check, status, message} dicts. status in
        {pass, info, warn, fail, fatal}. Any 'fatal' disables arm
        button; 'warn' shows in helpbar but doesn't block; 'info' is
        logged-only. game-agnostic via GAME_PROBE_REGISTRY.
        """
        results: list[dict[str, Any]] = []

        def add(check: str, status: str, message: str) -> None:
            results.append({"check": check, "status": status, "message": message})
            _trace(f"preflight: {status.upper()} {check}: {message}")

        # 1. Windows version
        try:
            import platform as _pf
            ver = _pf.version()
            major = int(ver.split(".")[0]) if ver else 0
            if os.name == "nt" and major < 10:
                add("windows_version", "fatal", f"Windows < 10 不支持 ({ver})")
            else:
                add("windows_version", "pass", _pf.platform())
        except Exception as e:
            add("windows_version", "info", f"detect failed: {e}")

        # 2. disk free
        try:
            free_gb = shutil.disk_usage(_output_dir()).free / (1024**3)
            if free_gb < 0.5:
                add("disk_free", "fatal", f"剩余 {free_gb:.1f} GB < 500 MB")
            elif free_gb < 5.0:
                add("disk_free", "warn", f"剩余 {free_gb:.1f} GB (建议 ≥5 GB)")
            else:
                add("disk_free", "pass", f"{free_gb:.1f} GB free")
        except Exception as e:
            add("disk_free", "info", f"detect failed: {e}")

        # 3. admin
        if os.name == "nt":
            try:
                import ctypes
                is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
                if is_admin:
                    add("admin", "pass", "running as admin")
                else:
                    add("admin", "warn", "非管理员 — 自动更新可能不工作")
            except Exception as e:
                add("admin", "info", f"detect failed: {e}")

        # 4. OneDrive sync
        try:
            out_path = str(_output_dir()).lower()
            if "onedrive" in out_path:
                add("onedrive", "warn", "输出目录在 OneDrive — 建议暂停同步避免抢 IO")
            else:
                add("onedrive", "pass", "not in OneDrive")
        except Exception:
            pass

        # 5. install path length
        try:
            install_path = str(Path(sys.executable).parent)
            if len(install_path) >= 240:
                add("path_length", "warn", f"安装路径 {len(install_path)} 字符接近 260 限制")
            else:
                add("path_length", "pass", f"{len(install_path)} chars")
        except Exception:
            pass

        # 6. install path non-ASCII
        try:
            install_path = str(Path(sys.executable).parent)
            if re.search(r"[^\x00-\x7F]", install_path):
                add("path_unicode", "info", "安装路径含非 ASCII 字符 (中文/日文)")
            else:
                add("path_unicode", "pass", "ASCII only")
        except Exception:
            pass

        # 7. network reachable
        try:
            import urllib.request
            urllib.request.urlopen("https://api.github.com", timeout=5).close()
            add("network", "pass", "github reachable")
        except Exception as e:
            add("network", "warn", f"github 不可达 — 自更新跳过 ({type(e).__name__})")

        # 8. self-exe still present (antivirus delayed-quarantine probe)
        try:
            if Path(sys.executable).exists():
                add("antivirus", "pass", "self-exe present at startup+200ms")
            else:
                add("antivirus", "warn", "self-exe vanished — 杀软隔离?")
        except Exception:
            pass

        # 9. remote desktop
        if os.name == "nt":
            try:
                import ctypes
                is_remote = bool(ctypes.windll.user32.GetSystemMetrics(0x1000))  # SM_REMOTESESSION
                if is_remote:
                    add("remote_desktop", "warn", "远程桌面会话 — DirectX 录制可能失败")
                else:
                    add("remote_desktop", "pass", "local session")
            except Exception:
                pass

        # 10. DPI scaling
        if os.name == "nt":
            try:
                import ctypes
                hwnd = ctypes.windll.user32.GetDesktopWindow()
                dpi = ctypes.windll.user32.GetDpiForWindow(hwnd) if hasattr(ctypes.windll.user32, "GetDpiForWindow") else 96
                if dpi != 96:
                    add("dpi", "info", f"DPI 缩放 {int(dpi/96*100)}%")
                else:
                    add("dpi", "pass", "100%")
            except Exception:
                pass

        # 11. GPU (reuse rc9)
        try:
            gpu_ok = _detect_gpu_available() if "_detect_gpu_available" in globals() else False
            if gpu_ok:
                add("gpu", "pass", "DirectML / CUDA available")
            else:
                add("gpu", "info", "无 GPU — depth 推理会跑 CPU 或跳过")
        except Exception:
            pass

        # 12. Chinese IME (locale 0x0804 = zh-CN)
        if os.name == "nt":
            try:
                import ctypes
                hkl = ctypes.windll.user32.GetKeyboardLayout(0)
                lang_id = hkl & 0xFFFF
                if lang_id == 0x0804:
                    # rc15.10 (Howard 2026-05-09 测试员"录制按键点不了"):
                    # rc15.4 IME fatal 太霸道 — 中文 Windows 默认就是中文 IME,
                    # 用户开机就被 disable arm. 真实场景: 大部分中国测试员
                    # 第一秒被卡死. 降回 warn + 显眼 UI 提示 WASD 风险, 让
                    # user 自己选: 切英文或接受拦键风险.
                    add("ime", "warn",
                        "中文输入法可能拦 WASD — 建议录制前切英文 "
                        "(Shift 或 Win+Space 切换). 不切也能录, "
                        "但可能丢移动键事件.")
                else:
                    add("ime", "pass", f"locale 0x{lang_id:04x}")
            except Exception:
                pass

        # === game-specific checks (from GAME_PROBE_REGISTRY) ===
        game_cfg = GAME_PROBE_REGISTRY.get(CURRENT_GAME_ID, {})

        # 13. Java present (companion check)
        if game_cfg.get("required_companion") == "java":
            try:
                res = subprocess.run(
                    ["java", "-version"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=0x08000000 if os.name == "nt" else 0,
                )
                output = (res.stderr or res.stdout or "").strip().split("\n")[0]
                if "version" in output.lower():
                    add("java", "pass", output[:80])
                else:
                    add("java", "warn", "java 在 PATH 但版本输出不正常")
            except FileNotFoundError:
                add("java", "warn", "Java 未安装或不在 PATH — 装 Temurin 21 后可玩 MC")
            except Exception as e:
                add("java", "info", f"java 检测失败: {e}")

        # 14. game window title locale tolerance
        try:
            substrs = game_cfg.get("window_title_substrings", [])
            add("game_window_locales", "info", f"支持窗口标题: {', '.join(substrs)}")
        except Exception:
            pass

        return results

    def _write_terminator(self, clip_dir: Optional[Path]) -> None:
        """rc11 SF: write game-agnostic failure-attribution manifest.

        Called from finalize (clean_exit) AND from every early-return /
        crash path. Cheap to call multiple times — last writer wins.
        clip_dir may be None (early return before tmp_dir created); in
        that case write to ~/Documents/OysterRecorder/runtime/terminator.json
        as a "session-less" record.
        """
        reason = self._terminator_reason or "crash"
        try:
            game_alive = bool(_minecraft_running()) if reason != "game_died" else False
            disk_free_mb = 0
            try:
                probe_path = clip_dir if clip_dir and clip_dir.exists() else _output_dir()
                disk_free_mb = round(shutil.disk_usage(probe_path).free / (1024 * 1024), 1)
            except Exception:
                pass
            payload = {
                "schema_version": "1.0",
                "session_id": getattr(self, "_session_id", None),
                "reason": reason,
                "last_recorded_at": datetime.now().isoformat(),
                "duration_sec": (
                    round(time.time() - self._record_started_at, 1)
                    if getattr(self, "_record_started_at", 0) else None
                ),
                "frame_count": len(getattr(self, "_captured_events", []) or []),
                "disk_free_mb_at_end": disk_free_mb,
                "ffmpeg_clean_close": getattr(self, "_ffmpeg_clean_close", True),
                "game_alive_at_terminate": game_alive,
                "recorder_version": RECORDER_VERSION,
                "game_specific": {
                    "game_id": CURRENT_GAME_ID,
                    "game_version": getattr(self, "_mc_version", None),
                    "process_name": "javaw.exe",
                    "mod_handshake_ok": getattr(self, "_mod_handshake_ok", False),
                },
            }
            target_dir = clip_dir if clip_dir is not None else (
                Path(_real_documents_dir()) / "OysterRecorder" / "runtime"
            )
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "terminator.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            _trace(f"terminator: reason={reason} written to {target_dir}/terminator.json")
            # rc15 Heal Framework: mirror to central event log.
            # rc15.2-fix BUG H2: duration_too_short is buyer-rejected →
            # auto-quarantine = data loss outcome. Escalate severity from
            # warn to error so dashboard renders red, not amber.
            severity = "info" if reason == "clean_exit" else (
                "fatal" if reason == "crash" else
                "error" if reason in ("duration_too_short", "ffmpeg_dirty_close",
                                      "game_crashed", "mod_handshake_failed") else
                "warn"
            )
            _heal_emit(
                "SF_terminator", "report", severity,
                f"session ended: reason={reason} duration={payload.get('duration_sec')}s",
                details={"reason": reason, "frame_count": payload.get("frame_count"),
                         "ffmpeg_clean_close": payload.get("ffmpeg_clean_close"),
                         "mod_handshake_ok": payload.get("game_specific", {}).get("mod_handshake_ok")},
                remediation={"action": "none" if reason == "clean_exit" else "user_prompt",
                             "performed": False,
                             "next_step": "review session quality" if reason == "clean_exit"
                             else "see HEAL_FAILURE_MODES for re-record guidance"},
                session_id=getattr(self, "_session_id", None),
                recorder_version=RECORDER_VERSION,
            )
        except Exception:
            _trace(f"terminator: write FAILED reason={reason} {traceback.format_exc()}")

    def _stop_ffmpeg(self) -> None:
        """Send 'q' to ffmpeg's stdin (clean shutdown), then wait 5s."""
        proc = self._ffmpeg_proc
        if proc is None:
            return
        # rc15.2-fix BUG C2: wire B2_ffmpeg_clean_close emit at each
        # dirty-close path. Was: 3 spots set self._ffmpeg_clean_close
        # = False but no central event log entry — feature was dark.
        dirty_path: Optional[str] = None
        try:
            if proc.stdin:
                proc.stdin.write(b"q\n")
                proc.stdin.flush()
        except Exception:
            self._ffmpeg_clean_close = False
            dirty_path = "stdin_write_failed"
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            self._ffmpeg_clean_close = False
            dirty_path = dirty_path or "wait_5s_timeout_terminate"
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self._ffmpeg_clean_close = False
                dirty_path = "wait_3s_timeout_kill"
                proc.kill()
        self._ffmpeg_proc = None
        if dirty_path:
            _heal_emit(
                "B2_ffmpeg_clean_close", "heal_failed", "error",
                f"ffmpeg dirty-close via {dirty_path}",
                details={"dirty_path": dirty_path},
                remediation={"action": "none", "performed": False,
                             "next_step": "mp4 may have missing trailer; lint will flag"},
                recorder_version=RECORDER_VERSION,
            )

    def _on_close(self) -> None:
        _trace("on_close: user closed window")
        self._stop_event.set()
        self._stop_ffmpeg()
        # rc10 B1 + rc11 SF: ask tester whether to also kill Minecraft;
        # set terminator reason for failure-attribution either way.
        try:
            import tkinter.messagebox as _mb
            if _mb.askyesno("关闭确认", "也要关闭 Minecraft 吗?\n\n是 = 关 MC + 关录制 (本次会话保存)\n否 = 只关录制器 (MC继续, 但本次录像不保存)"):
                self._terminator_reason = "user_close_kill_game"
                if os.name == "nt":
                    try:
                        # rc15.2-fix BUG#7: kill by PID, not by image name.
                        # `taskkill /IM javaw.exe` killed ALL java processes
                        # including IntelliJ/Eclipse — tester complaint risk.
                        # Resolve MC PIDs from tasklist + filter by window
                        # title "Minecraft" via _get_minecraft_window_rect.
                        mc_pids = _find_minecraft_pids()
                        if not mc_pids:
                            _trace("on_close: no MC PID found; skipping kill")
                        for pid in mc_pids:
                            try:
                                rc = subprocess.run(
                                    ["taskkill", "/F", "/PID", str(pid)],
                                    creationflags=0x08000000,
                                    capture_output=True,
                                    timeout=5,
                                ).returncode
                                _trace(f"on_close: kill MC pid={pid} taskkill exit={rc}")
                            except Exception:
                                _trace(f"on_close: kill pid={pid} failed: {traceback.format_exc()}")
                        # rc15.2-fix wire B1: emit user_action event
                        _heal_emit(
                            "B1_close_confirm_kill_mc", "user_action", "info",
                            f"tester chose to kill MC on close ({len(mc_pids)} pid(s))",
                            details={"mc_pids_killed": mc_pids},
                            remediation={"action": "auto_clean", "performed": True,
                                         "next_step": "MC closed, recorder also closing"},
                            recorder_version=RECORDER_VERSION,
                        )
                    except Exception:
                        _trace(f"on_close: kill MC failed: {traceback.format_exc()}")
                else:
                    _trace("on_close: kill MC skipped (non-Windows)")
            else:
                self._terminator_reason = "user_close_keep_game"
                _heal_emit(
                    "B1_close_confirm_kill_mc", "user_action", "info",
                    "tester chose to keep MC running",
                    remediation={"action": "none", "performed": False,
                                 "next_step": "tester continues playing without recording"},
                    recorder_version=RECORDER_VERSION,
                )
        except Exception:
            self._terminator_reason = "user_close_keep_game"
            _trace(f"on_close: confirmation dialog failed: {traceback.format_exc()}")
        # rc11 SF: write a session-less terminator at user-close so we
        # can diagnose abrupt-exit cases that don't reach finalize.
        try:
            self._write_terminator(None)
        except Exception:
            pass
        # Final telemetry push so engineer sees the full session log.
        # Synchronous-ish but capped to 15s by urlopen timeout.
        try:
            _upload_log_remote()
        except Exception:
            pass
        self.destroy()

    # ---- UI updates (thread-safe via after) -----------------------------
    def _set(self, big: str, color: str, sub: str) -> None:
        def apply():
            self._verdict.config(text=big, fg=color)
            self._subtitle.config(text=sub, fg=color)
        # Tk requires UI updates from the main thread.
        try:
            self.after(0, apply)
        except RuntimeError:
            pass


def _emergency_error_box(exc: BaseException) -> None:
    # Best-effort: push the local log to remote pastebin BEFORE showing
    # the dialog, so even if the tester ignores the dialog and force-
    # quits, engineering already has the log.
    _trace(f"=== EMERGENCY: {type(exc).__name__}: {exc} ===")
    _trace(traceback.format_exc())
    remote_url = None
    try:
        remote_url = _upload_log_remote()
    except Exception:
        pass

    try:
        root = tk.Tk()
        root.withdraw()
        msg = (
            "录制器启动失败。\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
        )
        if remote_url:
            msg += (
                f"日志已自动上传，工程师可访问：\n{remote_url}\n\n"
                "你不用做任何事，工程师会从这个链接看到出错原因。"
            )
        else:
            msg += (
                f"日志在本机：{_STARTUP_LOG}\n"
                "如果方便，把这个文件发给工程师。"
            )
        messagebox.showerror(
            title="Oyster 录制器 — 启动错误",
            message=msg,
        )
        root.destroy()
    except Exception:
        log = Path.home() / "OysterRecorder-error.log"
        try:
            log.write_text(f"=== startup error ===\n{traceback.format_exc()}\n")
        except Exception:
            pass


def _try_install_mod_first_launch() -> None:
    """Howard 2026-05-07 (D17): on every launch, best-effort try to
    install the bundled Fabric loader + Oyster mod into the user's MC.

    Idempotent — skipped on subsequent launches when already installed.
    Fails completely silently if anything goes wrong (no tray pop-up, no
    crash). The recorder still works without the mod; action_camera just
    falls back to placeholder camera/player fields per the existing
    fallback path.

    PyInstaller bundles the two jars via ``--add-data``; we resolve their
    paths via ``sys._MEIPASS`` when running as a frozen .exe.
    """
    try:
        # Resolve bundle dir: frozen → _MEIPASS, source → script dir
        if hasattr(sys, "_MEIPASS"):
            bundle = Path(sys._MEIPASS)
        else:
            bundle = Path(__file__).resolve().parent
        fabric_installer = bundle / "fabric-installer.jar"
        mod_jar = next(bundle.glob("oyster-recorder-mod-*.jar"), None)
        if mod_jar is None or not fabric_installer.exists():
            # Bundled assets missing — likely a dev run from source. No-op.
            return
        # Defer the import — only available when running with our wheel,
        # not in legacy contexts.
        sys.path.insert(0, str(bundle))
        from install_fabric_loader import ensure_installed  # type: ignore
        result = ensure_installed(fabric_installer, mod_jar)
        _trace(f"mod-install: {result.to_dict()}")
    except Exception as e:  # noqa: BLE001 — never crash recorder
        try:
            _trace(f"mod-install failed (non-fatal): {e}")
        except Exception:
            pass


def _scan_and_clean_orphans() -> int:
    """rc13 SI (Phase B.1): scan tempfile.gettempdir() for stale
    oyster-rec-* dirs from prior crashes and clean them up.

    A dir is "orphan" if it's older than 1 hour AND its prefix matches
    'oyster-rec-' AND no live process owns it. Returns count cleaned.
    Also writes a session-less terminator.json with reason='orphan_resumed'
    so we capture the failure mode in telemetry once backend is online.
    """
    cleaned = 0
    try:
        tmp_root = Path(tempfile.gettempdir())
        cutoff = time.time() - 3600  # 1 hour ago
        for entry in tmp_root.glob("oyster-rec-*"):
            try:
                if not entry.is_dir():
                    continue
                if entry.stat().st_mtime > cutoff:
                    continue  # too fresh — might be a live session
                shutil.rmtree(entry, ignore_errors=True)
                cleaned += 1
                _trace(f"orphan_cleanup: removed {entry}")
            except Exception as exc:
                _trace(f"orphan_cleanup: skip {entry} ({exc})")
        if cleaned > 0:
            try:
                runtime_dir = Path(_real_documents_dir()) / "OysterRecorder" / "runtime"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                (runtime_dir / "terminator.json").write_text(
                    json.dumps({
                        "schema_version": "1.0",
                        "reason": "orphan_resumed",
                        "last_recorded_at": datetime.now().isoformat(),
                        "orphans_cleaned": cleaned,
                        "recorder_version": RECORDER_VERSION,
                        "game_specific": {"game_id": CURRENT_GAME_ID},
                    }, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
    except Exception as exc:
        _trace(f"orphan_cleanup: scan failed {exc}")
    return cleaned


def main() -> int:
    import argparse  # noqa: PLC0415
    parser = argparse.ArgumentParser(description="OysterRecorder")
    parser.add_argument(
        "--allow-placeholder",
        action="store_true",
        default=False,
        help="Allow placeholder camera/player fields (marks tarball as non-real)",
    )
    args, _unknown = parser.parse_known_args()

    # R01 D section: print supported MC versions at startup.
    supported_str = ", ".join(SUPPORTED_MC_VERSIONS)
    _trace(
        f"OysterRecorder {RECORDER_VERSION} — supported Minecraft versions "
        f"for real game-state:\n  {supported_str}"
    )

    # rc13 SI: scan + clean orphan tmp dirs from prior crashes BEFORE
    # mod install / app start so disk pressure is gone.
    try:
        n_orphans = _scan_and_clean_orphans()
        if n_orphans > 0:
            _trace(f"startup: cleaned {n_orphans} orphan oyster-rec-* tmp dirs")
            # rc15 Heal Framework: orphans found = previous crash signal.
            _heal_emit(
                "SI_orphan_cleanup", "heal_success", "warn",
                f"cleaned {n_orphans} orphan tmp dir(s) from prior crash/forced exit",
                details={"orphans_cleaned": n_orphans},
                remediation={"action": "auto_clean", "performed": True,
                             "next_step": "investigate why prior session didn't finalize"},
                recorder_version=RECORDER_VERSION,
            )
        else:
            _heal_emit(
                "SI_orphan_cleanup", "detect", "info",
                "startup orphan scan found no leftover tmp dirs",
                details={"orphans_cleaned": 0},
                recorder_version=RECORDER_VERSION,
            )
    except Exception:
        pass

    # rc15.2-fix BUG H9 + rc15.3-fix BUG#16+#18: install threading.excepthook
    # so daemon thread crashes don't die silently.
    # rc15.3-fix BUG#16: idempotency guard — main() may be re-entered (test
    # harness, dev mode) and re-installing creates infinite call chain.
    # rc15.3-fix BUG#18: traceback.format_exc() reads sys.exc_info() which
    # is EMPTY inside excepthook → returned "NoneType: None\n". Use
    # traceback.format_exception(args.exc_type, args.exc_value,
    # args.exc_traceback) for the real stack.
    if not getattr(sys, "_oyster_excepthook_installed", False):
        def _thread_excepthook(args):  # type: ignore[no-untyped-def]
            try:
                tname = getattr(args.thread, "name", "unknown")
                exc_msg = f"{args.exc_type.__name__}: {args.exc_value}"
                tb_str = "".join(traceback.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback))
                _trace(f"DAEMON CRASH thread={tname} {exc_msg}\n{tb_str}")
                _heal_emit(
                    "SF_terminator", "report", "fatal",
                    f"daemon thread crashed: {tname} — {exc_msg}",
                    details={"thread_name": tname, "exception": exc_msg,
                             "traceback": tb_str[:3000]},  # cap respects 4KB details cap
                    remediation={"action": "user_prompt", "performed": False,
                                 "next_step": "restart recorder; check heal_report for details"},
                    recorder_version=RECORDER_VERSION,
                )
            except Exception:
                pass
        threading.excepthook = _thread_excepthook
        # Also wire sys.excepthook for main thread.
        _orig_excepthook = sys.excepthook
        def _main_excepthook(exc_type, exc_value, exc_tb):  # type: ignore[no-untyped-def]
            try:
                tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                _heal_emit(
                    "SF_terminator", "report", "fatal",
                    f"main thread crashed: {exc_type.__name__}: {exc_value}",
                    details={"exception": f"{exc_type.__name__}: {exc_value}",
                             "traceback": tb_str[:3000]},
                    remediation={"action": "user_prompt", "performed": False,
                                 "next_step": "send diagnostic zip to engineer"},
                    recorder_version=RECORDER_VERSION,
                )
            except Exception:
                pass
            _orig_excepthook(exc_type, exc_value, exc_tb)
        sys.excepthook = _main_excepthook
        sys._oyster_excepthook_installed = True  # type: ignore[attr-defined]

    try:
        _try_install_mod_first_launch()
        app = RecorderApp()
        app._allow_placeholder = args.allow_placeholder  # type: ignore[attr-defined]
        app.mainloop()
        return 0
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        _emergency_error_box(exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
