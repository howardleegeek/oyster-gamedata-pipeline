"""
Tests for .github/workflows/recorder-cargo-check.yml

Validates:
  - YAML is syntactically valid
  - Required triggers exist (pull_request + workflow_dispatch)
  - Paths include vendor/recorder and the workflow file itself
  - Steps include cargo check (not cargo build / cargo test)
  - Uses ubuntu-latest runner
  - Caches ~/.cargo and vendor/recorder/target
  - Cache key includes Cargo.lock hash
  - Timeout is set (≤ 5 min)
  - PR comment step exists for failure notification
"""

import os

import pytest
import yaml

WORKFLOW_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    ".github",
    "workflows",
    "recorder-cargo-check.yml",
)


@pytest.fixture(scope="module")
def workflow():
    """Load and parse the workflow YAML once for all tests."""
    with open(WORKFLOW_PATH, "r") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# YAML validity
# ---------------------------------------------------------------------------


class TestYamlValidity:
    def test_file_exists(self):
        assert os.path.isfile(WORKFLOW_PATH), f"Workflow file not found at {WORKFLOW_PATH}"

    def test_parses_as_yaml(self, workflow):
        assert isinstance(workflow, dict), "Workflow must be a YAML mapping"

    def test_has_name(self, workflow):
        assert "name" in workflow, "Workflow must have a 'name' field"
        assert len(workflow["name"]) > 0


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


class TestTriggers:
    def test_pull_request_trigger(self, workflow):
        on = workflow.get("on", workflow.get(True, {}))
        assert "pull_request" in on, "Must have pull_request trigger"

    def test_pull_request_branches(self, workflow):
        on = workflow.get("on", workflow.get(True, {}))
        pr = on.get("pull_request", {})
        branches = pr.get("branches", [])
        assert "main" in branches, "PR trigger must target 'main' branch"

    def test_workflow_dispatch_trigger(self, workflow):
        on = workflow.get("on", workflow.get(True, {}))
        assert "workflow_dispatch" in on, "Must have workflow_dispatch trigger"

    def test_paths_include_vendor_recorder(self, workflow):
        on = workflow.get("on", workflow.get(True, {}))
        pr = on.get("pull_request", {})
        paths = pr.get("paths", [])
        assert any(
            "vendor/recorder" in p for p in paths
        ), f"PR paths must include vendor/recorder, got: {paths}"

    def test_paths_include_workflow_file(self, workflow):
        on = workflow.get("on", workflow.get(True, {}))
        pr = on.get("pull_request", {})
        paths = pr.get("paths", [])
        assert any(
            "recorder-cargo-check.yml" in p for p in paths
        ), f"PR paths must include the workflow file itself, got: {paths}"


# ---------------------------------------------------------------------------
# Job configuration
# ---------------------------------------------------------------------------


class TestJobConfig:
    def test_has_jobs(self, workflow):
        assert "jobs" in workflow, "Workflow must define 'jobs'"

    def test_runs_on_ubuntu(self, workflow):
        jobs = workflow["jobs"]
        for job_name, job_def in jobs.items():
            assert (
                job_def.get("runs-on") == "ubuntu-latest"
            ), f"Job '{job_name}' must run on ubuntu-latest"

    def test_timeout_minutes(self, workflow):
        jobs = workflow["jobs"]
        for job_name, job_def in jobs.items():
            timeout = job_def.get("timeout-minutes")
            assert timeout is not None, f"Job '{job_name}' must have timeout-minutes"
            assert timeout <= 5, f"Job '{job_name}' timeout must be ≤ 5 min, got {timeout}"


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


