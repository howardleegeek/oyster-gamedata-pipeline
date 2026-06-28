#!/usr/bin/env python3
"""
BeamNG.drive extractor environment.

Connects to BeamNG.drive, polls vehicle sensors, captures camera depth-mode images,
and extracts ego pose at 60Hz.
"""

import argparse
import contextlib
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from oyster_agent_runner.environments.base import Action, Environment, Observation

# Lazy imports
try:
    import numpy as np
except ImportError:
    np = None

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from beamngpy import BeamNGpy, Road, Scenario, Vehicle
    from beamngpy.sensors import Camera

    HAS_BEAMNG = True
except ImportError:
    BeamNGpy = Vehicle = Scenario = Road = Camera = None
    HAS_BEAMNG = False

BEAMNGPY_MISSING_ERROR = (
    "BeamNGpy SDK is required for BeamNG.drive real mode. "
    "Install it with `python -m pip install beamngpy` and enable BeamNG research mode, "
    "or use `mode='mock'` / `--mock` for pure-Python dry-run smoke tests."
)

BeamNGMode = Literal["beamngpy", "mock"]


def _require_beamngpy() -> None:
    """Raise a clear operator-facing error when the optional SDK is absent."""

    if not HAS_BEAMNG:
        raise RuntimeError(BEAMNGPY_MISSING_ERROR)


def _shape(value: Any) -> list[int] | None:
    shape = getattr(value, "shape", None)
    if shape is None:
        return None
    return [int(dim) for dim in shape]


def _json_safe(value: Any) -> Any:
    """Best-effort conversion for BeamNGpy/numpy objects into JSON-safe data."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]

    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(item())
        except Exception:  # noqa: BLE001
            pass

    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            return _json_safe(tolist())
        except Exception:  # noqa: BLE001
            pass

    return str(value)


@dataclass
class SensorData:
    """Sensor data container."""

    timestamp: float
    ego_pose: Dict[str, float]  # x, y, z, roll, pitch, yaw
    camera_depth: Optional[Any]  # Depth image
    camera_rgb: Optional[Any]  # RGB image
    vehicle_sensors: Dict[str, Any]
    source: str = "beamngpy"

    def to_observation(self) -> Observation:
        """Convert to the plug-and-play BeamNG observation contract."""

        return {
            "timestamp": float(self.timestamp),
            "ego_pose": _json_safe(self.ego_pose),
            "camera": {
                "rgb_shape": _shape(self.camera_rgb),
                "depth_shape": _shape(self.camera_depth),
                "rgb_available": self.camera_rgb is not None,
                "depth_available": self.camera_depth is not None,
            },
            "vehicle_sensors": _json_safe(self.vehicle_sensors),
            "source": self.source,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to serializable dict."""
        return self.to_observation()


