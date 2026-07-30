"""Integration tests against the released sample tarball + synthetic fixtures."""

from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path

import pytest

from oyster_gamedata_sdk import (
    ActionCameraFrame,
    DependencyMissingError,
    SchemaValidationError,
    Systeminfo,
    Tarball,
    TarballNotFoundError,
    TarballStructureError,
)


# -- Open / construct --------------------------------------------------------


class TestTarballOpening:
    def test_from_extracted_directory(self, minimal_clip: Path):
        tar = Tarball.from_path(minimal_clip)
        assert tar.root == minimal_clip.resolve()
        assert tar.source == minimal_clip

    def test_missing_path(self, tmp_path: Path):
        with pytest.raises(TarballNotFoundError):
            Tarball.from_path(tmp_path / "does-not-exist.tar.gz")

    def test_incomplete_directory(self, tmp_path: Path):
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "video.mp4").write_bytes(b"\x00")
        with pytest.raises(TarballStructureError, match="missing required entries"):
            Tarball.from_path(bad)

    def test_context_manager(self, minimal_clip: Path):
        with Tarball.from_path(minimal_clip) as tar:
            assert tar.systeminfo.width == 1920

    def test_from_real_tarball(self, sample_tarball: Path, tmp_path: Path):
        extract_to = tmp_path / "extracted"
        with Tarball.from_path(sample_tarball, extract_to=extract_to) as tar:
            assert tar.source == sample_tarball
            assert tar.root.is_dir()
            assert (tar.root / "video.mp4").exists()


# -- systeminfo --------------------------------------------------------------


class TestSysteminfo:
    def test_synthetic(self, minimal_clip: Path):
        tar = Tarball.from_path(minimal_clip)
        assert isinstance(tar.systeminfo, Systeminfo)
        assert tar.systeminfo.game_process_name == "test.exe"
        assert tar.systeminfo.width == 1920
        assert tar.systeminfo.height == 1080

    def test_real_sample(self, extracted_sample: Path):
        tar = Tarball.from_path(extracted_sample)
        si = tar.systeminfo
        assert si.game_process_name == "minecraft.exe"
        assert si.width == 1920
        assert si.height == 1080
        assert si.record_dpi == 1.0

    def test_cached(self, minimal_clip: Path):
        tar = Tarball.from_path(minimal_clip)
        assert tar.systeminfo is tar.systeminfo  # same object both calls

    def test_invalid_json(self, minimal_clip: Path):
        (minimal_clip / "systeminfo.json").write_text("not-json{")
        tar = Tarball.from_path(minimal_clip)
        with pytest.raises(SchemaValidationError):
            _ = tar.systeminfo


# -- action_camera -----------------------------------------------------------


class TestActionCamera:
    def test_synthetic(self, minimal_clip: Path):
        tar = Tarball.from_path(minimal_clip)
        frames = tar.action_camera
        assert len(frames) == 3
        assert all(isinstance(f, ActionCameraFrame) for f in frames)
        assert frames[0].frame == 0
        assert frames[1].frame == 1

    def test_real_sample(self, extracted_sample: Path):
        tar = Tarball.from_path(extracted_sample)
        frames = tar.action_camera
        # The released sample contains 9000 frames (5 min × 30 fps × 60 s).
        assert len(frames) == 9000
        # First frame sanity:
        f0 = frames[0]
        assert f0.frame == 0
        assert f0.fps == 30.0
        assert f0.camera_intrinsics.is_pinhole

    def test_cached(self, minimal_clip: Path):
        tar = Tarball.from_path(minimal_clip)
        a = tar.action_camera
        b = tar.action_camera
        assert a is b


# -- gameinfo ----------------------------------------------------------------


class TestGameinfo:
    def test_synthetic(self, minimal_clip: Path):
        openpyxl = pytest.importorskip("openpyxl")
        tar = Tarball.from_path(minimal_clip)
        gi = tar.gameinfo
        assert gi.sheet_name == "GameInfo"
        assert gi.columns[:4] == ["clip_id", "duration_sec", "fps", "resolution"]
        assert gi.fields["clip_id"] == "synthetic-001"

    def test_real_sample(self, extracted_sample: Path):
        pytest.importorskip("openpyxl")
        tar = Tarball.from_path(extracted_sample)
        gi = tar.gameinfo
        # Sample is the 6-column variant with one data row.
        assert "clip_id" in gi.columns
        assert len(gi.rows) >= 1

    def test_missing_openpyxl(self, minimal_clip: Path, monkeypatch):
        """If openpyxl is not importable, we should raise DependencyMissingError."""
        import sys

        # Hide openpyxl from import for this test.
        monkeypatch.setitem(sys.modules, "openpyxl", None)
        tar = Tarball.from_path(minimal_clip)
        with pytest.raises(DependencyMissingError, match="openpyxl"):
            _ = tar.gameinfo


