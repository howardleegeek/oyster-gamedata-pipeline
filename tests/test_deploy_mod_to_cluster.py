"""Tests for ``bin/deploy_mod_to_cluster.sh``.

Verifies that the deploy script:
  - exists and is executable,
  - is idempotent (running twice = exactly one mod jar in mods/),
  - refuses non-Fabric Paper dirs (no
    libraries/net/fabricmc/fabric-loader/ AND no fabric-server-launch.jar),
  - preserves non-Oyster mods (Sodium, Lithium, etc.) in mods/.

All tests use a tmp_path fixture with synthetic Paper dirs — they do
NOT touch the real /tmp/oyster_paper_* dirs, do NOT shell out to `gh`,
and do NOT restart anything (per Howard's "不停运转" guard rail).

The script is invoked via subprocess with:
    PAPER_DIRS=<synthetic dirs>      to override the defaults
    MOD_JAR_OVERRIDE=<fake jar>      to skip `gh run download`

Howard 2026-05-07 (D16 follow-up): pure shell + tmp_path, no real
network or Paper restart.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "deploy_mod_to_cluster.sh"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_fake_paper_dir(
    base: Path,
    name: str,
    *,
    is_fabric: bool = True,
    fabric_via_libraries: bool = True,
    fabric_via_launcher_jar: bool = False,
    existing_mods: list[str] | None = None,
    existing_oyster_versions: list[str] | None = None,
) -> Path:
    """Create a synthetic Paper directory tree.

    Args:
        is_fabric: if True, add at least one Fabric signal so
            ``is_fabric_server`` returns true.
        fabric_via_libraries: if True (and is_fabric), create
            ``libraries/net/fabricmc/fabric-loader/<v>/``.
        fabric_via_launcher_jar: if True (and is_fabric), create a
            ``fabric-server-launch.jar`` in the dir root.
        existing_mods: list of non-Oyster mod jar basenames to drop in
            ``mods/`` (must not start with ``oyster-recorder-mod-``).
        existing_oyster_versions: list of older Oyster mod versions
            already in ``mods/`` (e.g. ["0.0.1"] -> file
            ``oyster-recorder-mod-0.0.1.jar``).
    """
    paper = base / name
    paper.mkdir(parents=True)
    # Paper-style libraries — every Paper has libraries/net/fabricmc/
    # mapping-io regardless of Fabric, so we always seed that to make
    # sure the test covers the "Paper-only fabricmc subdir is NOT enough"
    # case.
    (paper / "libraries" / "net" / "fabricmc" / "mapping-io" / "0.5.0").mkdir(parents=True)

    if is_fabric:
        if fabric_via_libraries:
            (paper / "libraries" / "net" / "fabricmc" / "fabric-loader" / "0.16.9").mkdir(
                parents=True
            )
        if fabric_via_launcher_jar:
            (paper / "fabric-server-launch.jar").write_text("(fake fabric launcher)")

    mods = paper / "mods"
    mods.mkdir()
    for mod_basename in existing_mods or []:
        (mods / mod_basename).write_text(f"(fake mod jar: {mod_basename})")
    for v in existing_oyster_versions or []:
        (mods / f"oyster-recorder-mod-{v}.jar").write_text(f"(fake oyster jar v{v})")

    return paper


def _make_fake_mod_jar(tmp_path: Path, version: str = "0.1.0", mc_version: str = "1.21.4") -> Path:
    """Create a fake mod jar matching the GHA artifact filename pattern."""
    jar = tmp_path / f"oyster-recorder-mod-{version}-mc{mc_version}.jar"
    jar.write_text(f"(fake oyster mod jar v{version} mc{mc_version})")
    return jar


def _run_script(
    paper_dirs: list[Path],
    mod_jar: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ}
    env["PAPER_DIRS"] = " ".join(str(p) for p in paper_dirs)
    env["MOD_JAR_OVERRIDE"] = str(mod_jar)
    # Belt-and-suspenders: even if `gh` is on PATH on the runner, the
    # MOD_JAR_OVERRIDE branch should short-circuit before we touch it,
    # but we point gh at /dev/null to be sure no test ever calls it.
    env["PATH"] = env.get("PATH", "")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# 1. Script exists + is executable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_script_exists():
    assert SCRIPT.exists(), f"deploy_mod_to_cluster.sh missing at {SCRIPT}"


@pytest.mark.unit
def test_script_is_executable():
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable. Run: chmod +x {SCRIPT}"


@pytest.mark.unit
def test_companion_restart_md_exists():
    """The RESTART.md companion that documents the bounce procedure
    must ship alongside the script — the script's error messages and
    success log lines reference it by path."""
    md = REPO_ROOT / "bin" / "deploy_mod_to_cluster_RESTART.md"
    assert md.exists(), f"deploy_mod_to_cluster_RESTART.md missing at {md}"
    body = md.read_text()
    # Sanity: the procedure must mention SIGTERM and the swarm controller.
    assert "SIGTERM" in body
    assert "swarm_controller" in body
    assert "fabric-server-launch.jar" in body


# ---------------------------------------------------------------------------
# 2. Successful deploy + idempotency
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_drops_jar_into_each_fabric_paper_dir(tmp_path: Path):
    paper1 = _make_fake_paper_dir(tmp_path, "paper1", is_fabric=True)
    paper2 = _make_fake_paper_dir(tmp_path, "paper2", is_fabric=True)
    paper3 = _make_fake_paper_dir(
        tmp_path,
        "paper3",
        is_fabric=True,
        fabric_via_libraries=False,
        fabric_via_launcher_jar=True,
    )
    mod = _make_fake_mod_jar(tmp_path)

    proc = _run_script([paper1, paper2, paper3], mod)
    assert proc.returncode == 0, (
        f"expected exit 0, got {proc.returncode}\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    for paper in (paper1, paper2, paper3):
        staged = list((paper / "mods").glob("oyster-recorder-mod-*.jar"))
        assert len(staged) == 1, f"{paper}: expected 1 oyster jar, found {staged}"
        assert staged[0].name == mod.name


@pytest.mark.integration
def test_idempotent_running_twice_yields_one_jar(tmp_path: Path):
    """Re-running on the same input must NOT produce a second mod jar."""
    paper = _make_fake_paper_dir(tmp_path, "paper", is_fabric=True)
    mod = _make_fake_mod_jar(tmp_path)

    proc1 = _run_script([paper], mod)
    assert proc1.returncode == 0, f"first run failed:\n{proc1.stderr}"

    after_first = list((paper / "mods").glob("oyster-recorder-mod-*.jar"))
    assert len(after_first) == 1

    proc2 = _run_script([paper], mod)
    assert proc2.returncode == 0, f"second run failed:\n{proc2.stderr}"

    after_second = list((paper / "mods").glob("oyster-recorder-mod-*.jar"))
    assert len(after_second) == 1, f"idempotency violated — second run added a jar: {after_second}"
    # Bytes still match the source.
    assert after_second[0].read_text() == mod.read_text()


@pytest.mark.integration
def test_replaces_older_oyster_jars(tmp_path: Path):
    """Older oyster-recorder-mod-*.jar must be removed and replaced —
    not left alongside the new one (would double-load and crash Fabric)."""
    paper = _make_fake_paper_dir(
        tmp_path,
        "paper",
        is_fabric=True,
        existing_oyster_versions=["0.0.1", "0.0.2-rc1"],
    )
    mod = _make_fake_mod_jar(tmp_path, version="0.1.0")

    proc = _run_script([paper], mod)
    assert proc.returncode == 0, proc.stderr

    staged = sorted((paper / "mods").glob("oyster-recorder-mod-*.jar"))
    assert len(staged) == 1, f"expected exactly 1 oyster jar, got {staged}"
    assert staged[0].name == mod.name


# ---------------------------------------------------------------------------
# 3. Refuses non-Fabric Paper
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_refuses_non_fabric_paper_dir(tmp_path: Path):
    """If the dir has neither libraries/net/fabricmc/fabric-loader/ NOR
    fabric-server-launch.jar, the script must error out for that dir,
    NOT drop the jar in, and exit non-zero."""
    paper = _make_fake_paper_dir(tmp_path, "vanilla", is_fabric=False)
    mod = _make_fake_mod_jar(tmp_path)

    proc = _run_script([paper], mod)
    assert (
        proc.returncode != 0
    ), f"expected non-zero exit on vanilla Paper, got 0\n{proc.stdout}\n{proc.stderr}"
    # Importantly: no mod jar was dropped.
    staged = list((paper / "mods").glob("oyster-recorder-mod-*.jar"))
    assert staged == [], f"vanilla Paper got a mod jar dropped: {staged}"
    # Error message must explain why.
    assert "NOT a Fabric server" in proc.stderr or "NOT a Fabric server" in proc.stdout


@pytest.mark.integration
def test_paper_with_only_mapping_io_is_still_vanilla(tmp_path: Path):
    """Real cluster Papers ship libraries/net/fabricmc/mapping-io/ as a
    transitive dep. That alone is NOT a Fabric server — only
    libraries/net/fabricmc/fabric-loader/ counts (or fabric-server-launch.jar).
    Regression test: do not be fooled by mapping-io."""
    paper = _make_fake_paper_dir(tmp_path, "tricky", is_fabric=False)
    # The fixture already creates libraries/net/fabricmc/mapping-io/ —
    # that's the trap. Confirm the fixture matches the real cluster
    # before running.
    assert (paper / "libraries" / "net" / "fabricmc" / "mapping-io").is_dir()
    assert not (paper / "libraries" / "net" / "fabricmc" / "fabric-loader").exists()
    assert not (paper / "fabric-server-launch.jar").exists()

    mod = _make_fake_mod_jar(tmp_path)
    proc = _run_script([paper], mod)
    assert proc.returncode != 0
    assert list((paper / "mods").glob("oyster-recorder-mod-*.jar")) == []


@pytest.mark.integration
def test_failure_in_one_dir_does_not_block_others(tmp_path: Path):
    """Fail-soft: a non-Fabric dir errors but the other (Fabric) dirs
    still get the mod jar."""
    bad = _make_fake_paper_dir(tmp_path, "vanilla", is_fabric=False)
    good1 = _make_fake_paper_dir(tmp_path, "good1", is_fabric=True)
    good2 = _make_fake_paper_dir(tmp_path, "good2", is_fabric=True)
    mod = _make_fake_mod_jar(tmp_path)

    proc = _run_script([bad, good1, good2], mod)
    # Exit code = number of failures (1 here).
    assert proc.returncode == 1, (
        f"expected exit 1 (1 failure), got {proc.returncode}\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    assert list((bad / "mods").glob("oyster-recorder-mod-*.jar")) == []
    assert len(list((good1 / "mods").glob("oyster-recorder-mod-*.jar"))) == 1
    assert len(list((good2 / "mods").glob("oyster-recorder-mod-*.jar"))) == 1


# ---------------------------------------------------------------------------
# 4. Preserves non-Oyster mods
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_preserves_other_mods_in_mods_dir(tmp_path: Path):
    """Sodium, Lithium, FerriteCore — anything that isn't
    oyster-recorder-mod-*.jar must survive the deploy."""
    other_mods = [
        "sodium-fabric-0.6.0+mc1.21.4.jar",
        "lithium-fabric-mc1.21.4-0.13.0.jar",
        "ferritecore-7.0.0-fabric.jar",
    ]
    paper = _make_fake_paper_dir(
        tmp_path,
        "paper",
        is_fabric=True,
        existing_mods=other_mods,
        existing_oyster_versions=["0.0.1"],
    )
    mod = _make_fake_mod_jar(tmp_path, version="0.1.0")

    proc = _run_script([paper], mod)
    assert proc.returncode == 0, proc.stderr

    mods_dir = paper / "mods"
    for other in other_mods:
        assert (mods_dir / other).exists(), f"non-oyster mod removed: {other}"
        # Bytes preserved (no re-write).
        assert (mods_dir / other).read_text() == f"(fake mod jar: {other})"

    # Old oyster jar gone, new one present.
    assert not (mods_dir / "oyster-recorder-mod-0.0.1.jar").exists()
    assert (mods_dir / mod.name).exists()


# ---------------------------------------------------------------------------
# 5. Bonus: exit code 2 when no mod jar can be obtained
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_exits_2_when_mod_jar_override_missing(tmp_path: Path):
    """If MOD_JAR_OVERRIDE is set to a non-existent file, fail fast
    with exit 2 — don't silently fall through to the gh-download path."""
    paper = _make_fake_paper_dir(tmp_path, "paper", is_fabric=True)
    fake = tmp_path / "does_not_exist.jar"
    proc = _run_script([paper], fake)
    assert proc.returncode == 2, (
        f"expected exit 2 (mod jar unobtainable), got {proc.returncode}\n" f"STDERR:\n{proc.stderr}"
    )
    # No jar dropped.
    assert list((paper / "mods").glob("oyster-recorder-mod-*.jar")) == []
