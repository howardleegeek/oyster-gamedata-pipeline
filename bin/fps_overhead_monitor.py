#!/usr/bin/env python3
"""
G229 · bin/fps_overhead_monitor.py

Real-time FPS-overhead monitor for consumer game recording.
Measures FPS with/without recording, auto-adjusts encoder bitrate
or disables depth track if overhead exceeds 5% threshold.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Callable, Optional

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

DEFAULT_OVERHEAD_THRESHOLD = 5.0
DEFAULT_SAMPLE_INTERVAL = 0.1
DEFAULT_BASELINE_DURATION = 5.0
DEFAULT_MONITOR_DURATION = 10.0
BITRATE_REDUCTION_FACTOR = 0.85
MIN_BITRATE_KBPS = 1000


@dataclass
class FPSConfig:
    """Configuration for FPS monitoring."""
    overhead_threshold: float = DEFAULT_OVERHEAD_THRESHOLD
    sample_interval: float = DEFAULT_SAMPLE_INTERVAL
    baseline_duration: float = DEFAULT_BASELINE_DURATION
    monitor_duration: float = DEFAULT_MONITOR_DURATION
    state_path: Optional[Path] = None

    @classmethod
    def from_file(cls, path: Path) -> "FPSConfig":
        """Load configuration from JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            overhead_threshold=data.get("overhead_threshold", DEFAULT_OVERHEAD_THRESHOLD),
            sample_interval=data.get("sample_interval", DEFAULT_SAMPLE_INTERVAL),
            baseline_duration=data.get("baseline_duration", DEFAULT_BASELINE_DURATION),
            monitor_duration=data.get("monitor_duration", DEFAULT_MONITOR_DURATION),
        )


@dataclass
class FPSMetrics:
    """Container for FPS measurement metrics."""
    samples: list[float] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def avg_fps(self) -> float:
        """Return the average FPS across all samples.

        Returns:
            Average FPS as float, or 0.0 if no samples collected.
        """
        return mean(self.samples) if self.samples else 0.0

    @property
    def min_fps(self) -> float:
        return min(self.samples) if self.samples else 0.0

    @property
    def max_fps(self) -> float:
        return max(self.samples) if self.samples else 0.0

    @property
    def std_dev(self) -> float:
        return stdev(self.samples) if len(self.samples) >= 2 else 0.0

    def reset(self) -> None:
        """Reset all metrics samples and timestamps to initial state."""
        self.samples.clear()
        self.start_time = self.end_time = 0.0