# -- depth -------------------------------------------------------------------


class TestDepth:
    def test_synthetic_count(self, minimal_clip: Path):
        tar = Tarball.from_path(minimal_clip)
        seq = tar.depth
        assert len(seq) == 1
        assert seq.paths()[0].name == "depth_000000.exr"

    def test_real_sample_count(self, extracted_sample: Path):
        tar = Tarball.from_path(extracted_sample)
        assert len(tar.depth) == 1800  # 5 min × 6 fps

    def test_real_sample_load(self, extracted_sample: Path):
        pytest.importorskip("OpenEXR")
        pytest.importorskip("numpy")
        import numpy as np

        tar = Tarball.from_path(extracted_sample)
        arr = tar.depth[0]
        assert isinstance(arr, np.ndarray)
        assert arr.dtype == np.float32
        # The released sample uses a 16x16 placeholder
        assert arr.ndim == 2

    def test_real_sample_iter(self, extracted_sample: Path):
        pytest.importorskip("OpenEXR")
        pytest.importorskip("numpy")
        tar = Tarball.from_path(extracted_sample)
        count = 0
        for frame_idx, depth_arr in tar.depth:
            assert frame_idx == count
            count += 1
            if count >= 3:
                break
        assert count == 3

    def test_depth_paths(self, extracted_sample: Path):
        tar = Tarball.from_path(extracted_sample)
        paths = tar.depth.paths()
        assert len(paths) == 1800
        assert all(p.suffix == ".exr" for p in paths)


# -- video -------------------------------------------------------------------


class TestVideo:
    def test_path_exposed(self, minimal_clip: Path):
        tar = Tarball.from_path(minimal_clip)
        v = tar.video
        assert v.path == minimal_clip / "video.mp4"
        assert str(v) == str(v.path)  # __fspath__

    def test_open_cv2_missing_dep(self, minimal_clip: Path, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "cv2", None)
        tar = Tarball.from_path(minimal_clip)
        with pytest.raises(DependencyMissingError, match="cv2"):
            tar.video.open_cv2()


# -- metadata_summary --------------------------------------------------------


class TestMetadataSummary:
    def test_synthetic(self, minimal_clip: Path):
        tar = Tarball.from_path(minimal_clip)
        s = tar.metadata_summary()
        assert s.systeminfo_resolution == (1920, 1080)
        assert s.systeminfo_game == "test.exe"
        assert s.n_action_frames == 3
        assert s.n_depth_frames == 1
        assert s.fps_first == 30.0
        # 2 frames of route_type=1, 1 frame of route_type=2
        assert s.route_type_distribution == {1: 2, 2: 1}

    def test_real_sample(self, extracted_sample: Path):
        tar = Tarball.from_path(extracted_sample)
        s = tar.metadata_summary()
        assert s.systeminfo_resolution == (1920, 1080)
        assert s.n_action_frames == 9000
        assert s.n_depth_frames == 1800
        assert s.fps_first == 30.0

    def test_to_dict_serializable(self, minimal_clip: Path):
        tar = Tarball.from_path(minimal_clip)
        d = tar.metadata_summary().to_dict()
        # Roundtrip through JSON
        json.dumps(d)


# -- validate (lint) ---------------------------------------------------------


class TestValidate:
    def test_synthetic_runs(self, minimal_clip: Path):
        tar = Tarball.from_path(minimal_clip)
        report = tar.validate()
        assert report.total > 0
        # The lint script may FAIL on synthetic stubs; we only care that it runs.
        assert report.summary().startswith("[")

    def test_real_sample_runs(self, extracted_sample: Path):
        tar = Tarball.from_path(extracted_sample)
        report = tar.validate()
        d = report.to_dict()
        assert "summary" in d
        assert "results" in d
        assert isinstance(d["summary"]["pass_rate"], str)

    def test_report_to_json(self, minimal_clip: Path):
        tar = Tarball.from_path(minimal_clip)
        report = tar.validate()
        payload = report.to_json()
        parsed = json.loads(payload)
        assert "results" in parsed


# -- path traversal safety ---------------------------------------------------


class TestSafeExtract:
    def test_rejects_path_traversal(self, tmp_path: Path):
        """A malicious tarball with ../etc/passwd entries must be rejected."""
        evil = tmp_path / "evil.tar.gz"
        with tarfile.open(evil, "w:gz") as tf:
            info = tarfile.TarInfo(name="../etc/passwd")
            info.size = 0
            tf.addfile(info, fileobj=None)
        with pytest.raises(TarballStructureError, match="path-traversal"):
            Tarball.from_path(evil)
