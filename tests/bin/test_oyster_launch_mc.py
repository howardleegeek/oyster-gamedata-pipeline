#!/usr/bin/env python3
"""Tests for ``bin/oyster_launch_mc.py`` (R05C).

Focus: the 5 bug-fixes baked in must not regress. Each test maps to a
numbered bug from the spec.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bin"))
import oyster_launch_mc as m

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_install(tmp_path: Path) -> Path:
    """Build a minimal install layout: jre/bin/javaw + Fabric profile +
    vanilla parent. Returns the install root."""
    inst = tmp_path / "mc-instance"
    versions = inst / "versions"

    # Vanilla parent — has the WRONG mainClass; LEAF rule must reject it.
    mc_ver = m.MC_VERSION
    v_dir = versions / mc_ver
    v_dir.mkdir(parents=True)
    (v_dir / f"{mc_ver}.jar").write_bytes(b"fake-mc-jar")
    (v_dir / f"{mc_ver}.json").write_text(
        json.dumps(
            {
                "id": mc_ver,
                "type": "release",
                "mainClass": "net.minecraft.client.main.Main",
                "assetIndex": {"id": "19"},
                "libraries": [
                    {"name": "com.mojang:logging:1.2.7"},
                    {"name": "org.lwjgl:lwjgl:3.3.3"},
                ],
                "arguments": {
                    "jvm": ["-Dvanillaonly=1"],
                    "game": ["--demo"],
                },
            }
        )
    )

    # Fabric leaf
    fab_name = m.FABRIC_PROFILE_NAME
    fab_dir = versions / fab_name
    fab_dir.mkdir(parents=True)
    (fab_dir / f"{fab_name}.json").write_text(
        json.dumps(
            {
                "id": fab_name,
                "inheritsFrom": mc_ver,
                "type": "release",
                "mainClass": m.EXPECTED_FABRIC_MAIN,
                "libraries": [
                    {"name": "net.fabricmc:fabric-loader:0.16.10"},
                    {"name": "net.fabricmc:intermediary:1.21.4"},
                ],
                "arguments": {
                    "jvm": [
                        "-DFabricMcEmu=net.minecraft.client.main.Main",
                        "-Dlog4j2.formatMsgNoLookups=true",
                    ],
                    "game": [],
                },
            }
        )
    )

    # JRE — file just has to exist
    java_bin = "javaw.exe" if os.name == "nt" else "java"
    (tmp_path / "jre" / "bin").mkdir(parents=True)
    (tmp_path / "jre" / "bin" / java_bin).write_text("#!/bin/sh\necho fake")

    return tmp_path


# ---------------------------------------------------------------------------
# Bug-fix #1: LEAF-only mainClass + arguments.jvm
# ---------------------------------------------------------------------------


def test_resolve_profile_chain_leaf_main(fake_install: Path) -> None:
    """LEAF mainClass MUST win over vanilla parent."""
    leaf = m.fabric_profile_json(fake_install)
    resolved = m.resolve_profile_chain(leaf)
    assert resolved.main_class == m.EXPECTED_FABRIC_MAIN, (
        "vanilla parent's net.minecraft.client.main.Main is "
        "wrongly overwriting Fabric's KnotClient"
    )


def test_resolve_profile_chain_leaf_jvm_args(fake_install: Path) -> None:
    """JVM args come ONLY from the leaf — vanilla's must be absent."""
    leaf = m.fabric_profile_json(fake_install)
    resolved = m.resolve_profile_chain(leaf)
    assert "-DFabricMcEmu=net.minecraft.client.main.Main" in resolved.jvm_args
    assert "-Dvanillaonly=1" not in resolved.jvm_args, "vanilla parent JVM args wrongly merged"


def test_resolve_profile_chain_libraries_accumulate(fake_install: Path) -> None:
    """libraries DO walk the chain (leaf + parent)."""
    leaf = m.fabric_profile_json(fake_install)
    resolved = m.resolve_profile_chain(leaf)
    names = [lib["name"] for lib in resolved.libraries]
    assert "net.fabricmc:fabric-loader:0.16.10" in names  # leaf
    assert "com.mojang:logging:1.2.7" in names  # vanilla parent


def test_resolve_profile_chain_asset_index(fake_install: Path) -> None:
    leaf = m.fabric_profile_json(fake_install)
    resolved = m.resolve_profile_chain(leaf)
    assert resolved.asset_index == "19"


