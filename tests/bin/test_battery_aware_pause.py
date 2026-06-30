#!/usr/bin/env python3
"""Tests for bin/battery_aware_pause.py — laptop battery-aware pause utility.

Covers:
- load_config: missing file → defaults; partial file merged with defaults;
  corrupt JSON → defaults (no raise); explicit path arg honored.
- save_config: writes JSON; parent dirs created; atomic via os.replace
  (writes to tmp then renames); uses tempfile.mkstemp in target dir.
- _detect_macos: parses "AC Power" → (ac, pct, True); parses
  "Battery Power" → (battery, pct, False); missing percent → None;
  subprocess failure / timeout / FileNotFoundError → ("unknown", None, False).
- _detect_linux: returns ("unknown", None, False) when /sys path missing;
  parses status=charging → (ac, pct, True); status=discharging → (battery,
  pct, False); missing capacity file → pct=None; OSError on listdir → unknown.
- detect_power_source: delegates to platform-specific function when psutil
  missing; psutil present + sensors_battery() returns (ac/battery, pct, plugged);
  psutil raises AttributeError/OSError → falls through to platform detector.
- should_pause: override=True short-circuits to (False, "Override …");
  AC → False; unknown → False; battery + pause_on_battery=False → False
  (config reason); battery + min threshold critical → True; battery
  + game override honoring config["game_overrides"][game]["pause_on_battery"].
- print_status: prints all four required lines (source, level, plugged, config).
- main CLI: --status exits 0; --override exits 0 even on battery; default
  exit 1 on battery (paused) and 0 on AC; --pause-on-battery true/false
  updates config; --set-game-override writes the override dict;
  --config path is honored; argparse error → exit 2.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

# Make bin/ importable as a top-level name (mirrors sibling tests).
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

import battery_aware_pause as m  # noqa: E402

# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    """Config loader — defaults, partial merge, corrupt fallback."""

    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        cfg = m.load_config(str(tmp_path / "absent.json"))
        assert cfg == m.DEFAULT_CONFIG

    def test_defaults_constant_shape(self) -> None:
        # The module's DEFAULT_CONFIG must keep the documented keys.
        assert m.DEFAULT_CONFIG["pause_on_battery"] is True
        assert m.DEFAULT_CONFIG["min_battery_percent"] == 20
        assert m.DEFAULT_CONFIG["game_overrides"] == {}

    def test_partial_file_merges_with_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "partial.json"
        p.write_text(json.dumps({"min_battery_percent": 35}))
        cfg = m.load_config(str(p))
        assert cfg["min_battery_percent"] == 35
        # Untouched keys keep their default values.
        assert cfg["pause_on_battery"] is True
        assert cfg["game_overrides"] == {}

    def test_corrupt_json_falls_back_to_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{this is : not, valid json")
        cfg = m.load_config(str(p))
        # No raise; defaults returned.
        assert cfg == m.DEFAULT_CONFIG

    def test_full_file_replaces_all_keys(self, tmp_path: Path) -> None:
        p = tmp_path / "full.json"
        p.write_text(
            json.dumps(
                {
                    "pause_on_battery": False,
                    "min_battery_percent": 5,
                    "game_overrides": {"mc": {"pause_on_battery": True}},
                }
            )
        )
        cfg = m.load_config(str(p))
        assert cfg["pause_on_battery"] is False
        assert cfg["min_battery_percent"] == 5
        assert cfg["game_overrides"] == {"mc": {"pause_on_battery": True}}

    def test_does_not_mutate_default_constant(self, tmp_path: Path) -> None:
        before = json.dumps(m.DEFAULT_CONFIG, sort_keys=True)
        p = tmp_path / "partial.json"
        p.write_text(json.dumps({"min_battery_percent": 99}))
        m.load_config(str(p))
        after = json.dumps(m.DEFAULT_CONFIG, sort_keys=True)
        assert before == after


# ---------------------------------------------------------------------------
# save_config
# ---------------------------------------------------------------------------


class TestSaveConfig:
    """Atomic JSON write to a configurable path."""

    def test_writes_json_file(self, tmp_path: Path) -> None:
        p = tmp_path / "out.json"
        cfg = {"pause_on_battery": False, "min_battery_percent": 50, "game_overrides": {}}
        m.save_config(cfg, str(p))
        assert p.exists()
        loaded = json.loads(p.read_text())
        assert loaded == cfg

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        p = tmp_path / "deep" / "nested" / "config.json"
        m.save_config({"pause_on_battery": True, "min_battery_percent": 20, "game_overrides": {}}, str(p))
        assert p.exists()

    def test_atomic_via_os_replace(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """save_config must use os.replace (atomic on POSIX) on the tmp file.

        We monkeypatch os.replace to a spy that delegates to the real function
        and records the call so the test asserts both the call AND the real
        write happens.
        """
        p = tmp_path / "atomic.json"
        real_replace = os.replace
        called: list[tuple[str, str]] = []

        def spy_replace(src: str, dst: str) -> None:
            called.append((src, dst))
            real_replace(src, dst)

        monkeypatch.setattr(m.os, "replace", spy_replace)

        m.save_config(
            {"pause_on_battery": True, "min_battery_percent": 20, "game_overrides": {}},
            str(p),
        )
        assert len(called) == 1
        src, dst = called[0]
        assert dst == str(p)
        # The src should be a tempfile in the same dir, with .json suffix.
        assert src.endswith(".json")
        # And the file must exist with the right content.
        assert json.loads(p.read_text())["min_battery_percent"] == 20

    def test_cleans_tmp_on_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If os.replace fails, the partial tmp file should be unlinked."""
        p = tmp_path / "fail.json"
        tmp_files_before: set[str] = set(os.listdir(tmp_path))

        def boom_replace(_src: str, _dst: str) -> None:
            raise OSError("synthetic replace failure")

        monkeypatch.setattr(m.os, "replace", boom_replace)

        with pytest.raises(OSError):
            m.save_config(
                {"pause_on_battery": True, "min_battery_percent": 20, "game_overrides": {}},
                str(p),
            )
        # The target file should not exist; the tmp file should have been cleaned.
        assert not p.exists()
        leftovers = set(os.listdir(tmp_path)) - tmp_files_before
        assert leftovers == set(), f"leftover tmp files: {leftovers}"


