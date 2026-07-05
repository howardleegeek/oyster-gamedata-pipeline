"""
Regression tests for silent error swallows in bin/lint_v3_prd_grounded.py.

These tests verify that exception handlers in the ffprobe video/audio
parsing, image spec check, and keycode parsing all bind the exception
and emit a debug log rather than silently swallowing it.
"""

import ast
from pathlib import Path


class TestLintV3PrdGroundedSilentError:
    """Tests for silent error handling in lint_v3_prd_grounded.py."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "lint_v3_prd_grounded.py"
        ).read_text()

    def _find_function(self, tree: ast.Module, name: str) -> ast.FunctionDef:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"function {name} not found")

    def test_targeted_functions_no_bare_except_exception_binding(self):
        """The functions touched by this fix must have all ``except Exception:``
        binding the exception. (Other bare handlers in the module are tracked
        for future rounds, not in scope here.)"""
        source = self._read_source()
        tree = ast.parse(source)
        targeted = {
            "_ffprobe_video_stream",
            "_ffprobe_format_duration",
            "_ffprobe_audio_stream",
            "_check_image_specs",
            "_check_keycode",
            "_check_mouse_camera_alignment",
        }
        bare_lines = []
        for fn_name in targeted:
            fn = self._find_function(tree, fn_name)
            for node in ast.walk(fn):
                if isinstance(node, ast.Try):
                    for handler in node.handlers:
                        if handler.type is not None:
                            type_src = ast.unparse(handler.type)
                            if "Exception" in type_src and handler.name is None:
                                bare_lines.append((fn_name, handler.lineno))
        assert bare_lines == [], (
            f"Found bare 'except Exception:' (no 'as' binding) in "
            f"targeted functions at: {bare_lines}. "
            f"Bind the exception and log it."
        )

    def test_logger_imported(self):
        """A module-level logger must be defined so exceptions can be logged."""
        source = self._read_source()
        assert "import logging" in source
        assert "logger = logging.getLogger" in source

    def test_ffprobe_video_fps_logs_at_debug(self):
        """_ffprobe_video_stream fps parse failure should log at DEBUG."""
        source = self._read_source()
        assert 'fps parse failed' in source
        assert 'r_frame_rate' in source

    def test_ffprobe_video_duration_logs_at_debug(self):
        """_ffprobe_video_stream duration parse failure should log at DEBUG."""
        source = self._read_source()
        assert 'duration parse failed' in source

    def test_ffprobe_format_duration_logs_at_debug(self):
        """_ffprobe_format_duration failure should log at DEBUG with the video path."""
        source = self._read_source()
        assert 'ffprobe format duration failed' in source

    def test_ffprobe_audio_sample_rate_logs_at_debug(self):
        """_ffprobe_audio_stream sample_rate parse failure should log at DEBUG."""
        source = self._read_source()
        assert 'audio sample_rate parse failed' in source

    def test_ffprobe_audio_channels_logs_at_debug(self):
        """_ffprobe_audio_stream channels parse failure should log at DEBUG."""
        source = self._read_source()
        assert 'audio channels parse failed' in source

    def test_ffprobe_audio_duration_logs_at_debug(self):
        """_ffprobe_audio_stream duration parse failure should log at DEBUG."""
        source = self._read_source()
        assert 'audio duration parse failed' in source

    def test_image_open_logs_at_debug(self):
        """_check_image_specs image open failure should log at DEBUG with the path."""
        source = self._read_source()
        assert 'image open failed' in source

    def test_keycode_json_parse_logs_at_debug(self):
        """_check_keycode json parse failure should log at DEBUG with the json path."""
        source = self._read_source()
        assert 'keycode json parse failed' in source

    def test_mouse_camera_alignment_pair_logs_at_debug(self):
        """_check_mouse_camera_alignment pair-parse failure should log at DEBUG."""
        source = self._read_source()
        assert 'Mouse/camera alignment pair parse failed' in source

    def test_targeted_functions_compile(self):
        """The targeted functions must still parse cleanly with the new debug calls."""
        source = self._read_source()
        tree = ast.parse(source)  # will raise SyntaxError if broken
        for fn in (
            "_ffprobe_video_stream",
            "_ffprobe_format_duration",
            "_ffprobe_audio_stream",
            "_check_image_specs",
            "_check_keycode",
            "_check_mouse_camera_alignment",
        ):
            self._find_function(tree, fn)

    def test_module_compiles(self):
        """Module must be valid Python (smoke check)."""
        import py_compile
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            py_compile.compile(
                str(Path(__file__).parent.parent.parent / "bin" / "lint_v3_prd_grounded.py"),
                tmp_path,
                doraise=True,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_inputs_jsonl_line_count_no_bare_except(self):
        """The inputs.jsonl line-count handler in criterion #27 must bind
        the exception (no bare ``except Exception:``)."""
        source = self._read_source()
        tree = ast.parse(source)
        # Find the try block whose body is `line_count = sum(1 for _ in open(...))`
        bare_lines = []
        bound_lines = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is None:
                        continue
                    type_src = ast.unparse(handler.type)
                    if "Exception" not in type_src:
                        continue
                    # Check if handler body touches line_count
                    body_src = ast.unparse(handler)
                    if "line_count" in body_src:
                        if handler.name is None:
                            bare_lines.append(handler.lineno)
                        else:
                            bound_lines.append(handler.lineno)
        assert bare_lines == [], (
            f"inputs.jsonl handler still bare at lines {bare_lines}; "
            f"bind the exception and log it."
        )
        assert bound_lines, (
            "Could not find a bound 'except Exception as ...' for the "
            "inputs.jsonl line-count open()."
        )

    def test_inputs_jsonl_line_count_logs_at_debug(self):
        """The inputs.jsonl open() failure must emit a logger.debug(...) call
        that includes the file path and the exception."""
        source = self._read_source()
        assert "inputs.jsonl line count failed" in source
        # logger.debug must be present near the line_count fallback
        assert "logger.debug" in source

    def test_inputs_jsonl_line_count_preserves_fallback(self):
        """The control flow must still set ``line_count = 0`` after the bound
        except so the LintResult still reports 0 events (not raise)."""
        source = self._read_source()
        # Sanity: the literal `line_count = 0` fallback still exists
        assert "line_count = 0" in source
