"""Tests for bin/recorder_motw_unblock.py."""

from __future__ import annotations

from unittest import mock

from bin import recorder_motw_unblock as motw


class TestIsWindows:
    """Cover the platform guard."""

    def test_returns_bool(self):
        assert isinstance(motw.is_windows(), bool)


class TestHasMotw:
    """``has_motw`` should always be False on non-Windows hosts."""

    def test_false_on_non_windows(self, tmp_path):
        target = tmp_path / "x.exe"
        target.write_bytes(b"MZ")
        with mock.patch.object(motw, "is_windows", return_value=False):
            assert motw.has_motw(target) is False

    def test_false_when_ads_missing(self, tmp_path):
        target = tmp_path / "y.exe"
        target.write_bytes(b"MZ")
        with mock.patch.object(motw, "is_windows", return_value=True):
            # No ADS exists on POSIX FS — the open() will raise OSError
            assert motw.has_motw(target) is False


class TestUnblockViaPowershell:
    """The wrapper should swallow CalledProcessError and return False."""

    def test_returns_false_when_powershell_missing(self, tmp_path):
        target = tmp_path / "z.exe"
        target.write_bytes(b"MZ")
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            assert motw.unblock_via_powershell(target) is False


class TestUnblockViaAdsDelete:
    """ADS delete should report False if the stream doesn't exist."""

    def test_returns_false_on_oserror(self, tmp_path):
        target = tmp_path / "no-ads.exe"
        target.write_bytes(b"MZ")
        # The :Zone.Identifier path doesn't exist; os.remove raises OSError.
        assert motw.unblock_via_ads_delete(target) is False


class TestMain:
    """Driver-level CLI exits."""

    def test_exit_3_on_non_windows(self, tmp_path, caplog):
        target = tmp_path / "a.exe"
        target.write_bytes(b"MZ")
        with mock.patch.object(motw, "is_windows", return_value=False):
            assert motw.main([str(target)]) == 3

    def test_exit_1_on_missing_file(self, tmp_path):
        missing = tmp_path / "nope.exe"
        with mock.patch.object(motw, "is_windows", return_value=True):
            assert motw.main([str(missing)]) == 1

    def test_exit_0_when_no_motw(self, tmp_path):
        target = tmp_path / "clean.exe"
        target.write_bytes(b"MZ")
        with (
            mock.patch.object(motw, "is_windows", return_value=True),
            mock.patch.object(motw, "has_motw", return_value=False),
        ):
            assert motw.main([str(target)]) == 0
