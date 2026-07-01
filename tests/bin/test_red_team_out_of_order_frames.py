#!/usr/bin/env python3
"""Test coverage for bin/red_team_out_of_order_frames.py.

This module exercises the red-team JSON frame shuffler used to validate
that lint pipelines reject non-monotonic frame_id sequences. Coverage:

- find_frames: top-level list, dict-wrapped "frames" key, deeply nested
  structures, dict values that themselves contain a frames array, lists
  whose first element is not a dict, and structures with no frames.
- replace_frames: top-level list, dict "frames" key, nested list,
  not-found case, mixed dict/list recursion, and empty list path.
- shuffle_frames: success path with output_file, default output path
  convention, seed reproducibility, single-frame short-circuit, missing
  frames error, malformed JSON error, OSError on read, write success
  preserves key order of parent data, and in-place mutation of nested
  list structure.
- main: successful CLI invocation, default output written next to input,
  custom -o flag, custom --seed flag, missing input file, non-monotonic
  result detection on output, help text, and unknown-arg failure.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# find_frames
# ---------------------------------------------------------------------------


class TestFindFrames:
    """Tests for find_frames function."""

    def test_top_level_list_with_frame_id(self):
        from bin.red_team_out_of_order_frames import find_frames

        data = [
            {"frame_id": 0, "x": 1},
            {"frame_id": 1, "x": 2},
        ]
        result = find_frames(data)
        assert result is data
        assert len(result) == 2

    def test_dict_with_frames_key(self):
        from bin.red_team_out_of_order_frames import find_frames

        frames = [{"frame_id": 0}, {"frame_id": 1}]
        data = {"frames": frames, "session_id": "abc"}
        result = find_frames(data)
        assert result is frames

    def test_nested_dict_search(self):
        from bin.red_team_out_of_order_frames import find_frames

        frames = [{"frame_id": 0}, {"frame_id": 1}]
        data = {"session": {"recording": {"frames": frames, "meta": {}}}}
        result = find_frames(data)
        assert result is frames

    def test_nested_list_search(self):
        from bin.red_team_out_of_order_frames import find_frames

        frames = [{"frame_id": 0}, {"frame_id": 1}]
        data = {"items": [{"name": "clip", "items": [frames]}]}
        result = find_frames(data)
        assert result is frames

    def test_first_element_not_dict_returns_none_for_top_list(self):
        from bin.red_team_out_of_order_frames import find_frames

        # Top-level list whose first element is not a dict with frame_id
        # should not match the top-list branch and recurses into items.
        data = ["not_a_dict", "also_not_a_dict"]
        result = find_frames(data)
        assert result is None

    def test_empty_list_returns_none(self):
        from bin.red_team_out_of_order_frames import find_frames

        data = []
        result = find_frames(data)
        assert result is None

    def test_no_frames_anywhere_returns_none(self):
        from bin.red_team_out_of_order_frames import find_frames

        data = {"session": {"recording": {"meta": {"fps": 30}}}}
        result = find_frames(data)
        assert result is None

    def test_prefers_top_dict_frames_key_over_nested_list(self):
        from bin.red_team_out_of_order_frames import find_frames

        top_frames = [{"frame_id": 0}, {"frame_id": 1}]
        nested_frames = [{"frame_id": 99}]
        data = {"frames": top_frames, "history": [nested_frames]}
        result = find_frames(data)
        # Dict branch returns top_frames immediately.
        assert result is top_frames


# ---------------------------------------------------------------------------
# replace_frames
# ---------------------------------------------------------------------------


class TestReplaceFrames:
    """Tests for replace_frames function."""

    def test_replace_top_level_list(self):
        from bin.red_team_out_of_order_frames import replace_frames

        data = [{"frame_id": 0}, {"frame_id": 1}]
        new = [{"frame_id": 1}, {"frame_id": 0}]
        ok = replace_frames(data, new)
        assert ok is True
        assert data == new
        # Replaced list is the same object — caller's reference updates.
        assert data is not new

    def test_replace_dict_frames_key(self):
        from bin.red_team_out_of_order_frames import replace_frames

        data = {"frames": [{"frame_id": 0}], "session": "abc"}
        new = [{"frame_id": 9}, {"frame_id": 8}]
        ok = replace_frames(data, new)
        assert ok is True
        assert data["frames"] == new
        assert data["session"] == "abc"

    def test_replace_nested_list(self):
        from bin.red_team_out_of_order_frames import replace_frames

        frames = [{"frame_id": 0}, {"frame_id": 1}]
        data = {"items": [frames]}
        new = [{"frame_id": 1}, {"frame_id": 0}]
        ok = replace_frames(data, new)
        assert ok is True
        assert frames == new

    def test_replace_returns_false_when_no_frames(self):
        from bin.red_team_out_of_order_frames import replace_frames

        data = {"session": {"meta": {"fps": 30}}}
        ok = replace_frames(data, [{"frame_id": 0}])
        assert ok is False

    def test_replace_dict_with_nonlist_frames_value_recurses(self):
        from bin.red_team_out_of_order_frames import replace_frames

        # data["frames"] is not a list, so the dict-branch is skipped
        # and the function recurses into other dict values.
        inner = [{"frame_id": 0}, {"frame_id": 1}]
        data = {"frames": "not-a-list", "other": inner}
        new = [{"frame_id": 1}, {"frame_id": 0}]
        ok = replace_frames(data, new)
        assert ok is True
        assert inner == new

    def test_replace_empty_list_does_not_match(self):
        from bin.red_team_out_of_order_frames import replace_frames

        # Top-level empty list — first branch of `isinstance(data, list)`
        # is gated by `if data and ...`, so it falls through to recursion
        # over an empty list (no items, returns False).
        data = []
        ok = replace_frames(data, [{"frame_id": 0}])
        assert ok is False


# ---------------------------------------------------------------------------
# shuffle_frames
# ---------------------------------------------------------------------------


class TestShuffleFrames:
    """Tests for shuffle_frames function."""

    def _write_frames_json(self, tmp_path: Path, frames, wrapper: bool = False) -> Path:
        payload = {"frames": frames} if wrapper else frames
        path = tmp_path / "input.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_shuffle_top_level_list_breaks_monotonic(self, tmp_path):
        from bin.red_team_out_of_order_frames import shuffle_frames

        frames = [{"frame_id": i} for i in range(10)]
        src = self._write_frames_json(tmp_path, frames)
        dst = tmp_path / "out.json"
        ok = shuffle_frames(src, dst, seed=42)
        assert ok is True
        assert dst.exists()
        loaded = json.loads(dst.read_text(encoding="utf-8"))
        ids = [f["frame_id"] for f in loaded]
        assert sorted(ids) == list(range(10))
        # Shuffled with seed 42 — must not be the original order.
        assert ids != list(range(10))

    def test_shuffle_dict_with_frames_key(self, tmp_path):
        from bin.red_team_out_of_order_frames import shuffle_frames

        frames = [{"frame_id": i} for i in range(8)]
        src = self._write_frames_json(tmp_path, frames, wrapper=True)
        dst = tmp_path / "out.json"
        ok = shuffle_frames(src, dst, seed=7)
        assert ok is True
        loaded = json.loads(dst.read_text(encoding="utf-8"))
        assert "session_id" not in loaded
        ids = [f["frame_id"] for f in loaded["frames"]]
        assert sorted(ids) == list(range(8))
        assert ids != list(range(8))

    def test_default_output_path_uses_shuffled_suffix(self, tmp_path):
        from bin.red_team_out_of_order_frames import shuffle_frames

        frames = [{"frame_id": 0}, {"frame_id": 1}, {"frame_id": 2}]
        src = self._write_frames_json(tmp_path, frames)
        ok = shuffle_frames(src, output_file=None, seed=1)
        assert ok is True
        default_out = tmp_path / "input_shuffled.json"
        assert default_out.exists()

    def test_seed_reproducible(self, tmp_path):
        from bin.red_team_out_of_order_frames import shuffle_frames

        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        frames = [{"frame_id": i} for i in range(20)]
        src1 = self._write_frames_json(dir_a, frames)
        src2 = self._write_frames_json(dir_b, frames)
        dst1 = tmp_path / "a" / "o1.json"
        dst2 = tmp_path / "b" / "o2.json"
        assert shuffle_frames(src1, dst1, seed=123) is True
        assert shuffle_frames(src2, dst2, seed=123) is True
        ids1 = [f["frame_id"] for f in json.loads(dst1.read_text(encoding="utf-8"))]
        ids2 = [f["frame_id"] for f in json.loads(dst2.read_text(encoding="utf-8"))]
        assert ids1 == ids2

    def test_single_frame_short_circuits(self, tmp_path, capsys):
        from bin.red_team_out_of_order_frames import shuffle_frames

        src = self._write_frames_json(tmp_path, [{"frame_id": 0}])
        dst = tmp_path / "out.json"
        ok = shuffle_frames(src, dst, seed=0)
        assert ok is False
        assert not dst.exists()
        captured = capsys.readouterr()
        assert "nothing to shuffle" in captured.err

    def test_no_frames_in_file_returns_false(self, tmp_path, capsys):
        from bin.red_team_out_of_order_frames import shuffle_frames

        path = tmp_path / "no_frames.json"
        path.write_text(json.dumps({"session_id": "abc"}), encoding="utf-8")
        dst = tmp_path / "out.json"
        ok = shuffle_frames(path, dst, seed=0)
        assert ok is False
        captured = capsys.readouterr()
        assert "No frames array found" in captured.err

    def test_malformed_json_returns_false(self, tmp_path, capsys):
        from bin.red_team_out_of_order_frames import shuffle_frames

        path = tmp_path / "bad.json"
        path.write_text("{not valid json", encoding="utf-8")
        dst = tmp_path / "out.json"
        ok = shuffle_frames(path, dst, seed=0)
        assert ok is False
        captured = capsys.readouterr()
        assert "Error reading" in captured.err

    def test_missing_input_file_returns_false(self, tmp_path, capsys):
        from bin.red_team_out_of_order_frames import shuffle_frames

        missing = tmp_path / "does_not_exist.json"
        dst = tmp_path / "out.json"
        ok = shuffle_frames(missing, dst, seed=0)
        assert ok is False
        captured = capsys.readouterr()
        # OSError path → "Error reading" message
        assert "Error reading" in captured.err

    def test_written_file_is_valid_utf8_json(self, tmp_path):
        from bin.red_team_out_of_order_frames import shuffle_frames

        frames = [{"frame_id": 0, "tag": "你好"}, {"frame_id": 1, "tag": "world"}]
        src = self._write_frames_json(tmp_path, frames)
        dst = tmp_path / "out.json"
        ok = shuffle_frames(src, dst, seed=0)
        assert ok is True
        text = dst.read_text(encoding="utf-8")
        loaded = json.loads(text)
        tags = sorted(f["tag"] for f in loaded)
        assert tags == ["world", "你好"]

    def test_preserves_sibling_keys_in_wrapper(self, tmp_path):
        from bin.red_team_out_of_order_frames import shuffle_frames

        frames = [{"frame_id": i} for i in range(5)]
        payload = {"frames": frames, "session_id": "S-001", "fps": 30}
        src = tmp_path / "input.json"
        src.write_text(json.dumps(payload), encoding="utf-8")
        dst = tmp_path / "out.json"
        ok = shuffle_frames(src, dst, seed=99)
        assert ok is True
        loaded = json.loads(dst.read_text(encoding="utf-8"))
        assert loaded["session_id"] == "S-001"
        assert loaded["fps"] == 30
        assert len(loaded["frames"]) == 5

    def test_nested_frames_in_list_recurses(self, tmp_path):
        from bin.red_team_out_of_order_frames import shuffle_frames

        frames = [{"frame_id": i} for i in range(6)]
        payload = {"items": [{"name": "clip", "items": [frames]}]}
        src = tmp_path / "input.json"
        src.write_text(json.dumps(payload), encoding="utf-8")
        dst = tmp_path / "out.json"
        ok = shuffle_frames(src, dst, seed=5)
        assert ok is True
        loaded = json.loads(dst.read_text(encoding="utf-8"))
        # First match in recursion is the inner list of frame dicts.
        found = loaded["items"][0]["items"][0]
        ids = [f["frame_id"] for f in found]
        assert sorted(ids) == list(range(6))
        assert ids != list(range(6))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the CLI main entry point."""

    def _write_input(self, tmp_path: Path) -> Path:
        frames = [{"frame_id": i} for i in range(5)]
        path = tmp_path / "in.json"
        path.write_text(json.dumps(frames), encoding="utf-8")
        return path

    def test_main_success_exits_zero(self, tmp_path):
        from bin.red_team_out_of_order_frames import main

        src = self._write_input(tmp_path)
        dst = tmp_path / "out.json"
        rc = main([str(src), "-o", str(dst), "--seed", "1"])
        assert rc == 0
        assert dst.exists()
        loaded = json.loads(dst.read_text(encoding="utf-8"))
        ids = [f["frame_id"] for f in loaded]
        assert sorted(ids) == list(range(5))
        assert ids != list(range(5))

    def test_main_default_output_creates_shuffled_sibling(self, tmp_path):
        from bin.red_team_out_of_order_frames import main

        src = self._write_input(tmp_path)
        rc = main([str(src), "--seed", "3"])
        assert rc == 0
        assert (tmp_path / "in_shuffled.json").exists()

    def test_main_missing_input_file_exits_one(self, tmp_path, capsys):
        from bin.red_team_out_of_order_frames import main

        missing = tmp_path / "ghost.json"
        rc = main([str(missing)])
        assert rc == 1
        captured = capsys.readouterr()
        assert "Input file not found" in captured.err

    def test_main_input_is_directory_exits_one(self, tmp_path, capsys):
        from bin.red_team_out_of_order_frames import main

        # is_file() is False for a directory
        rc = main([str(tmp_path)])
        assert rc == 1
        captured = capsys.readouterr()
        assert "Input file not found" in captured.err

    def test_main_help_exits_zero(self):
        from bin.red_team_out_of_order_frames import main

        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        # argparse help exits with code 0
        assert exc.value.code == 0

    def test_main_unknown_flag_exits_non_zero(self):
        from bin.red_team_out_of_order_frames import main

        with pytest.raises(SystemExit) as exc:
            main(["--not-a-real-flag"])
        assert exc.value.code != 0

    def test_main_no_seed_shuffles_still(self, tmp_path):
        from bin.red_team_out_of_order_frames import main

        # Use a larger frame list so any random shuffle breaks monotonicity
        # with overwhelming probability even without a fixed seed.
        frames = [{"frame_id": i} for i in range(50)]
        src = tmp_path / "big.json"
        src.write_text(json.dumps(frames), encoding="utf-8")
        dst = tmp_path / "big_out.json"
        rc = main([str(src), "-o", str(dst)])
        assert rc == 0
        loaded = json.loads(dst.read_text(encoding="utf-8"))
        ids = [f["frame_id"] for f in loaded]
        assert sorted(ids) == list(range(50))
        # Statistically certain: 50-element permutation is almost never
        # the identity. The probability is 1/50! ≈ 0.
        assert ids != list(range(50))

    def test_main_non_monotonic_output_breaks_lint(self, tmp_path):
        """End-to-end: shuffled output should be detected as non-monotonic
        by a simple monotonicity check — the contract the red-team tool
        promises."""
        from bin.red_team_out_of_order_frames import main

        frames = [{"frame_id": i} for i in range(10)]
        src = tmp_path / "in.json"
        src.write_text(json.dumps(frames), encoding="utf-8")
        dst = tmp_path / "out.json"
        rc = main([str(src), "-o", str(dst), "--seed", "2026"])
        assert rc == 0
        loaded = json.loads(dst.read_text(encoding="utf-8"))
        ids = [f["frame_id"] for f in loaded]
        is_monotonic = all(ids[i] < ids[i + 1] for i in range(len(ids) - 1))
        assert is_monotonic is False

    def test_subprocess_invocation(self, tmp_path):
        """End-to-end: run the module as `python -m bin.red_team_out_of_order_frames`."""
        frames = [{"frame_id": i} for i in range(6)]
        src = tmp_path / "in.json"
        src.write_text(json.dumps(frames), encoding="utf-8")
        dst = tmp_path / "out.json"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "bin.red_team_out_of_order_frames",
                str(src),
                "-o",
                str(dst),
                "--seed",
                "0",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert dst.exists()
        loaded = json.loads(dst.read_text(encoding="utf-8"))
        assert sorted(f["frame_id"] for f in loaded) == list(range(6))
