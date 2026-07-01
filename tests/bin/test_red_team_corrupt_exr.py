#!/usr/bin/env python3
"""Test coverage for bin/red_team_corrupt_exr.py.

This module exercises the red-team EXR corruption utility that zero-fills
a byte range inside a depth-EXR file and validates that the resulting
corruption is detectable via NaN/Inf scan. Coverage:

- _get_numpy: returns a usable numpy module (idempotent caching).
- corrupt_exr_file: writes output file, file size preserved, region zeroed,
  metadata dict schema (input/output/file_size/offset/corrupt_size keys),
  offset < 0 clamped to 0, offset >= file_size clamped to (size-corrupt),
  corrupt_size exceeding remainder truncated, default offset 0, default
  corrupt_size 1024, creates parent directories, error on missing input.
- validate_corruption_detection: returns dict schema (file/has_nan/has_inf/
  detection_possible/error), PIL failure path sets detection_possible=True
  and populates error, valid PIL image returns detection_possible=False
  on clean image, NaN-rich image returns has_nan=True, Inf-rich image
  returns has_inf=True, file key is the input path string.
- main: --help exits 0, missing input returns 1, --validate-only on
  existing file returns 0, no output for corruption mode returns 1,
  end-to-end corrupt+validate returns 0, custom --offset and --size,
  subprocess --help exits 0, subprocess end-to-end with real input.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from red_team_corrupt_exr import (  # noqa: E402
    _get_numpy,
    corrupt_exr_file,
    main,
    validate_corruption_detection,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_dummy_exr(path: Path, size: int = 4096) -> Path:
    """Write a dummy binary file (not a real EXR) of the given byte size.

    The corruption routine operates on raw bytes — it does not parse EXR
    headers — so any binary blob is a valid input for testing the
    corruption logic.  Validation that relies on PIL will see this as
    corrupt/unreadable, which is exactly what we want to test.
    """
    # Use a single non-zero byte (0xAB) so the zero-fill is easy to verify.
    path.write_bytes(b"\xab" * size)
    return path


def _write_minimal_png(path: Path) -> Path:
    """Write a tiny 1x1 PNG so PIL can actually decode something.

    We only need a valid raster to drive the has_nan / has_inf branches.
    """
    # Minimal 1x1 white PNG (precomputed).
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa3\xc7\xa6\x00"
        b"\x00\x00\x00IEND\xaeB`\x82"
    )
    path.write_bytes(png_bytes)
    return path


# ---------------------------------------------------------------------------
# _get_numpy
# ---------------------------------------------------------------------------


class TestGetNumpy:
    """Tests for the lazy numpy loader."""

    def test_returns_numpy_module(self):
        np = _get_numpy()
        assert np is not None
        assert hasattr(np, "array")
        assert hasattr(np, "isnan")
        assert hasattr(np, "isinf")

    def test_caches_module(self):
        """Repeated calls return the same module instance."""
        a = _get_numpy()
        b = _get_numpy()
        assert a is b


# ---------------------------------------------------------------------------
# corrupt_exr_file
# ---------------------------------------------------------------------------


class TestCorruptExrFile:
    """Tests for the zero-fill corruption routine."""

    def test_writes_output_file(self, tmp_path):
        src = _write_dummy_exr(tmp_path / "src.bin")
        dst = tmp_path / "out.bin"
        corrupt_exr_file(src, dst)
        assert dst.exists()

    def test_output_size_matches_input(self, tmp_path):
        size = 4096
        src = _write_dummy_exr(tmp_path / "src.bin", size=size)
        dst = tmp_path / "out.bin"
        corrupt_exr_file(src, dst)
        assert dst.stat().st_size == size

    def test_metadata_dict_schema(self, tmp_path):
        src = _write_dummy_exr(tmp_path / "src.bin")
        dst = tmp_path / "out.bin"
        result = corrupt_exr_file(src, dst)
        assert set(result.keys()) == {
            "input",
            "output",
            "file_size",
            "offset",
            "corrupt_size",
        }

    def test_metadata_input_output_strings(self, tmp_path):
        src = _write_dummy_exr(tmp_path / "src.bin")
        dst = tmp_path / "out.bin"
        result = corrupt_exr_file(src, dst)
        assert result["input"] == str(src)
        assert result["output"] == str(dst)

    def test_metadata_file_size(self, tmp_path):
        src = _write_dummy_exr(tmp_path / "src.bin", size=8192)
        dst = tmp_path / "out.bin"
        result = corrupt_exr_file(src, dst)
        assert result["file_size"] == 8192

    def test_zero_fills_region(self, tmp_path):
        """The targeted byte range must be zeroed; bytes outside stay intact."""
        size = 4096
        src = _write_dummy_exr(tmp_path / "src.bin", size=size)
        original = src.read_bytes()
        dst = tmp_path / "out.bin"
        offset = 1024
        corrupt_size = 512
        result = corrupt_exr_file(src, dst, offset=offset, corrupt_size=corrupt_size)
        corrupted = dst.read_bytes()
        # Region should now be all zeros.
        assert corrupted[offset : offset + corrupt_size] == b"\x00" * corrupt_size
        # Bytes before/after the region must be unchanged (still 0xAB).
        assert corrupted[:offset] == b"\xab" * offset
        assert corrupted[offset + corrupt_size :] == b"\xab" * (size - offset - corrupt_size)
        assert result["corrupt_size"] == corrupt_size

    def test_default_offset_zero(self, tmp_path):
        src = _write_dummy_exr(tmp_path / "src.bin", size=2048)
        dst = tmp_path / "out.bin"
        result = corrupt_exr_file(src, dst)
        assert result["offset"] == 0

    def test_default_corrupt_size_1024(self, tmp_path):
        size = 4096
        src = _write_dummy_exr(tmp_path / "src.bin", size=size)
        dst = tmp_path / "out.bin"
        result = corrupt_exr_file(src, dst)
        assert result["corrupt_size"] == 1024
        assert result["file_size"] == size
        # Bytes [0:1024] should be zeroed (default offset=0).
        corrupted = dst.read_bytes()
        assert corrupted[0:1024] == b"\x00" * 1024
        # Bytes [1024:] should be unchanged (still 0xAB).
        assert corrupted[1024:] == b"\xab" * (size - 1024)

    def test_negative_offset_clamps_to_zero(self, tmp_path):
        src = _write_dummy_exr(tmp_path / "src.bin", size=4096)
        dst = tmp_path / "out.bin"
        result = corrupt_exr_file(src, dst, offset=-500, corrupt_size=256)
        assert result["offset"] == 0

    def test_offset_past_end_clamps(self, tmp_path):
        size = 4096
        src = _write_dummy_exr(tmp_path / "src.bin", size=size)
        dst = tmp_path / "out.bin"
        corrupt_size = 512
        result = corrupt_exr_file(src, dst, offset=99999, corrupt_size=corrupt_size)
        # When offset >= file_size, it is clamped to file_size - corrupt_size.
        assert result["offset"] == size - corrupt_size
        assert result["corrupt_size"] == corrupt_size

    def test_corrupt_size_truncated_at_end(self, tmp_path):
        size = 1000
        src = _write_dummy_exr(tmp_path / "src.bin", size=size)
        dst = tmp_path / "out.bin"
        result = corrupt_exr_file(src, dst, offset=800, corrupt_size=99999)
        # Only bytes [800:1000] could be zeroed; actual_size is 200.
        assert result["corrupt_size"] == size - 800
        assert result["offset"] == 800

    def test_creates_parent_directories(self, tmp_path):
        src = _write_dummy_exr(tmp_path / "src.bin")
        nested = tmp_path / "deep" / "nested" / "out.bin"
        result = corrupt_exr_file(src, nested)
        assert nested.exists()
        assert result["output"] == str(nested)

    def test_overwrites_existing_output(self, tmp_path):
        src = _write_dummy_exr(tmp_path / "src.bin", size=2048)
        dst = tmp_path / "out.bin"
        # Pre-populate output.
        dst.write_bytes(b"GARBAGE" * 10)
        corrupt_exr_file(src, dst)
        assert dst.stat().st_size == 2048
        # No "GARBAGE" bytes should remain.
        assert b"GARBAGE" not in dst.read_bytes()

    def test_missing_input_raises(self, tmp_path):
        missing = tmp_path / "does_not_exist.bin"
        dst = tmp_path / "out.bin"
        with pytest.raises(FileNotFoundError):
            corrupt_exr_file(missing, dst)


# ---------------------------------------------------------------------------
# validate_corruption_detection
# ---------------------------------------------------------------------------


class TestValidateCorruptionDetection:
    """Tests for the PIL/numpy detection scanner."""

    def test_dict_schema(self, tmp_path):
        f = _write_dummy_exr(tmp_path / "blob.bin")
        result = validate_corruption_detection(f)
        assert set(result.keys()) == {
            "file",
            "has_nan",
            "has_inf",
            "detection_possible",
            "error",
        }

    def test_file_key_is_input_path(self, tmp_path):
        f = _write_dummy_exr(tmp_path / "blob.bin")
        result = validate_corruption_detection(f)
        assert result["file"] == str(f)

    def test_initial_flags_false(self, tmp_path):
        """has_nan, has_inf, detection_possible default to False on success."""
        f = _write_dummy_exr(tmp_path / "blob.bin")
        result = validate_corruption_detection(f)
        # If the file is unreadable as a PIL image, detection_possible flips
        # to True — the point of the validator.  Whichever branch we land
        # in, has_nan/has_inf/error must be mutually consistent.
        if result["error"] is None:
            assert result["has_nan"] is False
            assert result["has_inf"] is False
            assert result["detection_possible"] is False
        else:
            assert result["detection_possible"] is True
            assert isinstance(result["error"], str)

    def test_unreadable_file_sets_detection_possible_true(self, tmp_path):
        """Binary garbage is not a real image; PIL must fail.

        In that branch, the implementation reports detection_possible=True
        and populates the error string.
        """
        f = _write_dummy_exr(tmp_path / "blob.bin")
        result = validate_corruption_detection(f)
        # Either the underlying decoder accepts the bytes (unlikely) or
        # the except branch fires.  Force the except branch by using
        # a non-image extension and truly non-image content.
        non_image = tmp_path / "broken.exr"
        non_image.write_bytes(b"not an exr " * 50)
        result = validate_corruption_detection(non_image)
        assert result["detection_possible"] is True
        assert result["error"] is not None
        assert isinstance(result["error"], str)
        assert len(result["error"]) > 0

    def test_valid_png_clean_returns_no_detection(self, tmp_path):
        """A clean raster yields has_nan=False, has_inf=False, no error."""
        f = _write_minimal_png(tmp_path / "clean.png")
        result = validate_corruption_detection(f)
        if result["error"] is None:
            assert result["has_nan"] is False
            assert result["has_inf"] is False
            assert result["detection_possible"] is False

    def test_valid_png_with_nan_array(self, tmp_path, monkeypatch):
        """If the loaded array contains NaN, has_nan=True, detection_possible=True."""
        f = _write_minimal_png(tmp_path / "clean.png")
        import numpy as np

        def fake_array(*_args, **_kwargs):
            return np.array([[float("nan"), 1.0], [2.0, 3.0]], dtype=np.float32)

        with monkeypatch.context() as m:
            m.setattr(np, "array", fake_array)
            result = validate_corruption_detection(f)
        # If PIL is missing, the except branch still wins; otherwise NaN wins.
        if result["error"] is None:
            assert result["has_nan"] is True
            assert result["detection_possible"] is True

    def test_valid_png_with_inf_array(self, tmp_path, monkeypatch):
        """If the loaded array contains Inf, has_inf=True, detection_possible=True."""
        f = _write_minimal_png(tmp_path / "clean.png")
        import numpy as np

        def fake_array(*_args, **_kwargs):
            return np.array(
                [[float("inf"), 1.0], [2.0, 3.0]], dtype=np.float32
            )

        with monkeypatch.context() as m:
            m.setattr(np, "array", fake_array)
            result = validate_corruption_detection(f)
        if result["error"] is None:
            assert result["has_inf"] is True
            assert result["detection_possible"] is True

    def test_missing_file_sets_detection_possible_true(self, tmp_path):
        """A nonexistent path triggers the except branch."""
        missing = tmp_path / "does_not_exist.exr"
        result = validate_corruption_detection(missing)
        assert result["detection_possible"] is True
        assert result["error"] is not None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the CLI surface of the EXR corruption tool."""

    def test_help_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "corrupt" in captured.out.lower() or "exr" in captured.out.lower()

    def test_missing_input_returns_one(self, tmp_path, capsys):
        rc = main([str(tmp_path / "no_such_file.exr")])
        assert rc == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err.lower() or "error" in captured.err.lower()

    def test_validate_only_returns_zero(self, tmp_path, capsys):
        f = _write_dummy_exr(tmp_path / "blob.exr")
        rc = main(["--validate-only", str(f)])
        assert rc == 0
        captured = capsys.readouterr()
        assert "Validation" in captured.out or "has_nan" in captured.out

    def test_corrupt_without_output_returns_one(self, tmp_path, capsys):
        f = _write_dummy_exr(tmp_path / "src.exr")
        rc = main([str(f)])
        # No output path → 1.
        assert rc == 1
        captured = capsys.readouterr()
        assert "output" in captured.err.lower() or "error" in captured.err.lower()

    def test_corrupt_end_to_end(self, tmp_path, capsys):
        src = _write_dummy_exr(tmp_path / "src.exr", size=4096)
        dst = tmp_path / "corrupt.exr"
        rc = main([str(src), str(dst)])
        assert rc == 0
        assert dst.exists()
        captured = capsys.readouterr()
        assert "Corrupting" in captured.out
        assert "Corrupted" in captured.out
        # Detection line should appear too.
        assert "Detection possible" in captured.out

    def test_corrupt_custom_offset_and_size(self, tmp_path, capsys):
        src = _write_dummy_exr(tmp_path / "src.exr", size=8192)
        dst = tmp_path / "out.exr"
        rc = main([str(src), str(dst), "--offset", "2048", "--size", "256"])
        assert rc == 0
        # Region [2048:2304] must be zeroed.
        out_bytes = dst.read_bytes()
        assert out_bytes[2048:2304] == b"\x00" * 256

    def test_corrupt_offset_clamped_past_end(self, tmp_path, capsys):
        src = _write_dummy_exr(tmp_path / "src.exr", size=1024)
        dst = tmp_path / "out.exr"
        rc = main([str(src), str(dst), "--offset", "99999", "--size", "128"])
        assert rc == 0
        # No crash, output exists, end of file zeroed.
        assert dst.exists()
        out_bytes = dst.read_bytes()
        assert out_bytes[-128:] == b"\x00" * 128


