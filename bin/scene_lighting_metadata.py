#!/usr/bin/env python3
"""scene_lighting_metadata.py — Cluster C+ lighting metadata extractor.

Extracts per-frame sun direction (azimuth/elevation), ambient intensity,
and weather state for relighting experiments.

Usage:
    python3 bin/scene_lighting_metadata.py --input scene.yaml --output lighting.json
    python3 bin/scene_lighting_metadata.py --frames-dir ./frames/ --output lighting.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_yaml = None  # Lazy load PyYAML


def _get_yaml():
    """Lazy load PyYAML module."""
    global _yaml
    if _yaml is None:
        import yaml

        _yaml = yaml
    return _yaml


@dataclass
class SunDirection:
    """Sun direction as azimuth/elevation in degrees."""

    azimuth: float  # 0-360°, 0=North, 90=East
    elevation: float  # -90..+90°

    def to_vector(self) -> Tuple[float, float, float]:
        """Convert to unit (x,y,z) vector pointing toward the sun."""
        az_rad, el_rad = math.radians(self.azimuth), math.radians(self.elevation)
        return (
            math.cos(el_rad) * math.sin(az_rad),
            math.cos(el_rad) * math.cos(az_rad),
            math.sin(el_rad),
        )


@dataclass
class AmbientIntensity:
    """Normalized ambient lighting parameters."""

    intensity: float = 0.5
    color_temperature: float = 5500.0
    sky_model: str = "hosek_wilkie"


@dataclass
class WeatherState:
    """Discrete weather condition with continuous modifiers."""

    condition: str = "clear"
    cloud_cover: float = 0.0
    visibility_km: float = 10.0
    precipitation: float = 0.0


@dataclass
class FrameLighting:
    """Complete lighting metadata for a single frame."""

    frame_id: str
    timestamp: Optional[float] = None
    sun: Optional[SunDirection] = None
    ambient: Optional[AmbientIntensity] = None
    weather: Optional[WeatherState] = None
    source: str = "computed"
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {"frame_id": self.frame_id, "source": self.source}
        if self.timestamp is not None:
            result["timestamp"] = self.timestamp
        if self.sun:
            result["sun"] = asdict(self.sun)
        if self.ambient:
            result["ambient"] = asdict(self.ambient)
        if self.weather:
            result["weather"] = asdict(self.weather)
        if self.notes:
            result["notes"] = self.notes
        return result


def compute_sun_position(lat: float, lon: float, utc_ts: float) -> SunDirection:
    """Compute sun azimuth/elevation from geo coords and UTC timestamp.

    Uses simplified solar position algorithm (~±1° accuracy).
    """
    dt = datetime.datetime.utcfromtimestamp(utc_ts)
    doy = dt.timetuple().tm_yday
    hour = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    declination = 23.45 * math.sin(math.radians(360.0 / 365.0 * (doy - 81)))
    b = math.radians(360.0 / 365.0 * (doy - 81))
    eqt = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    solar_time = hour + lon / 15.0 + eqt / 60.0
    hour_angle = 15.0 * (solar_time - 12.0)
    lat_rad, dec_rad, ha_rad = (
        math.radians(lat),
        math.radians(declination),
        math.radians(hour_angle),
    )
    sin_elev = math.sin(lat_rad) * math.sin(dec_rad) + math.cos(lat_rad) * math.cos(
        dec_rad
    ) * math.cos(ha_rad)
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, sin_elev))))
    cos_az = (math.sin(dec_rad) - math.sin(lat_rad) * sin_elev) / (
        math.cos(lat_rad) * math.cos(math.radians(elevation)) + 1e-9
    )
    azimuth = math.degrees(math.acos(max(-1.0, min(1.0, cos_az))))
    if hour_angle > 0:
        azimuth = 360.0 - azimuth
    return SunDirection(azimuth=azimuth, elevation=elevation)


def estimate_ambient_from_weather(weather: WeatherState) -> AmbientIntensity:
    """Estimate ambient intensity from weather conditions."""
    intensity_map = {
        "clear": 0.8,
        "partly_cloudy": 0.6,
        "overcast": 0.4,
        "rain": 0.3,
        "snow": 0.5,
        "fog": 0.35,
    }
    temp_map = {
        "clear": 5500.0,
        "partly_cloudy": 6000.0,
        "overcast": 7000.0,
        "rain": 6500.0,
        "snow": 7500.0,
        "fog": 7000.0,
    }
    base = intensity_map.get(weather.condition, 0.5)
    adjusted = base * (1.0 - weather.cloud_cover * 0.3)
    return AmbientIntensity(
        intensity=min(1.0, max(0.0, adjusted)),
        color_temperature=temp_map.get(weather.condition, 5500.0),
    )


def infer_weather_from_image(image_path: Path) -> WeatherState:
    """Infer weather state from image brightness analysis."""
    try:
        from PIL import Image

        with Image.open(image_path) as img:
            gray_img = img.convert("L")
            pixels = list(gray_img.get_flattened_data())
            avg_brightness = sum(pixels) / len(pixels) / 255.0
    except Exception:
        avg_brightness = 0.5
    if avg_brightness > 0.7:
        return WeatherState(condition="clear", cloud_cover=0.1, visibility_km=15.0)
    elif avg_brightness > 0.5:
        return WeatherState(condition="partly_cloudy", cloud_cover=0.4, visibility_km=12.0)
    elif avg_brightness > 0.35:
        return WeatherState(condition="overcast", cloud_cover=0.8, visibility_km=8.0)
    return WeatherState(condition="fog", cloud_cover=0.9, visibility_km=3.0)


def load_scene_descriptor(path: Path) -> Dict[str, Any]:
    """Load scene descriptor from JSON or YAML file."""
    content = path.read_text()
    if path.suffix.lower() in (".yaml", ".yml"):
        return _get_yaml().safe_load(content)
    return json.loads(content)


def process_frame(frame_data: Dict[str, Any], frame_id: str) -> FrameLighting:
    """Process a single frame's data to extract lighting metadata."""
    lat, lon = frame_data.get("latitude", 0.0), frame_data.get("longitude", 0.0)
    ts = frame_data.get("timestamp", frame_data.get("utc_timestamp"))
    sun = compute_sun_position(lat, lon, ts) if lat and lon and ts else None
    weather_data = frame_data.get("weather", {})
    weather = WeatherState(
        condition=weather_data.get("condition", "clear")
        if isinstance(weather_data, dict)
        else "clear",
        cloud_cover=weather_data.get("cloud_cover", 0.0) if isinstance(weather_data, dict) else 0.0,
        visibility_km=weather_data.get("visibility_km", 10.0)
        if isinstance(weather_data, dict)
        else 10.0,
        precipitation=weather_data.get("precipitation", 0.0)
        if isinstance(weather_data, dict)
        else 0.0,
    )
    ambient_data = frame_data.get("ambient", {})
    ambient = (
        AmbientIntensity(
            intensity=ambient_data.get("intensity", 0.5),
            color_temperature=ambient_data.get("color_temperature", 5500.0),
            sky_model=ambient_data.get("sky_model", "hosek_wilkie"),
        )
        if ambient_data
        else estimate_ambient_from_weather(weather)
    )
    return FrameLighting(
        frame_id=frame_id,
        timestamp=ts,
        sun=sun,
        ambient=ambient,
        weather=weather,
        source=frame_data.get("source", "computed"),
        notes=frame_data.get("notes", ""),
    )


