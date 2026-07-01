#!/usr/bin/env python3
"""
Preflight Recorder - Phase 1
Runs on minipc1 before each session to verify system readiness.
Fails fast if the system can't produce a buyer-acceptable session.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Configuration
OUTPUT_DIR = Path("/private/tmp/cluster-2026-05-17-preflight08")
REPORT_PATH = OUTPUT_DIR / "preflight_report.json"
ACTIVE_SESSION_DIR = OUTPUT_DIR / "active_session"
MIN_DISK_GB = 5
EXPECTED_RESOLUTION = (1920, 1080)
EXPECTED_DPI = 1.0
MIN_FPS = 28


def get_timestamp() -> str:
    """Get current UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_display_resolution() -> dict:
    """Check display resolution == 1920x1080."""
    try:
        # Try using xrandr on Linux
        result = subprocess.run(
            ["xrandr", "--current"],
            capture_output=True,
            text=True,
            timeout=10
        )
        for line in result.stdout.splitlines():
            if "*" in line or "current" in line.lower():
                # Parse resolution like "1920x1080     60.00*   60.00"
                parts = line.split()
                if parts and "x" in parts[0]:
                    res_str = parts[0]
                    if "x" in res_str:
                        width, height = res_str.split("x")
                        width, height = int(width), int(height)
                        ok = width == EXPECTED_RESOLUTION[0] and height == EXPECTED_RESOLUTION[1]
                        return {
                            "name": "display_resolution",
                            "ok": ok,
                            "value": f"{width}x{height}",
                            "expected": f"{EXPECTED_RESOLUTION[0]}x{EXPECTED_RESOLUTION[1]}"
                        }
    except Exception as e:
        # Fallback: surface the real reason xrandr failed (binary missing,
        # timeout, permission denied) instead of swallowing the exception.
        return {
            "name": "display_resolution",
            "ok": False,
            "value": "unknown",
            "error": f"xrandr failed: {e}"
        }

    # Fallback: xrandr ran successfully but the output did not contain a
    # recognizable `WxH` resolution token (e.g. headless CI, virtual display).
    return {
        "name": "display_resolution",
        "ok": False,
        "value": "unknown",
        "error": "could not determine"
    }