class FPSMonitor:
    """Real-time FPS monitor that tracks game performance and adjusts settings."""

    def __init__(self, config: FPSConfig) -> None:
        self.config = config
        self.baseline_metrics = FPSMetrics()
        self.recording_metrics = FPSMetrics()
        self._running = False
        self._fps_source: Optional[Callable[[], float]] = None
        self._current_bitrate_kbps: int = 5000
        self._depth_track_enabled: bool = True

    def set_fps_source(self, source: Callable[[], float]) -> None:
        """Set the FPS data source callback.

        Args:
            source: A callable that returns the current FPS value as a float.
                If not set, the monitor will simulate FPS values internally.
        """
        self._fps_source = source

    def _get_current_fps(self) -> float:
        """Get current FPS from source or simulate."""
        if self._fps_source:
            return self._fps_source()
        base_fps = 60.0 if self._depth_track_enabled else 58.0
        noise = (time.time() % 1.0) * 2.0 - 1.0
        return max(1.0, base_fps + noise)

    def _collect_samples(self, metrics: FPSMetrics, duration: float) -> None:
        """Collect FPS samples for specified duration."""
        metrics.reset()
        metrics.start_time = time.time()
        end_time = metrics.start_time + duration
        while time.time() < end_time and self._running:
            metrics.samples.append(self._get_current_fps())
            time.sleep(self.config.sample_interval)
        metrics.end_time = time.time()
        logger.info(
            f"Collected {len(metrics.samples)} samples, avg: {metrics.avg_fps:.1f} fps"
        )

    def calculate_overhead(self) -> float:
        """Calculate FPS overhead percentage."""
        if not self.baseline_metrics.samples or not self.recording_metrics.samples:
            return 0.0
        baseline = self.baseline_metrics.avg_fps
        recording = self.recording_metrics.avg_fps
        return ((baseline - recording) / baseline * 100.0) if baseline else 0.0

    def _adjust_encoder_bitrate(self) -> bool:
        """Reduce encoder bitrate to lower overhead."""
        new_bitrate = max(MIN_BITRATE_KBPS,
                         int(self._current_bitrate_kbps * BITRATE_REDUCTION_FACTOR))
        if new_bitrate < self._current_bitrate_kbps:
            logger.warning(f"Reducing bitrate: {self._current_bitrate_kbps} -> {new_bitrate} kbps")
            self._current_bitrate_kbps = new_bitrate
            return True
        return False

    def _disable_depth_track(self) -> bool:
        """Disable depth tracking to reduce overhead."""
        if self._depth_track_enabled:
            self._depth_track_enabled = False
            logger.warning("Disabling depth track due to high overhead")
            return True
        return False

    def _persist_state(self) -> None:
        """Persist current state to state file."""
        if not self.config.state_path:
            return
        state = {
            "bitrate_kbps": self._current_bitrate_kbps,
            "depth_track_enabled": self._depth_track_enabled,
            "timestamp": time.time(),
        }
        try:
            self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config.state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except OSError as e:
            logger.error(f"Failed to persist state: {e}")

    def _load_state(self) -> None:
        """Load state from state file."""
        if not self.config.state_path or not self.config.state_path.exists():
            return
        try:
            with open(self.config.state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self._current_bitrate_kbps = state.get("bitrate_kbps", 5000)
            self._depth_track_enabled = state.get("depth_track_enabled", True)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to load state: {e}")

    def run_monitoring_cycle(self) -> dict[str, Any]:
        """Run complete monitoring cycle: baseline, recording, adjust."""
        self._running = True
        self._load_state()
        try:
            logger.info("Measuring baseline FPS...")
            self._collect_samples(self.baseline_metrics, self.config.baseline_duration)
            logger.info("Measuring recording FPS...")
            self._collect_samples(self.recording_metrics, self.config.monitor_duration)

            overhead = self.calculate_overhead()
            result: dict[str, Any] = {
                "overhead_percent": overhead,
                "threshold": self.config.overhead_threshold,
                "exceeded": overhead > self.config.overhead_threshold,
                "baseline_fps": self.baseline_metrics.avg_fps,
                "recording_fps": self.recording_metrics.avg_fps,
                "bitrate_adjusted": False,
                "depth_track_disabled": False,
                "current_bitrate_kbps": self._current_bitrate_kbps,
                "depth_track_enabled": self._depth_track_enabled,
            }

            logger.info(f"Overhead: {overhead:.1f}%")
            if overhead > self.config.overhead_threshold:
                logger.warning(f"Overhead exceeds {self.config.overhead_threshold}% threshold")
                result["bitrate_adjusted"] = self._adjust_encoder_bitrate()
                self._persist_state()
                if self.calculate_overhead() > self.config.overhead_threshold:
                    result["depth_track_disabled"] = self._disable_depth_track()
                    self._persist_state()
            return result
        finally:
            self._running = False

    def stop(self) -> None:
        """Stop the FPS monitoring loop."""
        self._running = False


def create_default_config(path: Path) -> None:
    """Create default configuration file."""
    config = {
        "overhead_threshold": DEFAULT_OVERHEAD_THRESHOLD,
        "sample_interval": DEFAULT_SAMPLE_INTERVAL,
        "baseline_duration": DEFAULT_BASELINE_DURATION,
        "monitor_duration": DEFAULT_MONITOR_DURATION,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    logger.info(f"Created default config at {path}")


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point for FPS overhead monitor."""
    parser = argparse.ArgumentParser(
        description="Real-time FPS overhead monitor for game recording"
    )
    parser.add_argument("--config", type=Path, help="Path to configuration JSON file")
    parser.add_argument("--threshold", type=float, default=DEFAULT_OVERHEAD_THRESHOLD,
                       help="Overhead threshold percentage")
    parser.add_argument("--baseline", type=float, default=DEFAULT_BASELINE_DURATION,
                       help="Baseline measurement duration (seconds)")
    parser.add_argument("--monitor", type=float, default=DEFAULT_MONITOR_DURATION,
                       help="Recording monitor duration (seconds)")
    parser.add_argument("--interval", type=float, default=DEFAULT_SAMPLE_INTERVAL,
                       help="Sample interval (seconds)")
    parser.add_argument("--state-file", type=Path, help="Path to state persistence file")
    parser.add_argument("--init-config", type=Path,
                       help="Create default configuration file and exit")
    parser.add_argument("--json-output", action="store_true", help="Output results as JSON")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args(argv)
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.init_config:
        try:
            create_default_config(args.init_config)
            return 0
        except OSError as e:
            logger.error(f"Failed to create config: {e}")
            return 1

    if args.config and args.config.exists():
        config = FPSConfig.from_file(args.config)
    else:
        config = FPSConfig(
            overhead_threshold=args.threshold,
            sample_interval=args.interval,
            baseline_duration=args.baseline,
            monitor_duration=args.monitor,
            state_path=args.state_file,
        )

    monitor = FPSMonitor(config)
    shutdown_requested = False

    def signal_handler(signum: int, frame: Any) -> None:
        """Handle SIGINT/SIGTERM to gracefully stop monitoring.

        Args:
            signum: Signal number (signal.SIGINT or signal.SIGTERM).
            frame: Current stack frame (unused, required by signal handler signature).
        """
        nonlocal shutdown_requested
        shutdown_requested = True
        monitor.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        result = monitor.run_monitoring_cycle()
        if shutdown_requested:
            return 130

        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            print("\n=== FPS Overhead Monitor Results ===")
            print(f"Baseline FPS:  {result['baseline_fps']:.1f}")
            print(f"Recording FPS: {result['recording_fps']:.1f}")
            print(f"Overhead:      {result['overhead_percent']:.1f}%")
            print(f"Threshold:     {result['threshold']:.1f}%")
            print(f"Exceeded:      {'Yes' if result['exceeded'] else 'No'}")
            if result["bitrate_adjusted"]:
                print(f"Bitrate:       {result['current_bitrate_kbps']} kbps")
            if result["depth_track_disabled"]:
                print("Depth track:   Disabled")

        return 0 if not result["exceeded"] else 2
    except Exception as e:
        logger.error(f"Monitoring failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())