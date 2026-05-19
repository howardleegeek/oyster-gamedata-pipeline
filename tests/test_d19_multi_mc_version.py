"""D19 multi-MC-version build matrix contract test.

Howard 2026-05-07: validates that the gradle config + GHA matrix + fabric.mod.json
template stay in sync. Without these checks, a future contributor could
add a new MC version to gradle.properties but forget to:
  - add it to the GHA matrix
  - update fabric.mod.json template variable
  - parameterize a hardcoded MC version somewhere

Reads files as text — no JVM, no gradle daemon needed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# gradle MC_MATRIX has 4 entries (architecture supports all), but only
# 1.21.4 is in the CI matrix because the other fabric-api versions need
# verification against maven.fabricmc.net before claiming they build.
# This list pins the CI-tested set — different from the gradle-supported
# set tested by test_build_gradle_declares_all_4_mc_versions.
EXPECTED_GRADLE_VERSIONS = ["1.20.1", "1.20.4", "1.21.1", "1.21.4"]
EXPECTED_CI_VERSIONS = ["1.20.1", "1.20.4", "1.21.1", "1.21.4"]
# Backwards compat alias for tests that don't care which list:
EXPECTED_MC_VERSIONS = EXPECTED_GRADLE_VERSIONS


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text()


def test_build_gradle_declares_all_4_mc_versions():
    """build.gradle's MC_MATRIX must list every supported MC version."""
    src = _read("mc-mod/build.gradle")
    for v in EXPECTED_MC_VERSIONS:
        assert f'"{v}"' in src, (
            f"mc-mod/build.gradle MC_MATRIX missing key {v!r} — " f"add a row for it"
        )


def test_build_gradle_reads_MC_VERSION_env():
    """build.gradle must look up MC_VERSION from env so the GHA matrix
    can pass per-cell config."""
    src = _read("mc-mod/build.gradle")
    assert (
        'System.getenv("MC_VERSION")' in src
    ), "build.gradle must read System.getenv('MC_VERSION') for D19 matrix"


def test_build_gradle_unknown_version_throws():
    """Unknown MC version must fail loudly, not silently use a default."""
    src = _read("mc-mod/build.gradle")
    assert (
        "throw new GradleException" in src
    ), "build.gradle must throw on unknown MC_VERSION (no silent fallback)"
    assert "Unknown MC_VERSION" in src


def test_build_gradle_jar_name_includes_mc_version():
    """Output jar name MUST carry -mcX.Y.Z so 4 matrix cells produce
    distinct artifacts (not overwrite each other)."""
    src = _read("mc-mod/build.gradle")
    assert "-mc${mcVersion}" in src, "version line must template -mc${mcVersion} into the jar name"


def test_fabric_mod_json_has_templated_minecraft_depends():
    """The fabric.mod.json minecraft dep MUST be templated, not hardcoded
    to one version, so each matrix cell produces a jar matching its MC."""
    src = _read("mc-mod/src/main/resources/fabric.mod.json")
    data = json.loads(src)
    mc_dep = data.get("depends", {}).get("minecraft", "")
    assert mc_dep == "${mc_depends}", (
        f"fabric.mod.json depends.minecraft must be '${{mc_depends}}' " f"template, got {mc_dep!r}"
    )


def test_build_gradle_expands_mc_depends_in_processResources():
    """processResources block must pass mc_depends through `expand` so
    the templated fabric.mod.json gets per-cell value."""
    src = _read("mc-mod/build.gradle")
    # Loose match — order/quoting may vary, but both keys must be present.
    assert "expand(" in src
    assert '"mc_depends"' in src or "'mc_depends'" in src


def test_gha_workflow_has_matrix_with_ci_versions():
    """GHA build-mc-mod.yml must declare the matrix strategy with the
    CI-verified subset (currently 1.21.4 only — other versions need
    fabric-api version verification before re-enabling)."""
    src = _read(".github/workflows/build-mc-mod.yml")
    assert "matrix:" in src
    assert "mc_version:" in src
    for v in EXPECTED_CI_VERSIONS:
        pattern = rf'-\s+"?{re.escape(v)}"?'
        assert re.search(pattern, src), f"GHA matrix missing CI-verified mc_version {v!r}"


def test_gha_workflow_passes_MC_VERSION_env():
    """Each matrix cell must export MC_VERSION so build.gradle picks
    the right MC_MATRIX row."""
    src = _read(".github/workflows/build-mc-mod.yml")
    assert "MC_VERSION:" in src
    assert "${{ matrix.mc_version }}" in src


def test_gha_workflow_uploads_per_version_artifact():
    """Each cell must upload a separately-named artifact so D17 .exe
    bundler can pick the right one."""
    src = _read(".github/workflows/build-mc-mod.yml")
    # Artifact name must include matrix.mc_version
    assert "oyster-recorder-mod-mc${{ matrix.mc_version }}" in src


def test_gha_workflow_has_failfast_false():
    """One MC version's build failure (e.g. fabric-api yank) shouldn't
    kill the others — fail-fast must be false."""
    src = _read(".github/workflows/build-mc-mod.yml")
    assert "fail-fast: false" in src


def test_gha_workflow_has_timeout():
    """Per the cascade lesson — every workflow needs timeout-minutes."""
    src = _read(".github/workflows/build-mc-mod.yml")
    assert "timeout-minutes:" in src


def test_gradle_matrix_versions_match_test_constant():
    """build.gradle MC_MATRIX keys MUST match EXPECTED_GRADLE_VERSIONS.
    Drift means a contributor added a row in gradle but not in this
    test (or vice versa)."""
    gradle_src = _read("mc-mod/build.gradle")
    for v in EXPECTED_GRADLE_VERSIONS:
        assert f'"{v}"' in gradle_src, f"build.gradle MC_MATRIX missing {v}"


def test_ci_matrix_subset_of_gradle_matrix():
    """Every version in the CI matrix MUST be in the gradle matrix —
    otherwise CI tries to build against an unconfigured version."""
    yaml_src = _read(".github/workflows/build-mc-mod.yml")
    for v in EXPECTED_CI_VERSIONS:
        assert v in EXPECTED_GRADLE_VERSIONS, (
            f"CI version {v} not declared in gradle matrix — add to "
            f"build.gradle MC_MATRIX first"
        )
        assert v in yaml_src, f"GHA workflow missing CI version {v}"