def test_resolve_profile_chain_no_mainclass_fails(tmp_path: Path) -> None:
    """A leaf with no mainClass must error out — refusing to inherit it."""
    leaf_dir = tmp_path / "versions" / "leaf"
    leaf_dir.mkdir(parents=True)
    leaf_path = leaf_dir / "leaf.json"
    leaf_path.write_text(json.dumps({"id": "leaf", "inheritsFrom": "parent"}))

    parent_dir = tmp_path / "versions" / "parent"
    parent_dir.mkdir(parents=True)
    (parent_dir / "parent.json").write_text(
        json.dumps(
            {
                "id": "parent",
                "mainClass": "net.minecraft.client.main.Main",
            }
        )
    )

    with pytest.raises(ValueError, match="no mainClass"):
        m.resolve_profile_chain(leaf_path)


def test_resolve_profile_chain_cycle_detected(tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    a = versions / "a"
    b = versions / "b"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "a.json").write_text(
        json.dumps(
            {
                "id": "a",
                "mainClass": "X",
                "inheritsFrom": "b",
            }
        )
    )
    (b / "b.json").write_text(
        json.dumps(
            {
                "id": "b",
                "mainClass": "Y",
                "inheritsFrom": "a",
            }
        )
    )
    with pytest.raises(ValueError, match="cycle"):
        m.resolve_profile_chain(a / "a.json")


def test_build_launch_plan_emits_knotclient(fake_install: Path) -> None:
    """End-to-end: the constructed cmd has KnotClient + -DFabricMcEmu."""
    plan = m.build_launch_plan(install_root_path=fake_install)
    assert plan.main_class == m.EXPECTED_FABRIC_MAIN
    assert plan.pause_on_lost_focus_disabled is True
    cmd = list(plan.cmd)
    # KnotClient appears as a positional arg
    assert m.EXPECTED_FABRIC_MAIN in cmd
    # FabricMcEmu is in JVM args (somewhere before the -cp / mainClass)
    fabric_emu = [a for a in cmd if a.startswith("-DFabricMcEmu=")]
    assert fabric_emu, f"missing -DFabricMcEmu in cmd: {cmd}"


def test_build_launch_plan_forces_pause_on_lost_focus_false(fake_install: Path) -> None:
    options = m.mc_instance_dir(fake_install) / "options.txt"
    options.write_text(
        "pauseOnLostFocus:true\nrenderDistance:12\n",
        encoding="utf-8",
    )

    m.build_launch_plan(install_root_path=fake_install)
    m.build_launch_plan(install_root_path=fake_install)

    text = options.read_text(encoding="utf-8")
    assert "pauseOnLostFocus:false\n" in text
    assert "renderDistance:12\n" in text
    assert text.count("pauseOnLostFocus:false") == 1


def test_build_launch_plan_creates_missing_options_txt(fake_install: Path) -> None:
    options = m.mc_instance_dir(fake_install) / "options.txt"
    assert not options.exists()

    m.build_launch_plan(install_root_path=fake_install)

    assert options.read_text(encoding="utf-8") == "pauseOnLostFocus:false\n"


# ---------------------------------------------------------------------------
# Bug-fix #2: registry / SHGetKnownFolderPath Desktop resolution
# ---------------------------------------------------------------------------


def test_get_desktop_path_returns_path() -> None:
    """The function must return a Path, even on non-Windows."""
    result = m.get_desktop_path()
    assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# Bug-fix #3: stderr surfacing helpers (file tail)
# ---------------------------------------------------------------------------


