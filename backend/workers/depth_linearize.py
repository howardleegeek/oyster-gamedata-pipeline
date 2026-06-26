"""Linearize engine Z-buffer captures into metric depth EXR files.

Raw captures are written by the Minecraft-side hook as:

* 32-byte little-endian header: ``ZBUF``, version, width, height, near, far,
  fov, tick
* ``width * height`` uint16 normalized depth samples

This worker converts the normalized perspective depth samples into metric
depth using the projection formula in ``docs/specs/SPEC_engine_zbuffer_hook.md``
and writes single-channel float32 OpenEXR files named ``depth/tick_*.exr``.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HEADER_MAGIC = b"ZBUF"
HEADER_VERSION = 1
HEADER_STRUCT = struct.Struct("<4sIIIfffI")
SOURCE_KIND = "engine_zbuffer"
SOURCE_BIT_DEPTH = "uint16_norm"


@dataclass(frozen=True)
class ZBufferHeader:
    """Decoded ``zbuffer/tick_*.bin`` header."""

    version: int
    width: int
    height: int
    z_near: float
    z_far: float
    fov_deg: float
    tick: int

    @property
    def body_bytes(self) -> int:
        return self.width * self.height * np.dtype("<u2").itemsize

    @property
    def source_resolution(self) -> str:
        return f"{self.width}x{self.height}"


def read_zbuffer_header(raw_bin: Path) -> ZBufferHeader:
    """Read and validate a 32-byte Z-buffer header."""

    with raw_bin.open("rb") as fh:
        header_bytes = fh.read(HEADER_STRUCT.size)

    if len(header_bytes) != HEADER_STRUCT.size:
        raise ValueError(f"{raw_bin} is too small for a {HEADER_STRUCT.size}-byte Z-buffer header")

    magic, version, width, height, z_near, z_far, fov_deg, tick = HEADER_STRUCT.unpack(header_bytes)
    if magic != HEADER_MAGIC:
        raise ValueError(f"{raw_bin} has invalid magic {magic!r}; expected {HEADER_MAGIC!r}")
    if version != HEADER_VERSION:
        raise ValueError(f"{raw_bin} has unsupported Z-buffer version {version}")
    if width <= 0 or height <= 0:
        raise ValueError(f"{raw_bin} has invalid dimensions {width}x{height}")
    if not (
        math.isfinite(z_near)
        and math.isfinite(z_far)
        and math.isfinite(fov_deg)
        and z_near > 0.0
        and z_far > z_near
    ):
        raise ValueError(
            f"{raw_bin} has invalid projection values: zNear={z_near}, zFar={z_far}, fov={fov_deg}"
        )

    return ZBufferHeader(
        version=version,
        width=width,
        height=height,
        z_near=float(z_near),
        z_far=float(z_far),
        fov_deg=float(fov_deg),
        tick=tick,
    )


def read_depth_values(raw_bin: Path, header: ZBufferHeader | None = None) -> np.ndarray:
    """Read the uint16 normalized Z-buffer body as ``(height, width)``."""

    header = header or read_zbuffer_header(raw_bin)
    expected_size = HEADER_STRUCT.size + header.body_bytes
    actual_size = raw_bin.stat().st_size
    if actual_size != expected_size:
        raise ValueError(
            f"{raw_bin} has {actual_size} bytes; expected {expected_size} "
            f"for {header.source_resolution} uint16 depth"
        )

    with raw_bin.open("rb") as fh:
        fh.seek(HEADER_STRUCT.size)
        body = fh.read(header.body_bytes)

    depth = np.frombuffer(body, dtype="<u2").reshape((header.height, header.width))
    return depth.copy()


def linearize_zbuffer(raw_bin: Path) -> np.ndarray:
    """Read ``raw_bin`` and return metric depth in meters as ``float32``."""

    header = read_zbuffer_header(raw_bin)
    z_norm = read_depth_values(raw_bin, header)
    # Keep the inversion in float64 to avoid cancellation at z01 == 1.0. The
    # EXR payload is still float32, but the far plane should round to zFar, not
    # overshoot by float32 denominator error.
    z01 = z_norm.astype(np.float64) / 65535.0

    near = float(header.z_near)
    far = float(header.z_far)
    z_metric = (near * far) / (far - z01 * (far - near))
    return z_metric.astype(np.float32, copy=False)


def _exr_attr(name: str, type_name: str, payload: bytes) -> bytes:
    return (
        name.encode("ascii")
        + b"\x00"
        + type_name.encode("ascii")
        + b"\x00"
        + struct.pack("<I", len(payload))
        + payload
    )


def write_exr_z(path: Path, depth_metric: np.ndarray) -> None:
    """Write a single-channel float32 ``Z`` OpenEXR scanline file.

    The writer is intentionally dependency-free. It emits the same simple,
    uncompressed scanline EXR layout used by the repo's fixture builders, but
    with the real metric depth bytes instead of placeholder zeros.
    """

    depth = np.asarray(depth_metric, dtype=np.float32)
    if depth.ndim != 2:
        raise ValueError(f"depth array must be 2D, got shape {depth.shape!r}")
    height, width = depth.shape
    if width <= 0 or height <= 0:
        raise ValueError(f"depth array has invalid shape {depth.shape!r}")

    depth_le = np.ascontiguousarray(depth, dtype="<f4")
    box = struct.pack("<iiii", 0, 0, width - 1, height - 1)
    channel = (
        b"Z\x00"
        + struct.pack("<i", 2)  # FLOAT
        + struct.pack("<B", 0)  # pLinear
        + b"\x00\x00\x00"
        + struct.pack("<i", 1)  # xSampling
        + struct.pack("<i", 1)  # ySampling
    )

    header = b"".join(
        [
            _exr_attr("channels", "chlist", channel + b"\x00"),
            _exr_attr("compression", "compression", struct.pack("<B", 0)),
            _exr_attr("dataWindow", "box2i", box),
            _exr_attr("displayWindow", "box2i", box),
            _exr_attr("lineOrder", "lineOrder", struct.pack("<B", 0)),
            _exr_attr("pixelAspectRatio", "float", struct.pack("<f", 1.0)),
            _exr_attr("screenWindowCenter", "v2f", struct.pack("<ff", 0.0, 0.0)),
            _exr_attr("screenWindowWidth", "float", struct.pack("<f", 1.0)),
            b"\x00",
        ]
    )

    magic_and_version = struct.pack("<I", 20000630) + struct.pack("<I", 2)
    row_bytes = width * np.dtype("<f4").itemsize
    first_block_offset = len(magic_and_version) + len(header) + 8 * height
    block_bytes = 8 + row_bytes
    offsets = b"".join(
        struct.pack("<Q", first_block_offset + y * block_bytes) for y in range(height)
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(magic_and_version)
        fh.write(header)
        fh.write(offsets)
        for y in range(height):
            fh.write(struct.pack("<i", y))
            fh.write(struct.pack("<i", row_bytes))
            fh.write(depth_le[y].tobytes(order="C"))


def to_exr(depth_metric: np.ndarray, out_path: Path) -> None:
    """Write metric depth to ``out_path`` as single-channel float32 EXR."""

    write_exr_z(out_path, depth_metric)


def process_session(session_dir: Path) -> dict[str, object]:
    """Linearize all ``zbuffer/tick_*.bin`` files in a session directory."""

    session_dir = session_dir.expanduser().resolve()
    raw_dir = session_dir / "zbuffer"
    exr_dir = session_dir / "depth"
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"missing zbuffer directory: {raw_dir}")

    raw_files = sorted(raw_dir.glob("tick_*.bin"))
    if not raw_files:
        raise FileNotFoundError(f"no zbuffer/tick_*.bin files found in {raw_dir}")

    exr_dir.mkdir(parents=True, exist_ok=True)
    first_header: ZBufferHeader | None = None

    for raw in raw_files:
        header = read_zbuffer_header(raw)
        if first_header is None:
            first_header = header
        depth_metric = linearize_zbuffer(raw)
        to_exr(depth_metric, exr_dir / raw.with_suffix(".exr").name)

    assert first_header is not None
    marker = {
        "kind": SOURCE_KIND,
        "frame_count": len(raw_files),
        "gap_miss_ratio": 0.0,
        "source_resolution": first_header.source_resolution,
        "source_bit_depth": SOURCE_BIT_DEPTH,
        "zNear": first_header.z_near,
        "zFar": first_header.z_far,
    }
    (exr_dir / ".source").write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    return marker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Linearize Minecraft engine Z-buffer dumps into depth/*.exr"
    )
    parser.add_argument("session_dir", type=Path, help="Session directory containing zbuffer/")
    args = parser.parse_args(argv)

    marker = process_session(args.session_dir)
    print(
        "linearized "
        f"{marker['frame_count']} zbuffer frames to "
        f"{args.session_dir.expanduser().resolve() / 'depth'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
