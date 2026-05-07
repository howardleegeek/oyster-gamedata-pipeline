"""D17 tests — bin/install_fabric_loader.py auto-installer.

Covers cross-platform path detection, idempotency, mod-jar drop with
clobber-only-Oyster semantics, and fail-soft return values.

Howard 2026-05-07: testing without a real Minecraft install. Uses
MINECRAFT_DIR_OVERRIDE env var so tests work on any CI runner.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "bin" / "install_fabric_loader.py"

_spec = importlib.util.spec_from_file_location("install_fabric_loader", SRC)
ifl = importlib.util.module_from_spec(_spec)
sys.modules["install_fabric_loader"] = ifl
_spec.loader.exec_module(ifl)


def _make_fake_mc_dir(tmp_path: Path,
                      fabric_installed: bool = False,
                      existing_mod_versions: list[str] | None = None) -> Path:
    """Create a fake .minecraft directory tree for testing."""
    mc = tmp_path / "minecraft"
    mc.mkdir()
    (mc / "versions").mkdir()
    (mc / "mods").mkdir()
    if fabric_installed:
        ver = ifl.MC_VERSION
        loader = ifl.FABRIC_LOADER_VERSION
        prof = mc / "versions" / f"fabric-loader-{loader}-{ver}"
        prof.mkdir()
        (prof / f"fabric-loader-{loader}-{ver}.json").write_text("{}")
    for v in (existing_mod_versions or []):
        (mc / "mods" / f"oyster-recorder-mod-{v}.jar").write_text("fake jar v" + v)
    return mc


def _make_fake_source_jar(tmp_path: Path, version: str = "0.1.0") -> Path:
    p = tmp_path / f"oyster-recorder-mod-{version}.jar"
    p.write_text(f"jar bytes v{version}")
    return p


def test_detect_minecraft_dir_via_override(tmp_path, monkeypatch):
    monkeypatch.setenv("MINECRAFT_DIR_OVERRIDE", str(tmp_path))
    assert ifl.detect_minecraft_dir() == tmp_path


def test_detect_minecraft_dir_returns_None_if_override_missing(monkeypatch):
    monkeypatch.setenv("MINECRAFT_DIR_OVERRIDE", "/this/path/does/not/exist/__never__")
    # No real .minecraft on the test runner, so this should return None
    # (we set override to a non-existent dir; default detection will also
    # most likely be None unless the runner happens to have MC installed)
    assert ifl.detect_minecraft_dir() is None


def test_fabric_profile_present_finds_exact_match(tmp_path):
    mc = _make_fake_mc_dir(tmp_path, fabric_installed=True)
    profile = ifl.fabric_profile_present(mc)
    assert profile is not None
    assert profile.name.startswith("fabric-loader-")
    assert profile.name.endswith(f"-{ifl.MC_VERSION}")


def test_fabric_profile_present_returns_None_when_absent(tmp_path):
    mc = _make_fake_mc_dir(tmp_path, fabric_installed=False)
    assert ifl.fabric_profile_present(mc) is None


def test_drop_mod_jar_creates_dest(tmp_path):
    mc = _make_fake_mc_dir(tmp_path)
    src = _make_fake_source_jar(tmp_path, "0.1.0")
    ok, dest, msg = ifl.drop_mod_jar(mc, src)
    assert ok, msg
    assert dest is not None
    assert dest.exists()
    assert dest.read_text() == "jar bytes v0.1.0"


def test_drop_mod_jar_idempotent_same_size(tmp_path):
    mc = _make_fake_mc_dir(tmp_path)
    src = _make_fake_source_jar(tmp_path, "0.1.0")
    ifl.drop_mod_jar(mc, src)  # first install
    # second call should be no-op (same size)
    ok, dest, msg = ifl.drop_mod_jar(mc, src)
    assert ok
    assert "already present" in msg.lower()


def test_drop_mod_jar_clobbers_only_old_oyster_jars(tmp_path):
    """Critical: must remove old oyster-recorder-mod-*.jar but preserve
    other mods (like Sodium, Iris, etc.) the user has installed."""
    mc = _make_fake_mc_dir(tmp_path,
                            existing_mod_versions=["0.0.1-old", "0.0.2-older"])
    other_mod = mc / "mods" / "sodium-0.6.0.jar"
    other_mod.write_text("sodium")
    iris_mod = mc / "mods" / "iris-mc1.21.4-1.7.6.jar"
    iris_mod.write_text("iris")

    src = _make_fake_source_jar(tmp_path, "0.1.0")
    ok, dest, msg = ifl.drop_mod_jar(mc, src)

    assert ok
    # Old oyster jars removed
    assert not (mc / "mods" / "oyster-recorder-mod-0.0.1-old.jar").exists()
    assert not (mc / "mods" / "oyster-recorder-mod-0.0.2-older.jar").exists()
    # Other mods preserved
    assert other_mod.exists() and other_mod.read_text() == "sodium"
    assert iris_mod.exists() and iris_mod.read_text() == "iris"
    # New jar in place
    assert dest.exists()


def test_drop_mod_jar_returns_error_when_source_missing(tmp_path):
    mc = _make_fake_mc_dir(tmp_path)
    src = tmp_path / "doesnt_exist.jar"
    ok, dest, msg = ifl.drop_mod_jar(mc, src)
    assert not ok
    assert dest is None
    assert "missing" in msg.lower()


def test_ensure_installed_no_mc_returns_failsoft(tmp_path):
    """When MC dir doesn't exist, ensure_installed must return
    installed=False with a clear reason — never raise."""
    fab = tmp_path / "fab.jar"
    mod = tmp_path / "mod.jar"
    result = ifl.ensure_installed(fab, mod, mc_dir=None)
    # detect_minecraft_dir likely returns None on a CI runner
    if result.mc_dir is None:
        assert not result.installed
        assert "not found" in result.reason.lower()


def test_ensure_installed_idempotent_when_fabric_already_present(tmp_path):
    """If Fabric is pre-installed, skip the installer step entirely."""
    mc = _make_fake_mc_dir(tmp_path, fabric_installed=True)
    fab = tmp_path / "fab.jar"
    fab.write_text("(would crash if invoked)")
    src = _make_fake_source_jar(tmp_path, "0.1.0")
    result = ifl.ensure_installed(fab, src, mc_dir=mc)
    assert result.installed
    assert result.fabric_profile_dir is not None
    assert result.mod_path is not None
    assert result.mod_path.exists()


def test_install_result_to_dict_serialisable(tmp_path):
    """Returned result must JSON-serialise so callers can log it."""
    mc = _make_fake_mc_dir(tmp_path, fabric_installed=True)
    fab = tmp_path / "fab.jar"
    fab.write_text("x")
    src = _make_fake_source_jar(tmp_path, "0.1.0")
    result = ifl.ensure_installed(fab, src, mc_dir=mc)
    d = result.to_dict()
    s = json.dumps(d)
    assert "installed" in d
    assert s  # round-trippable


def test_main_cli_returns_zero_on_install_success(tmp_path, monkeypatch, capsys):
    mc = _make_fake_mc_dir(tmp_path, fabric_installed=True)
    monkeypatch.setenv("MINECRAFT_DIR_OVERRIDE", str(mc))
    fab = tmp_path / "fab.jar"; fab.write_text("x")
    src = _make_fake_source_jar(tmp_path, "0.1.0")
    rc = ifl.main([str(fab), str(src)])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["installed"] is True
    assert rc == 0
