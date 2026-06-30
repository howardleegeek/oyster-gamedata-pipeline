#!/usr/bin/env python3
"""Tests for bin/tarball_diff.py — Compare two buyer-spec tarballs.

Validates tarball extraction, metric counting (action_camera records,
video duration, depth files), duration formatting, and the main()
CLI entry point with two real tarballs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

# Path to the diff script (in parent bin/ directory)
TARBALL_DIFF_SCRIPT = (
    Path(__file__).parent.parent.parent / "bin" / "tarball_diff.py"
)


def _make_tarball(tmpdir: str, records: list[dict], depth_files: list[str] = None) -> str:
    """Create a tarball with the given JSON records and optional depth files.

    Returns the tarball path.
    """
    if depth_files is None:
        depth_files = []
    tar_path = os.path.join(tmpdir, "bundle.tar.gz")
    payload_dir = os.path.join(tmpdir, "payload")
    os.makedirs(payload_dir, exist_ok=True)

    # Write records as a single JSON array
    rec_path = os.path.join(payload_dir, "records.json")
    with open(rec_path, "w") as fp:
        json.dump(records, fp)

    # Write depth marker files (empty placeholder content is fine)
    for name in depth_files:
        with open(os.path.join(payload_dir, name), "wb") as fp:
            fp.write(b"\x00" * 16)

    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(payload_dir, arcname="bundle")
    return tar_path


class TestTarballDiff:
    """Test suite for tarball_diff.py."""

    def test_script_exists(self):
        """Verify the diff script exists and is importable."""
        assert TARBALL_DIFF_SCRIPT.exists(), f"Script not found: {TARBALL_DIFF_SCRIPT}"

    def test_import_module(self):
        """Verify the module imports cleanly (no syntax errors / import-time side effects)."""
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        import tarball_diff  # noqa: F401

    def test_format_duration_seconds_only(self):
        """< 60s uses seconds-only format with two decimals."""
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        from tarball_diff import format_duration

        assert format_duration(0) == "0.00s"
        assert format_duration(45.5) == "45.50s"
        assert format_duration(59.999) == "60.00s"  # exactly 60 falls into < 60 branch
        assert format_duration(0.1) == "0.10s"

    def test_format_duration_minutes_and_seconds(self):
        """>= 60s uses minutes + seconds format."""
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        from tarball_diff import format_duration

        # >= 60 takes the minutes branch
        assert format_duration(60) == "1m 0.00s"
        assert format_duration(125) == "2m 5.00s"
        assert format_duration(3661.5) == "61m 1.50s"

    def test_extract_tarball_returns_directory(self):
        """extract_tarball extracts to a temp dir and returns the path."""
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        from tarball_diff import extract_tarball

        with tempfile.TemporaryDirectory() as tmp:
            tar_path = _make_tarball(
                tmp, [{"source": "action_camera", "duration": 1.0}]
            )
            extracted = extract_tarball(tar_path)
            try:
                assert os.path.isdir(extracted)
                # Verify the bundle payload is present
                assert os.path.isdir(os.path.join(extracted, "bundle"))
            finally:
                import shutil

                shutil.rmtree(extracted, ignore_errors=True)

    def test_count_action_camera_records_single(self):
        """Single action_camera record counts as 1."""
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        from tarball_diff import count_action_camera_records

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "r.json"), "w") as fp:
                json.dump(
                    [{"source": "action_camera", "duration": 2.0}], fp
                )
            assert count_action_camera_records(tmp) == 1

    def test_count_action_camera_records_filters_source(self):
        """Only records with source='action_camera' are counted."""
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        from tarball_diff import count_action_camera_records

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "r.json"), "w") as fp:
                json.dump(
                    [
                        {"source": "action_camera", "duration": 1.0},
                        {"source": "depth_camera", "duration": 1.0},
                        {"source": "action_camera", "duration": 1.0},
                        {"source": "screen_capture", "duration": 1.0},
                    ],
                    fp,
                )
            assert count_action_camera_records(tmp) == 2

    def test_count_action_camera_records_handles_object_and_list(self):
        """JSON may be a single object or a list; both are handled."""
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        from tarball_diff import count_action_camera_records

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "obj.json"), "w") as fp:
                json.dump({"source": "action_camera", "duration": 1.0}, fp)
            with open(os.path.join(tmp, "arr.json"), "w") as fp:
                json.dump([{"source": "action_camera", "duration": 1.0}], fp)
            with open(os.path.join(tmp, "other.json"), "w") as fp:
                json.dump({"source": "depth_camera", "duration": 1.0}, fp)
            assert count_action_camera_records(tmp) == 2

    def test_count_action_camera_records_ignores_bad_json(self):
        """Corrupt JSON files are silently skipped (no exception)."""
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        from tarball_diff import count_action_camera_records

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "bad.json"), "w") as fp:
                fp.write("not json at all {{{")
            with open(os.path.join(tmp, "good.json"), "w") as fp:
                json.dump([{"source": "action_camera", "duration": 1.0}], fp)
            # Should not raise; should count the one good record
            assert count_action_camera_records(tmp) == 1

    def test_count_action_camera_records_ignores_non_dict_items(self):
        """Non-dict items inside JSON arrays are safely skipped."""
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        from tarball_diff import count_action_camera_records

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "mixed.json"), "w") as fp:
                json.dump(
                    [
                        "not a dict",
                        42,
                        None,
                        {"source": "action_camera", "duration": 1.0},
                    ],
                    fp,
                )
            assert count_action_camera_records(tmp) == 1

    def test_get_video_duration_sums(self):
        """get_video_duration sums the duration field across records."""
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        from tarball_diff import get_video_duration

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "r.json"), "w") as fp:
                json.dump(
                    [
                        {"duration": 1.5},
                        {"duration": 2.5},
                        {"duration": 0.0},
                    ],
                    fp,
                )
            assert get_video_duration(tmp) == 4.0

    def test_get_video_duration_handles_object_and_list(self):
        """JSON may be a single object or a list."""
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        from tarball_diff import get_video_duration

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "obj.json"), "w") as fp:
                json.dump({"duration": 7.0}, fp)
            with open(os.path.join(tmp, "arr.json"), "w") as fp:
                json.dump([{"duration": 3.0}, {"duration": 2.0}], fp)
            assert get_video_duration(tmp) == 12.0

    def test_get_video_duration_skips_missing_field(self):
        """Records without a 'duration' field contribute 0 (no exception)."""
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        from tarball_diff import get_video_duration

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "r.json"), "w") as fp:
                json.dump(
                    [
                        {"source": "action_camera"},  # no duration key
                        {"duration": 5.0},
                    ],
                    fp,
                )
            assert get_video_duration(tmp) == 5.0

    def test_get_video_duration_ignores_bad_json(self):
        """Corrupt JSON files do not raise."""
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        from tarball_diff import get_video_duration

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "bad.json"), "w") as fp:
                fp.write("[not json")
            with open(os.path.join(tmp, "good.json"), "w") as fp:
                json.dump([{"duration": 2.0}], fp)
            assert get_video_duration(tmp) == 2.0

    def test_count_depth_files_matches_suffix_and_substring(self):
        """Counts files where name contains '.depth' or '_depth' (case-insensitive).

        The actual condition is `'.depth' in f.lower()` — note the leading dot.
        'depth_map.png' (no leading dot) does NOT match.
        """
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        from tarball_diff import count_depth_files

        with tempfile.TemporaryDirectory() as tmp:
            names = [
                "frame_0001.depth",  # matches '.depth' substring and suffix
                "depth_map.png",  # NO leading dot before 'depth' — does NOT match
                "scene_DEPTH.bin",  # '_depth' matches case-insensitive
                "frame_0001_color.png",  # not a depth file
                "notes.txt",  # not a depth file
            ]
            for n in names:
                with open(os.path.join(tmp, n), "wb") as fp:
                    fp.write(b"\x00" * 4)
            # 2 of 5 are depth files: frame_0001.depth and scene_DEPTH.bin
            assert count_depth_files(tmp) == 2

    def test_count_depth_files_empty_dir(self):
        """Empty directory returns 0."""
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        from tarball_diff import count_depth_files

        with tempfile.TemporaryDirectory() as tmp:
            assert count_depth_files(tmp) == 0

    def test_main_cli_missing_tarball_returns_error(self):
        """When a tarball path is missing, main() returns non-zero.

        main() reads sys.argv directly (no args parameter), so we patch
        sys.argv and capture stdout.
        """
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        import io as _io
        from contextlib import redirect_stdout

        import tarball_diff

        old_argv = sys.argv
        sys.argv = [
            "tarball_diff.py",
            "--left",
            "/nonexistent/a.tar.gz",
            "--right",
            "/nonexistent/b.tar.gz",
        ]
        buf = _io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = tarball_diff.main()
        finally:
            sys.argv = old_argv
        assert rc == 1
        # Error message should mention the missing path
        assert "Tarball not found" in buf.getvalue()

    def test_main_cli_prints_diff_table(self):
        """main() with two valid tarballs returns 0 and prints a markdown table."""
        sys.path.insert(0, str(TARBALL_DIFF_SCRIPT.parent))
        import tarball_diff

        with tempfile.TemporaryDirectory() as tmp:
            left = _make_tarball(
                tmp,
                records=[
                    {"source": "action_camera", "duration": 10.0},
                    {"source": "action_camera", "duration": 5.0},
                ],
                depth_files=["frame_0001.depth", "frame_0002.depth"],
            )
            # Right tarball needs a different temp dir to avoid collision
            right_tmp = tempfile.mkdtemp(prefix="tarball_diff_right_")
            try:
                right = _make_tarball(
                    right_tmp,
                    records=[
                        {"source": "action_camera", "duration": 7.0},
                    ],
                    depth_files=["frame_0001.depth"],
                )
                import io as _io
                from contextlib import redirect_stdout

                old_argv = sys.argv
                sys.argv = ["tarball_diff.py", "--left", left, "--right", right]
                buf = _io.StringIO()
                try:
                    with redirect_stdout(buf):
                        rc = tarball_diff.main()
                finally:
                    sys.argv = old_argv
                assert rc == 0
                output = buf.getvalue()
                assert "## Tarball Comparison" in output
                assert "Action Camera Records" in output
                assert "Video Duration" in output
                assert "Depth Files" in output
            finally:
                import shutil
                shutil.rmtree(right_tmp, ignore_errors=True)

    def test_main_cli_subprocess_invocation(self):
        """End-to-end: running the script as a subprocess exits 0 with valid tarballs."""
        with tempfile.TemporaryDirectory() as tmp:
            left = _make_tarball(
                tmp,
                records=[{"source": "action_camera", "duration": 1.0}],
                depth_files=["f1.depth"],
            )
            right = _make_tarball(
                tmp,
                records=[{"source": "action_camera", "duration": 2.0}],
                depth_files=["f1.depth", "f2.depth"],
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(TARBALL_DIFF_SCRIPT),
                    "--left",
                    left,
                    "--right",
                    right,
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, f"Script failed: {result.stderr}"
            assert "## Tarball Comparison" in result.stdout
            assert "Action Camera Records" in result.stdout
