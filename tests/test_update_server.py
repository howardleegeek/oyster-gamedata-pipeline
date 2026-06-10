#!/usr/bin/env python3
"""
Tests for G250 · bin/update_server_proxy.py

We exercise:
    - version parsing + ordering on rc-suffixed strings
    - cache hit / miss / TTL expiry
    - installer asset selection (priority order)
    - force-release detection
    - upstream-error propagation
    - CLI output shape

No real GitHub calls are made.  All HTTP is stubbed via the ``fetch=``
injection point on :func:`resolve_update`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Add the repo root to sys.path so `import bin.update_server_proxy` works
# regardless of where pytest is invoked from.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bin import update_server_proxy as usp  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sample_release(
    tag: str = "v0.28.0-rc19.0.1",
    body: str = "Routine maintenance.",
    asset_names: tuple[str, ...] = ("OysterRecorder-setup.exe",),
) -> dict[str, Any]:
    return {
        "tag_name": tag,
        "name": f"Recorder {tag}",
        "body": body,
        "assets": [
            {
                "name": n,
                "browser_download_url": f"https://example.test/dl/{n}",
            }
            for n in asset_names
        ],
    }


@pytest.fixture(autouse=True)
def _clear_cache():
    usp.reset_cache()
    yield
    usp.reset_cache()


@pytest.fixture
def fresh_cache():
    return usp.TTLCache(ttl_seconds=300)


# ---------------------------------------------------------------------------
# Version parsing
# ---------------------------------------------------------------------------


class TestParseVersion:
    def test_basic_semver(self):
        semver, _ = usp.parse_version("v1.2.3")
        assert semver == (1, 2, 3)

    def test_no_v_prefix_ok(self):
        semver, _ = usp.parse_version("1.2.3")
        assert semver == (1, 2, 3)

    def test_with_prerelease(self):
        _, pre = usp.parse_version("v0.28.0-rc19.0.1")
        # Should have at least one prerelease segment
        assert pre
        # The rc19 should be encoded with numeric extraction (1,"rc",19)
        assert any(
            isinstance(seg, tuple)
            and len(seg) == 3
            and seg[1] == "rc"
            and seg[2] == 19
            for seg in pre
        )

    def test_final_release_empty_pre(self):
        _, pre = usp.parse_version("v0.28.0")
        assert pre == ()

    def test_unparseable_raises(self):
        with pytest.raises(usp.InvalidVersionError):
            usp.parse_version("not-a-version")

    def test_none_raises(self):
        with pytest.raises(usp.InvalidVersionError):
            usp.parse_version(None)  # type: ignore[arg-type]


class TestIsNewer:
    def test_major_bump(self):
        assert usp.is_newer("v1.0.0", "v0.28.0-rc19.0.1") is True

    def test_minor_bump(self):
        assert usp.is_newer("v0.29.0-rc1", "v0.28.0-rc19.0.1") is True

    def test_rc_progression(self):
        assert (
            usp.is_newer("v0.28.0-rc20", "v0.28.0-rc19.0.1") is True
        ), "rc20 > rc19.x"
        assert (
            usp.is_newer("v0.28.0-rc19.0.2", "v0.28.0-rc19.0.1") is True
        )

    def test_rc100_vs_rc19_numeric_aware(self):
        assert usp.is_newer("v0.28.0-rc100", "v0.28.0-rc19") is True

    def test_no_pre_beats_pre_at_same_semver(self):
        assert usp.is_newer("v0.28.0", "v0.28.0-rc19.0.1") is True

    def test_same_version_not_newer(self):
        assert usp.is_newer("v0.28.0-rc19.0.1", "v0.28.0-rc19.0.1") is False

    def test_older_not_newer(self):
        assert usp.is_newer("v0.28.0-rc19.0.0", "v0.28.0-rc19.0.1") is False


# ---------------------------------------------------------------------------
# Installer selection
# ---------------------------------------------------------------------------


class TestInstallerSelection:
    def test_prefers_setup_exe(self):
        assets = [
            {"name": "Source.zip", "browser_download_url": "u1"},
            {"name": "Recorder.exe", "browser_download_url": "u2"},
            {"name": "OysterRecorder-setup.exe", "browser_download_url": "u3"},
            {"name": "Recorder.msi", "browser_download_url": "u4"},
        ]
        assert usp._select_installer_url(assets) == "u3"

    def test_falls_back_to_msi_when_no_setup_exe(self):
        assets = [
            {"name": "Recorder.exe", "browser_download_url": "u2"},
            {"name": "Recorder.msi", "browser_download_url": "u4"},
        ]
        # msi has higher priority than plain .exe
        assert usp._select_installer_url(assets) == "u4"

    def test_no_match_returns_none(self):
        assets = [{"name": "Source.zip", "browser_download_url": "u1"}]
        assert usp._select_installer_url(assets) is None

    def test_handles_non_dict_entries(self):
        assert usp._select_installer_url([None, "junk", 7]) is None  # type: ignore[list-item]

    def test_handles_empty_list(self):
        assert usp._select_installer_url([]) is None


# ---------------------------------------------------------------------------
# Force-release detection
# ---------------------------------------------------------------------------


class TestForceRelease:
    def test_force_token_at_line_start(self):
        body = "[FORCE] critical security fix\nMore notes here"
        assert usp._is_force_release(body) is True

    def test_force_token_case_insensitive(self):
        assert usp._is_force_release("[force] critical") is True

    def test_force_token_with_leading_whitespace(self):
        assert usp._is_force_release("   [FORCE] crit") is True

    def test_no_force_token(self):
        assert usp._is_force_release("Routine update.") is False

    def test_empty_body(self):
        assert usp._is_force_release("") is False
        assert usp._is_force_release(None) is False

    def test_force_word_in_middle_not_triggered(self):
        assert usp._is_force_release("Not a [FORCE] update") is False


# ---------------------------------------------------------------------------
# resolve_update — happy path
# ---------------------------------------------------------------------------


class TestResolveUpdate:
    def test_happy_path_update_available(self, fresh_cache):
        captured: dict[str, Any] = {}

        def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
            captured["url"] = url
            captured["headers"] = headers
            return _sample_release(
                tag="v0.28.0-rc19.0.2",
                body="Bug fixes.",
                asset_names=("OysterRecorder-setup.exe",),
            )

        info = usp.resolve_update(
            "v0.28.0-rc19.0.1",
            cache=fresh_cache,
            fetch=fake_fetch,
        )
        assert info["latest"] == "v0.28.0-rc19.0.2"
        assert info["installer_url"].endswith("OysterRecorder-setup.exe")
        assert info["release_notes"] == "Bug fixes."
        assert info["force"] is False
        assert info["update_available"] is True
        assert info["current"] == "v0.28.0-rc19.0.1"

    def test_no_update_available_when_caller_is_latest(self, fresh_cache):
        def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
            return _sample_release(tag="v0.28.0-rc19.0.1")

        info = usp.resolve_update(
            "v0.28.0-rc19.0.1", cache=fresh_cache, fetch=fake_fetch
        )
        assert info["update_available"] is False

    def test_force_release_propagates(self, fresh_cache):
        def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
            return _sample_release(
                tag="v0.28.0-rc20",
                body="[FORCE] critical fix — please upgrade immediately",
            )

        info = usp.resolve_update(
            "v0.28.0-rc19.0.1", cache=fresh_cache, fetch=fake_fetch
        )
        assert info["force"] is True
        assert info["update_available"] is True

    def test_invalid_current_rejected_before_network(self, fresh_cache):
        def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
            raise AssertionError("must not call upstream for invalid input")

        with pytest.raises(usp.InvalidVersionError):
            usp.resolve_update("garbage", cache=fresh_cache, fetch=fake_fetch)

    def test_token_is_sent_when_provided(self, fresh_cache):
        captured: dict[str, Any] = {}

        def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
            captured["headers"] = dict(headers)
            return _sample_release()

        usp.resolve_update(
            "v0.28.0-rc19.0.0",
            cache=fresh_cache,
            fetch=fake_fetch,
            token="gh_pat_test",
        )
        assert captured["headers"]["Authorization"] == "Bearer gh_pat_test"

    def test_unauthenticated_when_no_token(self, fresh_cache, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        captured: dict[str, Any] = {}

        def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
            captured["headers"] = dict(headers)
            return _sample_release()

        usp.resolve_update("v0.28.0-rc19.0.0", cache=fresh_cache, fetch=fake_fetch)
        assert "Authorization" not in captured["headers"]

    def test_env_token_picked_up(self, fresh_cache, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "from_env")
        captured: dict[str, Any] = {}

        def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
            captured["headers"] = dict(headers)
            return _sample_release()

        usp.resolve_update("v0.28.0-rc19.0.0", cache=fresh_cache, fetch=fake_fetch)
        assert captured["headers"]["Authorization"] == "Bearer from_env"

    def test_upstream_error_surfaces(self, fresh_cache):
        def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
            raise usp.UpstreamError("simulated 503")

        with pytest.raises(usp.UpstreamError):
            usp.resolve_update("v0.28.0-rc19.0.0", cache=fresh_cache, fetch=fake_fetch)

    def test_payload_missing_tag_raises(self, fresh_cache):
        def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
            return {"name": "no-tag"}

        with pytest.raises(usp.UpstreamError):
            usp.resolve_update("v0.28.0-rc19.0.0", cache=fresh_cache, fetch=fake_fetch)


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


class TestCache:
    def test_cache_hit_skips_fetch(self, fresh_cache):
        call_count = {"n": 0}

        def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
            call_count["n"] += 1
            return _sample_release()

        usp.resolve_update("v0.28.0-rc19.0.0", cache=fresh_cache, fetch=fake_fetch)
        usp.resolve_update("v0.28.0-rc19.0.0", cache=fresh_cache, fetch=fake_fetch)
        usp.resolve_update("v0.28.0-rc19.0.0", cache=fresh_cache, fetch=fake_fetch)
        assert call_count["n"] == 1

    def test_use_cache_false_forces_refetch(self, fresh_cache):
        call_count = {"n": 0}

        def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
            call_count["n"] += 1
            return _sample_release()

        usp.resolve_update("v0.28.0-rc19.0.0", cache=fresh_cache, fetch=fake_fetch)
        usp.resolve_update(
            "v0.28.0-rc19.0.0",
            cache=fresh_cache,
            fetch=fake_fetch,
            use_cache=False,
        )
        assert call_count["n"] == 2

    def test_cache_ttl_expiry(self):
        ttl_cache = usp.TTLCache(ttl_seconds=300)
        call_count = {"n": 0}

        def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
            call_count["n"] += 1
            return _sample_release()

        usp.resolve_update(
            "v0.28.0-rc19.0.0",
            cache=ttl_cache,
            fetch=fake_fetch,
            now=1_000_000.0,
        )
        assert call_count["n"] == 1

        # Just before TTL — still hit
        usp.resolve_update(
            "v0.28.0-rc19.0.0",
            cache=ttl_cache,
            fetch=fake_fetch,
            now=1_000_000.0 + 299,
        )
        assert call_count["n"] == 1

        # After TTL — refetch
        usp.resolve_update(
            "v0.28.0-rc19.0.0",
            cache=ttl_cache,
            fetch=fake_fetch,
            now=1_000_000.0 + 301,
        )
        assert call_count["n"] == 2

    def test_env_disable_cache_flag(self, monkeypatch, fresh_cache):
        monkeypatch.setenv("G250_DISABLE_CACHE", "1")
        call_count = {"n": 0}

        def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
            call_count["n"] += 1
            return _sample_release()

        usp.resolve_update("v0.28.0-rc19.0.0", cache=fresh_cache, fetch=fake_fetch)
        usp.resolve_update("v0.28.0-rc19.0.0", cache=fresh_cache, fetch=fake_fetch)
        assert call_count["n"] == 2

    def test_cache_independence_between_repos(self, fresh_cache):
        call_count = {"n": 0}

        def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
            call_count["n"] += 1
            return _sample_release(tag=f"v0.28.0-rc19.{call_count['n']}")

        usp.resolve_update(
            "v0.28.0-rc19.0.0",
            cache=fresh_cache,
            fetch=fake_fetch,
            repo="owner/one",
        )
        usp.resolve_update(
            "v0.28.0-rc19.0.0",
            cache=fresh_cache,
            fetch=fake_fetch,
            repo="owner/two",
        )
        assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


class TestCli:
    def test_cli_emits_json(self, capsys, monkeypatch):
        # Patch the default fetcher so we never hit the network.
        def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
            return _sample_release(tag="v0.28.0-rc20")

        monkeypatch.setattr(usp, "_default_github_fetch", fake_fetch)
        rc = usp.main(["--current", "v0.28.0-rc19.0.0", "--no-cache"])
        assert rc == 0
        out = capsys.readouterr().out
        info = json.loads(out)
        assert info["latest"] == "v0.28.0-rc20"
        assert info["update_available"] is True

    def test_cli_invalid_current_returns_2(self, capsys, monkeypatch):
        # Should not even attempt network.
        def must_not_call(url: str, headers: dict[str, str]) -> dict[str, Any]:
            raise AssertionError

        monkeypatch.setattr(usp, "_default_github_fetch", must_not_call)
        rc = usp.main(["--current", "bogus"])
        assert rc == 2

    def test_cli_upstream_error_returns_1(self, monkeypatch):
        def boom(url: str, headers: dict[str, str]) -> dict[str, Any]:
            raise usp.UpstreamError("simulated")

        monkeypatch.setattr(usp, "_default_github_fetch", boom)
        rc = usp.main(["--current", "v0.28.0-rc19.0.0", "--no-cache"])
        assert rc == 1