def test_tail_file_returns_last_n_lines(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("\n".join(f"line{i}" for i in range(200)) + "\n")
    out = m._tail_file(log, n_lines=10)
    lines = out.splitlines()
    assert len(lines) == 10
    assert lines[-1] == "line199"
    assert lines[0] == "line190"


def test_tail_file_handles_missing(tmp_path: Path) -> None:
    out = m._tail_file(tmp_path / "nope.log", n_lines=10)
    assert "not found" in out


# ---------------------------------------------------------------------------
# Bug-fix #5: latest.log polling
# ---------------------------------------------------------------------------


def test_wait_for_mc_ready_succeeds_on_marker(tmp_path: Path) -> None:
    log = tmp_path / "latest.log"
    log.write_text("Loading Minecraft 1.21.4 with Fabric Loader 0.16.10\n")
    assert m.wait_for_mc_ready(log, timeout_sec=2.0) is True


def test_wait_for_mc_ready_times_out(tmp_path: Path) -> None:
    log = tmp_path / "latest.log"
    log.write_text("nothing here\n")
    assert m.wait_for_mc_ready(log, timeout_sec=0.5, poll_interval_sec=0.05) is False


def test_wait_for_mc_ready_finds_setting_user(tmp_path: Path) -> None:
    log = tmp_path / "latest.log"
    log.write_text("[12:34:56] [main/INFO]: Setting user: Howard\n")
    assert m.wait_for_mc_ready(log, timeout_sec=2.0) is True


# ---------------------------------------------------------------------------
# Verify install
# ---------------------------------------------------------------------------


def test_verify_install_ok(fake_install: Path) -> None:
    status = m.verify_install(fake_install)
    assert status.ok, f"unexpected missing: {status.missing}"


def test_verify_install_missing(tmp_path: Path) -> None:
    status = m.verify_install(tmp_path)
    assert not status.ok
    assert any("javaw" in p or "java" in p for p in status.missing)


# ---------------------------------------------------------------------------
# CLI dry-run integration
# ---------------------------------------------------------------------------


def test_cli_dry_run_prints_knotclient(fake_install: Path, capsys) -> None:
    rc = m.main(
        [
            "--install-root",
            str(fake_install),
            "--dry-run",
            "--username",
            "Howard",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "net.fabricmc.loader.impl.launch.knot.KnotClient" in out
    assert "-DFabricMcEmu=net.minecraft.client.main.Main" in out
    # Vanilla's wrong main class must NOT appear as the resolved main.
    assert "main_class    : net.minecraft.client.main.Main" not in out


# ---------------------------------------------------------------------------
# _show_messagebox — exercise the ctypes-failure fallback path
# ---------------------------------------------------------------------------


def test_show_messagebox_windows_fallback_on_ctypes_failure(
    monkeypatch, caplog, capsys
) -> None:
    """When ctypes/windll raises (e.g. WSL, missing user32), the function
    must fall through to the stderr path and surface the failure via
    logger.debug — never swallow it silently."""
    monkeypatch.setattr(m.os, "name", "nt")

    def _boom(*_a, **_kw):
        raise OSError("user32.dll not available")

    fake_ctypes = type("FakeCtypes", (), {"windll": type("WD", (), {"user32": type("U", (), {"MessageBoxW": staticmethod(_boom)})()})()})
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    with caplog.at_level("DEBUG", logger="oyster_launch_mc"):
        m._show_messagebox("TestTitle", "TestBody")

    err = capsys.readouterr().err
    assert "[TestTitle] TestBody" in err
    # The debug log must bind the exception text, not be silent.
    assert any(
        "MessageBoxW unavailable" in rec.message and "user32.dll not available" in rec.message
        for rec in caplog.records
    ), f"expected debug log binding the exception, got: {[r.message for r in caplog.records]}"


def test_show_messagebox_windows_success_returns_early(
    monkeypatch, caplog, capsys
) -> None:
    """When ctypes succeeds, the function must return early and skip the
    stderr fallback (so a real Windows MessageBoxW is not duplicated)."""
    monkeypatch.setattr(m.os, "name", "nt")

    calls = {"n": 0}

    def _ok(*_a, **_kw):
        calls["n"] += 1
        return 1  # IDOK

    fake_ctypes = type("FakeCtypes", (), {"windll": type("WD", (), {"user32": type("U", (), {"MessageBoxW": staticmethod(_ok)})()})()})
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    m._show_messagebox("TestTitle", "TestBody")

    assert calls["n"] == 1
    assert capsys.readouterr().err == ""
    # No debug log on success path
    assert not any(
        "MessageBoxW unavailable" in rec.message for rec in caplog.records
    )


def test_show_messagebox_posix_writes_stderr(monkeypatch, capsys) -> None:
    """On non-Windows, the function writes to stderr directly with no ctypes import."""
    monkeypatch.setattr(m.os, "name", "posix")

    m._show_messagebox("TestTitle", "TestBody")

    err = capsys.readouterr().err
    assert "[TestTitle] TestBody" in err

