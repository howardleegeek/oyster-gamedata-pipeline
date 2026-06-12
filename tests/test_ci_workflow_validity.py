#!/usr/bin/env python3
"""
Test CI workflow validity.

Verifies that the yaml is parseable and checks for common typos.
"""

import re
from pathlib import Path

import pytest

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class TestCIWorkflowValidity:
    """Test that CI workflow files are valid and properly configured."""

    @pytest.fixture
    def repo_root(self):
        """Return the repository root directory path."""
        # This file is in tests/, so repo root is parent
        return Path(__file__).parent.parent

    @pytest.fixture
    def workflow_dir(self, repo_root):
        """Return the .github/workflows directory path."""
        return repo_root / ".github" / "workflows"

    @pytest.fixture
    def pipeline_ci_path(self, workflow_dir):
        """Return the pipeline-ci.yml path."""
        return workflow_dir / "pipeline-ci.yml"

    @pytest.fixture
    def recorder_ci_path(self, workflow_dir):
        """Return the recorder-cargo-check.yml path."""
        return workflow_dir / "recorder-cargo-check.yml"

    def test_workflow_directory_exists(self, workflow_dir):
        """Test that .github/workflows directory exists."""
        assert workflow_dir.exists(), f"Workflow directory {workflow_dir} does not exist"
        assert workflow_dir.is_dir(), f"{workflow_dir} is not a directory"

    def test_pipeline_ci_file_exists(self, pipeline_ci_path):
        """Test that pipeline-ci.yml exists."""
        assert pipeline_ci_path.exists(), f"Pipeline CI file {pipeline_ci_path} does not exist"

    def test_recorder_ci_file_exists(self, recorder_ci_path):
        """Test that recorder-ci.yml exists."""
        assert recorder_ci_path.exists(), f"Recorder CI file {recorder_ci_path} does not exist"

    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_pipeline_ci_yaml_parseable(self, pipeline_ci_path):
        """Test that pipeline-ci.yml is valid YAML."""
        with open(pipeline_ci_path) as f:
            content = yaml.safe_load(f)
        assert content is not None, "Pipeline CI YAML is empty"
        assert "name" in content, "Pipeline CI missing 'name' field"
        assert "jobs" in content, "Pipeline CI missing 'jobs' field"

    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_recorder_ci_yaml_parseable(self, recorder_ci_path):
        """Test that recorder-ci.yml is valid YAML."""
        with open(recorder_ci_path) as f:
            content = yaml.safe_load(f)
        assert content is not None, "Recorder CI YAML is empty"
        assert "name" in content, "Recorder CI missing 'name' field"
        assert "jobs" in content, "Recorder CI missing 'jobs' field"

    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_pipeline_ci_has_required_jobs(self, pipeline_ci_path):
        """Test that pipeline-ci.yml has all required jobs."""
        with open(pipeline_ci_path) as f:
            content = yaml.safe_load(f)

        jobs = content.get("jobs", {})
        required_jobs = ["lint", "test", "security", "audit-smoke"]

        for job_name in required_jobs:
            assert job_name in jobs, f"Pipeline CI missing required job: {job_name}"

    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_pipeline_ci_uses_checkout(self, pipeline_ci_path):
        """Test that pipeline-ci.yml uses actions/checkout."""
        with open(pipeline_ci_path) as f:
            content = f.read()

        assert "actions/checkout" in content, "Pipeline CI should use actions/checkout"

    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_pipeline_ci_uses_pytest(self, pipeline_ci_path):
        """Test that pipeline-ci.yml uses pytest."""
        with open(pipeline_ci_path) as f:
            content = f.read()

        assert "pytest" in content, "Pipeline CI should use pytest"

    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_pipeline_ci_uses_bandit(self, pipeline_ci_path):
        """Test that pipeline-ci.yml uses bandit for security scanning."""
        with open(pipeline_ci_path) as f:
            content = f.read()

        assert "bandit" in content, "Pipeline CI should use bandit for security scanning"

    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_pipeline_ci_uses_ruff(self, pipeline_ci_path):
        """Test that pipeline-ci.yml uses ruff for linting."""
        with open(pipeline_ci_path) as f:
            content = f.read()

        assert "ruff" in content, "Pipeline CI should use ruff for linting"

    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_pipeline_ci_triggers_on_push(self, pipeline_ci_path):
        """Test that pipeline-ci.yml triggers on push."""
        with open(pipeline_ci_path) as f:
            content = yaml.safe_load(f)

        on_config = content.get("on", {})
        assert "push" in on_config, "Pipeline CI should trigger on push"

    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_pipeline_ci_triggers_on_pr(self, pipeline_ci_path):
        """Test that pipeline-ci.yml triggers on pull requests."""
        with open(pipeline_ci_path) as f:
            content = yaml.safe_load(f)

        on_config = content.get("on", {})
        assert "pull_request" in on_config, "Pipeline CI should trigger on pull requests"

    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_recorder_ci_has_cargo_check_job(self, recorder_ci_path):
        """Test that recorder-cargo-check.yml has a cargo-check job."""
        with open(recorder_ci_path) as f:
            content = yaml.safe_load(f)

        jobs = content.get("jobs", {})
        assert "cargo-check" in jobs, "Recorder CI should have cargo-check job"

    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_recorder_ci_uses_cargo(self, recorder_ci_path):
        """Test that recorder-cargo-check.yml uses cargo for Rust builds."""
        with open(recorder_ci_path) as f:
            content = f.read()

        assert "cargo check" in content, "Recorder CI should use cargo check"

    def test_no_common_typos_in_pipeline_ci(self, pipeline_ci_path):
        """Test for common typos in pipeline-ci.yml."""
        with open(pipeline_ci_path) as f:
            content = f.read()

        # Check for common typos (using word boundaries to avoid false positives)
        typo_patterns = [
            (r"\bactons\b", "actions"),
            (r"\bchekout\b", "checkout"),
            (r"\bpyton\b", "python"),
            (r"\bpipytest\b", "pytest"),
            (r"\bbandid\b", "bandit"),
        ]

        for pattern, correct in typo_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            assert match is None, f"Possible typo '{match.group()}' found (should be '{correct}')"

    def test_no_common_typos_in_recorder_ci(self, recorder_ci_path):
        """Test for common typos in recorder-ci.yml."""
        with open(recorder_ci_path) as f:
            content = f.read()

        # Check for common typos (using word boundaries)
        typo_patterns = [
            (r"\bactons\b", "actions"),
            (r"\bchekout\b", "checkout"),
            (r"\bcrago\b", "cargo"),
            (r"\bcust\b", "rust"),
        ]

        for pattern, correct in typo_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            assert match is None, f"Possible typo '{match.group()}' found (should be '{correct}')"

    def test_build_minimal_session_script_exists(self):
        """Test that the build_minimal_session.py script exists."""
        script_path = Path(__file__).parent / "fixtures" / "build_minimal_session.py"
        assert script_path.exists(), f"Build minimal session script {script_path} does not exist"

    def test_build_minimal_session_is_executable_python(self):
        """Test that build_minimal_session.py is valid Python."""
        script_path = Path(__file__).parent / "fixtures" / "build_minimal_session.py"

        with open(script_path) as f:
            content = f.read()

        # This will raise SyntaxError if invalid
        compile(content, script_path, "exec")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
