"""Tests for the Phase 1 trajectory bundle packager.

Coverage matrix:

  * provenance shape (required keys + types)
  * checksum stability across repeated runs
  * manifest extension preserves original fields
  * round-trip zip → unpack → verify ok
  * tampered zip is detected
  * missing stream file at construction time raises FileNotFoundError
  * CLI subcommand smoke test
  * git SHA fallback when not in a git repo
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone

UTC = timezone.utc

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from oyster_agent_runner.cli import app
from oyster_agent_runner.minecraft_streams import (
    COT_FILENAME,
    INPUTS_FILENAME,
    MANIFEST_FILENAME,
    METADATA_FILENAME,
    MinecraftStreamWriter,
)
from oyster_agent_runner.schema import (
    EVENT_ACTION,
    EVENT_AGENT_STEP,
    EVENT_END,
    EVENT_LLM_REASONING,
    EVENT_LLM_THINKING,
    EVENT_OBSERVATION,
    EVENT_START,
    TrajectoryEvent,
)
from oyster_agent_runner.trajectory_packager import (
    PACKAGED_MANIFEST_FILENAME,
    PROVENANCE_FILENAME,
    PROVENANCE_SCHEMA_VERSION,
    TrajectoryPackager,
)

# --- Fixtures ---------------------------------------------------------------


def _write_phase1_bundle(bundle_dir: Path) -> Path:
    """Produce a valid Phase 1 bundle inside ``bundle_dir`` and return it.

    Uses :class:`MinecraftStreamWriter` so we exercise the same writer
    the runner uses in production. The bundle has one full step (start,
    thinking, reasoning, observation, action, agent_step, end) — enough
    for :class:`Replayer.verify_consistency` to walk without errors.
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)
    anchor = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
    with MinecraftStreamWriter(bundle_dir) as w:
        w.write(
            TrajectoryEvent(
                timestamp=0.0,
                event_type=EVENT_START,
                event_args={"task_id": "PKG-test-001"},
            )
        )
        w.write(
            TrajectoryEvent(
                timestamp=0.1,
                event_type=EVENT_LLM_THINKING,
                event_args={"step": 0, "text": "deliberating"},
            )
        )
        w.write(
            TrajectoryEvent(
                timestamp=0.2,
                event_type=EVENT_LLM_REASONING,
                event_args={"text": "answer"},
            )
        )
        w.write(
            TrajectoryEvent(
                timestamp=0.2,
                event_type=EVENT_OBSERVATION,
                event_args={"value": "obs0"},
            )
        )
        w.write(
            TrajectoryEvent(
                timestamp=0.2,
                event_type=EVENT_ACTION,
                event_args={"op": "noop"},
            )
        )
        w.write(
            TrajectoryEvent(
                timestamp=0.2,
                event_type=EVENT_AGENT_STEP,
                event_args={"step": 0, "success": True},
            )
        )
        w.write(
            TrajectoryEvent(
                timestamp=0.3,
                event_type=EVENT_END,
                event_args={"success": True, "total_steps": 1, "reason": "success"},
            )
        )
        w.finalize_manifest(
            task_id="PKG-test-001",
            model="claude-sonnet-4-5",
            provider="claude-thinking",
            environment="minecraft",
            anchor_utc=anchor,
            success=True,
            termination_reason="success",
            total_steps=1,
            wall_clock_sec=0.3,
            thinking_budget_tokens=16000,
            license="train-only",
        )
    return bundle_dir


@pytest.fixture
def bundle_dir(tmp_path: Path) -> Path:
    """A canonical Phase 1 bundle ready for packaging."""
    return _write_phase1_bundle(tmp_path / "bundle")


# --- Construction ----------------------------------------------------------


def test_packager_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="bundle directory not found"):
        TrajectoryPackager(tmp_path / "does_not_exist")


def test_packager_rejects_missing_manifest(tmp_path: Path) -> None:
    bundle = tmp_path / "incomplete"
    bundle.mkdir()
    (bundle / COT_FILENAME).write_text("", encoding="utf-8")
    (bundle / METADATA_FILENAME).write_text("", encoding="utf-8")
    (bundle / INPUTS_FILENAME).write_text("", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="missing required manifest"):
        TrajectoryPackager(bundle)


def test_packager_rejects_missing_stream_file(tmp_path: Path) -> None:
    bundle = _write_phase1_bundle(tmp_path / "bundle")
    (bundle / COT_FILENAME).unlink()
    with pytest.raises(FileNotFoundError, match="missing required stream"):
        TrajectoryPackager(bundle)


