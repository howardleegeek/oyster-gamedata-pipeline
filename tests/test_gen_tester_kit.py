"""Tests for scripts/gen_tester_kit.py."""

import importlib.util
import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "gen_tester_kit.py"

# Dynamically load the module to avoid E402
_spec = importlib.util.spec_from_file_location("gen_tester_kit", str(SCRIPT_PATH))
_gen = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_gen)  # type: ignore[union-attr]

DOCS = _gen.DOCS
VERSION = _gen.VERSION
create_kit = _gen.create_kit
get_project_root = _gen.get_project_root


class TestGetProjectRoot:
    def test_returns_parent_of_scripts(self):
        root = get_project_root()
        assert root.name == PROJECT_ROOT.name
        assert (root / "scripts").exists()
        assert (root / "docs").exists()


class TestCreateKit:
    def test_creates_zip_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "test_kit.zip")
            result = create_kit(out_path)
            assert result.exists()
            assert result == Path(out_path)

    def test_zip_contains_all_docs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "test_kit.zip")
            create_kit(out_path)
            with zipfile.ZipFile(out_path, "r") as zf:
                names = zf.namelist()
                for doc in DOCS:
                    expected = Path(doc).name
                    assert expected in names, f"{expected} not in zip"

    def test_zip_contains_exactly_three_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "test_kit.zip")
            create_kit(out_path)
            with zipfile.ZipFile(out_path, "r") as zf:
                assert len(zf.namelist()) == 3

    def test_zip_files_are_valid_markdown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "test_kit.zip")
            create_kit(out_path)
            with zipfile.ZipFile(out_path, "r") as zf:
                for name in zf.namelist():
                    assert name.endswith(".md"), f"{name} is not .md"
                    content = zf.read(name).decode("utf-8")
                    assert len(content) > 0, f"{name} is empty"

    def test_zip_is_deflated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "test_kit.zip")
            create_kit(out_path)
            with zipfile.ZipFile(out_path, "r") as zf:
                for info in zf.infolist():
                    assert info.compress_type == zipfile.ZIP_DEFLATED

    def test_missing_doc_exits_with_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "test_kit.zip")
            with patch.object(_gen, "DOCS", ["docs/NONEXISTENT.md"]):
                with pytest.raises(SystemExit) as exc_info:
                    create_kit(out_path)
                assert exc_info.value.code == 1

    def test_output_path_can_be_any_location(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "sub", "dir")
            os.makedirs(nested)
            out_path = os.path.join(nested, "kit.zip")
            result = create_kit(out_path)
            assert result.exists()


class TestDocsConstraints:
    """Verify the documentation files meet spec constraints."""

    def _doc_path(self, name: str) -> Path:
        return PROJECT_ROOT / "docs" / name

    def test_onboarding_line_count(self):
        path = self._doc_path("TESTER_ONBOARDING.md")
        lines = path.read_text().splitlines()
        assert len(lines) <= 300, f"TESTER_ONBOARDING.md has {len(lines)} lines (>300)"

    def test_faq_line_count(self):
        path = self._doc_path("TESTER_FAQ.md")
        lines = path.read_text().splitlines()
        assert len(lines) <= 300, f"TESTER_FAQ.md has {len(lines)} lines (>300)"

    def test_troubleshooting_line_count(self):
        path = self._doc_path("TESTER_TROUBLESHOOTING.md")
        lines = path.read_text().splitlines()
        assert (
            len(lines) <= 300
        ), f"TESTER_TROUBLESHOOTING.md has {len(lines)} lines (>300)"

    def test_faq_has_ten_questions(self):
        path = self._doc_path("TESTER_FAQ.md")
        content = path.read_text()
        count = sum(1 for i in range(1, 11) if f"Q{i}:" in content)
        assert count == 10, f"FAQ has {count} questions, expected 10"

    def test_troubleshooting_has_five_issues(self):
        path = self._doc_path("TESTER_TROUBLESHOOTING.md")
        content = path.read_text()
        count = sum(1 for i in range(1, 6) if f"Issue {i}:" in content)
        assert count == 5, f"Troubleshooting has {count} issues, expected 5"

    def test_onboarding_has_required_sections(self):
        path = self._doc_path("TESTER_ONBOARDING.md")
        content = path.read_text().lower()
        required = [
            "什么是 oyster gamedata",
            "安装",
            "oauth",
            "自动录制",
            "收入",
            "卸载",
        ]
        for keyword in required:
            assert keyword in content, f"Missing section: {keyword}"

    def test_docs_contain_chinese(self):
        for doc in DOCS:
            path = PROJECT_ROOT / doc
            content = path.read_text()
            has_chinese = any("\u4e00" <= c <= "\u9fff" for c in content)
            assert has_chinese, f"{doc} contains no Chinese characters"


class TestVersion:
    def test_version_is_semver(self):
        parts = VERSION.split(".")
        assert len(parts) == 3
        for part in parts:
            assert part.isdigit(), f"Version part '{part}' is not numeric"
