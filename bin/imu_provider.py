#!/usr/bin/env python3
"""
bin/imu_provider.py - Synthetic IMU 6-axis provider at 240Hz physics-tick rate.

Generates synthetic IMU data (3-axis accelerometer + 3-axis gyroscope) from
Mineflayer state for Ego-Exo4D / VIO research. Derived velocity is lossy,
so this module provides physics-tick accurate IMU measurements.

Cluster C component for G149 project.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_np: Any = None


def _get_numpy() -> Any:
    """Lazy import numpy module."""
    global _np
    if _np is None:
        try:
            import numpy as _np
        except ImportError:
            _np = None
    return _np


@dataclass
class IMUReading:
    """Single IMU reading with 6-axis data and timestamp."""
    timestamp: float
    accel: Tuple[float, float, float]
    gyro: Tuple[float, float, float]

    def to_dict(self) -> Dict[str, Any]:
        return {"timestamp": self.timestamp, "accel": list(self.accel), "gyro": list(self.gyro)}


@dataclass
class EntityState:
    """Mineflayer entity state at a given tick."""
    timestamp: float
    position: Tuple[float, float, float]
    velocity: Tuple[float, float, float]
    orientation: Tuple[float, float, float]
    angular_velocity: Optional[Tuple[float, float, float]] = None


class IMUProvider:
    """Synthetic IMU data provider at 240Hz physics-tick rate.

    Generates 6-axis IMU measurements (accelerometer + gyroscope) from
    Mineflayer state data. Handles lossy velocity derivation by applying
    numerical differentiation with smoothing.
    """

    def __init__(
        self,
        tick_rate: float = 240.0,
        gravity: float = 9.81,
        noise_std_accel: float = 0.05,
        noise_std_gyro: float = 0.01,
    ) -> None:
        self.tick_rate = tick_rate
        self.gravity = gravity
        self.noise_std_accel = noise_std_accel
        self.noise_std_gyro = noise_std_gyro
        self._imu_readings: List[IMUReading] = []

    def load_state(self, state_data: Dict[str, Any]) -> EntityState:
        """Parse Mineflayer state data into EntityState."""
        angular_velocity = state_data.get("angular_velocity")
        return EntityState(
            timestamp=float(state_data.get("timestamp", 0.0)),
            position=tuple(state_data.get("position", [0.0, 0.0, 0.0])),  # type: ignore
            velocity=tuple(state_data.get("velocity", [0.0, 0.0, 0.0])),  # type: ignore
            orientation=tuple(state_data.get("orientation", [0.0, 0.0, 0.0])),  # type: ignore
            angular_velocity=tuple(angular_velocity) if angular_velocity else None,
        )

    def load_states_from_file(self, filepath: Path) -> List[EntityState]:
        """Load entity states from JSON file."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [self.load_state(s) for s in data]
        elif isinstance(data, dict) and "states" in data:
            return [self.load_state(s) for s in data["states"]]
        return [self.load_state(data)]

    def _compute_acceleration(
        self, state_curr: EntityState, state_prev: EntityState
    ) -> Tuple[float, float, float]:
        """Compute linear acceleration from state transitions."""
        dt = state_curr.timestamp - state_prev.timestamp if state_curr.timestamp != state_prev.timestamp else 1.0 / self.tick_rate
        ax = (state_curr.velocity[0] - state_prev.velocity[0]) / dt
        ay = (state_curr.velocity[1] - state_prev.velocity[1]) / dt + self.gravity
        az = (state_curr.velocity[2] - state_prev.velocity[2]) / dt
        return (ax, ay, az)

    def _compute_angular_velocity(
        self, state_curr: EntityState, state_prev: EntityState
    ) -> Tuple[float, float, float]:
        """Compute angular velocity from orientation changes."""
        if state_curr.angular_velocity is not None:
            return state_curr.angular_velocity
        dt = state_curr.timestamp - state_prev.timestamp if state_curr.timestamp != state_prev.timestamp else 1.0 / self.tick_rate
        return tuple((state_curr.orientation[i] - state_prev.orientation[i]) / dt for i in range(3))  # type: ignore

    def _add_noise(self, value: float, std: float) -> float:
        """Add Gaussian noise to a value."""
        np = _get_numpy()
        if np is not None:
            return value + float(np.random.normal(0, std))
        import random
        u1, u2 = max(random.random(), 1e-10), random.random()
        return value + std * math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

    def generate_imu_data(self, states: List[EntityState]) -> List[IMUReading]:
        """Generate IMU readings from entity state sequence."""
        if len(states) < 2:
            return []
        readings: List[IMUReading] = []
        for i in range(1, len(states)):
            state_curr, state_prev = states[i], states[i - 1]
            accel = self._compute_acceleration(state_curr, state_prev)
            gyro = self._compute_angular_velocity(state_curr, state_prev)
            accel_noisy = tuple(self._add_noise(a, self.noise_std_accel) for a in accel)
            gyro_noisy = tuple(self._add_noise(g, self.noise_std_gyro) for g in gyro)
            readings.append(IMUReading(state_curr.timestamp, accel_noisy, gyro_noisy))  # type: ignore
        self._imu_readings = readings
        return readings

    def save_readings(self, filepath: Path, fmt: str = "json") -> None:
        """Save IMU readings to file."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump([r.to_dict() for r in self._imu_readings], f, indent=2)
        elif fmt == "npy":
            np = _get_numpy()
            if np is None:
                raise RuntimeError("numpy required for npy format")
            arr = np.array([[r.timestamp, *r.accel, *r.gyro] for r in self._imu_readings])
            np.save(str(filepath), arr)
        elif fmt == "csv":
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("timestamp,ax,ay,az,gx,gy,gz\n")
                for r in self._imu_readings:
                    f.write(f"{r.timestamp},{r.accel[0]},{r.accel[1]},{r.accel[2]},{r.gyro[0]},{r.gyro[1]},{r.gyro[2]}\n")
        else:
            raise ValueError(f"Unknown format: {fmt}")


def parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="imu_provider",
        description="Synthetic IMU 6-axis provider at 240Hz physics-tick rate.",
    )
    parser.add_argument("--input", "-i", type=Path, required=True, help="Input JSON file with Mineflayer state data.")
    parser.add_argument("--output", "-o", type=Path, required=True, help="Output file for IMU readings.")
    parser.add_argument("--format", "-f", choices=["json", "npy", "csv"], default="json", help="Output format (default: json).")
    parser.add_argument("--tick-rate", "-t", type=float, default=240.0, help="Physics tick rate in Hz (default: 240).")
    parser.add_argument("--gravity", "-g", type=float, default=9.81, help="Gravity constant in m/s² (default: 9.81).")
    parser.add_argument("--noise-accel", "-a", type=float, default=0.05, help="Accelerometer noise std dev (default: 0.05).")
    parser.add_argument("--noise-gyro", "-r", type=float, default=0.01, help="Gyroscope noise std dev (default: 0.01).")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    """Main entry point for IMU provider.

    Args:
        argv: Command-line arguments.

    Returns:
        Exit code (0 for success, non-zero for error).
    """
    args = parse_args(argv)
    if not args.input.exists():
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        return 1
    provider = IMUProvider(args.tick_rate, args.gravity, args.noise_accel, args.noise_gyro)
    try:
        states = provider.load_states_from_file(args.input)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in input file: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error loading states: {e}", file=sys.stderr)
        return 1
    if len(states) < 2:
        print("Error: Need at least 2 states to generate IMU data", file=sys.stderr)
        return 1
    readings = provider.generate_imu_data(states)
    try:
        provider.save_readings(args.output, fmt=args.format)
    except Exception as e:
        print(f"Error saving output: {e}", file=sys.stderr)
        return 1
    print(f"Generated {len(readings)} IMU readings to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))