def process_frames_dir(
    frames_dir: Path, lat: float, lon: float, ts: Optional[float]
) -> List[FrameLighting]:
    """Process all frames in a directory."""
    results, image_exts = [], {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
    sun = compute_sun_position(lat, lon, ts) if lat and lon and ts else None
    for img_path in sorted(frames_dir.iterdir()):
        if img_path.suffix.lower() not in image_exts:
            continue
        weather = infer_weather_from_image(img_path)
        results.append(
            FrameLighting(
                frame_id=img_path.stem,
                timestamp=ts,
                sun=sun,
                ambient=estimate_ambient_from_weather(weather),
                weather=weather,
                source="image_analysis",
            )
        )
    return results


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Main entry point with argparse CLI."""
    parser = argparse.ArgumentParser(description="Extract per-frame lighting metadata.")
    parser.add_argument("--input", "-i", type=Path, help="Input scene descriptor (JSON/YAML)")
    parser.add_argument("--frames-dir", "-d", type=Path, help="Directory containing frame images")
    parser.add_argument("--output", "-o", type=Path, required=True, help="Output JSON manifest")
    parser.add_argument("--latitude", "-lat", type=float, default=0.0, help="Default latitude")
    parser.add_argument("--longitude", "-lon", type=float, default=0.0, help="Default longitude")
    parser.add_argument("--timestamp", "-t", type=float, default=None, help="Default UTC timestamp")
    args = parser.parse_args(argv)
    results = []
    if args.input:
        if not args.input.exists():
            print(f"Error: Input file not found: {args.input}", file=sys.stderr)
            return 1
        scene_data = load_scene_descriptor(args.input)
        frames = scene_data.get("frames", [scene_data])
        for i, frame in enumerate(frames):
            frame_id = frame.get("frame_id", frame.get("id", f"frame_{i:04d}"))
            results.append(process_frame(frame, frame_id).to_dict())
    if args.frames_dir:
        if not args.frames_dir.exists():
            print(f"Error: Frames directory not found: {args.frames_dir}", file=sys.stderr)
            return 1
        for fl in process_frames_dir(
            args.frames_dir, args.latitude, args.longitude, args.timestamp
        ):
            results.append(fl.to_dict())
    if not results:
        print("Error: No input provided (--input or --frames-dir required)", file=sys.stderr)
        return 1
    output_data = {
        "version": "1.0",
        "generated": datetime.datetime.utcnow().isoformat() + "Z",
        "frames": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output_data, indent=2))
    print(f"Wrote lighting metadata for {len(results)} frames to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
