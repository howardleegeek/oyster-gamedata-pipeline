#!/usr/bin/env python3
"""
oyster_monitor.py — Background daemon that polls system health every 60 seconds.

Polls:
  - /api/health endpoints of: marketplace_api, dashboard, payout_engine, depth_endpoint
  - Local recorder daemon state (via ~/.oyster/daemon_state.json)
  - Upload daemon backlog size
  - Recent error count from ~/.oyster/*.log files
  - Free disk on minipc1 (via SSH ping)

Outputs to ~/.oyster/monitor_metrics.jsonl (one line per poll).
"""

import glob
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests
import yaml

# ---------------------------------------------------------------------------
# Paths & Config
# ---------------------------------------------------------------------------

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "monitor_thresholds.yaml")

def load_config() -> dict:
    """Load thresholds config from YAML."""
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)

def expand_oyster_path(path: str) -> str:
    """Expand ~ in oyster paths."""
    return os.path.expanduser(path)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("oyster_monitor")

# ---------------------------------------------------------------------------
# Health Checkers
# ---------------------------------------------------------------------------

class HealthChecker:
    """Check /api/health endpoints."""

    def __init__(self, endpoints: dict[str, str], timeout: int = 10):
        self.endpoints = endpoints
        self.timeout = timeout
        self.results: dict[str, dict] = {}

    def check_all(self) -> dict[str, dict]:
        """Check all endpoints and return results."""
        for name, url in self.endpoints.items():
            self.results[name] = self._check_endpoint(name, url)
        return self.results

    def _check_endpoint(self, name: str, url: str) -> dict:
        """Check a single endpoint."""
        try:
            resp = requests.get(url, timeout=self.timeout)
            return {
                "name": name,
                "url": url,
                "status": "healthy" if resp.status_code == 200 else "unhealthy",
                "status_code": resp.status_code,
                "response_time_ms": resp.elapsed.total_seconds() * 1000,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": None,
            }
        except requests.exceptions.ConnectionError:
            return {
                "name": name,
                "url": url,
                "status": "unhealthy",
                "status_code": None,
                "response_time_ms": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": f"ConnectionError on {url}",
            }
        except requests.exceptions.Timeout:
            return {
                "name": name,
                "url": url,
                "status": "unhealthy",
                "status_code": None,
                "response_time_ms": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": f"Timeout on {url}",
            }
        except Exception as e:
            return {
                "name": name,
                "url": url,
                "status": "unhealthy",
                "status_code": None,
                "response_time_ms": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
            }