class BeamNGDriveExtractor:
    """BeamNG.drive extractor for 60Hz sensor data collection."""

    def __init__(
        self,
        beamng_home: Union[str, Path],
        host: str = "localhost",
        port: int = 64256,
        vehicle_model: str = "etk800",
        scenario_name: str = "west_coast_usa",
        capture_rgb: bool = True,
        capture_depth: bool = True,
        frequency_hz: float = 60.0,
    ):
        if not HAS_BEAMNG:
            raise RuntimeError(BEAMNGPY_MISSING_ERROR)

        self.beamng_home = Path(beamng_home)
        self.host = host
        self.port = port
        self.vehicle_model = vehicle_model
        self.scenario_name = scenario_name
        self.capture_rgb = capture_rgb
        self.capture_depth = capture_depth
        self.frequency_hz = frequency_hz

        self.beamng = None
        self.vehicle = None
        self.camera = None
        self.is_running = False
        self.data = []

        if not self.beamng_home.exists():
            raise FileNotFoundError(f"BeamNG not found at: {self.beamng_home}")

    def connect(self):
        """Connect to BeamNG.drive."""
        logging.info(f"Connecting to {self.host}:{self.port}")
        self.beamng = BeamNGpy(
            host=self.host,
            port=self.port,
            home=str(self.beamng_home),
            user=str(self.beamng_home / "user"),
        )
        self.beamng.open(launch=True)

    def setup_scenario(self):
        """Set up scenario with vehicle."""
        scenario = Scenario(level=self.scenario_name, name=f"extractor_{int(time.time())}")

        self.vehicle = Vehicle(vid="ego_vehicle", model=self.vehicle_model, license="EXTRACTOR")

        scenario.add_vehicle(
            self.vehicle, pos=(-717.121, 101, 118.675), rot_quat=(0, 0, 0.3826834, 0.9238795)
        )

        scenario.make(self.beamng)
        self.beamng.load_scenario(scenario)
        self.beamng.start_scenario()

    def setup_camera(self):
        """Set up depth-mode camera."""
        if not (self.capture_rgb or self.capture_depth):
            return

        camera_config = {
            "pos": (0, 2, 1.5),
            "direction": (0, -1, -0.1),
            "fov": 70,
            "resolution": (640, 480),
            "colour": self.capture_rgb,
            "depth": self.capture_depth,
        }

        self.camera = Camera("camera_front", self.beamng, self.vehicle, **camera_config)

    def collect_sample(self) -> SensorData:
        """Collect single sensor sample."""
        timestamp = time.time()

        # Poll vehicle sensors
        sensors_data = self.vehicle.sensors.poll()

        # Get ego pose
        ego_state = self.vehicle.get_state()
        ego_pose = {
            "x": ego_state["pos"][0],
            "y": ego_state["pos"][1],
            "z": ego_state["pos"][2],
            "roll": ego_state["rot"][0],
            "pitch": ego_state["rot"][1],
            "yaw": ego_state["rot"][2],
        }

        # Capture camera images
        camera_depth = None
        camera_rgb = None

        if self.camera:
            camera_data = self.camera.get_data()

            if self.capture_rgb and "colour" in camera_data:
                rgb_data = camera_data["colour"]
                if np is not None and rgb_data is not None:
                    camera_rgb = np.array(rgb_data)

            if self.capture_depth and "depth" in camera_data:
                depth_data = camera_data["depth"]
                if np is not None and depth_data is not None:
                    camera_depth = np.array(depth_data)

        return SensorData(
            timestamp=timestamp,
            ego_pose=ego_pose,
            camera_depth=camera_depth,
            camera_rgb=camera_rgb,
            vehicle_sensors=sensors_data,
            source="beamngpy",
        )

    def run(self, duration_seconds: float = 10.0) -> List[SensorData]:
        """Run data collection at specified frequency."""
        interval = 1.0 / self.frequency_hz
        start_time = time.time()
        self.is_running = True
        self.data = []

        logging.info(f"Collecting at {self.frequency_hz}Hz for {duration_seconds}s")

        try:
            while self.is_running and (time.time() - start_time) < duration_seconds:
                sample_start = time.time()

                sample = self.collect_sample()
                self.data.append(sample)

                # Maintain frequency
                elapsed = time.time() - sample_start
                sleep_time = interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    logging.warning(f"Can't maintain {self.frequency_hz}Hz")

        except KeyboardInterrupt:
            logging.info("Interrupted")
        finally:
            self.is_running = False

        logging.info(f"Collected {len(self.data)} samples")
        return self.data

    def save(self, output_path: Union[str, Path]):
        """Save collected data as JSON."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        serialized = [d.to_dict() for d in self.data]
        with open(output_path, "w") as f:
            json.dump(serialized, f, indent=2, default=str)

        logging.info(f"Saved to {output_path}")

    def cleanup(self):
        """Clean up resources."""
        if self.beamng:
            with contextlib.suppress(Exception):
                self.beamng.close()

    def __enter__(self):
        self.connect()
        self.setup_scenario()
        self.setup_camera()
        return self

    def __exit__(self, *args):
        self.cleanup()


class BeamNGDriveEnvironment(Environment):
    """BeamNG.drive plug-and-play runner adapter.

    ``mode="beamngpy"`` keeps the existing real BeamNGpy path. ``mode="mock"``
    is a pure-Python dry-run that emits the same observation fields without the
    game, SDK, Windows host, or rendering stack. That lets CI and cluster
    workers smoke-test BeamNG wiring before a real Windows rig is available.
    """

    def __init__(
        self,
        *,
        mode: BeamNGMode = "beamngpy",
        beamng_home: Union[str, Path, None] = None,
        host: str = "localhost",
        port: int = 64256,
        vehicle_model: str = "etk800",
        scenario_name: str = "west_coast_usa",
        capture_rgb: bool = True,
        capture_depth: bool = True,
        frequency_hz: float = 60.0,
        done_after_steps: int = 600,
        extractor: BeamNGDriveExtractor | None = None,
    ) -> None:
        if mode not in ("beamngpy", "mock"):
            raise ValueError(f"unknown BeamNG mode: {mode!r}")

        self.mode = mode
        self.beamng_home = Path(beamng_home) if beamng_home is not None else None
        self.host = host
        self.port = port
        self.vehicle_model = vehicle_model
        self.scenario_name = scenario_name
        self.capture_rgb = capture_rgb
        self.capture_depth = capture_depth
        self.frequency_hz = frequency_hz
        self.done_after_steps = done_after_steps
        self._injected_extractor = extractor
        self._extractor: BeamNGDriveExtractor | None = None
        self._last_frame: bytes | None = None
        self._mock_step_count = 0
        self._mock_seed = 0
        self._mock_speed_mps = 0.0
        self._mock_x = 0.0
        self._mock_y = 0.0
        self._mock_yaw = 0.0
        self._mock_throttle = 0.0
        self._mock_brake = 0.0
        self._mock_steering = 0.0
        self._is_shutdown = False

    def reset(self, seed: int | None = None) -> Observation:
        if self._is_shutdown:
            raise RuntimeError("BeamNGDriveEnvironment is shut down; create a new instance.")

        self.shutdown()
        self._is_shutdown = False
        if self.mode == "mock":
            self._mock_step_count = 0
            self._mock_seed = seed if seed is not None else 0
            self._mock_speed_mps = 0.0
            self._mock_x = 0.0
            self._mock_y = 0.0
            self._mock_yaw = 0.0
            self._mock_throttle = 0.0
            self._mock_brake = 0.0
            self._mock_steering = 0.0
            self._last_frame = None
            return self._mock_observation()

        _require_beamngpy()
        if self.beamng_home is None:
            raise RuntimeError("beamng_home is required for BeamNGpy mode; use mode='mock' for CI.")

        extractor = self._injected_extractor or BeamNGDriveExtractor(
            beamng_home=self.beamng_home,
            host=self.host,
            port=self.port,
            vehicle_model=self.vehicle_model,
            scenario_name=self.scenario_name,
            capture_rgb=self.capture_rgb,
            capture_depth=self.capture_depth,
            frequency_hz=self.frequency_hz,
        )
        extractor.connect()
        extractor.setup_scenario()
        extractor.setup_camera()
        self._extractor = extractor
        return extractor.collect_sample().to_observation()

    def step(self, action: Action) -> tuple[Observation, float, bool, dict[str, Any]]:
        if self._is_shutdown:
            raise RuntimeError("BeamNGDriveEnvironment is shut down; create a new instance.")

        if self.mode == "mock":
            self._advance_mock(action)
            done = self._mock_step_count >= self.done_after_steps
            observation = self._mock_observation()
            reward = float(self._mock_speed_mps)
            info: dict[str, Any] = {
                "mode": "mock",
                "step_count": self._mock_step_count,
                "action": _json_safe(dict(action)),
            }
            return observation, reward, done, info

        if self._extractor is None:
            raise RuntimeError("BeamNGDriveEnvironment.step called before reset()")

        self._apply_real_action(action)
        observation = self._extractor.collect_sample().to_observation()
        info = {"mode": "beamngpy", "action_applied": bool(action)}
        return observation, 0.0, False, info

    def render_frame(self) -> bytes | None:
        return self._last_frame

    def last_frame(self) -> bytes | None:
        return self._last_frame

    def shutdown(self) -> None:
        if self._extractor is not None:
            try:
                self._extractor.cleanup()
            finally:
                self._extractor = None
        self._is_shutdown = True

    def _advance_mock(self, action: Action) -> None:
        throttle = float(action.get("throttle", action.get("accelerate", 0.0)) or 0.0)
        brake = float(action.get("brake", 0.0) or 0.0)
        steering = float(action.get("steering", action.get("steer", 0.0)) or 0.0)
        self._mock_throttle = throttle
        self._mock_brake = brake
        self._mock_steering = steering
        dt = 1.0 / self.frequency_hz

        acceleration = max(-8.0, min(6.0, throttle * 6.0 - brake * 8.0))
        self._mock_speed_mps = max(0.0, self._mock_speed_mps + acceleration * dt)
        self._mock_yaw += steering * dt
        self._mock_x += self._mock_speed_mps * dt
        self._mock_y += steering * self._mock_speed_mps * dt * 0.1
        self._mock_step_count += 1

    def _mock_observation(self) -> Observation:
        timestamp = self._mock_step_count / self.frequency_hz
        return {
            "timestamp": float(timestamp),
            "ego_pose": {
                "x": float(self._mock_x),
                "y": float(self._mock_y),
                "z": 0.0,
                "roll": 0.0,
                "pitch": 0.0,
                "yaw": float(self._mock_yaw),
            },
            "camera": {
                "rgb_shape": [480, 640, 3] if self.capture_rgb else None,
                "depth_shape": [480, 640] if self.capture_depth else None,
                "rgb_available": bool(self.capture_rgb),
                "depth_available": bool(self.capture_depth),
            },
            "vehicle_sensors": {
                "speed_mps": float(self._mock_speed_mps),
                "throttle": float(self._mock_throttle),
                "brake": float(self._mock_brake),
                "steering": float(self._mock_steering),
                "rpm": float(900 + self._mock_speed_mps * 120),
                "gear": 1 if self._mock_speed_mps > 0 else 0,
                "seed": self._mock_seed,
                "step": self._mock_step_count,
            },
            "source": "mock",
        }

    def _apply_real_action(self, action: Action) -> None:
        vehicle = self._extractor.vehicle if self._extractor is not None else None
        if vehicle is None or not action:
            return
        control = getattr(vehicle, "control", None)
        if not callable(control):
            return
        try:
            control(
                throttle=float(action.get("throttle", action.get("accelerate", 0.0)) or 0.0),
                brake=float(action.get("brake", 0.0) or 0.0),
                steering=float(action.get("steering", action.get("steer", 0.0)) or 0.0),
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"BeamNGpy vehicle control failed: {exc}") from exc


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="BeamNG.drive 60Hz sensor data extractor")

    parser.add_argument(
        "--beamng-home",
        required=False,
        type=Path,
        help="Path to BeamNG.drive installation",
    )

    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run pure-Python dry-run mode without BeamNG.drive or BeamNGpy",
    )

    parser.add_argument(
        "--host",
        default="localhost",
        help="BeamNG server host",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=64256,
        help="BeamNG server port",
    )

    parser.add_argument(
        "--duration",
        type=float,
        default=10.0,
        help="Collection duration in seconds",
    )

    parser.add_argument(
        "--frequency",
        type=float,
        default=60.0,
        help="Collection frequency in Hz",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default="beamng_data.json",
        help="Output JSON file",
    )

    parser.add_argument(
        "--no-rgb",
        action="store_true",
        help="Disable RGB camera",
    )

    parser.add_argument(
        "--no-depth",
        action="store_true",
        help="Disable depth camera",
    )

    parser.add_argument(
        "--vehicle",
        default="etk800",
        help="Vehicle model",
    )

    parser.add_argument(
        "--scenario",
        default="west_coast_usa",
        help="Scenario name",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args(argv)

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    try:
        if args.mock:
            env = BeamNGDriveEnvironment(
                mode="mock",
                capture_rgb=not args.no_rgb,
                capture_depth=not args.no_depth,
                frequency_hz=args.frequency,
                done_after_steps=max(1, int(args.duration * args.frequency)),
            )
            observations = [env.reset(seed=0)]
            for _ in range(max(1, int(args.duration * args.frequency))):
                observation, _, done, _ = env.step({"throttle": 0.25, "steering": 0.0})
                observations.append(observation)
                if done:
                    break
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with open(args.output, "w") as f:
                json.dump(observations, f, indent=2)
            logging.info(f"Saved mock BeamNG observations to {args.output}")
            return 0

        if args.beamng_home is None:
            parser.error("--beamng-home is required unless --mock is set")

        extractor = BeamNGDriveExtractor(
            beamng_home=args.beamng_home,
            host=args.host,
            port=args.port,
            vehicle_model=args.vehicle,
            scenario_name=args.scenario,
            capture_rgb=not args.no_rgb,
            capture_depth=not args.no_depth,
            frequency_hz=args.frequency,
        )

        with extractor:
            data = extractor.run(duration_seconds=args.duration)
            extractor.save(args.output)

            if data:
                duration = data[-1].timestamp - data[0].timestamp
                freq = len(data) / duration if duration > 0 else 0
                logging.info(f"Actual frequency: {freq:.1f} Hz")
                logging.info(f"Duration: {duration:.2f} s")
                logging.info(f"Samples: {len(data)}")

        return 0

    except RuntimeError as e:
        logging.error(f"Missing dependency: {e}")
        logging.error("Install: pip install beamngpy numpy pillow")
        return 1
    except FileNotFoundError as e:
        logging.error(f"File not found: {e}")
        return 2
    except Exception as e:
        logging.error(f"Error: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 3


if __name__ == "__main__":
    sys.exit(main())