# --- Provenance ------------------------------------------------------------


def test_provenance_has_required_top_level_shape(bundle_dir: Path) -> None:
    packager = TrajectoryPackager(bundle_dir)
    provenance = packager.compute_provenance()

    assert provenance["schema_version"] == PROVENANCE_SCHEMA_VERSION
    assert "packaged_at" in provenance
    # ISO 8601 UTC suffix.
    assert provenance["packaged_at"].endswith("Z")

    runtime = provenance["runtime"]
    assert "git_sha" in runtime
    assert runtime["package_name"] == "oyster-agent-runner"
    assert "package_version" in runtime

    collection = provenance["collection"]
    assert collection["task_id"] == "PKG-test-001"
    assert collection["environment"] == "minecraft"
    assert collection["provider"] == "claude-thinking"
    assert collection["model"] == "claude-sonnet-4-5"
    assert collection["phase"] == 1

    rights = provenance["rights_delivered"]
    assert rights["license"] == "train-only"
    assert "redistribution" in rights


def test_provenance_git_sha_falls_back_when_git_missing(bundle_dir: Path) -> None:
    """When git is not on PATH, git_sha must fall back to 'unknown' silently."""
    packager = TrajectoryPackager(bundle_dir)

    with patch(
        "oyster_agent_runner.trajectory_packager.subprocess.run",
        side_effect=FileNotFoundError("git"),
    ):
        provenance = packager.compute_provenance()
    assert provenance["runtime"]["git_sha"] == "unknown"


# --- Checksums -------------------------------------------------------------


def test_checksums_cover_all_three_streams_plus_manifest(bundle_dir: Path) -> None:
    packager = TrajectoryPackager(bundle_dir)
    digests = packager.compute_checksums()
    assert set(digests) == {
        COT_FILENAME,
        METADATA_FILENAME,
        INPUTS_FILENAME,
        MANIFEST_FILENAME,
    }
    # Every digest is a 64-char lowercase hex string.
    for name, digest in digests.items():
        assert len(digest) == 64, name
        assert all(c in "0123456789abcdef" for c in digest), name


def test_checksums_are_stable_across_repeated_runs(bundle_dir: Path) -> None:
    """Same input bytes → same digests every time."""
    packager = TrajectoryPackager(bundle_dir)
    first = packager.compute_checksums()
    second = packager.compute_checksums()
    third = TrajectoryPackager(bundle_dir).compute_checksums()
    assert first == second == third


def test_checksums_change_when_stream_changes(bundle_dir: Path) -> None:
    packager = TrajectoryPackager(bundle_dir)
    before = packager.compute_checksums()
    # Mutate the cot stream.
    cot_path = bundle_dir / COT_FILENAME
    cot_path.write_bytes(cot_path.read_bytes() + b'{"event_type":"X"}\n')
    after = TrajectoryPackager(bundle_dir).compute_checksums()
    assert before[COT_FILENAME] != after[COT_FILENAME]
    # Other streams are untouched.
    assert before[METADATA_FILENAME] == after[METADATA_FILENAME]
    assert before[INPUTS_FILENAME] == after[INPUTS_FILENAME]


# --- Manifest extension ----------------------------------------------------


def test_packaged_manifest_preserves_all_original_fields(bundle_dir: Path) -> None:
    """Extending must NOT drop fields, NOT rename them, NOT reorder."""
    packager = TrajectoryPackager(bundle_dir)
    output = bundle_dir.parent / "out.zip"
    packager.package(output)

    with zipfile.ZipFile(output, "r") as zf:
        original = json.loads(zf.read(MANIFEST_FILENAME).decode("utf-8"))
        packaged = json.loads(zf.read(PACKAGED_MANIFEST_FILENAME).decode("utf-8"))

    # Every original key must appear in the packaged variant with the
    # same value.
    for key, value in original.items():
        assert packaged[key] == value, key
    # The extension is exactly one new key: 'checksums'.
    new_keys = set(packaged) - set(original)
    assert new_keys == {"checksums"}
    assert packaged["checksums"]["algorithm"] == "sha256"
    assert MANIFEST_FILENAME in packaged["checksums"]["sha256"]


# --- Zip round-trip --------------------------------------------------------


def test_package_emits_zip_with_all_required_entries(bundle_dir: Path) -> None:
    output = bundle_dir.parent / "buyer.zip"
    packager = TrajectoryPackager(bundle_dir)
    result_path = packager.package(output)

    assert result_path == output
    assert output.exists()

    with zipfile.ZipFile(output, "r") as zf:
        names = set(zf.namelist())
    assert {
        COT_FILENAME,
        METADATA_FILENAME,
        INPUTS_FILENAME,
        MANIFEST_FILENAME,
        PROVENANCE_FILENAME,
        PACKAGED_MANIFEST_FILENAME,
    } == names