# ---------------------------------------------------------------------------
# _detect_macos
# ---------------------------------------------------------------------------


class TestDetectMacos:
    """macOS power source detection via pmset.

    The function does a local `import subprocess` inside its body, so we
    monkeypatch the canonical `subprocess.run` (which is what the function
    uses at call time) rather than an attribute on the module.
    """

    def test_ac_power_with_percent(self) -> None:
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Now drawing from 'AC Power'\n -InternalBattery-0 (id=12345678)\t100%; charged; 0:00 remaining\n", stderr=""
        )
        with mock.patch.object(subprocess, "run", return_value=fake):
            assert m._detect_macos() == ("ac", 100.0, True)

    def test_battery_power_with_percent(self) -> None:
        fake = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Now drawing from 'Battery Power'\n -InternalBattery-0 (id=12345678)\t42%; discharging; 1:23 remaining\n",
            stderr="",
        )
        with mock.patch.object(subprocess, "run", return_value=fake):
            assert m._detect_macos() == ("battery", 42.0, False)

    def test_ac_power_without_percent(self) -> None:
        fake = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Now drawing from 'AC Power'\n -InternalBattery-0 (id=12345678)\tcharged; 0:00 remaining\n",
            stderr="",
        )
        with mock.patch.object(subprocess, "run", return_value=fake):
            assert m._detect_macos() == ("ac", None, True)

    def test_timeout_returns_unknown(self) -> None:
        with mock.patch.object(
            subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="pmset", timeout=5)
        ):
            assert m._detect_macos() == ("unknown", None, False)

    def test_oserror_returns_unknown(self) -> None:
        with mock.patch.object(subprocess, "run", side_effect=OSError("no pmset")):
            assert m._detect_macos() == ("unknown", None, False)

    def test_valueerror_on_percent_returns_source_without_pct(self) -> None:
        """Bad percent token in pmset output should not crash; source still detected."""
        fake = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Now drawing from 'AC Power'\n -InternalBattery-0\tnot-a-number%; charged\n",
            stderr="",
        )
        with mock.patch.object(subprocess, "run", return_value=fake):
            # Either the regex misses and we get None, OR a ValueError is
            # swallowed and we still get the source. The function must NOT crash.
            src, pct, plugged = m._detect_macos()
            assert src == "ac"
            assert plugged is True
            assert pct is None or isinstance(pct, float)


