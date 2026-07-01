#!/usr/bin/env python3
"""Tests for bin/buyer_dashboard_html.py — Static HTML dashboard generator."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add bin to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bin"))

import buyer_dashboard_html as dashboard


class TestClipAnalyzerInit:
    """Tests for ClipAnalyzer constructor."""

    def test_stores_paths_as_pathlib(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            assert isinstance(analyzer.clip_path, Path)
            assert isinstance(analyzer.output_path, Path)
            assert analyzer.clip_path == clip
            assert analyzer.output_path == out

    def test_initial_state_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            assert analyzer.preview_frames == []
            assert analyzer.lint_results == {}
            assert analyzer.diversity_stats == {}


class TestValidateClip:
    """Tests for ClipAnalyzer.validate_clip."""

    def test_returns_true_for_existing_clip(self, capsys):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            assert analyzer.validate_clip() is True
            captured = capsys.readouterr()
            assert captured.err == ""

    def test_returns_false_for_missing_clip(self, capsys):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "missing.mp4"
            out = Path(tmpdir) / "out.html"
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            assert analyzer.validate_clip() is False
            captured = capsys.readouterr()
            assert "Error" in captured.err
            assert "missing.mp4" in captured.err


class TestExtractPreviewFrames:
    """Tests for ClipAnalyzer.extract_preview_frames."""

    def test_default_num_frames(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            assert analyzer.extract_preview_frames() is True
            assert len(analyzer.preview_frames) == 6

    def test_custom_num_frames(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            assert analyzer.extract_preview_frames(num_frames=3) is True
            assert len(analyzer.preview_frames) == 3

    def test_timestamps_are_floats_in_unit_interval(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            analyzer.extract_preview_frames(num_frames=4)
            for img_data, ts in analyzer.preview_frames:
                assert isinstance(ts, float)
                assert 0.0 <= ts < 1.0

    def test_find_spec_returns_none_still_produces_svg(self):
        # find_spec returns None (not raises) when spec is missing.
        # The source code's try/except ImportError does NOT catch this case,
        # so the SVG branch is taken even when PIL is not installed.
        # This is documented current behavior (a separate fix would be needed
        # to make the placeholder branch reachable).
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            with patch("buyer_dashboard_html.importlib.util.find_spec", return_value=None):
                assert analyzer.extract_preview_frames(num_frames=2) is True
            for img_data, ts in analyzer.preview_frames:
                # None branch is unreachable via find_spec; SVG is produced.
                assert img_data is not None
                assert "<svg" in img_data

    def test_pil_spec_present_uses_svg(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            # find_spec returning a non-None mock should trigger SVG path
            mock_spec = object()
            with patch("buyer_dashboard_html.importlib.util.find_spec", return_value=mock_spec):
                assert analyzer.extract_preview_frames(num_frames=2) is True
            for img_data, ts in analyzer.preview_frames:
                assert img_data is not None
                assert "<svg" in img_data


class TestRunLintAnalysis:
    """Tests for ClipAnalyzer.run_lint_analysis."""

    def test_returns_dict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            result = analyzer.run_lint_analysis()
            assert isinstance(result, dict)

    def test_lint_results_assigned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            analyzer.run_lint_analysis()
            assert analyzer.lint_results != {}
            assert "summary" in analyzer.lint_results
            assert "issues" in analyzer.lint_results
            assert "metadata" in analyzer.lint_results

    def test_summary_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            result = analyzer.run_lint_analysis()
            summary = result["summary"]
            for key in ("total_issues", "critical", "warning", "info"):
                assert key in summary
                assert isinstance(summary[key], int)

    def test_issues_have_required_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            result = analyzer.run_lint_analysis()
            for issue in result["issues"]:
                assert "type" in issue
                assert "description" in issue
                assert "timestamp" in issue

    def test_metadata_has_analyzed_at(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            result = analyzer.run_lint_analysis()
            assert "analyzed_at" in result["metadata"]


class TestCalculateDiversityStats:
    """Tests for ClipAnalyzer.calculate_diversity_stats."""

    def test_returns_dict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            result = analyzer.calculate_diversity_stats()
            assert isinstance(result, dict)

    def test_has_color_composition_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            result = analyzer.calculate_diversity_stats()
            assert "color" in result
            assert "composition" in result
            assert "summary" in result

    def test_palette_is_list_of_strings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            result = analyzer.calculate_diversity_stats()
            palette = result["color"]["palette"]
            assert isinstance(palette, list)
            assert len(palette) > 0
            for c in palette:
                assert isinstance(c, str)
                assert c.startswith("#")

    def test_overall_diversity_score_in_range(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            result = analyzer.calculate_diversity_stats()
            score = result["summary"]["overall_diversity_score"]
            assert 0.0 <= score <= 1.0


class TestFormatTimestamp:
    """Tests for ClipAnalyzer.format_timestamp."""

    def test_zero_seconds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            assert analyzer.format_timestamp(0) == "00:00"

    def test_under_one_minute(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            assert analyzer.format_timestamp(45.7) == "00:45"

    def test_over_one_minute(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            assert analyzer.format_timestamp(125) == "02:05"

    def test_exact_minute(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            assert analyzer.format_timestamp(60) == "01:00"

    def test_truncates_fractional_seconds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            # 59.9 should floor to 59
            assert analyzer.format_timestamp(59.9) == "00:59"


class TestGenerateHtml:
    """Tests for ClipAnalyzer.generate_html."""

    def _make_analyzer_with_data(self, tmpdir, with_svg=True):
        clip = Path(tmpdir) / "in.mp4"
        out = Path(tmpdir) / "out.html"
        clip.write_text("fake")
        analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
        if with_svg:
            analyzer.extract_preview_frames(num_frames=2)
        else:
            analyzer.preview_frames = [(None, 0.0), (None, 0.5)]
        analyzer.run_lint_analysis()
        analyzer.calculate_diversity_stats()
        return analyzer

    def test_starts_with_doctype(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            analyzer = self._make_analyzer_with_data(tmpdir)
            html = analyzer.generate_html()
            assert html.startswith("<!DOCTYPE html>")

    def test_ends_with_closing_html(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            analyzer = self._make_analyzer_with_data(tmpdir)
            html = analyzer.generate_html()
            assert html.rstrip().endswith("</html>")

    def test_includes_lint_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            analyzer = self._make_analyzer_with_data(tmpdir)
            html = analyzer.generate_html()
            assert "analyzed_at" in html
            assert "1920x1080" in html

    def test_includes_color_palette(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            analyzer = self._make_analyzer_with_data(tmpdir)
            html = analyzer.generate_html()
            # palette colors should appear
            assert "#2E4057" in html

    def test_includes_svg_frames_when_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            analyzer = self._make_analyzer_with_data(tmpdir, with_svg=True)
            html = analyzer.generate_html()
            assert "<svg" in html

    def test_renders_placeholder_when_no_img_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            analyzer = self._make_analyzer_with_data(tmpdir, with_svg=False)
            html = analyzer.generate_html()
            assert "placeholder" in html
            assert "Frame 1" in html

    def test_uses_alert_classes_for_issues(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            analyzer = self._make_analyzer_with_data(tmpdir)
            html = analyzer.generate_html()
            assert "alert-warning" in html
            assert "alert-info" in html

    def test_score_is_rendered_as_percentage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            analyzer = self._make_analyzer_with_data(tmpdir)
            html = analyzer.generate_html()
            # 0.73 * 100 = 73%
            assert "73%" in html

    def test_includes_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            analyzer = self._make_analyzer_with_data(tmpdir)
            html = analyzer.generate_html()
            assert "bootstrap" in html.lower()


class TestGenerateReport:
    """Tests for ClipAnalyzer.generate_report."""

    def test_success_writes_html_file(self, capsys):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "subdir" / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            assert analyzer.generate_report() is True
            assert out.exists()
            content = out.read_text(encoding="utf-8")
            assert "<!DOCTYPE html>" in content
            captured = capsys.readouterr()
            assert "Analyzing" in captured.err
            assert "Report saved" in captured.err

    def test_failure_does_not_write_file(self, capsys):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "missing.mp4"
            out = Path(tmpdir) / "out.html"
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            assert analyzer.generate_report() is False
            assert not out.exists()

    def test_creates_output_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "deep" / "nested" / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            assert analyzer.generate_report() is True
            assert out.exists()

    def test_populates_preview_frames_and_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            analyzer = dashboard.ClipAnalyzer(str(clip), str(out))
            analyzer.generate_report()
            assert len(analyzer.preview_frames) == 6
            assert analyzer.lint_results != {}
            assert analyzer.diversity_stats != {}


class TestMain:
    """Tests for main CLI entry point."""

    def test_success_returns_zero(self, capsys):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            rc = dashboard.main(["prog", "--clip", str(clip), "--output", str(out)])
            assert rc == 0
            assert out.exists()

    def test_missing_clip_returns_one(self, capsys):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "missing.mp4"
            out = Path(tmpdir) / "out.html"
            rc = dashboard.main(["prog", "--clip", str(clip), "--output", str(out)])
            assert rc == 1

    def test_missing_required_args_returns_one(self, capsys):
        rc = dashboard.main(["prog"])
        assert rc == 1

    def test_custom_frames_argument(self, capsys):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            rc = dashboard.main(
                [
                    "prog",
                    "--clip",
                    str(clip),
                    "--output",
                    str(out),
                    "--frames",
                    "3",
                ]
            )
            assert rc == 0
            assert out.exists()
            # Read the HTML and count Frame occurrences
            content = out.read_text(encoding="utf-8")
            assert content.count("Frame ") >= 3

    def test_short_flags(self, capsys):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            rc = dashboard.main(["prog", "-c", str(clip), "-o", str(out)])
            assert rc == 0
            assert out.exists()

    def test_short_frames_flag(self, capsys):
        with tempfile.TemporaryDirectory() as tmpdir:
            clip = Path(tmpdir) / "in.mp4"
            out = Path(tmpdir) / "out.html"
            clip.write_text("fake")
            rc = dashboard.main(
                [
                    "prog",
                    "-c",
                    str(clip),
                    "-o",
                    str(out),
                    "-f",
                    "4",
                ]
            )
            assert rc == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