def test_package_does_not_mutate_source_bundle(bundle_dir: Path) -> None:
    """The source directory must remain pristine — we never write provenance into it."""
    before = sorted(p.name for p in bundle_dir.iterdir())
    packager = TrajectoryPackager(bundle_dir)
    packager.package(bundle_dir.parent / "buyer.zip")
    after = sorted(p.name for p in bundle_dir.iterdir())
    assert before == after


def test_package_rejects_non_zip_format(bundle_dir: Path) -> None:
    packager = TrajectoryPackager(bundle_dir)
    with pytest.raises(ValueError, match="unsupported format"):
        packager.package(bundle_dir.parent / "buyer.tar", format="tar.gz")


def test_verify_package_ok_on_clean_zip(bundle_dir: Path) -> None:
    output = bundle_dir.parent / "buyer.zip"
    packager = TrajectoryPackager(bundle_dir)
    packager.package(output)

    report = packager.verify_package(output)
    assert report.ok, f"unexpected issues: {report.issues}"
    assert report.step_count >= 1


def test_verify_package_detects_tampered_zip(bundle_dir: Path) -> None:
    """Modifying a stream inside the zip must fail checksum verification."""
    output = bundle_dir.parent / "buyer.zip"
    packager = TrajectoryPackager(bundle_dir)
    packager.package(output)

    # Rebuild the zip with cot.jsonl swapped for tampered content. We
    # cannot edit a single entry in-place with stdlib zipfile, so the
    # idiomatic move is "extract → mutate → repackage".
    workdir = bundle_dir.parent / "tamper_work"
    workdir.mkdir()
    with zipfile.ZipFile(output, "r") as zf:
        zf.extractall(workdir)

    (workdir / COT_FILENAME).write_text(
        '{"event_type":"INJECTED","timestamp":0.0,"event_args":{}}\n',
        encoding="utf-8",
    )

    tampered = bundle_dir.parent / "tampered.zip"
    with zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in workdir.iterdir():
            zf.write(member, member.name)

    report = packager.verify_package(tampered)
    assert not report.ok
    assert any("checksum mismatch" in issue for issue in report.issues), report.issues


def test_verify_package_missing_zip_returns_failure(bundle_dir: Path) -> None:
    packager = TrajectoryPackager(bundle_dir)
    report = packager.verify_package(bundle_dir.parent / "no_such.zip")
    assert not report.ok
    assert any("zip not found" in issue for issue in report.issues)


def test_verify_package_missing_required_entry(bundle_dir: Path) -> None:
    """A zip missing provenance.json must be flagged as a packaging error."""
    output = bundle_dir.parent / "buyer.zip"
    packager = TrajectoryPackager(bundle_dir)
    packager.package(output)

    # Re-zip without provenance.json.
    workdir = bundle_dir.parent / "strip_work"
    workdir.mkdir()
    with zipfile.ZipFile(output, "r") as zf:
        zf.extractall(workdir)
    (workdir / PROVENANCE_FILENAME).unlink()

    incomplete = bundle_dir.parent / "incomplete.zip"
    with zipfile.ZipFile(incomplete, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in workdir.iterdir():
            zf.write(member, member.name)

    report = packager.verify_package(incomplete)
    assert not report.ok
    assert any(PROVENANCE_FILENAME in issue for issue in report.issues)


# --- CLI integration -------------------------------------------------------


def test_cli_package_trajectory_smoke(bundle_dir: Path) -> None:
    runner = CliRunner()
    output = bundle_dir.parent / "cli_buyer.zip"
    result = runner.invoke(
        app,
        [
            "package-trajectory",
            "--bundle-dir",
            str(bundle_dir),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert output.exists()


def test_cli_package_trajectory_verify_flag(bundle_dir: Path) -> None:
    runner = CliRunner()
    output = bundle_dir.parent / "cli_verified.zip"
    result = runner.invoke(
        app,
        [
            "package-trajectory",
            "--bundle-dir",
            str(bundle_dir),
            "--output",
            str(output),
            "--verify",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Verification" in result.output


def test_cli_package_trajectory_missing_bundle(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "package-trajectory",
            "--bundle-dir",
            str(tmp_path / "nope"),
            "--output",
            str(tmp_path / "out.zip"),
        ],
    )
    assert result.exit_code == 2
    assert "cannot load bundle" in result.output
