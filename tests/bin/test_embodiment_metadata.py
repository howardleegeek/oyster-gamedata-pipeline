"""Tests for bin/embodiment_metadata.py — per-scene embodiment.json generation.

Covers the dataclasses and helper functions used to characterize embodied
agents for the arxiv 2505.05753 scaling-laws axis:

- AgentGeometry / LocomotionParams / EmbodimentMetadata dataclasses
- generate_embodiment_id (format + uniqueness)
- create_default_geometry / create_default_locomotion
- generate_scene_metadata (default + custom geometry/locomotion)
- process_scene_directory (missing dir, no output, with output, dry-run paths)
- main() CLI entry point (with and without --dry-run, success exit code)
"""

import json
from pathlib import Path

import pytest

from bin.embodiment_metadata import (
    AgentGeometry,
    EmbodimentMetadata,
    LocomotionParams,
    create_default_geometry,
    create_default_locomotion,
    generate_embodiment_id,
    generate_scene_metadata,
    main,
    process_scene_directory,
)


class TestAgentGeometry:
    """Tests for the AgentGeometry dataclass."""

    def test_required_fields(self) -> None:
        geom = AgentGeometry(height=1.0, width=0.5, depth=0.3, mass=60.0)
        assert geom.height == 1.0
        assert geom.width == 0.5
        assert geom.depth == 0.3
        assert geom.mass == 60.0

    def test_default_bounding_box_type(self) -> None:
        geom = AgentGeometry(height=1.0, width=0.5, depth=0.3, mass=60.0)
        assert geom.bounding_box_type == "axis_aligned"

    def test_custom_bounding_box_type(self) -> None:
        geom = AgentGeometry(
            height=1.0, width=0.5, depth=0.3, mass=60.0, bounding_box_type="obb"
        )
        assert geom.bounding_box_type == "obb"


class TestLocomotionParams:
    """Tests for the LocomotionParams dataclass."""

    def test_required_fields(self) -> None:
        loco = LocomotionParams(mode="wheeled", max_speed=2.5, turn_radius=0.5)
        assert loco.mode == "wheeled"
        assert loco.max_speed == 2.5
        assert loco.turn_radius == 0.5

    def test_default_terrain_capability(self) -> None:
        loco = LocomotionParams(mode="legged", max_speed=1.5, turn_radius=0.3)
        assert loco.terrain_capability == "flat"

    def test_custom_terrain_capability(self) -> None:
        loco = LocomotionParams(
            mode="legged", max_speed=1.5, turn_radius=0.3, terrain_capability="rough"
        )
        assert loco.terrain_capability == "rough"


class TestEmbodimentMetadata:
    """Tests for the EmbodimentMetadata dataclass."""

    def test_required_fields(self) -> None:
        meta = EmbodimentMetadata(
            embodiment_id="emb_test_abcd1234",
            scene_id="test_scene",
            agent_geometry={"height": 1.0},
            locomotion_mode={"mode": "legged"},
        )
        assert meta.embodiment_id == "emb_test_abcd1234"
        assert meta.scene_id == "test_scene"
        assert meta.agent_geometry == {"height": 1.0}
        assert meta.locomotion_mode == {"mode": "legged"}

    def test_default_version(self) -> None:
        meta = EmbodimentMetadata(
            embodiment_id="x", scene_id="y", agent_geometry={}, locomotion_mode={}
        )
        assert meta.version == "1.0"

    def test_custom_version(self) -> None:
        meta = EmbodimentMetadata(
            embodiment_id="x",
            scene_id="y",
            agent_geometry={},
            locomotion_mode={},
            version="2.0",
        )
        assert meta.version == "2.0"


class TestGenerateEmbodimentId:
    """Tests for generate_embodiment_id()."""

    def test_format_starts_with_emb_prefix(self) -> None:
        eid = generate_embodiment_id("kitchen_01")
        assert eid.startswith("emb_")

    def test_format_contains_scene_id(self) -> None:
        eid = generate_embodiment_id("warehouse_42")
        assert "warehouse_42" in eid

    def test_format_has_8_hex_chars_suffix(self) -> None:
        eid = generate_embodiment_id("scene")
        # emb_<scene>_<8 hex chars>
        suffix = eid.rsplit("_", 1)[-1]
        assert len(suffix) == 8
        int(suffix, 16)  # should parse as hex

    def test_uniqueness_across_calls(self) -> None:
        ids = {generate_embodiment_id("scene") for _ in range(50)}
        assert len(ids) == 50  # all unique (uuid4 collision is negligible)


class TestCreateDefaultGeometry:
    """Tests for create_default_geometry()."""

    def test_returns_agent_geometry_instance(self) -> None:
        geom = create_default_geometry()
        assert isinstance(geom, AgentGeometry)

    def test_default_values_match_spec(self) -> None:
        geom = create_default_geometry()
        assert geom.height == 1.75
        assert geom.width == 0.6
        assert geom.depth == 0.4
        assert geom.mass == 70.0
        assert geom.bounding_box_type == "axis_aligned"


class TestCreateDefaultLocomotion:
    """Tests for create_default_locomotion()."""

    def test_returns_locomotion_params_instance(self) -> None:
        loco = create_default_locomotion()
        assert isinstance(loco, LocomotionParams)

    def test_default_values_match_spec(self) -> None:
        loco = create_default_locomotion()
        assert loco.mode == "legged"
        assert loco.max_speed == 1.5
        assert loco.turn_radius == 0.3
        assert loco.terrain_capability == "flat"