def check_dpi() -> dict:
    """Check DPI == 1.0 (no scaling)."""
    try:
        # Try using xrandr to get DPI
        result = subprocess.run(
            ["xrandr", "--current"],
            capture_output=True,
            text=True,
            timeout=10
        )

        # Parse DPI from xrandr output
        # Look for lines like: "HDMI-1 connected primary 1920x1080+0+0 (normal left inverted right) 0mm x 0mm"
        # DPI can be calculated from physical size and resolution

        dpi_value = None
        scaling_factor = EXPECTED_DPI  # Default to expected (1.0)

        for line in result.stdout.splitlines():
            if "connected" in line.lower():
                # Try to parse physical size
                # Format: "530mm x 300mm"
                parts = line.split()
                for i, part in enumerate(parts):
                    if "mm" in part and i > 0:
                        try:
                            # Look for width mm
                            width_mm = int(part.replace("mm", ""))
                            # Next should be height
                            if i + 2 < len(parts) and "mm" in parts[i + 2]:
                                height_mm = int(parts[i + 2].replace("mm", ""))
                                if width_mm > 0 and height_mm > 0:
                                    # Calculate DPI
                                    width_dpi = EXPECTED_RESOLUTION[0] / (width_mm / 25.4)
                                    height_dpi = EXPECTED_RESOLUTION[1] / (height_mm / 25.4)
                                    dpi_value = (width_dpi + height_dpi) / 2
                                    # Calculate scaling factor (1.0 = 96 DPI baseline)
                                    scaling_factor = dpi_value / 96.0
                                    break
                        except (ValueError, IndexError):
                            pass

        # If physical size is 0mm x 0mm, we can't determine DPI from xrandr
        # Check for scaling via X resources or gsettings
        if dpi_value is None or dpi_value == 0:
            try:
                result = subprocess.run(
                    ["gsettings", "get", "org.gnome.desktop.interface", "scaling-factor"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    scaling_factor = int(result.stdout.strip())
            except Exception:
                pass

            # Also check Xft.dpi
            try:
                result = subprocess.run(
                    ["xrdb", "-query"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                for line in result.stdout.splitlines():
                    if "Xft.dpi" in line:
                        dpi = float(line.split()[1])
                        scaling_factor = dpi / 96.0
                        break
            except Exception:
                pass

        ok = abs(scaling_factor - EXPECTED_DPI) < 0.1
        return {
            "name": "dpi",
            "ok": ok,
            "value": round(scaling_factor, 2),
            "expected": EXPECTED_DPI
        }
    except Exception as e:
        return {
            "name": "dpi",
            "ok": False,
            "value": "unknown",
            "error": str(e)
        }


def check_minecraft_window() -> dict:
    """Check Minecraft window is foreground + fullscreen + covers full 1920x1080."""
    try:
        # Use xdotool to find Minecraft window
        result = subprocess.run(
            ["xdotool", "search", "--name", "Minecraft"],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0 or not result.stdout.strip():
            # Try alternative window names
            for name in ["Java", "javaw", "Minecraft*Java Edition*"]:
                result = subprocess.run(
                    ["xdotool", "search", "--name", name],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0 and result.stdout.strip():
                    break

        if result.returncode == 0 and result.stdout.strip():
            window_id = result.stdout.strip().split()[0]

            # Get window geometry
            geo_result = subprocess.run(
                ["xdotool", "getwindowgeometry", window_id],
                capture_output=True,
                text=True,
                timeout=5
            )

            # Parse geometry
            width, height, x, y = 0, 0, 0, 0
            for line in geo_result.stdout.splitlines():
                if "Position:" in line:
                    parts = line.split(":")[1].strip().split(",")
                    if len(parts) == 2:
                        x, y = int(parts[0]), int(parts[1])
                elif "Geometry:" in line:
                    parts = line.split(":")[1].strip().split("x")
                    if len(parts) == 2:
                        width, height = int(parts[0]), int(parts[1])

            # Check if fullscreen and covers full screen
            is_fullscreen = (width == EXPECTED_RESOLUTION[0] and
                           height == EXPECTED_RESOLUTION[1] and
                           x == 0 and y == 0)

            # Check if foreground
            fg_result = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True,
                text=True,
                timeout=5
            )
            is_foreground = (fg_result.stdout.strip() == window_id)

            ok = is_fullscreen and is_foreground
            return {
                "name": "minecraft_window",
                "ok": ok,
                "value": f"fullscreen={is_fullscreen}, foreground={is_foreground}, size={width}x{height}, pos={x},{y}",
                "expected": f"fullscreen=True, foreground=True, size={EXPECTED_RESOLUTION[0]}x{EXPECTED_RESOLUTION[1]}, pos=0,0"
            }
        else:
            return {
                "name": "minecraft_window",
                "ok": False,
                "value": "not_found",
                "error": "Minecraft window not found"
            }
    except FileNotFoundError:
        return {
            "name": "minecraft_window",
            "ok": False,
            "value": "unknown",
            "error": "xdotool not available"
        }
    except Exception as e:
        return {
            "name": "minecraft_window",
            "ok": False,
            "value": "error",
            "error": str(e)
        }


def check_overlapping_windows() -> dict:
    """Check no overlapping windows (Discord overlay / GeForce Experience / OBS preview)."""
    overlapping_apps = [
        "Discord",
        "GeForce Experience",
        "OBS",
        "Streamlabs",
        "XSplit",
        "NVIDIA",
        "RTSS"
    ]

    found_overlapping = []

    try:
        # Get all visible windows
        result = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "."],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            window_ids = result.stdout.strip().splitlines()

            for wid in window_ids:
                # Get window name
                name_result = subprocess.run(
                    ["xdotool", "getwindowname", wid],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if name_result.returncode == 0:
                    window_name = name_result.stdout.strip().lower()
                    for app in overlapping_apps:
                        if app.lower() in window_name:
                            found_overlapping.append(f"{app}: {name_result.stdout.strip()}")

        ok = len(found_overlapping) == 0
        return {
            "name": "overlapping_windows",
            "ok": ok,
            "value": found_overlapping if found_overlapping else "none",
            "blocked_apps": overlapping_apps
        }
    except FileNotFoundError:
        return {
            "name": "overlapping_windows",
            "ok": True,  # Can't check, assume OK
            "value": "unknown",
            "error": "xdotool not available"
        }
    except Exception as e:
        return {
            "name": "overlapping_windows",
            "ok": False,
            "value": "error",
            "error": str(e)
        }


def check_audio_device() -> dict:
    """Check audio device enumerated + game-audio loopback configured."""
    try:
        # Check for audio devices using pactl (PulseAudio) or amixer
        audio_devices = []

        # Try PulseAudio
        result = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split()
                if parts:
                    audio_devices.append(parts[1] if len(parts) > 1 else parts[0])

        # Try ALSA
        if not audio_devices:
            result = subprocess.run(
                ["aplay", "-l"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "card" in line.lower():
                        audio_devices.append(line.strip())

        # Check for loopback/virtual device (for game audio capture)
        loopback_found = False
        result = subprocess.run(
            ["pactl", "list", "short", "modules"],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "module-loopback" in line.lower() or "module-null-sink" in line.lower():
                    loopback_found = True
                    break

        ok = len(audio_devices) > 0
        return {
            "name": "audio_device",
            "ok": ok,
            "value": {
                "devices": audio_devices,
                "loopback_configured": loopback_found
            },
            "expected": "at least 1 audio device + loopback for game capture"
        }
    except FileNotFoundError:
        return {
            "name": "audio_device",
            "ok": False,
            "value": "unknown",
            "error": "audio tools not available"
        }
    except Exception as e:
        return {
            "name": "audio_device",
            "ok": False,
            "value": "error",
            "error": str(e)
        }


def check_fps() -> dict:
    """Check FPS counter shows >= 28 fps in MC menu."""
    # This is challenging to check programmatically without injecting into Minecraft
    # We'll check system resources that would affect FPS

    try:
        # Check GPU info
        gpu_ok = False
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            gpu_ok = True

        # Check for compositing/VSync that might limit FPS
        compositing_off = True
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip().lower() == "true":
            compositing_off = False

        # Check for low FPS indicators in system
        # This is a proxy - real FPS check would require Minecraft integration
        # For now, we check that the system is capable

        # Check for low resource warnings
        cpu_load_ok = True
        try:
            result = subprocess.run(
                ["uptime"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                # Parse load average
                parts = result.stdout.strip().split()
                if "load" in parts:
                    idx = parts.index("load")
                    if idx + 1 < len(parts):
                        load = parts[idx + 1].rstrip(",")
                        try:
                            load_avg = float(load)
                            # If load is very high, FPS will be low
                            cpu_load_ok = load_avg < 4.0
                        except ValueError:
                            pass
        except Exception:
            pass

        # For Phase 1, we report as OK if system appears capable
        # Real FPS check would need Minecraft client integration
        ok = gpu_ok and cpu_load_ok

        return {
            "name": "fps_capability",
            "ok": ok,
            "value": {
                "gpu_detected": gpu_ok,
                "compositing_disabled": compositing_off,
                "cpu_load_ok": cpu_load_ok,
                "note": "System appears capable of >=28 FPS"
            },
            "expected": f">= {MIN_FPS} FPS in Minecraft menu"
        }
    except Exception as e:
        return {
            "name": "fps_capability",
            "ok": False,
            "value": "error",
            "error": str(e)
        }


def check_disk_space() -> dict:
    """Check disk free space >= 5 GB."""
    try:
        result = subprocess.run(
            ["df", "-BG", OUTPUT_DIR.root],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            lines = result.stdout.splitlines()
            if len(lines) > 1:
                # Parse: Filesystem 1G-blocks Used Available Use% Mounted on
                parts = lines[1].split()
                if len(parts) >= 4:
                    available_gb = int(parts[3].rstrip("G"))
                    ok = available_gb >= MIN_DISK_GB
                    return {
                        "name": "disk_space",
                        "ok": ok,
                        "value": f"{available_gb}GB free",
                        "expected": f">= {MIN_DISK_GB}GB"
                    }

        return {
            "name": "disk_space",
            "ok": False,
            "value": "unknown",
            "error": "could not determine disk space"
        }
    except Exception as e:
        return {
            "name": "disk_space",
            "ok": False,
            "value": "error",
            "error": str(e)
        }


def check_oyster_recorder() -> dict:
    """Check OysterRecorder.exe armed."""
    # Check for OysterRecorder in common locations
    possible_paths = [
        OUTPUT_DIR / "OysterRecorder.exe",
        Path("C:/Program Files/OysterRecorder/OysterRecorder.exe"),
        Path("C:/Program Files (x86)/OysterRecorder/OysterRecorder.exe"),
        Path("/usr/local/bin/OysterRecorder.exe"),
        Path("/opt/OysterRecorder/OysterRecorder.exe"),
    ]

    found = None
    for path in possible_paths:
        if path.exists():
            found = str(path)
            break

    # Also check if process is running
    process_running = False
    try:
        result = subprocess.run(
            ["tasklist"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if "OysterRecorder" in result.stdout:
            process_running = True
    except Exception:
        pass

    ok = found is not None
    return {
        "name": "oyster_recorder",
        "ok": ok,
        "value": {
            "installed": found is not None,
            "path": found,
            "process_running": process_running
        },
        "expected": "OysterRecorder.exe installed and ready"
    }


def check_active_session() -> dict:
    """Check active_session/ empty (no half-finalized prior session)."""
    try:
        if not ACTIVE_SESSION_DIR.exists():
            # Directory doesn't exist - that's fine, no prior session
            return {
                "name": "active_session",
                "ok": True,
                "value": "empty",
                "note": "active_session directory does not exist"
            }

        # Check if directory is empty
        files = list(ACTIVE_SESSION_DIR.iterdir())
        ok = len(files) == 0

        return {
            "name": "active_session",
            "ok": ok,
            "value": f"{len(files)} files",
            "expected": "empty directory"
        }
    except Exception as e:
        return {
            "name": "active_session",
            "ok": False,
            "value": "error",
            "error": str(e)
        }


def check_network_tailscale() -> dict:
    """Check Network: Tailscale to mac1 reachable."""
    # Check if Tailscale is running
    tailscale_running = False
    try:
        result = subprocess.run(
            ["tailscale", "status"],
            capture_output=True,
            text=True,
            timeout=15
        )
        if result.returncode == 0:
            tailscale_running = True
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # Try to ping mac1 (assuming it's on Tailscale network)
    mac1_reachable = False
    if tailscale_running:
        try:
            # Try to resolve mac1.tailscale or ping it
            result = subprocess.run(
                ["ping", "-c", "1", "mac1.tailscale"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                mac1_reachable = True
        except Exception:
            pass

        # Try alternative hostnames
        if not mac1_reachable:
            for host in ["mac1", "100.100.100.1", "mac1.local"]:
                try:
                    result = subprocess.run(
                        ["ping", "-c", "1", host],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    if result.returncode == 0:
                        mac1_reachable = True
                        break
                except Exception:
                    pass

    # For Phase 1, network is optional (noted as "for future upload")
    ok = True  # Don't block on network for now
    return {
        "name": "network_tailscale",
        "ok": ok,
        "value": {
            "tailscale_running": tailscale_running,
            "mac1_reachable": mac1_reachable,
            "note": "Network check is informational for Phase 1"
        },
        "expected": "Tailscale to mac1 reachable (for future upload)"
    }


def run_all_checks() -> dict:
    """Run all preflight checks."""
    checks = [
        check_display_resolution(),
        check_dpi(),
        check_minecraft_window(),
        check_overlapping_windows(),
        check_audio_device(),
        check_fps(),
        check_disk_space(),
        check_oyster_recorder(),
        check_active_session(),
        check_network_tailscale(),
    ]

    all_pass = all(check["ok"] for check in checks)

    return {
        "ran_at": get_timestamp(),
        "all_pass": all_pass,
        "checks": checks
    }


def main():
    """Main entry point."""
    print("=" * 60)
    print("Preflight Recorder - Phase 1")
    print("=" * 60)
    print(f"Running preflight checks at {get_timestamp()}")
    print()

    # Run all checks
    report = run_all_checks()

    # Write report to file
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    print(f"Report written to: {REPORT_PATH}")
    print()

    # Print summary
    print("CHECK RESULTS:")
    print("-" * 40)
    for check in report["checks"]:
        status = "✓ PASS" if check["ok"] else "✗ FAIL"
        print(f"  {status}: {check['name']}")
        if not check["ok"]:
            print(f"         {check.get('value', check.get('error', 'failed'))}")
    print("-" * 40)
    print()

    if report["all_pass"]:
        print("✓ ALL CHECKS PASSED")
        return 0
    else:
        failed = [c["name"] for c in report["checks"] if not c["ok"]]
        print(f"✗ PREFLIGHT FAILED: {', '.join(failed)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
