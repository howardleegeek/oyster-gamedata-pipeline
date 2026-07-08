#!/usr/bin/env python3
"""
BeamNG.drive extractor environment.

Connects to BeamNG.drive, polls vehicle sensors, captures camera depth-mode images,
and extracts ego pose at 60Hz.
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

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


@dataclass
class SensorData:
    """Sensor data container."""

    timestamp: float
    ego_pose: Dict[str, float]  # x, y, z, roll, pitch, yaw
    camera_depth: Optional[Any]  # Depth image
    camera_rgb: Optional[Any]  # RGB image
    vehicle_sensors: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to serializable dict."""
        result = {
            "timestamp": self.timestamp,
            "ego_pose": self.ego_pose,
            "vehicle_sensors": self.vehicle_sensors,
        }
        if self.camera_depth is not None and np is not None:
            result["camera_depth_shape"] = self.camera_depth.shape
        if self.camera_rgb is not None and np is not None:
            result["camera_rgb_shape"] = self.camera_rgb.shape
        return result


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
            raise ImportError("BeamNGpy not installed. pip install beamngpy")

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

    def connect(self) -> None:
        """Connect to BeamNG.drive.

        Establishes a connection to the BeamNG.drive instance and launches
        the simulator if not already running.

        Raises:
            Exception: If connection fails.
        """
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
            try:
                self.beamng.close()
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self):
        self.connect()
        self.setup_scenario()
        self.setup_camera()
        return self

    def __exit__(self, *args):
        self.cleanup()


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="BeamNG.drive 60Hz sensor data extractor")

    parser.add_argument(
        "--beamng-home",
        required=True,
        type=Path,
        help="Path to BeamNG.drive installation",
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

    except ImportError as e:
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