class TestGenerateSceneMetadata:
    """Tests for generate_scene_metadata()."""

    def test_default_geometry_and_locomotion(self) -> None:
        meta = generate_scene_metadata("scene_1")
        assert isinstance(meta, EmbodimentMetadata)
        assert meta.scene_id == "scene_1"
        assert meta.embodiment_id.startswith("emb_scene_1_")
        # default geometry fields are propagated to the dict
        assert meta.agent_geometry["height"] == 1.75
        assert meta.agent_geometry["mass"] == 70.0
        # default locomotion fields
        assert meta.locomotion_mode["mode"] == "legged"
        assert meta.locomotion_mode["max_speed"] == 1.5
        assert meta.version == "1.0"

    def test_custom_geometry_used(self) -> None:
        custom = AgentGeometry(height=2.0, width=0.8, depth=0.5, mass=80.0)
        meta = generate_scene_metadata("scene_2", geometry=custom)
        assert meta.agent_geometry == {
            "height": 2.0,
            "width": 0.8,
            "depth": 0.5,
            "mass": 80.0,
            "bounding_box_type": "axis_aligned",
        }

    def test_custom_locomotion_used(self) -> None:
        custom = LocomotionParams(
            mode="aerial", max_speed=10.0, turn_radius=2.0, terrain_capability="air"
        )
        meta = generate_scene_metadata("scene_3", locomotion=custom)
        assert meta.locomotion_mode == {
            "mode": "aerial",
            "max_speed": 10.0,
            "turn_radius": 2.0,
            "terrain_capability": "air",
        }

    def test_both_custom_used(self) -> None:
        geom = AgentGeometry(height=3.0, width=1.0, depth=1.0, mass=100.0)
        loco = LocomotionParams(mode="static", max_speed=0.0, turn_radius=0.0)
        meta = generate_scene_metadata("scene_4", geometry=geom, locomotion=loco)
        assert meta.agent_geometry["height"] == 3.0
        assert meta.locomotion_mode["mode"] == "static"


class TestProcessSceneDirectory:
    """Tests for process_scene_directory()."""

    def test_missing_directory_returns_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        missing = Path("/tmp/does_not_exist_embodiment_test_xyz")
        results = process_scene_directory(missing)
        assert results == []
        captured = capsys.readouterr()
        assert "Warning" in captured.err
        assert "not found" in captured.err

    def test_directory_with_no_subdirs_returns_empty(self, tmp_path: Path) -> None:
        # tmp_path exists but has no subdirectories
        (tmp_path / "stray_file.txt").write_text("hi")
        results = process_scene_directory(tmp_path)
        assert results == []

    def test_directory_with_subdirs_generates_metadata(self, tmp_path: Path) -> None:
        (tmp_path / "kitchen").mkdir()
        (tmp_path / "garage").mkdir()
        results = process_scene_directory(tmp_path)
        assert len(results) == 2
        scene_ids = {r.scene_id for r in results}
        assert scene_ids == {"kitchen", "garage"}
        for r in results:
            assert r.embodiment_id.startswith(f"emb_{r.scene_id}_")

    def test_writes_output_files(self, tmp_path: Path) -> None:
        (tmp_path / "kitchen").mkdir()
        output_dir = tmp_path / "out"
        results = process_scene_directory(tmp_path, output_dir=output_dir)
        assert len(results) == 1
        out_file = output_dir / "kitchen_embodiment.json"
        assert out_file.exists()
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data["scene_id"] == "kitchen"
        assert data["embodiment_id"].startswith("emb_kitchen_")
        assert "agent_geometry" in data
        assert "locomotion_mode" in data

    def test_creates_output_dir_if_missing(self, tmp_path: Path) -> None:
        (tmp_path / "scene").mkdir()
        output_dir = tmp_path / "deeply" / "nested" / "out"
        results = process_scene_directory(tmp_path, output_dir=output_dir)
        assert len(results) == 1
        assert output_dir.exists()
        assert (output_dir / "scene_embodiment.json").exists()

    def test_files_at_top_level_are_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "sceneA").mkdir()
        (tmp_path / "stray.txt").write_text("noise")
        results = process_scene_directory(tmp_path)
        assert len(results) == 1
        assert results[0].scene_id == "sceneA"


class TestMain:
    """Tests for main() CLI entry point."""

    def test_dry_run_prints_metadata(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        (tmp_path / "alpha").mkdir()
        (tmp_path / "beta").mkdir()
        rc = main(["--scene-dir", str(tmp_path), "--dry-run"])
        assert rc == 0
        captured = capsys.readouterr()
        # Each scene emits a JSON object containing the scene_id
        assert '"scene_id": "alpha"' in captured.out
        assert '"scene_id": "beta"' in captured.out
        assert "Processed 2 scenes." in captured.out

    def test_dry_run_does_not_write_files(self, tmp_path: Path) -> None:
        (tmp_path / "gamma").mkdir()
        rc = main(["--scene-dir", str(tmp_path), "--dry-run"])
        assert rc == 0
        # No file should have been written inside tmp_path
        files = list(tmp_path.rglob("*_embodiment.json"))
        assert files == []

    def test_with_output_writes_files(self, tmp_path: Path) -> None:
        (tmp_path / "delta").mkdir()
        output_dir = tmp_path / "out"
        rc = main(
            [
                "--scene-dir",
                str(tmp_path),
                "--output",
                str(output_dir),
            ]
        )
        assert rc == 0
        assert (output_dir / "delta_embodiment.json").exists()
        captured_file = output_dir / "delta_embodiment.json"
        data = json.loads(captured_file.read_text(encoding="utf-8"))
        assert data["scene_id"] == "delta"

    def test_missing_scene_dir_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Warning to stderr but process exits 0
        rc = main(["--scene-dir", "/tmp/nonexistent_embodiment_test_zzz"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "Warning" in captured.err
        assert "Processed 0 scenes." in captured.out
