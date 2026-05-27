from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from backend.workers.depth_linearize import (
    HEADER_STRUCT,
    linearize_zbuffer,
    process_session,
    read_zbuffer_header,
)

WIDTH = 480
HEIGHT = 270
NEAR = 0.05
FAR = 1024.0
FOV = 70.0


def _write_zbuffer_bin(path: Path, depth_u16: "np.ndarray", *, tick: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = HEADER_STRUCT.pack(b"ZBUF", 1, WIDTH, HEIGHT, NEAR, FAR, FOV, tick)
    path.write_bytes(header + np.ascontiguousarray(depth_u16, dtype="<u2").tobytes())


def _expected_metric(z_norm: int) -> float:
    z01 = z_norm / 65535.0
    return (NEAR * FAR) / (FAR - z01 * (FAR - NEAR))


def _read_exr_z(path: Path) -> "np.ndarray":
    data = path.read_bytes()
    assert struct.unpack_from("<I", data, 0)[0] == 20000630
    assert struct.unpack_from("<I", data, 4)[0] == 2

    pos = 8
    attrs: dict[str, tuple[str, bytes]] = {}
    while data[pos] != 0:
        end = data.index(b"\x00", pos)
        name = data[pos:end].decode("ascii")
        pos = end + 1
        end = data.index(b"\x00", pos)
        type_name = data[pos:end].decode("ascii")
        pos = end + 1
        size = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        attrs[name] = (type_name, data[pos : pos + size])
        pos += size
    pos += 1

    assert attrs["channels"][0] == "chlist"
    assert attrs["channels"][1].startswith(b"Z\x00")
    x_min, y_min, x_max, y_max = struct.unpack("<iiii", attrs["dataWindow"][1])
    width = x_max - x_min + 1
    height = y_max - y_min + 1

    offsets = [struct.unpack_from("<Q", data, pos + y * 8)[0] for y in range(height)]
    pos += 8 * height
    assert offsets[0] == pos

    depth = np.empty((height, width), dtype=np.float32)
    for _ in range(height):
        y, byte_count = struct.unpack_from("<ii", data, pos)
        pos += 8
        assert byte_count == width * 4
        depth[y - y_min, :] = np.frombuffer(data, dtype="<f4", count=width, offset=pos)
        pos += byte_count
    return depth


def test_linearize_zbuffer_reads_uint16_body_and_projection_formula(tmp_path: Path) -> None:
    zbuffer = np.zeros((HEIGHT, WIDTH), dtype=np.uint16)
    zbuffer[0, 0] = 0
    zbuffer[0, 1] = 32768
    zbuffer[-1, -1] = 65535
    raw = tmp_path / "zbuffer" / "tick_00000042.bin"
    _write_zbuffer_bin(raw, zbuffer, tick=42)

    header = read_zbuffer_header(raw)
    depth = linearize_zbuffer(raw)

    assert header.width == WIDTH
    assert header.height == HEIGHT
    assert header.tick == 42
    assert depth.shape == (HEIGHT, WIDTH)
    assert depth.dtype == np.float32
    assert depth[0, 0] == pytest.approx(_expected_metric(0), abs=1e-6)
    assert depth[0, 1] == pytest.approx(_expected_metric(32768), rel=1e-5)
    assert depth[-1, -1] == pytest.approx(FAR, rel=1e-6)


def test_process_session_writes_tick_exrs_and_engine_zbuffer_source(
    tmp_path: Path,
) -> None:
    session = tmp_path / "session"
    first = np.full((HEIGHT, WIDTH), 0, dtype=np.uint16)
    second = np.full((HEIGHT, WIDTH), 65535, dtype=np.uint16)
    _write_zbuffer_bin(session / "zbuffer" / "tick_00000000.bin", first, tick=0)
    _write_zbuffer_bin(session / "zbuffer" / "tick_00000001.bin", second, tick=1)

    marker = process_session(session)

    assert marker["kind"] == "engine_zbuffer"
    assert marker["frame_count"] == 2
    assert marker["gap_miss_ratio"] == 0.0
    assert marker["source_resolution"] == "480x270"
    assert marker["source_bit_depth"] == "uint16_norm"
    assert marker["zNear"] == pytest.approx(NEAR)
    assert marker["zFar"] == pytest.approx(FAR)

    source = json.loads((session / "depth" / ".source").read_text(encoding="utf-8"))
    assert source == marker
    assert (session / "depth" / "tick_00000000.exr").is_file()
    assert (session / "depth" / "tick_00000001.exr").is_file()

    first_exr = _read_exr_z(session / "depth" / "tick_00000000.exr")
    second_exr = _read_exr_z(session / "depth" / "tick_00000001.exr")
    assert first_exr.shape == (HEIGHT, WIDTH)
    assert np.allclose(first_exr, NEAR, atol=1e-6)
    assert np.allclose(second_exr, FAR, rtol=1e-6)

    OpenEXR = pytest.importorskip("OpenEXR")
    exr = OpenEXR.InputFile(str(session / "depth" / "tick_00000000.exr"))
    try:
        assert "Z" in exr.header()["channels"]
    finally:
        exr.close()


def test_cli_module_processes_session(tmp_path: Path) -> None:
    session = tmp_path / "session"
    zbuffer = np.full((HEIGHT, WIDTH), 32768, dtype=np.uint16)
    _write_zbuffer_bin(session / "zbuffer" / "tick_00000007.bin", zbuffer, tick=7)

    result = subprocess.run(
        [sys.executable, "-m", "backend.workers.depth_linearize", str(session)],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "linearized 1 zbuffer frames" in result.stdout
    assert (session / "depth" / "tick_00000007.exr").is_file()
    source = json.loads((session / "depth" / ".source").read_text(encoding="utf-8"))
    assert source["kind"] == "engine_zbuffer"
    assert source["frame_count"] == 1


def test_process_session_rejects_truncated_depth_body(tmp_path: Path) -> None:
    session = tmp_path / "session"
    raw = session / "zbuffer" / "tick_00000000.bin"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(HEADER_STRUCT.pack(b"ZBUF", 1, WIDTH, HEIGHT, NEAR, FAR, FOV, 0))

    with pytest.raises(ValueError, match="expected .* uint16 depth"):
        process_session(session)
