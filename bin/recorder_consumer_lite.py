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
     javaw.exe/java.exe (Minecraft Java game) or Minecraft.exe (Bedrock)
  4. When a real Minecraft game window is visible and stable → spawns
     bundled ffmpeg.exe to record the Minecraft window with H.265, saving to
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
import math
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
import types
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

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
RECORDER_VERSION = "lite-v0.28.0-rc9"

# R01 iron-law: supported MC versions for real game-state Fabric mod.
# Kept in sync with .github/workflows/build-mc-mod.yml matrix.
SUPPORTED_MC_VERSIONS = [
    "1.20.1",
    "1.20.2",
    "1.20.4",
    "1.20.6",
    "1.21.1",
    "1.21.2",
    "1.21.3",
    "1.21.4",
    "1.21.5",
]


def _depth_mode() -> str:
    """Return the depth processing mode for this recorder process."""

    return os.environ.get("OYSTER_DEPTH_MODE", "server").strip().lower()


def _client_depth_inference_enabled() -> bool:
    """Only allow legacy local depth when explicitly requested by engineers."""

    if os.environ.get("OYSTER_ALLOW_CLIENT_DEPTH", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "legacy-local",
    }:
        return True
    return _depth_mode() in {"client", "local", "legacy-local"}


class RecorderError(RuntimeError):
    """Hard-fail error for iron-law violations (no silent fallback)."""


