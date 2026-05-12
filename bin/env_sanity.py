"""rc16.13 — pre-record environment sanity check.

Detect known-bad runtime conditions on Windows BEFORE the recorder spawns:
HDR (tone-mapped frames), NVIDIA Shadowplay overlay (capture conflict),
Discord overlay (DLL injection), running OBS Studio (embedded OBS clash),
MSI Afterburner/RTSS OSD, and non-100% display scaling. All Windows-only
ctypes/winreg imports are lazy so the module is importable on Mac/Linux
for tests; detectors return safe defaults (False/empty/None) on non-
Windows OR on detection failure — they MUST NOT raise.

Public API: check_environment() -> SanityReport,
            show_sanity_warnings(report, lang) -> bool (True = continue).
"""
from __future__ import annotations

import logging
import os
import platform
import sys
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SanityIssue:
    """One detected environmental problem."""

    severity: str            # "fatal", "warning", "info"
    code: str                # stable machine-readable id, e.g. "HDR_ENABLED"
    message_en: str
    message_zh: str
    auto_fix_available: bool = False


@dataclass(frozen=True)
class SanityReport:
    """Bundle of issues + summary of the rig we sniffed."""

    issues: list[SanityIssue] = field(default_factory=list)
    rig_summary: dict[str, Any] = field(default_factory=dict)




def _is_windows() -> bool:
    return os.name == "nt"


def detect_hdr() -> bool:
    """True if Windows HDR is enabled. ORs two signals: registry key
    HKLM\\...\\HighDynamicRange Enabled=1, and DEVMODE.dmBitsPerPel > 24."""
    if not _is_windows():
        return False
    try:
        import winreg  # noqa: PLC0415
    except Exception:  # pragma: no cover
        return False
    # Signal 1: registry (key may be absent on older Windows).
    try:
        with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers\HighDynamicRange",
        ) as k:
            for name in ("Enabled", "HDREnabled"):
                try:
                    val, _ = winreg.QueryValueEx(k, name)
                    if int(val) == 1:
                        return True
                except OSError:
                    continue
    except OSError:
        pass
    # Signal 2: DEVMODE color depth > 24bpp (HDR pushes 30/32bpp). Define
    # a portable struct: fields we read are at the documented offsets, the
    # rest is opaque padding so DEVMODEW.dmSize stays valid.
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        class _DM(ctypes.Structure):
            _fields_ = [
                ("dmDeviceName", ctypes.c_wchar * 32),
                ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD),
                ("dmSize", wintypes.WORD),
                ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD),
                ("_pad1", ctypes.c_byte * 16),
                ("dmFormName", ctypes.c_wchar * 32),
                ("dmLogPixels", wintypes.WORD),
                ("dmBitsPerPel", wintypes.DWORD),
                ("_tail", ctypes.c_byte * 32),
            ]
        dm = _DM()
        dm.dmSize = ctypes.sizeof(_DM)
        if ctypes.windll.user32.EnumDisplaySettingsW(
                None, -1, ctypes.byref(dm)) and dm.dmBitsPerPel > 24:
            return True
    except Exception:
        pass
    return False


def _running_processes() -> set[str]:
    """Lowercase set of running process image names via Toolhelp32 snapshot
    (no psutil dep — keep the installer slim)."""
    if not _is_windows():
        return set()
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        class _PE(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]
        k32 = ctypes.windll.kernel32
        TH32CS_SNAPPROCESS = 0x00000002
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap in (0, -1):
            return set()
        try:
            pe = _PE()
            pe.dwSize = ctypes.sizeof(_PE)
            names: set[str] = set()
            if k32.Process32FirstW(snap, ctypes.byref(pe)):
                while True:
                    names.add(pe.szExeFile.lower())
                    if not k32.Process32NextW(snap, ctypes.byref(pe)):
                        break
            return names
        finally:
            k32.CloseHandle(snap)
    except Exception:
        return set()


def detect_shadowplay() -> bool:
    """True if Shadowplay / GeForce Experience overlay is active.

    NVIDIA Share.exe is the Shadowplay overlay UI (NVDisplay.Container
    alone is the driver service — we don't flag it). Presence of
    NVIDIA Share.exe is a strong enough signal."""
    if not _is_windows():
        return False
    return "nvidia share.exe" in _running_processes()


def detect_discord_overlay() -> bool:
    """True if Discord is running AND overlay is enabled in settings.json.

    Discord stores overlay state under %APPDATA%\\discord\\settings.json.
    Be conservative on missing/parse failure — flag the issue."""
    if not _is_windows() or "discord.exe" not in _running_processes():
        return False
    path = os.path.join(os.environ.get("APPDATA") or "",
                        "discord", "settings.json")
    if not os.path.isfile(path):
        return True
    try:
        import json  # noqa: PLC0415
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return any(data.get(k) is True for k in
                   ("OPEN_ON_GAME_START", "openOverlayOnGameStart",
                    "OVERLAY_ENABLED"))
    except Exception:
        return True


