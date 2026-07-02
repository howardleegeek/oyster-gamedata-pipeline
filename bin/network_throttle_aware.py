#!/usr/bin/env python3
"""
Network Throttle Awareness Module.

Detects metered/cellular/paid networks via Windows NetworkInformation API
and macOS NWPath; provides upload throttling on metered connections.
"""

from __future__ import annotations

import argparse
import platform
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# Lazy imports for optional dependencies
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


class NetworkType:
    """Network type classification."""
    UNMETERED = "unmetered"
    METERED = "metered"
    CELLULAR = "cellular"
    UNKNOWN = "unknown"


class NetworkThrottleAware:
    """Detects network type and manages upload throttling."""

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self._config_path = config_path or self._default_config_path()
        self._is_metered: bool = False
        self._paused: bool = False
        self._lock = threading.Lock()
        self._config = self._load_config()

    def _default_config_path(self) -> Path:
        """Get default config path."""
        base = Path.home() / ".config" / "g247"
        base.mkdir(parents=True, exist_ok=True)
        return base / "network_throttle.yaml"

    def _load_config(self) -> dict:
        """Load configuration from YAML file."""
        default_config = {
            "pause_on_metered": True,
            "check_interval_seconds": 30,
            "log_level": "INFO",
        }
        if not YAML_AVAILABLE or not self._config_path.exists():
            return default_config
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                user_config = yaml.safe_load(f) or {}
            default_config.update(user_config)
        except Exception:
            pass
        return default_config

    def detect_network_type(self) -> str:
        """Detect current network type (metered/unmetered/cellular/unknown)."""
        system = platform.system()
        if system == "Windows":
            return self._detect_windows()
        elif system == "Darwin":
            return self._detect_macos()
        else:
            return NetworkType.UNKNOWN

    def _detect_windows(self) -> str:
        """Detect network type on Windows using NetworkInformation API."""
        try:
            import ctypes
            from ctypes import wintypes

            # NetworkCostType enum values
            NETWORK_COST_TYPE_UNRESTRICTED = 1
            NETWORK_COST_TYPE_FIXED = 2
            NETWORK_COST_TYPE_VARIABLE = 3

            # Define the GetNetworkInformation function
            nlas = ctypes.windll.networkapis
            nlas.GetNetworkInformation.argtypes = [wintypes.LPCWSTR]
            nlas.GetNetworkInformation.restype = ctypes.c_void_p

            # Use default network
            result = nlas.GetNetworkInformation("")
            if result:
                # Get cost info
                nlas.GetNetworkCost.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG)]
                nlas.GetNetworkCost.restype = ctypes.c_int
                cost = wintypes.ULONG()
                if nlas.GetNetworkCost(result, ctypes.byref(cost)) == 0:
                    cost_value = cost.value
                    if cost_value == NETWORK_COST_TYPE_UNRESTRICTED:
                        return NetworkType.UNMETERED
                    elif cost_value in (NETWORK_COST_TYPE_FIXED, NETWORK_COST_TYPE_VARIABLE):
                        return NetworkType.METERED
        except Exception:
            pass
        return NetworkType.UNKNOWN

    def _detect_macos(self) -> str:
        """Detect network type on macOS using NWPath."""
        try:
            # Use scutil to get network information
            import subprocess
            result = subprocess.run(
                ["scutil", "--nwi"],
                capture_output=True,
                text=True,
                check=False,
            )
            output = result.stdout.lower()
            # Check for common metered indicators
            if "cellular" in output or "mobile" in output:
                return NetworkType.CELLULAR
            # Check for metered flag in network information
            if "metered" in output:
                return NetworkType.METERED
            # Check for WiFi interface
            if "en0" in output or "en1" in output:
                return NetworkType.UNMETERED
        except Exception:
            pass
        return NetworkType.UNKNOWN

    def check_and_update(self) -> bool:
        """Check network type and update throttling state. Returns True if state changed."""
        network_type = self.detect_network_type()
        should_pause = (
            network_type in (NetworkType.METERED, NetworkType.CELLULAR)
            and self._config.get("pause_on_metered", True)
        )
        with self._lock:
            if should_pause != self._paused:
                self._paused = should_pause
                self._is_metered = network_type != NetworkType.UNMETERED
                return True
            self._is_metered = network_type != NetworkType.UNMETERED
        return False

    @property
    def is_paused(self) -> bool:
        """Check if uploads are currently paused."""
        with self._lock:
            return self._paused

    @property
    def is_metered(self) -> bool:
        """Check if current network is metered."""
        with self._lock:
            return self._is_metered

    def get_status(self) -> dict:
        """Get current network throttle status."""
        with self._lock:
            return {
                "paused": self._paused,
                "metered": self._is_metered,
                "network_type": self.detect_network_type(),
                "config": self._config.copy(),
            }

    def set_pause_on_metered(self, enabled: bool) -> None:
        """Enable or disable auto-pause on metered networks."""
        self._config["pause_on_metered"] = enabled
        self._save_config()

    def _save_config(self) -> None:
        """Save configuration to YAML file."""
        if YAML_AVAILABLE:
            try:
                self._config_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._config_path, "w", encoding="utf-8") as f:
                    yaml.safe_dump(self._config, f, default_flow_style=False)
            except Exception:
                pass


def run_monitor(interval: int = 30) -> None:
    """Run continuous network monitoring."""
    monitor = NetworkThrottleAware()
    print(f"Starting network throttle monitor (interval: {interval}s)")
    print("Press Ctrl+C to stop")

    try:
        while True:
            changed = monitor.check_and_update()
            status = monitor.get_status()
            if changed:
                state = "PAUSED" if status["paused"] else "ACTIVE"
                print(f"Network: {status['network_type']} | Uploads: {state}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopping monitor...")


def main(argv: list[str]) -> int:
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description="Network throttle awareness tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s status          Show current network status
  %(prog)s monitor         Run continuous monitoring
  %(prog)s enable          Enable auto-pause on metered networks
  %(prog)s disable         Disable auto-pause on metered networks
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Status command
    subparsers.add_parser("status", help="Show current network status")

    # Monitor command
    monitor_parser = subparsers.add_parser("monitor", help="Run continuous monitoring")
    monitor_parser.add_argument(
        "-i", "--interval",
        type=int,
        default=30,
        help="Check interval in seconds (default: 30)",
    )

    # Enable/Disable commands
    subparsers.add_parser("enable", help="Enable auto-pause on metered networks")
    subparsers.add_parser("disable", help="Disable auto-pause on metered networks")

    args = parser.parse_args(argv[1:] if argv else ["--help"])

    if not args.command or args.command == "status":
        monitor = NetworkThrottleAware()
        status = monitor.get_status()
        print(f"Network Type: {status['network_type']}")
        print(f"Metered: {status['metered']}")
        print(f"Uploads Paused: {status['paused']}")
        print(f"Auto-pause enabled: {status['config'].get('pause_on_metered', True)}")
        return 0

    if args.command == "monitor":
        run_monitor(interval=args.interval)
        return 0

    if args.command == "enable":
        monitor = NetworkThrottleAware()
        monitor.set_pause_on_metered(True)
        print("Auto-pause on metered networks: ENABLED")
        return 0

    if args.command == "disable":
        monitor = NetworkThrottleAware()
        monitor.set_pause_on_metered(False)
        print("Auto-pause on metered networks: DISABLED")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