# ---------------------------------------------------------------------------
# subprocess smoke
# ---------------------------------------------------------------------------


class TestSubprocess:
    """End-to-end subprocess tests for the CLI."""

    def test_subprocess_help(self):
        proc = subprocess.run(
            [sys.executable, str(_BIN_DIR / "red_team_corrupt_exr.py"), "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc.returncode == 0
        assert "corrupt" in proc.stdout.lower() or "exr" in proc.stdout.lower()

    def test_subprocess_missing_input(self, tmp_path):
        proc = subprocess.run(
            [
                sys.executable,
                str(_BIN_DIR / "red_team_corrupt_exr.py"),
                str(tmp_path / "nope.exr"),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc.returncode == 1
        assert "not found" in proc.stderr.lower() or "error" in proc.stderr.lower()

    def test_subprocess_end_to_end(self, tmp_path):
        src = _write_dummy_exr(tmp_path / "src.exr", size=2048)
        dst = tmp_path / "corrupt.exr"
        proc = subprocess.run(
            [
                sys.executable,
                str(_BIN_DIR / "red_team_corrupt_exr.py"),
                str(src),
                str(dst),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0
        assert dst.exists()
        assert dst.stat().st_size == 2048
        out_bytes = dst.read_bytes()
        # Default corruption is 1024 bytes from offset 0.
        assert out_bytes[:1024] == b"\x00" * 1024

    def test_subprocess_validate_only(self, tmp_path):
        f = _write_dummy_exr(tmp_path / "blob.exr")
        proc = subprocess.run(
            [
                sys.executable,
                str(_BIN_DIR / "red_team_corrupt_exr.py"),
                "--validate-only",
                str(f),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc.returncode == 0
        assert "Validation" in proc.stdout or "has_nan" in proc.stdout
