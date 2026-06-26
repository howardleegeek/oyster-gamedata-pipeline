#!/usr/bin/env python3
"""
R041 · bin/preflight_check.py — vendor pre-run env validation

Independent CLI tool vendor runs FIRST (before any pipeline) to verify
hardware + network + deps. Distinct from doctor.sh — Python, JSON output,
CI-integrable, more granular checks.
"""

import argparse
import json
import os
import shutil
import socket
import sys


def check_disk_free(path: str = "/tmp", min_gb: float = 100) -> dict:
    """Check if disk has at least min_gb free space at path."""
    try:
        stat = shutil.disk_usage(path)
        free_gb = stat.free / (1024**3)
        ok = free_gb >= min_gb
        return {
            "name": "disk_free",
            "ok": ok,
            "message": f"{free_gb:.1f} GB free at {path}",
            "details": {"path": path, "min_gb": min_gb, "free_gb": round(free_gb, 2)},
        }
    except Exception as e:
        return {"name": "disk_free", "ok": False, "message": str(e), "details": {}}


def check_ram(min_gb: int = 16) -> dict:
    """Check if system has at least min_gb of RAM."""
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    total_gb = kb / (1024**2)
                    ok = total_gb >= min_gb
                    return {
                        "name": "ram",
                        "ok": ok,
                        "message": f"{total_gb:.1f} GB RAM total",
                        "details": {"min_gb": min_gb, "total_gb": round(total_gb, 2)},
                    }
        return {"name": "ram", "ok": False, "message": "Could not read meminfo", "details": {}}
    except Exception as e:
        return {"name": "ram", "ok": False, "message": str(e), "details": {}}


def check_cpu_cores(min_cores: int = 4) -> dict:
    """Check if system has at least min_cores CPU cores."""
    try:
        cores = os.cpu_count() or 0
        ok = cores >= min_cores
        return {
            "name": "cpu_cores",
            "ok": ok,
            "message": f"{cores} CPU cores available",
            "details": {"min_cores": min_cores, "cores": cores},
        }
    except Exception as e:
        return {"name": "cpu_cores", "ok": False, "message": str(e), "details": {}}


def check_python_version(min_major: int = 3, min_minor: int = 10) -> dict:
    """Check if Python version is at least min_major.min_minor."""
    try:
        version = sys.version_info
        ok = (version.major > min_major) or (
            version.major == min_major and version.minor >= min_minor
        )
        version_str = f"{version.major}.{version.minor}.{version.micro}"
        return {
            "name": "python_version",
            "ok": ok,
            "message": f"Python {version_str}",
            "details": {"min_version": f"{min_major}.{min_minor}", "version": version_str},
        }
    except Exception as e:
        return {"name": "python_version", "ok": False, "message": str(e), "details": {}}


def check_network_upload_mbps(min_mbps: float = 50, target: str = "https://speedtest.net") -> dict:
    """Check network upload speed (simulated check via connectivity)."""
    try:
        # Simple connectivity check - in production would measure actual speed
        import urllib.request

        start = __import__("time").time()
        try:
            urllib.request.urlopen(target, timeout=10)
            elapsed = __import__("time").time() - start
            # Rough estimate: if we can fetch in <2s, assume decent connection
            ok = elapsed < 2.0
            return {
                "name": "network_upload",
                "ok": ok,
                "message": f"Network reachable ({elapsed:.2f}s response)",
                "details": {"target": target, "response_time_s": round(elapsed, 2)},
            }
        except Exception as e:
            return {
                "name": "network_upload",
                "ok": False,
                "message": f"Network unreachable: {e}",
                "details": {"target": target},
            }
    except Exception as e:
        return {"name": "network_upload", "ok": False, "message": str(e), "details": {}}


def check_network_latency_ms(max_ms: int = 20, target: str = "8.8.8.8") -> dict:
    """Check network latency to target host."""
    try:
        start = __import__("time").time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect((target, 53))
            sock.close()
            elapsed = (__import__("time").time() - start) * 1000
            ok = elapsed <= max_ms
            return {
                "name": "network_latency",
                "ok": ok,
                "message": f"{elapsed:.0f}ms latency to {target}",
                "details": {"target": target, "max_ms": max_ms, "latency_ms": round(elapsed, 0)},
            }
        except socket.error as e:
            return {
                "name": "network_latency",
                "ok": False,
                "message": f"Cannot reach {target}: {e}",
                "details": {"target": target},
            }
    except Exception as e:
        return {"name": "network_latency", "ok": False, "message": str(e), "details": {}}


def check_port_available(port: int) -> dict:
    """Check if a port is available (not in use)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        try:
            sock.bind(("127.0.0.1", port))
            sock.close()
            ok = True
            message = f"Port {port} is available"
        except OSError:
            ok = False
            message = f"Port {port} is in use"
        return {"name": "port_available", "ok": ok, "message": message, "details": {"port": port}}
    except Exception as e:
        return {"name": "port_available", "ok": False, "message": str(e), "details": {}}


def check_command_exists(cmd: str) -> dict:
    """Check if a command exists in PATH."""
    try:
        ok = shutil.which(cmd) is not None
        return {
            "name": "command_exists",
            "ok": ok,
            "message": f'Command "{cmd}" {"found" if ok else "not found"}',
            "details": {"command": cmd},
        }
    except Exception as e:
        return {"name": "command_exists", "ok": False, "message": str(e), "details": {}}


def run_all_checks(skip_network: bool = False, test_port: int = None) -> dict:
    """Run all preflight checks and return results."""
    checks = []

    # Hardware checks
    checks.append(check_disk_free())
    checks.append(check_ram())
    checks.append(check_cpu_cores())
    checks.append(check_python_version())

    # Network checks (optional)
    if not skip_network:
        checks.append(check_network_latency_ms())
        checks.append(check_network_upload_mbps())

    # Port check (if specified)
    if test_port is not None:
        checks.append(check_port_available(test_port))

    # Determine overall status
    all_ok = all(c["ok"] for c in checks)

    return {
        "ok": all_ok,
        "checks": checks,
        "summary": {
            "total": len(checks),
            "passed": sum(1 for c in checks if c["ok"]),
            "failed": sum(1 for c in checks if not c["ok"]),
        },
    }


def main(argv: list = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Vendor pre-run environment validation")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument(
        "--no-network", action="store_true", help="Skip network connectivity checks"
    )
    parser.add_argument(
        "--port-test", type=int, default=None, help="Test if specific port is available"
    )

    args = parser.parse_args(argv)

    result = run_all_checks(skip_network=args.no_network, test_port=args.port_test)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("=== Preflight Check Results ===")
        for check in result["checks"]:
            status = "PASS" if check["ok"] else "FAIL"
            print(f"[{status}] {check['name']}: {check['message']}")
        print(f"\nSummary: {result['summary']['passed']}/{result['summary']['total']} passed")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