class TestSteps:
    @pytest.fixture(autouse=True)
    def collect_steps(self, workflow):
        """Collect all step dicts from the first job."""
        jobs = workflow["jobs"]
        first_job = next(iter(jobs.values()))
        self.steps = first_job.get("steps", [])

    def test_has_checkout_step(self):
        names = [s.get("name", "") for s in self.steps]
        assert any("checkout" in n.lower() for n in names), "Must have a checkout step"

    def test_has_submodule_init(self):
        run_steps = [s for s in self.steps if "run" in s]
        combined = " ".join(s["run"] for s in run_steps)
        assert "submodule" in combined, "Must initialize submodules"

    def test_has_rust_install(self):
        uses_steps = [s.get("uses", "") for s in self.steps]
        assert any("rust-toolchain" in u for u in uses_steps), "Must install Rust toolchain"

    def test_no_hardcoded_rust_version(self):
        """Must not hardcode a specific Rust version (use stable)."""
        for step in self.steps:
            uses = step.get("uses", "")
            if "rust-toolchain" in uses:
                with_kw = step.get("with", {})
                # 'stable' is fine; a specific version like '1.78.0' is not
                version = with_kw.get("toolchain", "")
                if version:
                    assert version == "stable", f"Rust version should be 'stable', got '{version}'"

    def test_has_cache_step(self):
        uses_steps = [s.get("uses", "") for s in self.steps]
        assert any("actions/cache" in u for u in uses_steps), "Must have a cache step"

    def test_cache_includes_cargo_home(self):
        cache_steps = [s for s in self.steps if "actions/cache" in s.get("uses", "")]
        assert len(cache_steps) > 0
        cache_path = cache_steps[0].get("with", {}).get("path", "")
        assert ".cargo" in cache_path, f"Cache path must include ~/.cargo, got: {cache_path}"

    def test_cache_includes_recorder_target(self):
        cache_steps = [s for s in self.steps if "actions/cache" in s.get("uses", "")]
        assert len(cache_steps) > 0
        cache_path = cache_steps[0].get("with", {}).get("path", "")
        assert (
            "vendor/recorder/target" in cache_path
        ), f"Cache path must include vendor/recorder/target, got: {cache_path}"

    def test_cache_key_includes_cargo_lock_hash(self):
        cache_steps = [s for s in self.steps if "actions/cache" in s.get("uses", "")]
        assert len(cache_steps) > 0
        cache_key = str(cache_steps[0].get("with", {}).get("key", ""))
        assert (
            "Cargo.lock" in cache_key
        ), f"Cache key must include Cargo.lock hash, got: {cache_key}"

    def test_cargo_check_present(self):
        run_steps = [s for s in self.steps if "run" in s]
        combined = " ".join(s["run"] for s in run_steps)
        assert "cargo check" in combined, "Must run 'cargo check'"

    def test_no_cargo_build(self):
        """Must NOT do a full cargo build (only check)."""
        run_steps = [s for s in self.steps if "run" in s]
        combined = " ".join(s["run"] for s in run_steps)
        # Allow 'cargo check' but not standalone 'cargo build'
        # We check for 'cargo build' that is NOT part of 'cargo check'
        assert "cargo build" not in combined, "Must not run 'cargo build' — only 'cargo check'"

    def test_no_cargo_test(self):
        """Must NOT run cargo test (too slow)."""
        run_steps = [s for s in self.steps if "run" in s]
        combined = " ".join(s["run"] for s in run_steps)
        assert "cargo test" not in combined, "Must not run 'cargo test'"

    def test_cargo_check_flags(self):
        """cargo check should use --release --no-default-features."""
        run_steps = [s for s in self.steps if "run" in s]
        combined = " ".join(s["run"] for s in run_steps)
        assert "--release" in combined, "cargo check should use --release flag"
        assert (
            "--no-default-features" in combined
        ), "cargo check should use --no-default-features flag"

    def test_tee_log(self):
        """cargo check output should be tee'd to a log file."""
        run_steps = [s for s in self.steps if "run" in s]
        combined = " ".join(s["run"] for s in run_steps)
        assert "tee" in combined, "cargo check output should be tee'd to a log file"

    def test_pr_comment_on_failure(self):
        """Must have a step that posts a PR comment on failure."""
        failure_steps = [s for s in self.steps if s.get("if", "").startswith("failure()")]
        assert len(failure_steps) > 0, "Must have a step with 'if: failure()' for PR comment"
        # Check it uses github-script or similar to post a comment
        uses = [s.get("uses", "") for s in failure_steps]
        assert any(
            "github-script" in u for u in uses
        ), "Failure step should use actions/github-script to post PR comment"


# ---------------------------------------------------------------------------
# Grep-style checks (as required by acceptance criteria)
# ---------------------------------------------------------------------------


class TestGrepChecks:
    def test_grep_cargo_check_in_workflow(self):
        with open(WORKFLOW_PATH, "r") as fh:
            content = fh.read()
        assert "cargo check" in content, "grep 'cargo check' must match in workflow file"

    def test_grep_vendor_recorder_in_paths(self):
        with open(WORKFLOW_PATH, "r") as fh:
            content = fh.read()
        assert "vendor/recorder" in content, "grep 'vendor/recorder' must match in workflow file"