def detect_existing_obs() -> bool:
    """True if OBS Studio is already running (conflicts with embedded OBS)."""
    if not _is_windows():
        return False
    procs = _running_processes()
    return "obs64.exe" in procs or "obs32.exe" in procs


def detect_msi_afterburner() -> bool:
    """True if MSI Afterburner or RTSS OSD is running."""
    if not _is_windows():
        return False
    procs = _running_processes()
    return "msiafterburner.exe" in procs or "rtss.exe" in procs


def detect_display_scaling() -> dict[int, float]:
    """Return {monitor_index: dpi_scale} where 1.0 == 100%.

    Uses Shcore.GetDpiForMonitor with MDT_EFFECTIVE_DPI. Returns {} on
    failure or non-Windows."""
    if not _is_windows():
        return {}
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415
        shcore = ctypes.windll.shcore
        user32 = ctypes.windll.user32
        scales: dict[int, float] = {}
        idx = [0]
        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
            ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
        )
        def _cb(hmon, _hdc, _rect, _data):
            dpi_x = wintypes.UINT(0); dpi_y = wintypes.UINT(0)
            # MDT_EFFECTIVE_DPI = 0
            if shcore.GetDpiForMonitor(
                    hmon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0 \
                    and dpi_x.value:
                scales[idx[0]] = dpi_x.value / 96.0
            idx[0] += 1
            return 1
        user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
        return scales
    except Exception:
        return {}


def detect_amd_igpu_battery_mode() -> dict[str, Any]:
    """Audit I-1: detect AMD iGPU running on battery.

    AMD 780M (and other AMD iGPUs) throttle DXGI Desktop Duplication to
    ~1Hz when the laptop is on battery power. The recorder will silently
    produce a near-frozen capture. We must warn the user.

    Returns ``{'on_battery': bool, 'is_amd_igpu': bool, 'risk': str}``
    where ``risk`` is one of ``'high'`` (battery + AMD), ``'medium'``
    (battery only), ``'low'`` (AC power), ``'n/a'`` (non-Windows), or
    ``'unknown'`` (detection failed). Never raises."""
    if not _is_windows():
        return {"on_battery": False, "is_amd_igpu": False, "risk": "n/a"}
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        class _SPS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", wintypes.BYTE),
                ("BatteryFlag", wintypes.BYTE),
                ("BatteryLifePercent", wintypes.BYTE),
                ("SystemStatusFlag", wintypes.BYTE),
                ("BatteryLifeTime", wintypes.DWORD),
                ("BatteryFullLifeTime", wintypes.DWORD),
            ]
        sps = _SPS()
        ok = bool(ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps)))
        # ACLineStatus: 0 = offline (battery), 1 = online, 255 = unknown.
        on_battery = ok and sps.ACLineStatus == 0
    except Exception as exc:  # noqa: BLE001 — must never raise
        logger.warning("env_sanity: power status probe raised %s", exc)
        return {"on_battery": False, "is_amd_igpu": False,
                "risk": "unknown", "error": str(exc)}

    # Best-effort AMD iGPU detection via WMIC. wmic is deprecated on
    # newer Windows builds; fall back to registry if it fails.
    is_amd = False
    try:
        import subprocess  # noqa: PLC0415
        result = subprocess.run(  # noqa: S603,S607
            ["wmic", "path", "win32_VideoController", "get", "Name"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        out = (result.stdout or "").upper()
        is_amd = "AMD" in out or "RADEON" in out
    except Exception:  # noqa: BLE001
        # Registry fallback — enumerate display adapters under
        # HKLM\SYSTEM\CurrentControlSet\Enum\PCI\... is brittle; skip.
        pass

    if on_battery and is_amd:
        risk = "high"
    elif on_battery:
        risk = "medium"
    else:
        risk = "low"
    return {"on_battery": on_battery, "is_amd_igpu": is_amd, "risk": risk}


def _rig_summary() -> dict[str, Any]:
    """Best-effort hardware/OS fingerprint for logging + telemetry."""
    summary: dict[str, Any] = {
        "os": f"{platform.system()} {platform.release()}",
        "python": sys.version.split()[0],
        "monitor_count": None,
    }
    if _is_windows():
        try:
            import ctypes  # noqa: PLC0415
            # SM_CMONITORS = 80
            summary["monitor_count"] = ctypes.windll.user32.GetSystemMetrics(80)
        except Exception:
            pass
    return summary


# (key, detector_fn_name, code, severity, message_en, message_zh, auto_fix)
_DETECTORS = (
    ("hdr", "detect_hdr", "HDR_ENABLED", "warning",
     "Windows HDR is enabled. Captured frames may appear black or washed out. "
     "Turn HDR OFF on the gameplay monitor (Win+Alt+B) before recording.",
     "检测到 Windows HDR 已开启。录制画面可能全黑或泛白。"
     "请在游戏显示器上按 Win+Alt+B 关闭 HDR 后再录制。", False),
    ("shadowplay", "detect_shadowplay", "SHADOWPLAY_OVERLAY", "warning",
     "NVIDIA Shadowplay / GeForce Experience overlay is active and conflicts "
     "with the recorder's capture path. Disable 'In-Game Overlay'.",
     "检测到 NVIDIA Shadowplay (GeForce Experience) 覆盖层已启用，"
     "会与录制器冲突。请在 GeForce Experience 中关闭 In-Game Overlay。", False),
    ("discord", "detect_discord_overlay", "DISCORD_OVERLAY", "warning",
     "Discord is running with in-game overlay enabled. It injects into the "
     "game process and can cause capture failure. Disable Discord's overlay.",
     "检测到 Discord 正在运行且覆盖层启用，会注入游戏进程并干扰录制。"
     "请关闭 Discord 游戏覆盖层或退出 Discord。", False),
    ("obs", "detect_existing_obs", "OBS_RUNNING", "fatal",
     "OBS Studio is already running. The recorder uses an embedded OBS "
     "instance and cannot coexist with an open OBS Studio. Close OBS first.",
     "检测到 OBS Studio 已在运行。录制器内置 OBS 实例，无法与已打开的 "
     "OBS Studio 共存。请先关闭 OBS Studio。", False),
    ("afterburner", "detect_msi_afterburner", "RTSS_OVERLAY", "warning",
     "MSI Afterburner / RivaTuner (RTSS) OSD is running. RTSS overlays "
     "inject into the game and can cause black frames. Close RTSS.",
     "检测到 MSI Afterburner / RTSS OSD 正在运行，会注入游戏并可能"
     "导致录制黑屏。请关闭 RTSS。", False),
)


def check_environment() -> SanityReport:
    """Run all detectors and return a SanityReport. Never raises."""
    issues: list[SanityIssue] = []
    summary = _rig_summary()
    g = globals()
    for key, fn_name, code, severity, en, zh, auto in _DETECTORS:
        try:
            triggered = bool(g[fn_name]())
        except Exception as exc:
            logger.warning("env_sanity: detector %s raised %s", key, exc)
            triggered = False
        summary[key] = triggered
        if triggered:
            issues.append(SanityIssue(
                severity=severity, code=code,
                message_en=en, message_zh=zh, auto_fix_available=auto,
            ))

    # Audit I-1: AMD iGPU + battery — high risk of 1Hz DXGI throttle.
    try:
        amd_batt = detect_amd_igpu_battery_mode()
    except Exception as exc:  # noqa: BLE001 — never raise
        logger.warning("env_sanity: amd-battery probe raised %s", exc)
        amd_batt = {"on_battery": False, "is_amd_igpu": False,
                    "risk": "unknown"}
    summary["amd_igpu_battery"] = amd_batt
    if amd_batt.get("risk") == "high":
        issues.append(SanityIssue(
            severity="warning", code="AMD_IGPU_BATTERY_MODE",
            message_en=("AMD iGPU detected on battery power. DXGI Desktop "
                        "Duplication throttles to ~1Hz on battery and the "
                        "recorder will produce a near-frozen capture. Plug "
                        "in AC power, or set OYSTER_CAPTURE_MODE_ENV=window "
                        "to use the window-capture path instead."),
            message_zh=("检测到 AMD 集显且笔记本在电池供电下运行。"
                        "AMD 在电池模式下会将 DXGI Desktop Duplication "
                        "降至 ~1Hz，录制画面会几乎冻结。请接通电源，"
                        "或设置 OYSTER_CAPTURE_MODE_ENV=window 使用"
                        "窗口捕获模式。"),
            auto_fix_available=False,
        ))

    # DPI scaling — special: report info-level if any monitor != 100%.
    try:
        scales = detect_display_scaling()
    except Exception as exc:
        logger.warning("env_sanity: dpi probe raised %s", exc)
        scales = {}
    summary["dpi_scales"] = scales
    non_100 = {i: s for i, s in scales.items() if abs(s - 1.0) > 0.01}
    if non_100:
        scale_desc = ", ".join(f"#{i}: {int(s*100)}%" for i, s in non_100.items())
        issues.append(SanityIssue(
            severity="info", code="DISPLAY_SCALING_NON_100",
            message_en=("Display scaling is not 100% on one or more "
                        f"monitors ({scale_desc}). Cursor-monitor "
                        "fallback may pick the wrong region. If capture "
                        "looks cropped, set scaling to 100%."),
            message_zh=(f"显示缩放不是 100%（{scale_desc}）。"
                        "光标显示器回退可能选错区域。"
                        "如录制画面被裁剪，请将缩放设为 100%。"),
            auto_fix_available=False,
        ))

    return SanityReport(issues=issues, rig_summary=summary)


_DIALOG_STRINGS = {
    "en": {"title": "Oyster Recorder — Environment Check",
           "intro": "The following environment issues may affect recording:",
           "cont": "Continue anyway", "abort": "Abort",
           "timer": "Auto-continue in {s}s…"},
    "zh": {"title": "录制环境检查 — 发现问题",
           "intro": "录制前检测到以下环境问题，可能影响录制质量：",
           "cont": "仍然录制", "abort": "取消录制",
           "timer": "将在 {s} 秒后自动继续…"},
}
_SEV_COLOR = {"fatal": "#c0392b", "warning": "#d35400", "info": "#2980b9"}


def show_sanity_warnings(report: SanityReport, lang: str = "en") -> bool:
    """Modal Tk dialog showing all warnings. Returns True if user chose to
    continue, False if they aborted. Auto-confirms (continue) on 30s
    timeout — warnings are advisory, not blocking. Also hard-killed after
    60s as a safety net against any Tk hang."""
    if not report.issues:
        return True
    try:
        import tkinter as tk  # noqa: PLC0415
        from tkinter import ttk  # noqa: PLC0415
    except Exception:  # pragma: no cover — headless / no Tk
        for i in report.issues:
            logger.warning("[%s] %s: %s", i.severity, i.code,
                           i.message_zh if lang == "zh" else i.message_en)
        return True

    use_zh = lang.lower().startswith("zh")
    s = _DIALOG_STRINGS["zh" if use_zh else "en"]
    result = {"continue": True}
    root = tk.Tk()
    root.title(s["title"])
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    root.geometry("640x420")
    ttk.Label(root, text=s["intro"], wraplength=600,
              font=("Segoe UI", 11, "bold")).pack(
                  padx=16, pady=(16, 8), anchor="w")

    # Issue rows (capped at ~6 visible; rest extend dialog).
    body = ttk.Frame(root)
    body.pack(fill="both", expand=True, padx=16, pady=(0, 8))
    for issue in report.issues:
        row = ttk.Frame(body)
        row.pack(fill="x", pady=4, anchor="w")
        tk.Label(row, text=f"[{issue.severity.upper()}]",
                 fg=_SEV_COLOR.get(issue.severity, "black"),
                 font=("Segoe UI", 10, "bold")).pack(side="left", anchor="n")
        ttk.Label(row,
                  text=issue.message_zh if use_zh else issue.message_en,
                  wraplength=520, justify="left").pack(side="left", padx=8)

    # Footer: countdown + buttons.
    foot = ttk.Frame(root)
    foot.pack(fill="x", pady=(0, 12), padx=16)
    cd_var = tk.StringVar(value=s["timer"].format(s=30))
    ttk.Label(foot, textvariable=cd_var, foreground="#555").pack(side="left")

    def _close(cont: bool) -> None:
        result["continue"] = cont
        try:
            root.destroy()
        except Exception:
            pass

    ttk.Button(foot, text=s["abort"],
               command=lambda: _close(False)).pack(side="right", padx=4)
    ttk.Button(foot, text=s["cont"],
               command=lambda: _close(True)).pack(side="right", padx=4)

    remaining = [30]
    def _tick() -> None:
        remaining[0] -= 1
        if remaining[0] <= 0:
            _close(True)
            return
        cd_var.set(s["timer"].format(s=remaining[0]))
        try:
            root.after(1000, _tick)
        except Exception:
            pass
    root.after(1000, _tick)

    # Hard safety net: if Tk loop hangs > 60s, force-close → continue.
    timer = threading.Timer(60.0, lambda: _close(True))
    timer.daemon = True
    timer.start()
    try:
        root.mainloop()
    finally:
        timer.cancel()
    return result["continue"]


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    rep = check_environment()
    print("Rig:", rep.rig_summary)
    print(f"Found {len(rep.issues)} issue(s):")
    for it in rep.issues:
        print(f"  [{it.severity}] {it.code}: {it.message_en}")
    if rep.issues:
        ok = show_sanity_warnings(rep, lang=os.environ.get("OYSTER_LANG", "en"))
        print("continue =", ok)