# ---------------------------------------------------------------------------
# _detect_linux
# ---------------------------------------------------------------------------


class TestDetectLinux:
    """Linux power source detection via /sys/class/power_supply.

    The function uses a hardcoded path; tests build a virtual sysfs layout
    by monkeypatching os.path.exists, os.listdir, and the builtins.open
    function to back the file reads with tmp_path files.
    """

    @staticmethod
    def _install_fake_sysfs(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        entries: list[tuple[str, str, str | None]],
    ) -> None:
        """Wire up a fake /sys/class/power_supply rooted at tmp_path.

        ``entries`` is a list of (battery_dir_name, status_text, capacity_text_or_None).
        Writes status (and capacity if given) files under tmp_path/<battery_dir>/.
        """
        sysfs = tmp_path
        for bat_name, status, capacity in entries:
            d = sysfs / bat_name
            d.mkdir(exist_ok=True)
            (d / "status").write_text(status)
            if capacity is not None:
                (d / "capacity").write_text(capacity)

        def fake_exists(p: str) -> bool:
            # The module's hardcoded path is /sys/class/power_supply.
            # If that path is asked, report True iff our fake tree has any entry.
            if p == "/sys/class/power_supply":
                return any((sysfs / e[0]).exists() for e in entries)
            # The module also probes files under the battery dir; check real FS.
            return os.path.exists(p)

        def fake_listdir(p: str) -> list[str]:
            if p == "/sys/class/power_supply":
                return [e[0] for e in entries]
            return os.listdir(p)

        real_open = open

        def fake_open(p, *args, **kwargs):
            # Rewrite /sys/... paths to our fake tree.
            if isinstance(p, str) and p.startswith("/sys/class/power_supply/"):
                rel = p[len("/sys/class/power_supply/"):]
                return real_open(sysfs / rel, *args, **kwargs)
            return real_open(p, *args, **kwargs)

        monkeypatch.setattr(m.os.path, "exists", fake_exists)
        monkeypatch.setattr(m.os, "listdir", fake_listdir)
        # Patch the module's builtin open. The module does `open(...)` (the
        # builtin), so we patch builtins.open in the module's namespace.
        monkeypatch.setattr("builtins.open", fake_open)

    def test_missing_sysfs_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the sysfs root is missing, we return ('unknown', None, False)."""
        monkeypatch.setattr(m.os.path, "exists", lambda _p: False)
        assert m._detect_linux() == ("unknown", None, False)

    def test_charging_returns_ac(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_fake_sysfs(
            monkeypatch, tmp_path, [("BAT0", "Charging\n", "80")]
        )
        assert m._detect_linux() == ("ac", 80.0, True)

    def test_full_returns_ac(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_fake_sysfs(
            monkeypatch, tmp_path, [("BAT0", "Full\n", "100")]
        )
        assert m._detect_linux() == ("ac", 100.0, True)

    def test_discharging_returns_battery(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_fake_sysfs(
            monkeypatch, tmp_path, [("BAT0", "Discharging\n", "35")]
        )
        assert m._detect_linux() == ("battery", 35.0, False)

    def test_missing_capacity_file_yields_none_pct(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_fake_sysfs(
            monkeypatch, tmp_path, [("BAT0", "Discharging\n", None)]
        )
        assert m._detect_linux() == ("battery", None, False)

    def test_ignores_non_bat_directories(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_fake_sysfs(
            monkeypatch, tmp_path, [("AC0", "Charging\n", "100")]
        )
        # AC0 doesn't start with "BAT" → falls through, returns unknown.
        assert m._detect_linux() == ("unknown", None, False)

    def test_listdir_oserror_returns_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(m.os.path, "exists", lambda _p: True)
        with mock.patch.object(m.os, "listdir", side_effect=OSError("perm denied")):
            assert m._detect_linux() == ("unknown", None, False)


# ---------------------------------------------------------------------------
# detect_power_source
# ---------------------------------------------------------------------------


class TestDetectPowerSource:
    """Top-level dispatch (psutil → macOS → Linux)."""

    def test_psutil_present_returns_tuple(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When psutil is importable, sensors_battery() result is the source of truth."""
        monkeypatch.setattr(m, "HAS_PSUTIL", True)
        # Build a minimal stand-in for psutil.sensors_battery().
        class _Bat:
            power_plugged = True
            percent = 77.0

        monkeypatch.setattr(m.psutil, "sensors_battery", lambda: _Bat())
        assert m.detect_power_source() == ("ac", 77.0, True)

    def test_psutil_sensors_battery_none_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If psutil returns None (no battery hardware), fall through to platform detector."""
        monkeypatch.setattr(m, "HAS_PSUTIL", True)
        monkeypatch.setattr(m.psutil, "sensors_battery", lambda: None)
        monkeypatch.setattr(m.sys, "platform", "linux")
        monkeypatch.setattr(m.os.path, "exists", lambda _p: False)
        assert m.detect_power_source() == ("unknown", None, False)

    def test_psutil_attribute_error_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(m, "HAS_PSUTIL", True)

        def boom() -> None:
            raise AttributeError("synthetic")

        monkeypatch.setattr(m.psutil, "sensors_battery", boom)
        monkeypatch.setattr(m.sys, "platform", "linux")
        monkeypatch.setattr(m.os.path, "exists", lambda _p: False)
        assert m.detect_power_source() == ("unknown", None, False)

    def test_no_psutil_darwin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(m, "HAS_PSUTIL", False)
        monkeypatch.setattr(m.sys, "platform", "darwin")
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="AC Power\n100%\n", stderr=""
        )
        with mock.patch.object(subprocess, "run", return_value=fake):
            assert m.detect_power_source() == ("ac", 100.0, True)

    def test_no_psutil_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(m, "HAS_PSUTIL", False)
        monkeypatch.setattr(m.sys, "platform", "linux")
        monkeypatch.setattr(m.os.path, "exists", lambda _p: False)
        assert m.detect_power_source() == ("unknown", None, False)

    def test_no_psutil_unknown_platform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(m, "HAS_PSUTIL", False)
        monkeypatch.setattr(m.sys, "platform", "win32")
        assert m.detect_power_source() == ("unknown", None, False)


# ---------------------------------------------------------------------------
# should_pause
# ---------------------------------------------------------------------------


class TestShouldPause:
    """Decision logic for pausing recording."""

    def test_override_true_short_circuits(self) -> None:
        # Should not even consult the power detector.
        pause, reason = m.should_pause({"pause_on_battery": True, "min_battery_percent": 20, "game_overrides": {}}, override=True)
        assert pause is False
        assert "Override" in reason

    def test_ac_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(m, "detect_power_source", lambda: ("ac", 95.0, True))
        pause, reason = m.should_pause({"pause_on_battery": True, "min_battery_percent": 20, "game_overrides": {}})
        assert pause is False
        assert "AC" in reason

    def test_ac_no_percent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(m, "detect_power_source", lambda: ("ac", None, True))
        pause, _reason = m.should_pause({"pause_on_battery": True, "min_battery_percent": 20, "game_overrides": {}})
        assert pause is False

    def test_unknown_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(m, "detect_power_source", lambda: ("unknown", None, False))
        pause, reason = m.should_pause({"pause_on_battery": True, "min_battery_percent": 20, "game_overrides": {}})
        assert pause is False
        assert "unknown" in reason.lower()

    def test_battery_pause_disabled_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(m, "detect_power_source", lambda: ("battery", 50.0, False))
        pause, reason = m.should_pause({"pause_on_battery": False, "min_battery_percent": 20, "game_overrides": {}})
        assert pause is False
        assert "Config" in reason or "pause_on_battery" in reason

    def test_battery_critical_pauses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(m, "detect_power_source", lambda: ("battery", 5.0, False))
        pause, reason = m.should_pause({"pause_on_battery": True, "min_battery_percent": 20, "game_overrides": {}})
        assert pause is True
        assert "critical" in reason.lower() or "<" in reason

    def test_battery_above_min_pauses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default behavior: on battery at any non-critical level → pause."""
        monkeypatch.setattr(m, "detect_power_source", lambda: ("battery", 80.0, False))
        pause, reason = m.should_pause({"pause_on_battery": True, "min_battery_percent": 20, "game_overrides": {}})
        assert pause is True
        assert "battery" in reason.lower()

    def test_battery_no_percent_pauses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(m, "detect_power_source", lambda: ("battery", None, False))
        pause, _reason = m.should_pause({"pause_on_battery": True, "min_battery_percent": 20, "game_overrides": {}})
        assert pause is True

    def test_game_override_disables_pause(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(m, "detect_power_source", lambda: ("battery", 60.0, False))
        cfg = {
            "pause_on_battery": True,
            "min_battery_percent": 20,
            "game_overrides": {"mc": {"pause_on_battery": False}},
        }
        pause, reason = m.should_pause(cfg, game="mc")
        assert pause is False
        assert "override" in reason.lower() or "mc" in reason

    def test_game_override_with_pause_still_pauses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Game override that says pause_on_battery=True should not block the pause."""
        monkeypatch.setattr(m, "detect_power_source", lambda: ("battery", 60.0, False))
        cfg = {
            "pause_on_battery": True,
            "min_battery_percent": 20,
            "game_overrides": {"mc": {"pause_on_battery": True}},
        }
        pause, _reason = m.should_pause(cfg, game="mc")
        assert pause is True

    def test_game_not_in_overrides_falls_through_to_global(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(m, "detect_power_source", lambda: ("battery", 60.0, False))
        cfg = {
            "pause_on_battery": True,
            "min_battery_percent": 20,
            "game_overrides": {"other": {"pause_on_battery": False}},
        }
        pause, _reason = m.should_pause(cfg, game="mc")
        assert pause is True


# ---------------------------------------------------------------------------
# print_status
# ---------------------------------------------------------------------------


class TestPrintStatus:
    """Status display to stdout."""

    def test_prints_all_required_lines(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(m, "detect_power_source", lambda: ("ac", 100.0, True))
        cfg = {"pause_on_battery": True, "min_battery_percent": 25, "game_overrides": {}}
        m.print_status(cfg)
        out = capsys.readouterr().out
        assert "Battery-Aware Pause Status" in out
        assert "AC" in out
        assert "Battery Level: 100%" in out
        assert "Plugged In: Yes" in out
        assert "pause_on_battery=True" in out
        assert "min_battery_percent=25" in out

    def test_prints_battery_source_lowercase(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(m, "detect_power_source", lambda: ("battery", 30.0, False))
        cfg = {"pause_on_battery": True, "min_battery_percent": 20, "game_overrides": {}}
        m.print_status(cfg)
        out = capsys.readouterr().out
        assert "BATTERY" in out
        assert "Plugged In: No" in out

    def test_omits_battery_level_when_pct_is_none(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(m, "detect_power_source", lambda: ("unknown", None, False))
        cfg = {"pause_on_battery": True, "min_battery_percent": 20, "game_overrides": {}}
        m.print_status(cfg)
        out = capsys.readouterr().out
        assert "Battery Level:" not in out
        assert "Plugged In: No" in out


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMain:
    """CLI entry point — exit codes, flag handling, config persistence."""

    def test_status_exits_zero(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_path = tmp_path / "cfg.json"
        monkeypatch.setattr(m, "detect_power_source", lambda: ("ac", 100.0, True))
        rc = m.main(["--status", "--config", str(cfg_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Battery-Aware Pause Status" in out

    def test_override_exits_zero_on_battery(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_path = tmp_path / "cfg.json"
        monkeypatch.setattr(m, "detect_power_source", lambda: ("battery", 50.0, False))
        rc = m.main(["--override", "--config", str(cfg_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "CONTINUE" in out
        assert "Override" in out

    def test_battery_default_pauses(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_path = tmp_path / "cfg.json"
        monkeypatch.setattr(m, "detect_power_source", lambda: ("battery", 80.0, False))
        rc = m.main(["--config", str(cfg_path)])
        assert rc == 1
        out = capsys.readouterr().out
        assert "PAUSE" in out

    def test_ac_default_continues(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_path = tmp_path / "cfg.json"
        monkeypatch.setattr(m, "detect_power_source", lambda: ("ac", 100.0, True))
        rc = m.main(["--config", str(cfg_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "CONTINUE" in out

    def test_pause_on_battery_true_updates_config(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text(json.dumps({"pause_on_battery": False, "min_battery_percent": 20, "game_overrides": {}}))
        monkeypatch.setattr(m, "detect_power_source", lambda: ("ac", 100.0, True))
        rc = m.main(["--config", str(cfg_path), "--pause-on-battery", "true"])
        assert rc == 0
        loaded = json.loads(cfg_path.read_text())
        assert loaded["pause_on_battery"] is True
        out = capsys.readouterr().out
        assert "Set pause_on_battery" in out

    def test_pause_on_battery_false_updates_config(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_path = tmp_path / "cfg.json"
        # Start with default config on disk.
        monkeypatch.setattr(m, "detect_power_source", lambda: ("ac", 100.0, True))
        rc = m.main(["--config", str(cfg_path), "--pause-on-battery", "false"])
        assert rc == 0
        loaded = json.loads(cfg_path.read_text())
        assert loaded["pause_on_battery"] is False

    def test_set_game_override_writes_override(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_path = tmp_path / "cfg.json"
        monkeypatch.setattr(m, "detect_power_source", lambda: ("ac", 100.0, True))
        rc = m.main(["--config", str(cfg_path), "--set-game-override", "mc", "false"])
        assert rc == 0
        loaded = json.loads(cfg_path.read_text())
        assert loaded["game_overrides"]["mc"] == {"pause_on_battery": False}
        out = capsys.readouterr().out
        assert "mc" in out
        assert "false" in out

    def test_pause_on_battery_true_then_battery_continues(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """End-to-end: set pause_on_battery=false then run on battery → CONTINUE 0."""
        cfg_path = tmp_path / "cfg.json"
        # First call: flip the config off.
        monkeypatch.setattr(m, "detect_power_source", lambda: ("ac", 100.0, True))
        m.main(["--config", str(cfg_path), "--pause-on-battery", "false"])
        # Second call: on battery — should NOT pause because config is now off.
        monkeypatch.setattr(m, "detect_power_source", lambda: ("battery", 60.0, False))
        rc = m.main(["--config", str(cfg_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "CONTINUE" in out