RELEASES_API = (
    "https://api.github.com/repos/howardleegeek/oyster-gamedata-pipeline" "/releases?per_page=20"
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
    """Compare recorder-vA.B.C semver-ish tags. Returns True if latest > current."""

    def _key(t: str) -> tuple[int, ...]:
        v = t.replace("recorder-v", "").split(".")
        out = []
        for piece in v:
            try:
                out.append(int(piece))
            except ValueError:
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
        _trace(
            "update: SKIP — onedir bundle, refuses single-.exe overwrite (would orphan _internal/)"
        )
        return False
    try:
        import urllib.request

        new_path = Path(tempfile.gettempdir()) / "OysterRecorder-update.exe"
        _trace(f"update: downloading {new_exe_url} -> {new_path}")
        with urllib.request.urlopen(new_exe_url, timeout=120) as resp, new_path.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
        size = new_path.stat().st_size
        _trace(f"update: downloaded {size} bytes")
        if size < 1_000_000:  # under 1 MB is suspicious — likely a 4xx error page
            _trace(
                f"update: ABORT — downloaded file too small ({size} bytes), likely error page not exe"
            )
            return False
    except Exception as e:
        # v0.20.1: explicit exception type for diagnostic clarity
        _trace(f"update: download failed [{type(e).__name__}]: {e}")
        return False

    current_exe = Path(sys.executable)
    bat_path = Path(tempfile.gettempdir()) / "OysterRecorder-update.bat"
    bat_body = (
        "@echo off\r\n"
        "rem Wait for the recorder to fully exit before swapping.\r\n"
        "timeout /t 3 /nobreak > nul\r\n"
        f'move /Y "{new_path}" "{current_exe}"\r\n'
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
        return True
    except Exception as e:
        _trace(f"update: stage failed {e}")
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
        import platform
        import zipfile

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
except Exception as _tk_exc:
    _trace(f"tkinter FAILED:\n{traceback.format_exc()}")
    _TK_IMPORT_ERROR = _tk_exc

    class _MissingTkWidget:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError(f"tkinter unavailable: {_TK_IMPORT_ERROR}") from _TK_IMPORT_ERROR

        def __getattr__(self, _name: str) -> Any:
            return lambda *a, **kw: None

    tk = types.SimpleNamespace(  # type: ignore[assignment]
        Tk=_MissingTkWidget,
        Frame=_MissingTkWidget,
        Label=_MissingTkWidget,
        Button=_MissingTkWidget,
        Checkbutton=_MissingTkWidget,
        BooleanVar=_MissingTkWidget,
    )
    messagebox = types.SimpleNamespace(showerror=lambda *a, **kw: None)
    ttk = types.SimpleNamespace(Progressbar=_MissingTkWidget)

# pynput is lazily imported in InputCapture.start() so that startup of
# the .exe doesn't fail if pynput's hooks misbehave on a tester's box.
# PyInstaller still picks it up because we --hidden-import it in the
# workflow.
try:
    from raw_input_capture import RawInputCapture
except Exception as _raw_input_capture_exc:  # noqa: BLE001 - additive capture only
    RawInputCapture = None  # type: ignore[assignment]
    _RAW_INPUT_CAPTURE_IMPORT_ERROR = _raw_input_capture_exc
else:
    _RAW_INPUT_CAPTURE_IMPORT_ERROR = None

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
_FFPROBE = _BUNDLE_ROOT / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
if not _FFPROBE.exists():
    found = shutil.which("ffprobe")
    _FFPROBE = Path(found) if found else _FFPROBE  # may not exist on dev box

_FFMPEG_CLEAN_QUIT_TIMEOUT_SEC = 60.0
_FFMPEG_FORCE_STOP_TIMEOUT_SEC = 3.0
_MP4_REMUX_REPAIR_TIMEOUT_SEC = 180.0
_FFMPEG_DURATION_ARG = "-t"
_CAPTURE_MODE_ENV = "OYSTER_CAPTURE_MODE"
_VALID_CAPTURE_MODES = {"auto", "ddagrab", "gdigrab"}
_CAPTURE_STARTUP_CHECK_SEC = 0.75
_CAPTURE_ENTROPY_DELAY_SEC = 5.0
_CAPTURE_ENTROPY_SAMPLE_SEC = 5.0
_CAPTURE_ENTROPY_SAMPLE_FRAMES = 5
_CAPTURE_ENTROPY_WIDTH = 160
_CAPTURE_ENTROPY_HEIGHT = 90
_CAPTURE_ENTROPY_THRESHOLD_BITS = 1.0


def _normalize_capture_mode(value: Optional[str]) -> str:
    raw = (value or "auto").strip().lower()
    if raw in _VALID_CAPTURE_MODES:
        return raw
    _trace(f"WARNING: invalid {_CAPTURE_MODE_ENV}={value!r}; defaulting to auto")
    return "auto"


_CAPTURE_MODE = _normalize_capture_mode(os.environ.get(_CAPTURE_MODE_ENV, "auto"))


def _mp4_has_moov_atom(mp4_path: Path) -> bool:
    """Return True when an MP4 has a top-level moov atom."""

    try:
        file_size = mp4_path.stat().st_size
        with mp4_path.open("rb") as fh:
            offset = 0
            while offset + 8 <= file_size:
                fh.seek(offset)
                header = fh.read(8)
                if len(header) < 8:
                    return False
                box_size = int.from_bytes(header[:4], "big")
                box_type = header[4:8]
                header_size = 8
                if box_type == b"moov":
                    return True
                if box_size == 1:
                    ext_size = fh.read(8)
                    if len(ext_size) < 8:
                        return False
                    box_size = int.from_bytes(ext_size, "big")
                    header_size = 16
                elif box_size == 0:
                    return False
                if box_size < header_size:
                    return False
                offset += box_size
    except OSError as exc:
        _trace(f"mp4: unable to inspect moov atom for {mp4_path}: {exc}")
        return False
    return False


def _attempt_mp4_remux_repair(mp4_path: Path) -> bool:
    """Best-effort remux repair for an MP4 killed before moov finalization."""

    fixed_path = mp4_path.with_name(f"{mp4_path.stem}.fixed{mp4_path.suffix}")
    try:
        fixed_path.unlink(missing_ok=True)
    except OSError:
        pass

    cmd = [
        str(_FFMPEG),
        "-y",
        "-i",
        str(mp4_path),
        "-c",
        "copy",
        str(fixed_path),
    ]
    _trace(f"mp4: missing moov after forced ffmpeg stop; attempting remux repair: {cmd}")
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=_MP4_REMUX_REPAIR_TIMEOUT_SEC,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - repair is best-effort
        _trace(f"mp4: remux repair failed to run: {type(exc).__name__}: {exc}")
        return False

    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")[-500:]
        _trace(f"mp4: remux repair failed rc={result.returncode}: {stderr}")
        try:
            fixed_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    if not fixed_path.exists() or not _mp4_has_moov_atom(fixed_path):
        _trace("mp4: remux repair did not produce a playable MP4 with moov atom")
        try:
            fixed_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    try:
        fixed_path.replace(mp4_path)
    except OSError as exc:
        _trace(f"mp4: remux repair could not replace original: {exc}")
        return False
    _fsync_file(mp4_path)
    _fsync_dir(mp4_path.parent)
    _trace(f"mp4: remux repair succeeded and replaced {mp4_path}")
    return True


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
    return docs


def _fsync_dir(path: Path) -> None:
    """Best-effort directory fsync after atomic rename."""

    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _fsync_file(path: Path) -> None:
    """Best-effort fsync for files produced by external processes."""

    try:
        with path.open("rb") as fh:
            os.fsync(fh.fileno())
    except OSError:
        pass


def _atomic_write_text(path: Path, data: str, *, encoding: str = "utf-8") -> None:
    """Write text through a same-directory temp file, then os.replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
        _fsync_dir(path.parent)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, data: Any, *, indent: int | None = 2) -> None:
    kwargs: dict[str, Any] = {"ensure_ascii": False}
    if indent is None:
        kwargs["separators"] = (",", ":")
    else:
        kwargs["indent"] = indent
    _atomic_write_text(path, json.dumps(data, **kwargs), encoding="utf-8")


def _atomic_write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    _atomic_write_text(path, body, encoding="utf-8")


def _ensure_recording_mp4_alias(clip_dir: Path) -> Path:
    """Expose lite ``video.mp4`` under the canonical ``recording.mp4`` name."""

    source = Path(clip_dir) / "video.mp4"
    alias = Path(clip_dir) / "recording.mp4"
    if not source.is_file():
        raise FileNotFoundError(f"video.mp4 missing in {clip_dir}")

    if alias.exists():
        try:
            if alias.samefile(source):
                return alias
        except OSError:
            pass
        if alias.is_dir():
            raise IsADirectoryError(f"recording.mp4 alias path is a directory: {alias}")
        alias.unlink()
    elif alias.is_symlink():
        alias.unlink()

    try:
        os.link(source, alias)
    except (OSError, NotImplementedError):
        shutil.copy2(source, alias)
    _fsync_file(alias)
    _fsync_dir(alias.parent)
    return alias


def _write_session_complete_marker(session_dir: Path) -> None:
    marker = {
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "recorder_version": RECORDER_VERSION,
    }
    _atomic_write_json(session_dir / ".session_complete", marker)


def _generate_silent_audio_fallback(session_dir: Path, video_path: Path) -> None:
    """All audio probes failed: write silent stereo FLAC matching video duration."""

    def _fail(error: str, *, duration_sec: float = 0.0) -> None:
        _atomic_write_json(
            session_dir / "audio_check.json",
            {
                "audio_source": "failed",
                "reason": "silent audio fallback generation failed",
                "audio_file": "audio.flac",
                "duration_sec": duration_sec,
                "size_bytes": 0,
                "error": error,
                "method": "ffmpeg lavfi anullsrc fallback (no device)",
            },
        )
        _trace(f"silent_audio_fallback: failed ({error})")
        raise RecorderError(f"silent audio fallback failed: {error}")

    try:
        probe = subprocess.run(
            [
                str(_FFPROBE),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fail(f"ffprobe failed: {exc}")

    if probe.returncode != 0:
        stderr = (probe.stderr or "").strip()
        _fail(stderr or f"ffprobe exited with code {probe.returncode}")

    try:
        duration = float(probe.stdout.strip())
    except (ValueError, AttributeError):
        _fail(f"ffprobe returned invalid duration: {probe.stdout!r}")
    if duration <= 0:
        _fail(f"ffprobe returned non-positive duration: {duration!r}")

    out_path = session_dir / "audio.flac"
    try:
        result = subprocess.run(
            [
                str(_FFMPEG),
                "-hide_banner",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                _FFMPEG_DURATION_ARG,
                str(duration),
                "-c:a",
                "flac",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fail(f"ffmpeg failed: {exc}", duration_sec=duration)

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        _fail(stderr or f"ffmpeg exited with code {result.returncode}", duration_sec=duration)

    size_bytes = out_path.stat().st_size if out_path.is_file() else 0
    if size_bytes <= 0:
        stderr = (result.stderr or "").strip()
        detail = stderr or "ffmpeg exited successfully but audio.flac is missing or empty"
        _fail(detail, duration_sec=duration)

    _atomic_write_json(
        session_dir / "audio_check.json",
        {
            "audio_source": "silent_fallback",
            "reason": "no audio device available on tester machine",
            "audio_file": "audio.flac",
            "duration_sec": duration,
            "size_bytes": size_bytes,
            "rms_db": -120.0,
            "peak_db": -120.0,
            "snr_db": 0.0,
            "is_silent": True,
            "continuous": True,
            "max_silence_gap_s": duration,
            "longest_silence_s": duration,
            "method": "ffmpeg lavfi anullsrc fallback (no device)",
        },
    )
    _trace(f"silent_audio_fallback: wrote {duration:.1f}s silent FLAC")


class AudioCaptureMode:
    """Recorder audio source identifiers, ordered from most to least precise."""

    APPLICATION = "application_audio_capture"
    DESKTOP = "desktop_audio_output"
    INPUT = "input_device"
    NONE = "none"


@dataclass(frozen=True)
class AudioSourceProbe:
    """One attempted audio source in the ffmpeg priority chain."""

    mode: str
    label: str
    available: bool
    ffmpeg_args: tuple[str, ...] = ()
    device: Optional[str] = None
    reason: str = ""
    fallback_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "label": self.label,
            "available": self.available,
            "device": self.device,
            "reason": self.reason,
            "fallback_used": self.fallback_used,
            "ffmpeg_args": list(self.ffmpeg_args),
        }


@dataclass(frozen=True)
class AudioProbeReport:
    """Full result from probing the recorder audio chain."""

    process_name: str
    selected: Optional[AudioSourceProbe]
    probes: list[AudioSourceProbe] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "process_name": self.process_name,
            "selected": self.selected.to_dict() if self.selected else None,
            "probes": [p.to_dict() for p in self.probes],
        }


@dataclass(frozen=True)
class VideoCapturePlan:
    """ffmpeg screen-capture input plan for the selected recorder mode."""

    mode: str
    input_args: tuple[str, ...]
    pre_encode_filters: tuple[str, ...] = ()

    def encode_filter(self) -> str:
        return ",".join((*self.pre_encode_filters, "scale=1920:1080:flags=lanczos"))

    def entropy_probe_filter(self) -> str:
        return ",".join(
            (
                *self.pre_encode_filters,
                "fps=1",
                f"scale={_CAPTURE_ENTROPY_WIDTH}:{_CAPTURE_ENTROPY_HEIGHT}:flags=bilinear",
                "format=gray",
            )
        )


@dataclass(frozen=True)
class CaptureEntropyResult:
    """Low-cost diagnostic from sampling the live capture source."""

    average_bits: float
    frames_sampled: int
    unique_frames: int


def _build_video_capture_plan(mode: str, *, x: int, y: int, w: int, h: int) -> VideoCapturePlan:
    if mode == "ddagrab":
        # ddagrab captures the full DXGI output. Crop to the resolved
        # Minecraft window rectangle after capture so GPU-rendered frames are visible.
        return VideoCapturePlan(
            mode="ddagrab",
            input_args=(
                "-f",
                "ddagrab",
                "-framerate",
                "30",
                "-draw_mouse",
                "0",
                "-i",
                "output=0",
            ),
            pre_encode_filters=(f"crop={int(w)}:{int(h)}:{int(x)}:{int(y)}",),
        )
    if mode == "gdigrab":
        return VideoCapturePlan(
            mode="gdigrab",
            input_args=(
                "-f",
                "gdigrab",
                "-framerate",
                "30",
                "-draw_mouse",
                "0",
                "-offset_x",
                str(int(x)),
                "-offset_y",
                str(int(y)),
                "-video_size",
                f"{int(w)}x{int(h)}",
                "-i",
                "desktop",
            ),
        )
    raise ValueError(f"unsupported capture mode: {mode}")


def _capture_mode_candidates(
    requested_mode: str, *, x: int, y: int, w: int, h: int
) -> list[VideoCapturePlan]:
    requested_mode = _normalize_capture_mode(requested_mode)
    modes = ("ddagrab", "gdigrab") if requested_mode == "auto" else (requested_mode,)
    return [_build_video_capture_plan(mode, x=x, y=y, w=w, h=h) for mode in modes]


def _byte_entropy_bits(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for value in data:
        counts[value] += 1
    total = float(len(data))
    entropy = 0.0
    for count in counts:
        if count:
            probability = count / total
            entropy -= probability * math.log2(probability)
    return entropy


def _measure_capture_entropy(plan: VideoCapturePlan) -> Optional[CaptureEntropyResult]:
    """Sample the live capture source after startup and flag flat/static frames."""

    frame_size = _CAPTURE_ENTROPY_WIDTH * _CAPTURE_ENTROPY_HEIGHT
    cmd = [
        str(_FFMPEG),
        "-hide_banner",
        "-loglevel",
        "error",
        *plan.input_args,
        _FFMPEG_DURATION_ARG,
        f"{_CAPTURE_ENTROPY_SAMPLE_SEC:.1f}",
        "-vf",
        plan.entropy_probe_filter(),
        "-frames:v",
        str(_CAPTURE_ENTROPY_SAMPLE_FRAMES),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "pipe:1",
    ]
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_CAPTURE_ENTROPY_SAMPLE_SEC + 10.0,
            creationflags=flags,
        )
    except Exception as exc:  # noqa: BLE001
        _trace(f"capture_entropy: probe failed to run mode={plan.mode}: {exc}")
        return None
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        _trace(f"capture_entropy: probe exited rc={result.returncode} mode={plan.mode}: {stderr}")
        return None

    frames = [
        result.stdout[offset : offset + frame_size]
        for offset in range(0, len(result.stdout), frame_size)
        if len(result.stdout[offset : offset + frame_size]) == frame_size
    ]
    if not frames:
        _trace(f"capture_entropy: no frames sampled mode={plan.mode}")
        return None

    average = sum(_byte_entropy_bits(frame) for frame in frames) / len(frames)
    unique_frames = len({frame for frame in frames})
    return CaptureEntropyResult(
        average_bits=average,
        frames_sampled=len(frames),
        unique_frames=unique_frames,
    )


_DSHOW_LOOPBACK_HINTS = (
    "virtual-audio-capturer",
    "stereo mix",
    "stereomix",
    "what u hear",
    "what you hear",
    "vb-audio virtual cable",
    "voicemeeter output",
    "cable output",
)

_APPLICATION_AUDIO_HINTS = (
    "application audio capture",
    "application loopback",
    "process loopback",
    "process audio",
)


def _run_ffmpeg_probe(args: Sequence[str], timeout: float = 30.0) -> tuple[int, str]:
    """Run an ffmpeg probe and return (returncode, combined output)."""

    start = time.monotonic()
    try:
        res = subprocess.run(
            [str(_FFMPEG), *args],
            capture_output=True,
            timeout=timeout,
            text=True,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
    except Exception as e:  # noqa: BLE001 - diagnostics must not crash recorder startup
        return 127, f"{type(e).__name__}: {e}"
    elapsed = time.monotonic() - start
    if elapsed > 5.0:
        _trace(
            "WARNING: ffmpeg_probe slow completion "
            f"elapsed={elapsed:.1f}s timeout={timeout:.1f}s args={' '.join(args)}"
        )
    return res.returncode, (res.stdout or "") + (res.stderr or "")


def _list_ffmpeg_devices(demuxer: str) -> str:
    """Return ffmpeg -list_devices output for a Windows demuxer."""

    _rc, output = _run_ffmpeg_probe(
        ["-hide_banner", "-list_devices", "true", "-f", demuxer, "-i", "dummy"]
    )
    return output


def _ffmpeg_supports_device(demuxer: str) -> bool:
    """Return True if ffmpeg -devices lists a demuxing device by name."""

    _rc, output = _run_ffmpeg_probe(["-hide_banner", "-devices"])
    needle = demuxer.lower()
    for line in output.lower().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("d") and parts[1] == needle:
            return True
    return False


def _windows_supports_application_audio_capture() -> bool:
    """Windows 10 2004 (build 19041) introduced process loopback capture."""

    if os.name != "nt":
        return False
    getwindowsversion = getattr(sys, "getwindowsversion", None)
    if getwindowsversion is None:
        return False
    try:
        return int(getwindowsversion().build) >= 19041
    except Exception:
        return False


def _parse_dshow_audio_devices(output: str) -> list[str]:
    """Parse DirectShow audio device names from ffmpeg -list_devices output."""

    devices: list[str] = []
    in_audio = False
    for raw in output.splitlines():
        line = raw.strip()
        lower = line.lower()
        if "directshow audio devices" in lower:
            in_audio = True
            continue
        if "directshow video devices" in lower:
            in_audio = False
            continue
        if not in_audio:
            continue
        if "alternative name" in lower:
            continue
        start = line.find('"')
        end = line.find('"', start + 1)
        if start >= 0 and end > start:
            devices.append(line[start + 1 : end])
    return devices


def _parse_wasapi_output_devices(output: str) -> list[str]:
    """Best-effort parse for WASAPI render/output endpoints."""

    devices: list[str] = []
    section = ""
    for raw in output.splitlines():
        line = raw.strip()
        lower = line.lower()
        if any(
            token in lower for token in ("output devices", "render devices", "playback devices")
        ):
            section = "output"
            continue
        if any(
            token in lower for token in ("input devices", "capture devices", "recording devices")
        ):
            section = "input"
            continue
        if section != "output":
            continue
        start = line.find('"')
        end = line.find('"', start + 1)
        if start >= 0 and end > start:
            devices.append(line[start + 1 : end])
    return devices


def _find_device_by_hints(devices: Sequence[str], hints: Sequence[str]) -> Optional[str]:
    for device in devices:
        low = device.lower()
        if any(hint in low for hint in hints):
            return device
    return None


def _probe_application_audio_capture(
    process_name: str, dshow_devices: Sequence[str]
) -> AudioSourceProbe:
    """Try a process/application-specific audio source for javaw.exe."""

    if not _windows_supports_application_audio_capture():
        return AudioSourceProbe(
            mode=AudioCaptureMode.APPLICATION,
            label=f"Application Audio Capture ({process_name})",
            available=False,
            reason="Windows build does not expose process loopback capture",
        )

    process_low = process_name.lower()
    process_stem = process_low.removesuffix(".exe")
    for device in dshow_devices:
        low = device.lower()
        looks_like_app_audio = any(hint in low for hint in _APPLICATION_AUDIO_HINTS)
        matches_process = process_low in low or process_stem in low
        if looks_like_app_audio and matches_process:
            return AudioSourceProbe(
                mode=AudioCaptureMode.APPLICATION,
                label=f"Application Audio Capture ({process_name})",
                available=True,
                ffmpeg_args=("-f", "dshow", "-i", f"audio={device}"),
                device=device,
                reason="matched process-specific DirectShow application audio device",
            )

    return AudioSourceProbe(
        mode=AudioCaptureMode.APPLICATION,
        label=f"Application Audio Capture ({process_name})",
        available=False,
        reason="no process-specific application audio device found in ffmpeg list_devices",
    )


def _probe_desktop_audio_output(
    wasapi_output: str, dshow_devices: Sequence[str]
) -> AudioSourceProbe:
    """Try system audio via WASAPI loopback, then known DirectShow loopback shims."""

    wasapi_supported = _ffmpeg_supports_device("wasapi")
    wasapi_outputs = _parse_wasapi_output_devices(wasapi_output)
    if wasapi_supported or wasapi_outputs or "loopback" in wasapi_output.lower():
        device = "loopback"
        if wasapi_outputs:
            device = wasapi_outputs[0]
        return AudioSourceProbe(
            mode=AudioCaptureMode.DESKTOP,
            label="Desktop Audio Output (WASAPI loopback)",
            available=True,
            ffmpeg_args=("-f", "wasapi", "-i", "loopback"),
            device=device,
            reason="WASAPI loopback available for default playback device",
            fallback_used=True,
        )

    loopback = _find_device_by_hints(dshow_devices, _DSHOW_LOOPBACK_HINTS)
    if loopback:
        return AudioSourceProbe(
            mode=AudioCaptureMode.DESKTOP,
            label="Desktop Audio Output (DirectShow loopback)",
            available=True,
            ffmpeg_args=("-f", "dshow", "-i", f"audio={loopback}"),
            device=loopback,
            reason="matched DirectShow loopback/virtual cable device",
            fallback_used=True,
        )

    return AudioSourceProbe(
        mode=AudioCaptureMode.DESKTOP,
        label="Desktop Audio Output (WASAPI loopback)",
        available=False,
        reason="no WASAPI loopback or DirectShow loopback device found",
        fallback_used=True,
    )


def _probe_any_input_device(dshow_devices: Sequence[str]) -> AudioSourceProbe:
    """Last resort: any ffmpeg-visible DirectShow input device."""

    if dshow_devices:
        device = dshow_devices[0]
        return AudioSourceProbe(
            mode=AudioCaptureMode.INPUT,
            label="Any audio input device",
            available=True,
            ffmpeg_args=("-f", "dshow", "-i", f"audio={device}"),
            device=device,
            reason="fallback to first DirectShow audio input device",
            fallback_used=True,
        )
    return AudioSourceProbe(
        mode=AudioCaptureMode.INPUT,
        label="Any audio input device",
        available=False,
        reason="no DirectShow audio input devices found",
        fallback_used=True,
    )


def probe_audio_source_chain(process_name: str = "javaw.exe") -> AudioProbeReport:
    """Probe recorder audio sources in app -> desktop -> input order."""

    if os.name != "nt":
        probe = AudioSourceProbe(
            mode=AudioCaptureMode.NONE,
            label="Windows recorder audio chain",
            available=False,
            reason="non-Windows host; Windows ffmpeg devices unavailable",
            fallback_used=True,
        )
        return AudioProbeReport(process_name=process_name, selected=None, probes=[probe])

    dshow_output = _list_ffmpeg_devices("dshow")
    wasapi_output = _list_ffmpeg_devices("wasapi")
    dshow_devices = _parse_dshow_audio_devices(dshow_output)

    probes = [
        _probe_application_audio_capture(process_name, dshow_devices),
        _probe_desktop_audio_output(wasapi_output, dshow_devices),
        _probe_any_input_device(dshow_devices),
    ]
    selected = next((probe for probe in probes if probe.available), None)
    return AudioProbeReport(process_name=process_name, selected=selected, probes=probes)


def _print_audio_probe_report(report: AudioProbeReport, *, as_json: bool = False) -> None:
    """Emit the audio-chain probe result for CLI diagnostics."""

    if as_json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return

    print(f"process: {report.process_name}")
    for idx, probe in enumerate(report.probes, start=1):
        status = "OK" if probe.available else "MISS"
        print(f"{idx}. {status} {probe.label}")
        if probe.device:
            print(f"   device: {probe.device}")
        if probe.reason:
            print(f"   reason: {probe.reason}")
        if probe.available:
            print(f"   ffmpeg: {' '.join(probe.ffmpeg_args)}")
    if report.selected:
        print(f"selected: {report.selected.mode}")
    else:
        print("selected: none")


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
    # 2. DirectML path — only meaningful if torch-directml made it into the
    # bundle. Pure dxgi.dll presence isn't sufficient (every Win10+ has it).
    try:
        import importlib.util  # noqa: PLC0415

        if importlib.util.find_spec("torch_directml") is not None:
            return True
    except Exception:
        pass
    return False


# Process names treated as "Minecraft" — both Java and Bedrock variants.
# Critical: do NOT include MinecraftLauncher.exe here. The launcher is a
# pre-game surface; recording it creates empty/false sessions and misses
# actual gameplay.
MC_PROCESS_NAMES = {"javaw.exe", "java.exe", "Minecraft.exe"}
MC_LAUNCHER_PROCESS_NAMES = {"minecraftlauncher.exe"}
MC_WINDOW_TITLE_EXCLUDE_MARKERS = ("launcher", "启动器")
MIN_MC_WINDOW_WIDTH = 640
MIN_MC_WINDOW_HEIGHT = 360


def _candidate_mc_instance_dirs() -> list[Path]:
    """Known Minecraft instance roots whose options.txt may affect recording."""

    candidates: list[Path] = []
    override = os.environ.get("OYSTER_MC_INSTANCE_DIR", "").strip()
    if override:
        candidates.append(Path(override).expanduser())

    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if local_appdata:
        candidates.append(Path(local_appdata) / "OysterRecorder" / "mc-instance")
    elif os.name == "nt":
        candidates.append(Path.home() / "AppData" / "Local" / "OysterRecorder" / "mc-instance")

    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        candidates.append(Path(appdata) / ".minecraft")
    elif os.name == "nt":
        candidates.append(Path.home() / "AppData" / "Roaming" / ".minecraft")
    elif sys.platform == "darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / "minecraft")
    else:
        candidates.append(Path.home() / ".minecraft")

    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.expanduser()).casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate.expanduser())
    return out


def _ensure_mc_focus_loss_safe(mc_instance_dir: Path) -> None:
    """Force Minecraft to keep ticking if the recorder/UI steals focus."""

    if os.name != "nt":
        _trace("non-Windows, skipping options.txt patch")
        return

    options_path = Path(mc_instance_dir) / "options.txt"
    if not options_path.exists():
        _trace(f"options.txt not found at {options_path}, skipping")
        return

    try:
        lines = options_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:  # noqa: BLE001
        _trace(f"options.txt patch failed at {options_path}: {exc}")
        return

    required = {
        "pauseOnLostFocus": "false",
        "fullscreen": "false",
    }
    seen: set[str] = set()
    patched: list[str] = []
    for line in lines:
        key, sep, _value = line.partition(":")
        if sep and key in required:
            patched.append(f"{key}:{required[key]}")
            seen.add(key)
        else:
            patched.append(line)

    for key, value in required.items():
        if key not in seen:
            patched.append(f"{key}:{value}")

    try:
        _atomic_write_text(options_path, "\n".join(patched) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        _trace(f"options.txt patch failed at {options_path}: {exc}")
        return

    _trace(f"options.txt patched: pauseOnLostFocus=false; fullscreen=false at {options_path}")


def _ensure_known_mc_instances_focus_loss_safe() -> None:
    """Patch all likely MC instance options files without failing recorder startup."""

    for mc_dir in _candidate_mc_instance_dirs():
        _ensure_mc_focus_loss_safe(mc_dir)


def _normalise_process_name(name: str) -> str:
    return name.strip().casefold()


def _is_supported_minecraft_process_name(name: str) -> bool:
    """Return True for game processes only, never the launcher."""
    normalized = _normalise_process_name(name)
    if normalized in MC_LAUNCHER_PROCESS_NAMES:
        return False
    return normalized in {_normalise_process_name(n) for n in MC_PROCESS_NAMES}


def _is_real_minecraft_window_title(title: str) -> bool:
    """True only for gameplay windows, not Minecraft Launcher/pre-game UI."""
    normalized = title.strip().casefold()
    if "minecraft" not in normalized:
        return False
    return not any(marker in normalized for marker in MC_WINDOW_TITLE_EXCLUDE_MARKERS)


def _is_real_minecraft_window_geometry(width: int, height: int) -> bool:
    """Reject tiny splash/login surfaces before starting ffmpeg."""
    return width >= MIN_MC_WINDOW_WIDTH and height >= MIN_MC_WINDOW_HEIGHT


def _windows_process_name_for_pid(pid: int) -> Optional[str]:
    """Return Windows image name for a PID, or None if tasklist lookup fails."""
    if os.name != "nt" or pid <= 0:
        return None
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception:  # noqa: BLE001
        return None
    for line in out.splitlines():
        if line.startswith('"'):
            try:
                return line.split('","', 1)[0].lstrip('"')
            except Exception:
                return None
    return None


def _get_minecraft_window_rect() -> Optional[dict[str, Any]]:
    """Return Minecraft window geometry on Windows, or None if not found.

    Uses Win32 EnumWindows + GetWindowText + GetWindowRect via ctypes
    (no extra deps, built into Python). We scan all top-level windows
    and pick the first game-sized Minecraft window that is not the
    launcher/pre-game surface. PRD requires
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
    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    GetDpiForWindow = getattr(user32, "GetDpiForWindow", None)

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    found_hwnd: list[int] = []
    found_title: list[str] = []
    found_process_name: list[str | None] = []
    ignored_titles: list[str] = []

    def _callback(hwnd, _lparam):
        if not IsWindowVisible(hwnd):
            return True
        ln = GetWindowTextLength(hwnd)
        if ln == 0:
            return True
        buf = ctypes.create_unicode_buffer(ln + 1)
        GetWindowText(hwnd, buf, ln + 1)
        title = buf.value
        if "minecraft" not in title.casefold():
            return True

        if not _is_real_minecraft_window_title(title):
            ignored_titles.append(title)
            return True

        pid = wt.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = _windows_process_name_for_pid(int(pid.value))
        if process_name is not None and not _is_supported_minecraft_process_name(process_name):
            ignored_titles.append(f"{title} [{process_name}]")
            return True

        rect = wt.RECT()
        if not GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if not _is_real_minecraft_window_geometry(width, height):
            ignored_titles.append(f"{title} ({width}x{height})")
            return True

        found_hwnd.append(hwnd)
        found_title.append(title)
        found_process_name.append(process_name)
        return False  # stop iteration

    EnumWindows(EnumWindowsProc(_callback), 0)

    if not found_hwnd:
        if ignored_titles:
            _trace(
                "minecraft_window_gate: ignored non-game windows: " + "; ".join(ignored_titles[:3])
            )
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
        "hwnd": int(found_hwnd[0]),
        "title": found_title[0],
        "processName": found_process_name[0],
        "x": int(rect.left),
        "y": int(rect.top),
        "width": int(rect.right - rect.left),
        "height": int(rect.bottom - rect.top),
        "recordDpi": dpi,
    }


def _restore_minecraft_window_for_capture(rect: Optional[dict[str, Any]]) -> None:
    """Best-effort restore/foreground the captured Minecraft window."""
    if os.name != "nt" or not rect:
        return
    try:
        hwnd = int(rect.get("hwnd") or 0)
    except Exception:
        hwnd = 0
    if hwnd <= 0:
        return

    try:
        import ctypes

        user32 = ctypes.windll.user32
        SW_RESTORE = 9
        # Never minimize the capture target. Windows stops rendering
        # minimized game windows, and screen capture can then record the
        # last visible frame forever, producing a frozen-looking video.
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        _trace("minecraft window restored/foregrounded for screen capture")
    except Exception as exc:  # noqa: BLE001
        _trace(f"minecraft foreground restore failed: {exc}")


def _wait_for_stable_minecraft_window(
    *,
    timeout_sec: int = 120,
    stable_polls: int = 3,
    poll_interval: float = 1.0,
    should_abort: Optional[Callable[[], bool]] = None,
) -> Optional[dict[str, Any]]:
    """Wait until the real game window is visible and stable across polls."""
    deadline = time.time() + timeout_sec
    last_signature: Optional[tuple[Any, ...]] = None
    stable_count = 0

    while time.time() < deadline:
        if should_abort is not None and should_abort():
            return None
        rect = _get_minecraft_window_rect()
        if rect is None:
            last_signature = None
            stable_count = 0
        else:
            signature = (
                rect.get("hwnd"),
                rect.get("title"),
                rect.get("x"),
                rect.get("y"),
                rect.get("width"),
                rect.get("height"),
            )
            if signature == last_signature:
                stable_count += 1
            else:
                last_signature = signature
                stable_count = 1
            if stable_count >= stable_polls:
                return rect
        time.sleep(poll_interval)
    return None


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
        self._raw_input_capture = None
        self._start_time = 0.0
        self._lock = threading.Lock()

    def _now_ms(self) -> int:
        return int((time.time() - self._start_time) * 1000)

    def start(self) -> bool:
        """Begin listening. Returns False if every input backend is unavailable."""
        self._start_time = time.time()
        pynput_ok = False
        try:
            from pynput import keyboard, mouse
        except Exception as exc:  # noqa: BLE001 — best-effort
            _trace(f"input_capture: pynput unavailable: {exc}")
        else:
            try:
                self._start_pynput_listeners(keyboard, mouse)
                pynput_ok = True
            except Exception as exc:  # noqa: BLE001
                _trace(f"input_capture: pynput listener start failed: {exc}")

        raw_ok = self._start_raw_input_capture()
        return pynput_ok or raw_ok

    def _start_pynput_listeners(self, keyboard: Any, mouse: Any) -> None:
        """Start the legacy absolute cursor/key listeners."""

        def on_press(key):  # noqa: ANN001
            self._record_key(key, "key_down")

        def on_release(key):  # noqa: ANN001
            self._record_key(key, "key_up")

        def on_move(x, y):  # noqa: ANN001
            with self._lock:
                self.events.append(
                    {
                        "timestamp_ms": self._now_ms(),
                        "event_type": "mouse_move",
                        "mouseX": int(x),
                        "mouseY": int(y),
                    }
                )

        def on_click(x, y, button, pressed):  # noqa: ANN001
            with self._lock:
                self.events.append(
                    {
                        "timestamp_ms": self._now_ms(),
                        "event_type": "mouse_click",
                        "mouseX": int(x),
                        "mouseY": int(y),
                        "button": str(button),
                        "pressed": bool(pressed),
                    }
                )

        self._kbd_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._mouse_listener = mouse.Listener(on_move=on_move, on_click=on_click)
        self._kbd_listener.start()
        self._mouse_listener.start()

    def _start_raw_input_capture(self) -> bool:
        """Start additive WM_INPUT relative mouse delta capture on Windows."""

        if RawInputCapture is None:
            if _RAW_INPUT_CAPTURE_IMPORT_ERROR is not None:
                _trace(f"WARN: raw input capture unavailable: {_RAW_INPUT_CAPTURE_IMPORT_ERROR}")
            return False

        try:
            raw_capture = RawInputCapture(self._record_raw_mouse_delta)
            self._raw_input_capture = raw_capture
            raw_ok = bool(raw_capture.start())
        except Exception as exc:  # noqa: BLE001
            self._raw_input_capture = None
            _trace(f"WARN: raw input capture failed to start: {exc}")
            return False

        if raw_ok:
            _trace("input_capture: raw input WM_INPUT registered")
        else:
            reason = getattr(raw_capture, "last_error", "") or "registration failed"
            _trace(f"WARN: raw input capture disabled; continuing with pynput only ({reason})")
        return raw_ok

    def _record_raw_mouse_delta(self, dx: int, dy: int, timestamp_ms: int) -> None:
        try:
            ts = max(0, int(timestamp_ms))
        except Exception:
            ts = self._now_ms()
        with self._lock:
            self.events.append(
                {
                    "timestamp_ms": ts,
                    "event_type": "mouse_raw_delta",
                    "dx": int(dx),
                    "dy": int(dy),
                }
            )

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
            self.events.append(
                {
                    "timestamp_ms": self._now_ms(),
                    "event_type": event_type,
                    "keyCode": kc,
                }
            )

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
            1
            for ev in sample
            if ev.get("event_type", "").startswith("key_") and ev.get("keyCode", -1) == -1
        )
        # Mouse out-of-bounds (primary screen heuristic)
        # NOTE: multi-monitor setups can legitimately have negative coords;
        # we use a generous ±5000 envelope to avoid false alarms.
        mouse_oob = sum(
            1
            for ev in sample
            if ev.get("event_type", "").startswith("mouse_")
            and (
                ev.get("mouseX", 0) < -5000
                or ev.get("mouseX", 0) > 10000
                or ev.get("mouseY", 0) < -5000
                or ev.get("mouseY", 0) > 10000
            )
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
        if self._raw_input_capture is not None:
            try:
                self._raw_input_capture.stop()
            except Exception:
                pass
        for L in (self._kbd_listener, self._mouse_listener):
            try:
                if L is not None:
                    L.stop()
            except Exception:
                pass
        with self._lock:
            indexed = list(enumerate(self.events))
        return [
            event
            for _idx, event in sorted(
                indexed,
                key=lambda item: (int(item[1].get("timestamp_ms", 0)), item[0]),
            )
        ]

    def raw_input_diagnostics(self) -> dict[str, Any]:
        raw_capture = self._raw_input_capture
        if raw_capture is None:
            return {
                "registration_tier": "none",
                "wm_input_total": 0,
                "get_raw_input_data_failures": 0,
            }
        return {
            "registration_tier": getattr(raw_capture, "tier", "none"),
            "wm_input_total": int(getattr(raw_capture, "wm_input_total", 0)),
            "get_raw_input_data_failures": int(getattr(raw_capture, "failures", 0)),
        }


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


def _minecraft_running() -> bool:
    """True iff any process matching MC_PROCESS_NAMES is alive."""
    return any(_is_supported_minecraft_process_name(name) for name in _list_windows_processes())


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
        try:
            self.attributes("-toolwindow", True)
        except Exception as exc:  # noqa: BLE001 - Windows-only hint; harmless elsewhere
            _trace(f"toolwindow attribute failed: {exc}")

        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._output_path: Optional[Path] = None
        self._stop_event = threading.Event()
        self._input_capture: Optional[InputCapture] = None
        self._captured_events: list[dict[str, Any]] = []
        self._input_capture_diagnostics: dict[str, Any] = {
            "registration_tier": "none",
            "wm_input_total": 0,
            "get_raw_input_data_failures": 0,
        }
        self._window_no_activate_hwnd: Optional[int] = None
        self._window_original_ex_style: Optional[int] = None
        self._window_disabled_for_recording = False
        self._disabled_stop_hotkey_thread: Optional[threading.Thread] = None
        self._disabled_stop_hotkey_thread_id: Optional[int] = None
        self._disabled_stop_hotkey_id = 0x534F
        # v0.4.0: tester explicitly opts in to recording. Default is
        # observe-only mode so our .exe can NEVER be blamed for MC
        # crashing — a tester whose MC crashes can verify it crashes
        # WITHOUT us recording first.
        self._record_armed = False
        self._mc_window_rect: Optional[dict[str, int]] = None
        self._last_mc_focus_check_at: float = 0.0
        self._audio_probe_failed = False

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
        _ensure_known_mc_instances_focus_loss_safe()
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
            helpbar,
            text="打开日志文件夹",
            font=("Helvetica", 8),
            bg="white",
            fg="#666",
            bd=0,
            cursor="hand2",
            command=lambda: self._open_path(_STARTUP_LOG.parent),
        ).pack(side="left", padx=4)
        tk.Button(
            helpbar,
            text="复制日志路径",
            font=("Helvetica", 8),
            bg="white",
            fg="#666",
            bd=0,
            cursor="hand2",
            command=lambda: self._copy_to_clipboard(str(_STARTUP_LOG)),
        ).pack(side="left", padx=4)
        tk.Button(
            helpbar,
            text="导出诊断包到桌面",
            font=("Helvetica", 8),
            bg="white",
            fg="#666",
            bd=0,
            cursor="hand2",
            command=self._export_diagnostic_only,
        ).pack(side="left", padx=4)
        # rc8: prominent "View My Recordings" button — opens the OysterClips
        # folder in Explorer at the registry-resolved (OneDrive-aware) path
        # where session tarballs actually live. Bigger / styled vs the other
        # helpbar buttons because this is the action testers ask for first.
        tk.Button(
            helpbar,
            text="📂 我的录像",
            font=("Helvetica", 9, "bold"),
            bg="#1976d2",
            fg="white",
            activebackground="#1565c0",
            activeforeground="white",
            bd=0,
            padx=10,
            pady=3,
            cursor="hand2",
            command=lambda: self._open_path(_output_dir()),
        ).pack(side="left", padx=4)

    def _export_diagnostic_only(self) -> None:
        """Build diagnostic zip and open the Desktop folder. No network."""
        _trace("user clicked export-diagnostic button")

        def _go():
            zp = _build_diagnostic_zip()

            def apply():
                if zp:
                    self._hint.config(
                        text=f"诊断包已导出: {zp}\n请发给工程师",
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
        """Open a file or folder in the OS file explorer."""
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
            _trace(f"open_path: opened {path}")
        except Exception as e:
            _trace(f"open_path: failed {e}")

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
                    fail_names = [f"#{r.criterion_id}" for r in rpt.results if not r.passed][:8]
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
                self.after(
                    0,
                    lambda: self._hint.config(
                        text=f"已保存: {tarball}\n（自动验证失败 — 见远程日志）",
                        fg=ORANGE,
                    ),
                )

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
                import json  # noqa: PLC0415

                from bin.bft_orchestrator import orchestrator as orch  # noqa: PLC0415

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
                        candidates = [p for p in inner if (p / "action_camera.json").exists()]
                        target = (
                            candidates[0]
                            if candidates
                            else (inner[0] if len(inner) == 1 else td_path)
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
                        tag = f"{rname}({rej}REJ)" if rej > 0 else f"{rname}({vc}VC)"
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
                            detail_lines.append(f"多模态失败: {', '.join(multimodal_bad[:5])}")
                        if single_bad:
                            detail_lines.append(f"单模态失败: {', '.join(single_bad[:5])}")
                        if not detail_lines:
                            detail_lines.append("(详情见日志)")
                        self._set(
                            f"⚠️ BFT 共识 FAIL ({decision})",
                            ORANGE,
                            "\n".join(detail_lines) + "\n建议重录或检查 producer。",
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
            self.after(
                0,
                lambda: self._hint.config(
                    text=f"已经是最新版 ({_current_version_tag()})\n如果出问题，请把 {_STARTUP_LOG} 截图给工程师。",
                    fg=TEXT_GRAY,
                ),
            )
            return
        # Don't auto-replace mid-recording; wait until tester is idle.
        if self._record_armed:
            _trace("update: deferred — recording in progress, will retry on close")
            return
        _trace(f"update: staging {latest_tag}")
        self.after(
            0,
            lambda: self._set(
                "⏳ 自动更新中…", ORANGE, f"正在下载 {latest_tag}，几秒后会自动重启。"
            ),
        )
        if _stage_self_update(exe_url):
            _trace("update: staged ok, exiting for relaunch")
            self.after(2000, self._on_close)
        else:
            self.after(
                0,
                lambda: self._hint.config(
                    text=f"自动更新失败 — 见 {_STARTUP_LOG}",
                    fg=ORANGE,
                ),
            )

    def _tick_recording_status(self) -> None:
        """v0.12.0: tick once per second while ffmpeg is alive, updating
        the subtitle with elapsed time + current video file size.
        Self-stops when ffmpeg dies.
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
        # v0.22.0: real-time data quality (Howard '录的时候确保数据的精确度')
        # Lightweight check on the live event buffer — if anything looks off,
        # surface it NOW so the tester aborts and re-records instead of
        # discovering a problem later in _auto_bft.
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
                text=f"⏱  {mm}分{ss:02d}秒\n📦 视频文件 {size_str}{quality_line}",
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
        self._set(
            "📊 处理深度图中…",
            "#1976d2",
            "录制已结束，正在生成每帧深度图。\n" "深度图完成后会打包成最终 tarball。",
        )

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
            progress_frame,
            counter,
            bar,
            eta_label,
            skip_row,
            skip_check,
            skip_btn,
            skip_var,
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
        self._set("⏳ 正在收尾跳过…", "#d97706", "等当前帧推断完，就会打包不带深度图的 tarball。")

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

        Keep the recorder UI visible while waiting for Minecraft. Once
        recording starts, the watcher foregrounds the Minecraft HWND
        without minimizing it, because screen capture freezes on the last
        visible frame if Windows stops rendering a minimized game window.
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
            # Do not minimize/iconify on arm. The tester needs the Phase-1
            # status messages, and the capture target must remain rendered.
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
            self._set("⚠️ 缺少 ffmpeg", ORANGE, "ffmpeg.exe 没有打包进来，请联系工程师。")
            return

        while not self._stop_event.is_set():
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
        """
        try:
            self._record_armed = False
            self._arm_btn.config(
                text="▶ 开始录制",
                bg="#1976d2",
                activebackground="#1565c0",
            )
            _trace("button reset for next session")
        except Exception:
            pass

    def _watch_mc_focus_alive(self, *, force: bool = False) -> None:
        """Best-effort keep the captured Minecraft HWND foregrounded while recording."""

        now = time.time()
        if not force and now - getattr(self, "_last_mc_focus_check_at", 0.0) < 5.0:
            return
        self._last_mc_focus_check_at = now

        rect = self._mc_window_rect or {}
        try:
            hwnd = int(rect.get("hwnd") or 0)
        except Exception:
            hwnd = 0
        if os.name != "nt" or hwnd <= 0:
            return

        try:
            import ctypes

            user32 = ctypes.windll.user32
            foreground = int(user32.GetForegroundWindow())
            if foreground == hwnd:
                return

            _trace(
                "WARN: MC mod data frozen — possible focus loss; "
                f"foreground={foreground} mc_hwnd={hwnd}; restoring"
            )
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        except Exception as exc:  # noqa: BLE001
            _trace(f"minecraft focus watchdog failed: {exc}")

    def _make_window_non_focus_stealing(self) -> None:
        if os.name != "nt":
            return
        try:
            import ctypes

            WS_EX_NOACTIVATE = 0x08000000
            GWL_EXSTYLE = -20
            hwnd = self.winfo_id()
            frame = self.wm_frame()
            if frame:
                hwnd = frame if isinstance(frame, int) else int(str(frame), 16)
            user32 = ctypes.windll.user32
            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_NOACTIVATE)
            self._window_no_activate_hwnd = hwnd
            self._window_original_ex_style = ex_style
            self._window_disabled_for_recording = False
            _trace("window made non-activatable (WS_EX_NOACTIVATE)")
        except Exception as exc:  # noqa: BLE001 - fall back to disabled window
            _trace(f"WS_EX_NOACTIVATE failed: {exc}")
            self._disable_window_focus_fallback()

    def _disable_window_focus_fallback(self) -> None:
        try:
            self.attributes("-disabled", True)
            self._window_disabled_for_recording = True
            self._start_disabled_stop_hotkey()
            _trace("window disabled during recording; Ctrl+Shift+S stop hotkey armed")
        except Exception as exc:  # noqa: BLE001
            _trace(f"disabled-window fallback failed: {exc}")

    def _start_disabled_stop_hotkey(self) -> None:
        if os.name != "nt" or self._disabled_stop_hotkey_thread is not None:
            return

        def _hotkey_loop() -> None:
            try:
                import ctypes

                MOD_CONTROL = 0x0002
                MOD_SHIFT = 0x0004
                VK_S = 0x53
                WM_HOTKEY = 0x0312

                class POINT(ctypes.Structure):
                    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

                class MSG(ctypes.Structure):
                    _fields_ = [
                        ("hwnd", ctypes.c_void_p),
                        ("message", ctypes.c_uint),
                        ("wParam", ctypes.c_size_t),
                        ("lParam", ctypes.c_ssize_t),
                        ("time", ctypes.c_ulong),
                        ("pt", POINT),
                    ]

                user32 = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32
                self._disabled_stop_hotkey_thread_id = int(kernel32.GetCurrentThreadId())
                hotkey_id = int(self._disabled_stop_hotkey_id)
                if not user32.RegisterHotKey(None, hotkey_id, MOD_CONTROL | MOD_SHIFT, VK_S):
                    _trace("disabled-window stop hotkey registration failed")
                    return
                try:
                    msg = MSG()
                    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                        if int(msg.message) == WM_HOTKEY and int(msg.wParam) == hotkey_id:
                            _trace("disabled-window stop hotkey received")
                            self._record_armed = False
                            self._stop_event.set()
                            self._stop_ffmpeg()
                            break
                finally:
                    user32.UnregisterHotKey(None, hotkey_id)
            except Exception as exc:  # noqa: BLE001
                _trace(f"disabled-window stop hotkey failed: {exc}")
            finally:
                self._disabled_stop_hotkey_thread_id = None

        self._disabled_stop_hotkey_thread = threading.Thread(
            target=_hotkey_loop,
            daemon=True,
            name="recorder-stop-hotkey",
        )
        self._disabled_stop_hotkey_thread.start()

    def _stop_disabled_stop_hotkey(self) -> None:
        thread = self._disabled_stop_hotkey_thread
        thread_id = self._disabled_stop_hotkey_thread_id
        if thread is None:
            return
        if os.name == "nt" and thread_id:
            try:
                import ctypes

                WM_QUIT = 0x0012
                ctypes.windll.user32.PostThreadMessageW(int(thread_id), WM_QUIT, 0, 0)
            except Exception as exc:  # noqa: BLE001
                _trace(f"disabled-window stop hotkey shutdown failed: {exc}")
        if thread is not threading.current_thread():
            thread.join(timeout=1.0)
        if not thread.is_alive():
            self._disabled_stop_hotkey_thread = None

    def _restore_window_activatable(self) -> None:
        if os.name != "nt":
            return
        try:
            if self._window_disabled_for_recording:
                self.attributes("-disabled", False)
                self._window_disabled_for_recording = False
            self._stop_disabled_stop_hotkey()

            hwnd = self._window_no_activate_hwnd
            ex_style = self._window_original_ex_style
            if hwnd is not None and ex_style is not None:
                import ctypes

                GWL_EXSTYLE = -20
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style)
                _trace("window restored activatable")
            self._window_no_activate_hwnd = None
            self._window_original_ex_style = None
        except Exception as exc:  # noqa: BLE001
            _trace(f"restore window activatable failed: {exc}")

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
        self._set(
            "准备好",
            TEXT_GRAY,
            "先打开 Minecraft 玩一会儿确认它不崩。\n确认后再点上面 ▶ 开始录制。",
        )
        arm_announced_at = None
        last_status_update = 0.0
        while not self._stop_event.is_set():
            armed = self._record_armed
            mc_alive = _minecraft_running()
            if armed and mc_alive:
                _ensure_known_mc_instances_focus_loss_safe()
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
                    self._set(
                        "⏸ 已 arm — 请打开 Minecraft",
                        ORANGE,
                        f"录制器在等 Minecraft 启动…（已等 {waited} 秒）\n"
                        f"打开 Minecraft 后 1-2 秒会自动开始录",
                    )
                elif not armed and mc_alive:
                    self._set(
                        "Minecraft 已开 — 等你点 ▶ 开始录制",
                        TEXT_GRAY,
                        "MC 检测到了，点上面蓝色按钮就开始录",
                    )
                elif not armed and not mc_alive:
                    self._set(
                        "准备好",
                        TEXT_GRAY,
                        "先打开 Minecraft 玩一会儿确认它不崩。\n" "确认后再点上面 ▶ 开始录制。",
                    )
            time.sleep(0.5)
        if self._stop_event.is_set():
            _trace("watch_loop: stopped before recording")
            return

        # Wait for the real gameplay window, not the Mojang/Minecraft
        # Launcher. Tester report 2026-05-26: old builds generated many
        # sessions and recorded pre-game launcher frames because the window
        # gate accepted any title containing "Minecraft".
        _trace("watch_loop: waiting for stable real MC game window")
        self._set(
            "⏳ 等待进入游戏窗口",
            ORANGE,
            "游戏已启动。录制器会等真实 Minecraft 游戏窗口稳定后自动开始，" "不会录启动器。",
        )
        self._mc_window_rect = _wait_for_stable_minecraft_window(
            timeout_sec=120,
            stable_polls=3,
            poll_interval=1.0,
            should_abort=lambda: (
                self._stop_event.is_set() or not self._record_armed or not _minecraft_running()
            ),
        )
        _trace(f"watch_loop: gated mc_window={self._mc_window_rect}")
        if self._mc_window_rect is None:
            if self._stop_event.is_set() or not self._record_armed:
                _trace("watch_loop: aborted before real MC window")
                return
            if not _minecraft_running():
                self._set(
                    "⏸ Minecraft 已退出",
                    ORANGE,
                    "还没看到真实游戏窗口，Minecraft 就退出了；没有生成空录制。",
                )
                _trace("watch_loop: MC exited before real window; no empty session")
                return
            self._set(
                "⏸ 还没进入游戏",
                ORANGE,
                "只检测到启动器/加载窗口，没有开始录制。请用 Oyster Recording "
                "自动打开游戏，或进入世界后再试。",
            )
            _trace("watch_loop: timed out waiting for real MC window; no empty session")
            return

        # Short settle after the real game window is stable.
        for _ in range(2):
            if self._stop_event.is_set() or not self._record_armed:
                _trace("watch_loop: aborted during real-window settle")
                return
            time.sleep(1.0)

        # Phase 2: start recording into a temp dir; we'll package it as
        # a 5-file PRD-shaped tarball after MC exits.
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._tmp_dir = Path(tempfile.mkdtemp(prefix=f"oyster-rec-{ts}-"))
        self._video_path = self._tmp_dir / "video.mp4"
        self._record_started_at = time.time()
        # R18 session-binding: one UUID per recording, propagated into
        # session_manifest.json + every action_camera frame + inputs.jsonl
        # session_start. Closes red-team B-05 (Frankenstein splice).
        import uuid as _uuid_mod

        self._session_id = str(_uuid_mod.uuid4())
        # Bug fix: leave Minecraft visible/foreground for screen capture. If the
        # captured game window is minimized after ffmpeg starts, Windows
        # stops rendering it and ffmpeg records one stale frame forever.
        _restore_minecraft_window_for_capture(self._mc_window_rect)
        self._watch_mc_focus_alive(force=True)
        self._make_window_non_focus_stealing()
        try:
            self._start_ffmpeg(self._video_path)
        except Exception as exc:  # noqa: BLE001
            self._restore_window_activatable()
            self._set("⚠️ 录制启动失败", ORANGE, f"{type(exc).__name__}: {exc}")
            return

        # Start input capture in parallel with video. If pynput fails
        # (rare — e.g. tester hardened OS), record video only and note
        # in subtitle so the tester knows.
        self._input_capture = InputCapture()
        input_ok = self._input_capture.start()
        if input_ok:
            self._set(
                "● 正在录制",
                RED,
                "玩你的 Minecraft 即可，退出游戏会自动停止录制。" "（视频 + 键鼠输入同步采集中）",
            )
        else:
            self._set("● 正在录制（仅视频）", RED, "键鼠采集未启动，仅录制视频。继续玩游戏即可。")

        # v0.12.0: live progress ticker so the tester knows recording is
        # actually working. Updates every second with elapsed seconds +
        # current video file size. Self-stops when ffmpeg ends.
        self.after(0, self._tick_recording_status)

        # Do not iconify/minimize anything after ffmpeg starts. The recorder
        # UI stays available for Stop, and Minecraft stays visible so screen capture
        # keeps receiving fresh rendered frames.
        _trace("post-ffmpeg iconify skipped; Minecraft left visible for capture")

        # Phase 3: wait for MC to exit, for the user to disarm, or for the
        # process-level stop event. ffmpeg is terminated only through
        # _stop_ffmpeg(), which writes "q" for a clean container finalization.
        while True:
            self._watch_mc_focus_alive()
            if self._stop_event.is_set():
                _trace("watch_loop: stop_event set — finalizing whatever we have")
                break
            if not self._record_armed:
                _trace("watch_loop: user disarmed — finalizing whatever we have")
                break
            if not _minecraft_running():
                _trace("watch_loop: MC exited — finalizing")
                break
            time.sleep(2.0)

        # v0.9.0 BUG FIX: previously, hitting `_stop_event` returned
        # before `_package_tarball`, throwing away the recording. Now ANY
        # stop reason (MC exit / user disarm / stop_event)
        # falls through to packaging so the tester always gets a tarball
        # representing what was actually recorded up to that point.

        # Phase 4: finalize ffmpeg + input capture, then package.
        self._stop_ffmpeg()
        if self._input_capture is not None:
            self._captured_events = self._input_capture.stop()
            self._input_capture_diagnostics = self._input_capture.raw_input_diagnostics()
        else:
            self._captured_events = []
            self._input_capture_diagnostics = {
                "registration_tier": "none",
                "wm_input_total": 0,
                "get_raw_input_data_failures": 0,
            }
        try:
            output_tar = self._package_tarball(ts)
        except Exception as exc:  # noqa: BLE001
            self._set("⚠️ 打包失败", ORANGE, f"{type(exc).__name__}: {exc}")
            return

        if output_tar.exists():
            size_mb = output_tar.stat().st_size / (1024 * 1024)
            self._set(
                "✓ 录制完成",
                GREEN,
                f"{output_tar.name} ({size_mb:.1f} MB) 已保存。" f"正在验证买家规格…",
            )
            self._hint.config(
                text=f"已保存: {output_tar}",
                fg=GREEN,
            )
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
                    self.after(
                        0,
                        lambda: self._hint.config(
                            text=f"已保存: {output_tar}\n远程日志: {url}",
                            fg=GREEN,
                        ),
                    )

            _upload_log_in_background(_on_url)
        else:
            self._set("⚠️ 录制结束但文件未生成", ORANGE, "请联系工程师并截图本窗口。")

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
            video_target = clip_dir / "video.mp4"
            os.replace(self._video_path, video_target)
            _fsync_file(video_target)
            _fsync_dir(clip_dir)

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
            sys_info["recorderVersion"] = "lite-v0.10.0"
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
                "recorderVersion": "lite-v0.10.0-fallback",
            }
        _atomic_write_json(clip_dir / "systeminfo.json", sys_info)

        # 3. action_camera.json — v0.19.0 BIG REWRITE: PRD-aligned schema
        # was event-based with mouseX/cameraX scalars; PRD wants 9000
        # frame-aligned records at 30Hz. Sample/sample_tarball_builder.py
        # is the canonical schema reference.
        from datetime import datetime as _dt  # noqa: PLC0415
        from datetime import timedelta as _td

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
        intrinsics = {"fx": round(fy, 3), "fy": round(fy, 3), "cx": 960.0, "cy": 540.0}

        # R01 iron-law: load real game-state from Fabric mod JSONL.
        # On v0.26.0+ this is MANDATORY — hard-fail if missing, unless
        # --allow-placeholder was explicitly passed.
        try:
            from game_state_overlay import apply_to_record as _gs_apply
            from game_state_overlay import jsonl_path as _gs_jsonl_path
            from game_state_overlay import load as _gs_load  # type: ignore  # noqa: PLC0415
            from game_state_overlay import lookup_at_ms as _gs_lookup
        except ImportError:
            _gs_load = _gs_lookup = _gs_apply = None  # type: ignore
            _gs_jsonl_path = None  # type: ignore

        _gs_samples = _gs_load() if _gs_load else None
        if _gs_samples:
            _trace(
                f"package: real game-state JSONL found, {len(_gs_samples)} samples — overlay enabled"
            )
            if not _gs_jsonl_path:
                raise RecorderError(
                    "Real game-state samples loaded but raw game_state.jsonl source is unknown; "
                    "refusing to package unauditable data."
                )
            gs_source = Path(_gs_jsonl_path())
            gs_target = clip_dir / "game_state.jsonl"
            shutil.copy2(gs_source, gs_target)
            _trace(
                "package: copied raw game_state.jsonl "
                f"from {gs_source} ({gs_target.stat().st_size} bytes)"
            )
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
                    r"Install path:         %APPDATA%\.minecraft\mods"
                    "\n"
                    "Tarball NOT created."
                )
            if allow_placeholder:
                _trace(
                    "package: --allow-placeholder active — using placeholder camera/player fields"
                )
            else:
                _trace(
                    "package: no game-state JSONL — using placeholder camera/player fields (pre-v0.26.0)"
                )

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

        _atomic_write_json(
            clip_dir / "action_camera.json",
            # PRD format: top-level array of records, no wrapper dict.
            # sample_tarball_builder.py writes this format too.
            action_records,
            indent=None,
        )

        # v0.20.2: write inputs.jsonl — raw pynput events (key_down, key_up,
        # mouse_move, mouse_click) with millisecond timestamps. Producer-side
        # artifact for R13 multimodal residual (keyCode-vs-input-replay)
        # which closes the FI-02 blind spot in BFT N=4 single-modal mesh.
        # See docs/SPEC_R13_MULTIMODAL.md § R13. Per IL10, this file is OK
        # to be missing on legacy/headless runs — R13 will ABSTAIN, not FAIL.
        try:
            # First line: session_start sentinel (frame-time alignment).
            input_rows = [
                {
                    "event_type": "session_start",
                    "timestamp_ms": 0,
                    "fps": FPS,
                    "frame_count": target_frame_count,
                    # R18: session_id ties this inputs.jsonl to the same
                    # session_manifest.json that action_camera frames cite.
                    "session_id": getattr(self, "_session_id", ""),
                },
                *self._captured_events,
            ]
            _atomic_write_jsonl(clip_dir / "inputs.jsonl", input_rows)
            _trace(f"package: wrote inputs.jsonl ({len(self._captured_events)} events)")
        except Exception as e:
            _trace(f"package: inputs.jsonl write failed: {e}")
            # Non-fatal: action_camera.json still ships; R13 will ABSTAIN.

        # R18: write session_manifest.json so consumer-side R18 residual can
        # bind every artifact (action_camera frames + inputs.jsonl session_start)
        # to a single recording. Closes red-team B-05 (Frankenstein splice).
        try:
            _atomic_write_json(
                clip_dir / "session_manifest.json",
                {
                    "session_id": getattr(self, "_session_id", ""),
                    "recorder_version": "lite-v0.21.0",
                    "start_time": _dt.fromtimestamp(self._record_started_at).isoformat(),
                    "frame_count": target_frame_count,
                    "fps": FPS,
                },
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
        mc_version = ggx.parse_game_version_from_window_title(str(rect.get("title", "")))
        if mc_version is None:
            _trace("gameinfo: Minecraft version not detected; leaving game_version blank")
        game_info = ggx.build_gameinfo_dict(
            game_name="Minecraft",
            game_version=mc_version,
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

        # 5. depth/ — production clients do NOT run DepthAnything/OpenEXR
        # locally. The recorder captures raw evidence and writes a manifest
        # telling the backend to produce linear depth / EXR during
        # post-processing. Legacy local inference is kept only for explicit
        # engineering runs via OYSTER_DEPTH_MODE=local or
        # OYSTER_ALLOW_CLIENT_DEPTH=1.
        video_path = clip_dir / "video.mp4"
        depth_dir = clip_dir / "depth"
        if _client_depth_inference_enabled():
            depth_skipped = False
            try:
                from depth_anything_v2_inference import infer_depth_for_video  # noqa: PLC0415

                _trace(f"depth: LEGACY local DepthAnything inference on {video_path}")
                self.after(0, self._show_depth_progress_ui)
                try:
                    manifest = infer_depth_for_video(
                        video_path,
                        depth_dir,
                        model_variant="vits",
                        device="cpu",
                        progress_callback=self._on_depth_progress,
                        should_skip=self._skip_depth_flag.is_set,
                    )
                    _trace(f"depth: rendered {len(manifest)} local EXR frames")
                    if self._skip_depth_flag.is_set():
                        # Cooperative skip raced past the loop's last poll.
                        depth_skipped = True
                        _trace("depth: skip flag observed after loop — treating as user skip")
                finally:
                    self.after(0, self._hide_depth_progress_ui)
                if depth_skipped:
                    # Drop the partial depth dir so the tarball cleanly OMITS
                    # depth/ rather than shipping a half-finished version.
                    try:
                        if depth_dir.exists():
                            shutil.rmtree(depth_dir, ignore_errors=True)
                    except Exception:
                        pass
                    _trace("package: depth skipped by user — partial tarball")
                    self._set(
                        "⚠️ 已跳过深度图",
                        "#d97706",
                        "tarball 完成，但深度数据未包含。\n" "下游买家规格会在深度项标记 FAIL。",
                    )
            except Exception as e:
                self.after(0, self._hide_depth_progress_ui)
                _trace(f"depth: local DepthAnything inference FAILED: {e!r}")
                raise RuntimeError(
                    f"Local depth inference failed: {e}. Production tester builds "
                    f"should use OYSTER_DEPTH_MODE=server. See ~/OysterRecorder.log "
                    f"for details."
                )
        else:
            postprocess_manifest = {
                "mode": _depth_mode(),
                "status": "pending_server_postprocess",
                "client_depth_inference": False,
                "source_video": "video.mp4",
                "server_outputs": [
                    "linear_depth",
                    "openexr_depth",
                    "depth_uint16",
                ],
                "note": (
                    "Production clients capture raw evidence only. Depth "
                    "linearization and OpenEXR generation run server-side."
                ),
            }
            _atomic_write_json(clip_dir / "depth_postprocess.json", postprocess_manifest)
            _trace("depth: local inference disabled; wrote depth_postprocess.json")

        # R22 (D-04 defense): hash every *.exr in depth/ and write
        # depth_manifest.json next to the directory. Stop-gap path
        # has zero EXR files so the manifest is the empty object {},
        # which still lets the consumer-side R22 vote PASS instead of
        # ABSTAIN once the full recorder ships EXR frames.
        try:
            import hashlib as _hashlib  # noqa: PLC0415

            depth_dir_path = clip_dir / "depth"
            depth_manifest: dict[str, str] = {}
            for exr_path in sorted(depth_dir_path.glob("*.exr")):
                sha = _hashlib.sha256()
                with exr_path.open("rb") as fh:
                    for chunk in iter(lambda fh=fh: fh.read(1 << 20), b""):
                        sha.update(chunk)
                depth_manifest[exr_path.name] = sha.hexdigest()
            _atomic_write_json(clip_dir / "depth_manifest.json", depth_manifest)
            _trace(f"package: wrote depth_manifest.json ({len(depth_manifest)} entries)")
        except Exception as e:  # noqa: BLE001
            _trace(f"package: depth_manifest.json write failed: {e}")
            # Non-fatal — R22 will ABSTAIN on missing manifest, same as
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

            _atomic_write_text(
                clip_dir / "intrinsics.yaml",
                yaml.safe_dump(intrinsics, sort_keys=False),
                encoding="utf-8",
            )
        except Exception:
            # Fall back to a plain text file in YAML-ish format.
            _atomic_write_text(
                clip_dir / "intrinsics.yaml",
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
        _atomic_write_json(clip_dir / "systeminfo.json", sys_info)

        # R01: if --allow-placeholder is active and JSONL was missing,
        # stamp metadata.json with data_authenticity='placeholder' so
        # buyers can identify non-real game-state tarballs.
        metadata: dict[str, Any] = {
            "input_capture_diagnostics": self.__dict__.get(
                "_input_capture_diagnostics",
                {
                    "registration_tier": "none",
                    "wm_input_total": 0,
                    "get_raw_input_data_failures": 0,
                },
            ),
            "video_capture": {
                "requested_mode": _CAPTURE_MODE,
                "selected_mode": self.__dict__.get("_video_capture_mode", "unknown"),
            },
        }
        if getattr(self, "_allow_placeholder", False) and not _gs_samples:
            metadata.update(
                {
                    "data_authenticity": "placeholder",
                    "warning": "camera/player fields are constant [0.0, 64.0, 0.0]",
                }
            )
        _atomic_write_json(clip_dir / "metadata.json", metadata)
        _trace("package: wrote metadata.json with input capture diagnostics")

        if not (clip_dir / "video.mp4").is_file():
            raise RecorderError("video.mp4 missing after ffmpeg finalize; session not complete")
        if getattr(self, "_audio_probe_failed", False):
            _generate_silent_audio_fallback(clip_dir, clip_dir / "video.mp4")
        _ensure_recording_mp4_alias(clip_dir)

        _write_session_complete_marker(clip_dir)
        _trace("package: wrote .session_complete marker")

        # Write the tarball into the user's Documents/OysterClips/.
        out_tar = _output_dir() / f"clip-{ts}.tar.gz"
        tmp_tar = out_tar.with_name(f".{out_tar.name}.tmp")
        try:
            with tarfile.open(tmp_tar, "w:gz") as tf:
                tf.add(clip_dir, arcname=f"clip-{ts}")
            _fsync_file(tmp_tar)
            os.replace(tmp_tar, out_tar)
            _fsync_dir(out_tar.parent)
        except Exception:
            try:
                tmp_tar.unlink()
            except OSError:
                pass
            raise

        # Cleanup tmp dir.
        try:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
        except Exception:
            pass
        return out_tar

    def _start_ffmpeg(self, out_path: Path) -> None:
        """Spawn ffmpeg to record the Minecraft window.

        R01 v3 (iron-law-strict): prefer DXGI Desktop Duplication
        (ddagrab) full-output capture cropped to the Minecraft geometry.
        The gdigrab geometry path remains only as an auto fallback for
        older Windows hosts where ddagrab exits during startup. Title
        encoding is irrelevant in both paths. Hard-fails if mc_window is
        None (no window detected). The old title-based branch has been
        removed.

        v0.29.0: captures audio via an explicit priority chain:
        Application Audio Capture(javaw.exe) -> Desktop Audio Output ->
        any DirectShow input fallback. Never chooses microphone before
        system audio when a loopback source is available.
        """
        # R01 iron-law: hard-fail if Minecraft window not detected.
        if self._mc_window_rect is None:
            raise RecorderError("Minecraft window not detected. Is Minecraft running and visible?")

        rect = self._mc_window_rect
        mc_title = rect.get("title", "")
        x = rect.get("x", 0)
        y = rect.get("y", 0)
        w = rect.get("width", 1920)
        h = rect.get("height", 1080)

        # R01 D section: parse MC version from title and warn if unsupported.
        mc_ver = _parse_mc_version_from_title(mc_title)
        if mc_ver and mc_ver not in SUPPORTED_MC_VERSIONS:
            _trace(
                f"WARN: Minecraft {mc_ver} not in supported list. "
                "Real game-state mod only loads on stable releases. "
                "Recording will hard-fail at packaging unless you switch "
                "to a supported version OR pass --allow-placeholder."
            )

        # Audio probe. The old implementation only scanned DirectShow input
        # devices, so machines with no microphone produced device=None and a
        # video-only ffmpeg command. Dedicated minipc collectors need system
        # audio even when no capture input is present.
        audio_report = probe_audio_source_chain("javaw.exe")
        audio_source = audio_report.selected
        self._audio_probe_failed = audio_source is None
        audio_inputs = []
        audio_codec = []
        for probe in audio_report.probes:
            _trace(
                "audio_probe: "
                f"mode={probe.mode} available={probe.available} "
                f"device={probe.device!r} reason={probe.reason}"
            )
        if audio_source:
            audio_inputs = list(audio_source.ffmpeg_args)
            audio_codec = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
            _trace(
                "ffmpeg: capturing audio "
                f"mode={audio_source.mode} device={audio_source.device!r} "
                f"args={' '.join(audio_inputs)}"
            )
        else:
            _trace(
                "WARNING: audio_probe all sources failed; no ffmpeg audio source is "
                "available. Recording video-only because there is no system loopback "
                "or input device to attach."
            )

        # On Windows, CREATE_NO_WINDOW (0x08000000) hides the ffmpeg
        # console window so the tester only sees our Tk window.
        flags = 0x08000000 if os.name == "nt" else 0
        requested_capture_mode = _normalize_capture_mode(_CAPTURE_MODE)
        plans = _capture_mode_candidates(
            requested_capture_mode,
            x=int(x),
            y=int(y),
            w=int(w),
            h=int(h),
        )
        startup_errors: list[str] = []
        for plan in plans:
            _trace(
                "ffmpeg: capture "
                f"requested={requested_capture_mode} selected={plan.mode} "
                f"title='{mc_title}' geometry={x},{y},{w},{h}"
            )
            if plan.mode == "gdigrab":
                _trace(
                    "WARNING: ffmpeg capture_mode=gdigrab uses GDI-layer capture; "
                    "hardware-accelerated Minecraft may record a static frame. "
                    f"Prefer {_CAPTURE_MODE_ENV}=ddagrab on Windows 8+."
                )

            cmd = [
                str(_FFMPEG),
                "-hide_banner",
                *plan.input_args,
                *audio_inputs,
                "-vf",
                plan.encode_filter(),
                "-c:v",
                "libx265",
                "-preset",
                "ultrafast",
                # libx265 defaults to CRF 28 if no bitrate/CRF is specified. On
                # static game scenes that collapsed clips to ~67 kbps and looked frozen.
                "-b:v",
                "10M",
                "-maxrate",
                "12M",
                "-bufsize",
                "20M",
                "-pix_fmt",
                "yuv420p",
                *audio_codec,
                "-r",
                "30",
                "-y",
                str(out_path),
            ]
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
            except Exception as exc:  # noqa: BLE001
                startup_errors.append(f"{plan.mode} spawn failed: {exc}")
                _trace(f"ffmpeg: capture_mode={plan.mode} spawn failed: {exc}")
                continue

            if requested_capture_mode == "auto" and plan.mode == "ddagrab":
                time.sleep(_CAPTURE_STARTUP_CHECK_SEC)
                returncode = proc.poll()
                if returncode is not None:
                    startup_errors.append(f"ddagrab exited during startup rc={returncode}")
                    _trace(
                        "WARNING: ffmpeg ddagrab exited during startup "
                        f"rc={returncode}; falling back to gdigrab. "
                        "Fallback may record static frames for hardware-accelerated MC."
                    )
                    continue

            self._ffmpeg_proc = proc
            self._video_capture_mode = plan.mode
            self._video_capture_plan = plan
            self._start_capture_entropy_monitor(plan)
            return

        detail = "; ".join(startup_errors) or "no capture candidates were available"
        raise RecorderError(f"ffmpeg capture startup failed: {detail}")

    def _start_capture_entropy_monitor(self, plan: VideoCapturePlan) -> None:
        """After startup, sample live capture frames and warn on static output."""

        if os.name != "nt":
            return

        proc = self._ffmpeg_proc
        if proc is None:
            return

        def _go() -> None:
            time.sleep(_CAPTURE_ENTROPY_DELAY_SEC)
            current_proc = self._ffmpeg_proc
            if current_proc is None or current_proc is not proc or current_proc.poll() is not None:
                return
            result = _measure_capture_entropy(plan)
            if result is None:
                return
            _trace(
                "capture_entropy: "
                f"mode={plan.mode} avg_bits={result.average_bits:.3f} "
                f"frames={result.frames_sampled} unique={result.unique_frames}"
            )
            looks_static = result.average_bits < _CAPTURE_ENTROPY_THRESHOLD_BITS or (
                result.frames_sampled >= 2 and result.unique_frames <= 1
            )
            if not looks_static:
                return
            _trace(
                "WARNING: capture_entropy indicates possibly static video "
                f"mode={plan.mode} avg_bits={result.average_bits:.3f} "
                f"unique={result.unique_frames}/{result.frames_sampled}"
            )
            try:
                self.after(0, lambda: self._offer_capture_mode_switch(plan.mode, result))
            except RuntimeError:
                pass

        threading.Thread(target=_go, daemon=True, name="capture-entropy-monitor").start()

    def _offer_capture_mode_switch(self, mode: str, result: CaptureEntropyResult) -> None:
        alternate = "gdigrab" if mode == "ddagrab" else "ddagrab"
        warning = (
            "视频捕获看起来可能是静止画面。\n"
            f"mode={mode}, entropy={result.average_bits:.2f}, "
            f"unique={result.unique_frames}/{result.frames_sampled}\n"
            f"是否停止当前录制并切换到 {alternate}？"
        )
        self._set(
            "⚠️ 视频可能卡帧",
            ORANGE,
            f"检测到捕获画面可能是静止的。当前模式: {mode}；可切换到 {alternate} 后重录。",
        )
        ask_yes_no = getattr(messagebox, "askyesno", None)
        if not callable(ask_yes_no):
            _trace("capture_entropy: messagebox.askyesno unavailable; warning shown in UI only")
            return
        try:
            switch = ask_yes_no("OysterRecorder 捕获模式", warning)
        except Exception as exc:  # noqa: BLE001
            _trace(f"capture_entropy: switch prompt failed: {exc}")
            return
        if not switch:
            _trace("capture_entropy: user kept current capture mode")
            return

        global _CAPTURE_MODE
        _CAPTURE_MODE = alternate
        os.environ[_CAPTURE_MODE_ENV] = alternate
        _trace(f"capture_entropy: user switched capture mode to {alternate}; stopping current clip")
        self._record_armed = False
        self._stop_ffmpeg()
        self._set(
            "⚠️ 已切换捕获模式",
            ORANGE,
            f"当前录制已停止。已切换到 {alternate}，请重新点击 ▶ 开始录制。",
        )

    def _stop_ffmpeg(self) -> None:
        """Send 'q' to ffmpeg's stdin and wait for MP4 finalization."""
        proc = self._ffmpeg_proc
        if proc is None:
            self._restore_window_activatable()
            return
        forced_stop = False
        try:
            if proc.stdin:
                proc.stdin.write(b"q\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=_FFMPEG_CLEAN_QUIT_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            forced_stop = True
            _trace(
                "ffmpeg: clean quit timed out after "
                f"{_FFMPEG_CLEAN_QUIT_TIMEOUT_SEC:.1f}s; terminating"
            )
            proc.terminate()
            try:
                proc.wait(timeout=_FFMPEG_FORCE_STOP_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                _trace("ffmpeg: terminate timed out; killing process")
                proc.kill()
                try:
                    proc.wait(timeout=_FFMPEG_FORCE_STOP_TIMEOUT_SEC)
                except subprocess.TimeoutExpired:
                    _trace("ffmpeg: kill did not complete before timeout")
        self._ffmpeg_proc = None
        self._restore_window_activatable()
        video_path = getattr(self, "_video_path", None)
        if (
            forced_stop
            and isinstance(video_path, Path)
            and video_path.exists()
            and not _mp4_has_moov_atom(video_path)
        ):
            _attempt_mp4_remux_repair(video_path)
        if isinstance(video_path, Path) and video_path.exists():
            _fsync_file(video_path)

    def _on_close(self) -> None:
        _trace("on_close: user closed window")
        self._stop_event.set()
        self._stop_ffmpeg()
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
        msg = "录制器启动失败。\n\n" f"{type(exc).__name__}: {exc}\n\n"
        if remote_url:
            msg += (
                f"日志已自动上传，工程师可访问：\n{remote_url}\n\n"
                "你不用做任何事，工程师会从这个链接看到出错原因。"
            )
        else:
            msg += f"日志在本机：{_STARTUP_LOG}\n" "如果方便，把这个文件发给工程师。"
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


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="OysterRecorder")
    parser.add_argument(
        "--allow-placeholder",
        action="store_true",
        default=False,
        help="Allow placeholder camera/player fields (marks tarball as non-real)",
    )
    parser.add_argument(
        "--probe-audio-chain",
        action="store_true",
        default=False,
        help="Probe ffmpeg audio sources and exit without launching the recorder UI",
    )
    parser.add_argument(
        "--probe-audio-chain-json",
        action="store_true",
        default=False,
        help="Emit --probe-audio-chain diagnostics as JSON",
    )
    parser.add_argument(
        "--audio-process-name",
        default="javaw.exe",
        help="Process name to target for application audio probing (default: javaw.exe)",
    )
    parser.add_argument(
        "--capture-mode",
        choices=sorted(_VALID_CAPTURE_MODES),
        default=None,
        help=(
            "Video capture mode: auto tries ddagrab then gdigrab; "
            f"default comes from {_CAPTURE_MODE_ENV} or auto"
        ),
    )
    args, _unknown = parser.parse_known_args(argv)

    global _CAPTURE_MODE
    _CAPTURE_MODE = _normalize_capture_mode(
        args.capture_mode or os.environ.get(_CAPTURE_MODE_ENV, "auto")
    )

    if args.probe_audio_chain or args.probe_audio_chain_json:
        report = probe_audio_source_chain(args.audio_process_name)
        _print_audio_probe_report(report, as_json=args.probe_audio_chain_json)
        if report.selected is None:
            _trace("audio_probe_cli: selected=None")
        else:
            _trace(
                "audio_probe_cli: "
                f"selected={report.selected.mode} device={report.selected.device!r}"
            )
        return 0

    # R01 D section: print supported MC versions at startup.
    supported_str = ", ".join(SUPPORTED_MC_VERSIONS)
    _trace(
        f"OysterRecorder {RECORDER_VERSION} — supported Minecraft versions "
        f"for real game-state:\n  {supported_str}"
    )

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