class DaemonStateChecker:
    """Check local recorder daemon state."""

    def __init__(self, state_file: str):
        self.state_file = expand_oyster_path(state_file)

    def check(self) -> dict:
        """Read daemon state file and return status."""
        try:
            with open(self.state_file, "r") as f:
                state = json.load(f)
            return {
                "status": "ok",
                "daemon_state": state.get("state", "unknown"),
                "last_updated": state.get("last_updated", None),
                "game_state_file": state.get("game_state_file", None),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except FileNotFoundError:
            return {
                "status": "error",
                "daemon_state": "unknown",
                "last_updated": None,
                "game_state_file": None,
                "error": f"State file not found: {self.state_file}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except json.JSONDecodeError as e:
            return {
                "status": "error",
                "daemon_state": "unknown",
                "last_updated": None,
                "game_state_file": None,
                "error": f"Invalid JSON in state file: {e}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }


class UploadBacklogChecker:
    """Check upload daemon backlog size."""

    def __init__(self, oyster_dir: str):
        self.oyster_dir = expand_oyster_path(oyster_dir)

    def check(self) -> dict:
        """Estimate upload backlog size in GB."""
        backlog_dir = os.path.join(self.oyster_dir, "upload_backlog")
        total_bytes = 0
        file_count = 0

        if os.path.isdir(backlog_dir):
            for root, _dirs, files in os.walk(backlog_dir):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        total_bytes += os.path.getsize(fp)
                        file_count += 1
                    except OSError as exc:
                        log.debug("oyster_monitor: skipping unreadable backlog file %s: %s", fp, exc)

        backlog_gb = total_bytes / (1024 ** 3)
        return {
            "status": "ok",
            "backlog_gb": round(backlog_gb, 2),
            "backlog_bytes": total_bytes,
            "file_count": file_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


class ErrorRateChecker:
    """Count recent errors from ~/.oyster/*.log files."""

    def __init__(self, oyster_dir: str, window_minutes: int = 5):
        self.oyster_dir = expand_oyster_path(oyster_dir)
        self.window_minutes = window_minutes

    def check(self) -> dict:
        """Count errors in recent log files."""
        log_pattern = os.path.join(self.oyster_dir, "*.log")
        log_files = glob.glob(log_pattern)

        cutoff = time.time() - (self.window_minutes * 60)
        error_count = 0
        error_lines: list[str] = []

        for log_file in log_files:
            try:
                with open(log_file, "r") as f:
                    for line in f:
                        # Check if line contains error indicators
                        if re.search(r'\b(ERROR|CRITICAL|FATAL|Exception|Traceback)\b', line, re.IGNORECASE):
                            # Try to extract timestamp
                            ts_match = re.match(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})', line)
                            if ts_match:
                                try:
                                    ts = datetime.fromisoformat(ts_match.group(1).replace(" ", "T"))
                                    if ts.timestamp() >= cutoff:
                                        error_count += 1
                                        if len(error_lines) < 5:
                                            error_lines.append(line.strip())
                                except (ValueError, OSError):
                                    error_count += 1
                            else:
                                # No timestamp, count it
                                error_count += 1
            except (OSError, IOError) as exc:
                log.debug("oyster_monitor: skipping unreadable error log %s: %s", log_file, exc)

        error_rate_per_min = error_count / max(self.window_minutes, 1)
        return {
            "status": "ok",
            "error_count": error_count,
            "error_rate_per_min": round(error_rate_per_min, 2),
            "window_minutes": self.window_minutes,
            "sample_errors": error_lines,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


class DiskChecker:
    """Check free disk on minipc1 via SSH."""

    def __init__(self, host: str, user: str, timeout: int = 10):
        self.host = host
        self.user = user
        self.timeout = timeout

    def check(self) -> dict:
        """SSH to minipc1 and check free disk space."""
        try:
            cmd = ["ssh", "-o", f"ConnectTimeout={self.timeout}", "-o", "StrictHostKeyChecking=no",
                   f"{self.user}@{self.host}", "df -BG / | tail -1 | awk '{print $4}'"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout + 5)

            if result.returncode == 0:
                free_gb = int(result.stdout.strip().rstrip("G"))
                return {
                    "status": "ok",
                    "free_disk_gb": free_gb,
                    "host": self.host,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            else:
                return {
                    "status": "error",
                    "free_disk_gb": None,
                    "host": self.host,
                    "error": f"SSH command failed: {result.stderr.strip()}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "free_disk_gb": None,
                "host": self.host,
                "error": "SSH connection timed out",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except FileNotFoundError:
            return {
                "status": "error",
                "free_disk_gb": None,
                "host": self.host,
                "error": "ssh command not found",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {
                "status": "error",
                "free_disk_gb": None,
                "host": self.host,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }


class DaemonStuckChecker:
    """Check if daemon is stuck in same state for too long."""

    def __init__(self, state_file: str, oyster_dir: str, stuck_minutes: int = 30):
        self.state_file = expand_oyster_path(state_file)
        self.oyster_dir = expand_oyster_path(oyster_dir)
        self.stuck_minutes = stuck_minutes

    def check(self) -> dict:
        """Check if daemon state hasn't changed in stuck_minutes."""
        try:
            with open(self.state_file, "r") as f:
                state = json.load(f)

            daemon_state = state.get("state", "unknown")
            last_updated_str = state.get("last_updated", None)
            game_state_file = state.get("game_state_file", None)

            if last_updated_str:
                last_updated = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                age_minutes = (now - last_updated).total_seconds() / 60
            else:
                age_minutes = 0

            # Check game_state.jsonl growth if in RECORDING state
            game_state_growing = True
            if daemon_state == "RECORDING" and game_state_file:
                game_state_path = expand_oyster_path(game_state_file)
                if os.path.exists(game_state_path):
                    # Check if file has been modified recently
                    mtime = os.path.getmtime(game_state_path)
                    age_since_mod = (time.time() - mtime) / 60
                    game_state_growing = age_since_mod < self.stuck_minutes
                else:
                    game_state_growing = False

            is_stuck = (age_minutes > self.stuck_minutes) or (daemon_state == "RECORDING" and not game_state_growing)

            return {
                "status": "stuck" if is_stuck else "ok",
                "daemon_state": daemon_state,
                "state_age_minutes": round(age_minutes, 1),
                "game_state_growing": game_state_growing,
                "is_stuck": is_stuck,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except (FileNotFoundError, json.JSONDecodeError) as e:
            return {
                "status": "error",
                "daemon_state": "unknown",
                "state_age_minutes": None,
                "game_state_growing": None,
                "is_stuck": False,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }


# ---------------------------------------------------------------------------
# Metrics Writer
# ---------------------------------------------------------------------------

class MetricsWriter:
    """Write metrics to JSONL file."""

    def __init__(self, metrics_file: str):
        self.metrics_file = expand_oyster_path(metrics_file)
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.metrics_file), exist_ok=True)

    def write(self, metrics: dict[str, Any]):
        """Append a metrics line to the JSONL file."""
        metrics["poll_timestamp"] = datetime.now(timezone.utc).isoformat()
        with open(self.metrics_file, "a") as f:
            f.write(json.dumps(metrics) + "\n")


# ---------------------------------------------------------------------------
# Main Monitor Loop
# ---------------------------------------------------------------------------

class OysterMonitor:
    """Main monitor daemon."""

    def __init__(self, config: dict):
        self.config = config
        self.poll_interval = config.get("poll_interval_seconds", 60)
        self.running = True

        # Initialize checkers
        self.health_checker = HealthChecker(config.get("health_endpoints", {}))
        self.daemon_state_checker = DaemonStateChecker(config.get("daemon_state_file", "~/.oyster/daemon_state.json"))
        self.upload_backlog_checker = UploadBacklogChecker(config.get("oyster_dir", "~/.oyster"))
        self.error_rate_checker = ErrorRateChecker(config.get("oyster_dir", "~/.oyster"))
        self.disk_checker = DiskChecker(
            config.get("minipc1_host", "minipc1"),
            config.get("minipc1_user", "oyster"),
        )
        self.daemon_stuck_checker = DaemonStuckChecker(
            config.get("daemon_state_file", "~/.oyster/daemon_state.json"),
            config.get("oyster_dir", "~/.oyster"),
            config.get("daemon_stuck_minutes", 30),
        )
        self.metrics_writer = MetricsWriter(config.get("metrics_file", "~/.oyster/monitor_metrics.jsonl"))

        # Signal handling
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum, frame):
        log.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def poll(self) -> dict:
        """Run one full poll cycle."""
        log.info("Starting poll cycle...")

        # Run all checks
        health_results = self.health_checker.check_all()
        daemon_state = self.daemon_state_checker.check()
        upload_backlog = self.upload_backlog_checker.check()
        error_rate = self.error_rate_checker.check()
        disk = self.disk_checker.check()
        daemon_stuck = self.daemon_stuck_checker.check()

        # Compile metrics
        metrics = {
            "health": health_results,
            "daemon_state": daemon_state,
            "upload_backlog": upload_backlog,
            "error_rate": error_rate,
            "disk": disk,
            "daemon_stuck": daemon_stuck,
        }

        # Write metrics
        self.metrics_writer.write(metrics)
        log.info(f"Poll cycle complete. Metrics written to {self.metrics_writer.metrics_file}")

        return metrics

    def run(self):
        """Main loop — polls every 60 seconds."""
        log.info(f"Oyster Monitor starting. Poll interval: {self.poll_interval}s")
        log.info(f"Metrics output: {self.metrics_writer.metrics_file}")

        while self.running:
            try:
                self.poll()
            except Exception as e:
                log.error(f"Poll cycle failed: {e}", exc_info=True)

            # Sleep in small increments so we can respond to signals
            for _ in range(self.poll_interval):
                if not self.running:
                    break
                time.sleep(1)

        log.info("Oyster Monitor stopped.")


def main():
    config = load_config()
    monitor = OysterMonitor(config)
    monitor.run()


if __name__ == "__main__":
    main()
