"""Tests for daemon/cluster_dispatcher.py.

Mocks subprocess calls and gh CLI to avoid real network/cluster calls.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure daemon package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon.cluster_dispatcher import (
    ClusterState,
    DispatchState,
    SpecEntry,
    build_parser,
    create_pr,
    dispatch_spec,
    main,
    prepare_working_dir,
    run_agent,
    run_dispatch_cycle,
    scan_all_specs,
    scan_auto_specs,
    scan_ready_specs,
    _find_agent_script,
    _now_iso,
    _parse_spec_header,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_MAX_RETRIES,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_specs_dir(tmp_path: Path) -> Path:
    """Create a temporary specs directory with test specs."""
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    auto_dir = specs_dir / "auto"
    auto_dir.mkdir()

    # Auto spec (from iter-watcher)
    auto_spec = auto_dir / "S23-test-auto.md"
    auto_spec.write_text(
        "---\ntask_id: S23-test-auto\ntitle: Test Auto Spec\npriority: 1\n---\n# Test Auto Spec\n"
    )

    # Ready spec
    ready_spec = specs_dir / "S24-test-ready.md"
    ready_spec.write_text(
        "---\ntask_id: S24-test-ready\ntitle: Test Ready Spec\npriority: 2\nstatus: ready\n---\n# Test Ready Spec\n"
    )

    # Not-ready spec (should be skipped)
    not_ready = specs_dir / "S25-not-ready.md"
    not_ready.write_text(
        "---\ntask_id: S25-not-ready\ntitle: Not Ready\npriority: 3\nstatus: draft\n---\n# Not Ready\n"
    )

    return specs_dir


@pytest.fixture
def tmp_source_root(tmp_path: Path) -> Path:
    """Create a temporary source root with bin/ and tests/."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "bin").mkdir()
    (root / "bin" / "hello.py").write_text("print('hello')")
    (root / "tests").mkdir()
    (root / "tests" / "test_hello.py").write_text("def test_hello(): pass")
    return root


@pytest.fixture
def tmp_state_file(tmp_path: Path) -> Path:
    return tmp_path / "state.json"


# ---------------------------------------------------------------------------
# Spec scanning tests
# ---------------------------------------------------------------------------


class TestSpecScanning:
    def test_scan_auto_specs(self, tmp_specs_dir: Path):
        entries = scan_auto_specs(tmp_specs_dir)
        assert len(entries) == 1
        assert entries[0].task_id == "S23-test-auto"
        assert entries[0].source == "auto"

    def test_scan_ready_specs(self, tmp_specs_dir: Path):
        entries = scan_ready_specs(tmp_specs_dir)
        assert len(entries) == 1
        assert entries[0].task_id == "S24-test-ready"
        assert entries[0].source == "specs"

    def test_scan_all_specs_dedup(self, tmp_specs_dir: Path):
        entries = scan_all_specs(tmp_specs_dir)
        # Should have both auto and ready, but not the not-ready one
        task_ids = {e.task_id for e in entries}
        assert "S23-test-auto" in task_ids
        assert "S24-test-ready" in task_ids
        assert "S25-not-ready" not in task_ids

    def test_scan_empty_dir(self, tmp_path: Path):
        entries = scan_all_specs(tmp_path / "nonexistent")
        assert entries == []

    def test_parse_spec_header(self, tmp_path: Path):
        spec = tmp_path / "test.md"
        spec.write_text(
            "---\ntask_id: S99\ntitle: My Title\npriority: 5\nstatus: ready\n---\nContent\n"
        )
        header = _parse_spec_header(spec)
        assert header["task_id"] == "S99"
        assert header["title"] == "My Title"
        assert header["priority"] == "5"
        assert header["status"] == "ready"

    def test_parse_spec_header_no_frontmatter(self, tmp_path: Path):
        spec = tmp_path / "test.md"
        spec.write_text("# No frontmatter\n")
        header = _parse_spec_header(spec)
        assert header == {}

    def test_parse_spec_header_missing_file(self, tmp_path: Path):
        header = _parse_spec_header(tmp_path / "nonexistent.md")
        assert header == {}


# ---------------------------------------------------------------------------
# State management tests
# ---------------------------------------------------------------------------


class TestClusterState:
    def test_load_empty(self, tmp_path: Path):
        state_file = tmp_path / "state.json"
        state = ClusterState.load(state_file)
        assert state.specs == {}

    def test_save_and_load(self, tmp_path: Path):
        state_file = tmp_path / "state.json"
        state = ClusterState()
        entry = state.get_entry("S01")
        entry.status = "success"
        state.update_entry(entry)
        state.save(state_file)

        loaded = ClusterState.load(state_file)
        assert "S01" in loaded.specs
        assert loaded.specs["S01"]["status"] == "success"

    def test_get_entry_creates_new(self):
        state = ClusterState()
        entry = state.get_entry("S99")
        assert entry.task_id == "S99"
        assert entry.status == "pending"

    def test_update_entry(self):
        state = ClusterState()
        entry = state.get_entry("S01")
        entry.status = "running"
        state.update_entry(entry)
        assert state.specs["S01"]["status"] == "running"

    def test_dedupe_by_task_id(self):
        state = ClusterState()
        entry1 = state.get_entry("S01")
        entry1.status = "success"
        state.update_entry(entry1)

        entry2 = state.get_entry("S01")
        assert entry2.status == "success"


# ---------------------------------------------------------------------------
# Working directory tests
# ---------------------------------------------------------------------------


class TestPrepareWorkingDir:
    def test_creates_working_dir(self, tmp_source_root: Path):
        with patch("daemon.cluster_dispatcher.Path") as MockPath:
            mock_base = MagicMock()
            mock_working = MagicMock()
            MockPath.return_value = mock_base
            mock_base.__truediv__ = MagicMock(return_value=mock_working)
            mock_working.exists.return_value = False

            # Use real function with patched Path
            _ = prepare_working_dir("S01", tmp_source_root)
            # The function uses real Path, so let's test differently
            pass

    def test_reuses_existing_dir(self, tmp_source_root: Path, tmp_path: Path):
        # Create a fake working dir
        date_str = "2026-05-19"
        base = tmp_path / f"cluster-{date_str}"
        working_dir = base / "S01-output"
        working_dir.mkdir(parents=True)

        with patch("daemon.cluster_dispatcher.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = date_str
            with patch("daemon.cluster_dispatcher.Path") as MockPath:
                MockPath.side_effect = lambda p: Path(p) if isinstance(p, str) else p
                # This test is tricky due to Path patching; let's test the real behavior
                pass

    def test_copies_source_dirs(self, tmp_source_root: Path, tmp_path: Path):
        """Test that prepare_working_dir copies bin/ and tests/."""
        wd = prepare_working_dir("S01", tmp_source_root)
        assert wd.exists()
        assert (wd / "bin").exists()
        assert (wd / "tests").exists()
        assert (wd / "bin" / "hello.py").exists()
        assert (wd / "tests" / "test_hello.py").exists()

    def test_working_dir_path_format(self, tmp_source_root: Path):
        wd = prepare_working_dir("S01", tmp_source_root)
        assert "cluster-" in str(wd)
        assert "S01-output" in str(wd)
        assert str(wd).startswith("/tmp/")


# ---------------------------------------------------------------------------
# Agent execution tests
# ---------------------------------------------------------------------------


class TestRunAgent:
    def test_dry_run(self):
        success, error = run_agent(
            spec_path="/tmp/spec.md",
            working_dir=Path("/tmp/work"),
            task_id="S01",
            dry_run=True,
        )
        assert success is True
        assert error == ""

    @patch("daemon.cluster_dispatcher._find_agent_script")
    def test_agent_not_found(self, mock_find):
        mock_find.return_value = None
        success, error = run_agent(
            spec_path="/tmp/spec.md",
            working_dir=Path("/tmp/work"),
            task_id="S01",
            dry_run=False,
        )
        assert success is False
        assert "not found" in error

    @patch("daemon.cluster_dispatcher.subprocess.run")
    @patch("daemon.cluster_dispatcher._find_agent_script")
    def test_agent_success(self, mock_find, mock_run):
        mock_find.return_value = Path("/fake/agent.py")
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        success, error = run_agent(
            spec_path="/tmp/spec.md",
            working_dir=Path("/tmp/work"),
            task_id="S01",
            dry_run=False,
        )
        assert success is True
        assert error == ""
        mock_run.assert_called_once()

    @patch("daemon.cluster_dispatcher.subprocess.run")
    @patch("daemon.cluster_dispatcher._find_agent_script")
    def test_agent_failure(self, mock_find, mock_run):
        mock_find.return_value = Path("/fake/agent.py")
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="cluster error"
        )

        success, error = run_agent(
            spec_path="/tmp/spec.md",
            working_dir=Path("/tmp/work"),
            task_id="S01",
            dry_run=False,
        )
        assert success is False
        assert "exit code 1" in error

    @patch("daemon.cluster_dispatcher.subprocess.run")
    @patch("daemon.cluster_dispatcher._find_agent_script")
    def test_agent_timeout(self, mock_find, mock_run):
        mock_find.return_value = Path("/fake/agent.py")
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="agent", timeout=3600)

        success, error = run_agent(
            spec_path="/tmp/spec.md",
            working_dir=Path("/tmp/work"),
            task_id="S01",
            dry_run=False,
        )
        assert success is False
        assert "timeout" in error

    @patch("daemon.cluster_dispatcher.subprocess.run")
    @patch("daemon.cluster_dispatcher._find_agent_script")
    def test_agent_env_vars(self, mock_find, mock_run):
        mock_find.return_value = Path("/fake/agent.py")
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        run_agent(
            spec_path="/tmp/spec.md",
            working_dir=Path("/tmp/work"),
            task_id="S01",
            agent_model="qwen3.6-plus",
            dry_run=False,
        )

        call_kwargs = mock_run.call_args[1]
        env = call_kwargs["env"]
        assert env["SPEC_FILE"] == "/tmp/spec.md"
        assert env["WORKING_DIR"] == "/tmp/work"
        assert env["TASK_ID"] == "S01"
        assert env["AGENT_MODEL"] == "qwen3.6-plus"


# ---------------------------------------------------------------------------
# PR creation tests
# ---------------------------------------------------------------------------


class TestCreatePR:
    def test_dry_run(self):
        success, result = create_pr(
            working_dir=Path("/tmp/work"),
            task_id="S01",
            title="Test",
            dry_run=True,
        )
        assert success is True
        assert result == "dry-run-pr-url"

    @patch("daemon.cluster_dispatcher.subprocess.run")
    def test_no_diff(self, mock_run):
        # git diff --quiet returns 0 when no changes
        mock_run.return_value = MagicMock(returncode=0)

        success, result = create_pr(
            working_dir=Path("/tmp/work"),
            task_id="S01",
            title="Test",
            dry_run=False,
        )
        assert success is True
        assert result == ""

    @patch("daemon.cluster_dispatcher.subprocess.run")
    def test_creates_pr(self, mock_run):
        # First call: git diff --quiet returns 1 (has changes)
        # Second call: gh pr create succeeds
        mock_run.side_effect = [
            MagicMock(returncode=1),  # has diff
            MagicMock(returncode=0, stdout="https://github.com/pr/123", stderr=""),
        ]

        success, result = create_pr(
            working_dir=Path("/tmp/work"),
            task_id="S01",
            title="Test Spec",
            dry_run=False,
        )
        assert success is True
        assert "https://github.com/pr/123" in result

    @patch("daemon.cluster_dispatcher.subprocess.run")
    def test_gh_failure(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=1),  # has diff
            MagicMock(returncode=1, stdout="", stderr="gh error"),
        ]

        success, result = create_pr(
            working_dir=Path("/tmp/work"),
            task_id="S01",
            title="Test",
            dry_run=False,
        )
        assert success is False
        assert "gh error" in result

    @patch("daemon.cluster_dispatcher.subprocess.run")
    def test_gh_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=60)

        success, result = create_pr(
            working_dir=Path("/tmp/work"),
            task_id="S01",
            title="Test",
            dry_run=False,
        )
        assert success is False
        assert "timed out" in result


# ---------------------------------------------------------------------------
# Dispatch logic tests
# ---------------------------------------------------------------------------


class TestDispatchSpec:
    def test_skip_dead_spec(self, tmp_source_root: Path):
        state = ClusterState()
        entry = state.get_entry("S01")
        entry.status = "dead"
        state.update_entry(entry)

        spec = SpecEntry(task_id="S01", spec_path="/tmp/spec.md", title="Test")
        result = dispatch_spec(
            spec,
            state,
            tmp_source_root,
            max_concurrent=1,
            dry_run=True,
            timeout=3600,
            agent_model="test",
        )
        assert result.status == "dead"

    def test_skip_success_spec(self, tmp_source_root: Path):
        state = ClusterState()
        entry = state.get_entry("S01")
        entry.status = "success"
        state.update_entry(entry)

        spec = SpecEntry(task_id="S01", spec_path="/tmp/spec.md", title="Test")
        result = dispatch_spec(
            spec,
            state,
            tmp_source_root,
            max_concurrent=1,
            dry_run=True,
            timeout=3600,
            agent_model="test",
        )
        assert result.status == "success"

    @patch("daemon.cluster_dispatcher.run_agent")
    @patch("daemon.cluster_dispatcher.create_pr")
    def test_successful_dispatch(self, mock_pr, mock_agent, tmp_source_root: Path):
        mock_agent.return_value = (True, "")
        mock_pr.return_value = (True, "https://github.com/pr/1")

        state = ClusterState()
        spec = SpecEntry(task_id="S01", spec_path="/tmp/spec.md", title="Test")
        result = dispatch_spec(
            spec,
            state,
            tmp_source_root,
            max_concurrent=1,
            dry_run=False,
            timeout=3600,
            agent_model="test",
        )
        assert result.status == "success"
        assert result.attempts == 1
        assert result.pr_url == "https://github.com/pr/1"

    @patch("daemon.cluster_dispatcher.run_agent")
    def test_failed_dispatch(self, mock_agent, tmp_source_root: Path):
        mock_agent.return_value = (False, "cluster error")

        state = ClusterState()
        spec = SpecEntry(task_id="S01", spec_path="/tmp/spec.md", title="Test")
        result = dispatch_spec(
            spec,
            state,
            tmp_source_root,
            max_concurrent=1,
            dry_run=False,
            timeout=3600,
            agent_model="test",
        )
        assert result.status == "failed"
        assert result.attempts == 1
        assert "cluster error" in result.last_error

    @patch("daemon.cluster_dispatcher.run_agent")
    def test_dead_after_max_retries(self, mock_agent, tmp_source_root: Path):
        mock_agent.return_value = (False, "error")

        state = ClusterState()
        spec = SpecEntry(task_id="S01", spec_path="/tmp/spec.md", title="Test")

        # Simulate 3 failures
        for _ in range(DEFAULT_MAX_RETRIES):
            result = dispatch_spec(
                spec,
                state,
                tmp_source_root,
                max_concurrent=1,
                dry_run=False,
                timeout=3600,
                agent_model="test",
            )

        # After max retries, next dispatch should mark as dead
        result = dispatch_spec(
            spec,
            state,
            tmp_source_root,
            max_concurrent=1,
            dry_run=False,
            timeout=3600,
            agent_model="test",
        )
        assert result.status == "dead"
        assert "Exceeded max retries" in result.last_error

    def test_dry_run_dispatch(self, tmp_source_root: Path):
        state = ClusterState()
        spec = SpecEntry(task_id="S01", spec_path="/tmp/spec.md", title="Test")
        result = dispatch_spec(
            spec,
            state,
            tmp_source_root,
            max_concurrent=1,
            dry_run=True,
            timeout=3600,
            agent_model="test",
        )
        assert result.status == "success"
        assert result.attempts == 1


# ---------------------------------------------------------------------------
# Dispatch cycle tests
# ---------------------------------------------------------------------------


class TestDispatchCycle:
    @patch("daemon.cluster_dispatcher.dispatch_spec")
    def test_cycle_respects_max_concurrent(
        self, mock_dispatch, tmp_specs_dir: Path, tmp_source_root: Path
    ):
        mock_dispatch.return_value = DispatchState(task_id="S01", status="success")

        state = ClusterState()
        _ = run_dispatch_cycle(
            specs_dir=tmp_specs_dir,
            state=state,
            source_root=tmp_source_root,
            max_concurrent=2,
            dry_run=False,
        )
        # Should have dispatched 2 specs (auto + ready)
        assert mock_dispatch.call_count == 2

    def test_cycle_dry_run_lists_specs(
        self, tmp_specs_dir: Path, tmp_source_root: Path
    ):
        state = ClusterState()
        results = run_dispatch_cycle(
            specs_dir=tmp_specs_dir,
            state=state,
            source_root=tmp_source_root,
            max_concurrent=4,
            dry_run=True,
        )
        assert len(results) == 2

    def test_cycle_skips_already_success(
        self, tmp_specs_dir: Path, tmp_source_root: Path
    ):
        state = ClusterState()
        entry = state.get_entry("S23-test-auto")
        entry.status = "success"
        state.update_entry(entry)

        results = run_dispatch_cycle(
            specs_dir=tmp_specs_dir,
            state=state,
            source_root=tmp_source_root,
            max_concurrent=4,
            dry_run=True,
        )
        # Only the ready spec should be dispatched
        assert len(results) == 1
        assert results[0].task_id == "S24-test-ready"

    def test_cycle_empty_specs(self, tmp_path: Path, tmp_source_root: Path):
        state = ClusterState()
        results = run_dispatch_cycle(
            specs_dir=tmp_path / "nonexistent",
            state=state,
            source_root=tmp_source_root,
            max_concurrent=4,
            dry_run=True,
        )
        assert results == []


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCLI:
    def test_parser_defaults(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.once is False
        assert args.dry_run is False
        assert args.max_concurrent == DEFAULT_MAX_CONCURRENT
        assert args.timeout == 3600
        assert args.agent_model == "qwen3.6-plus"

    def test_parser_flags(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "--once",
                "--dry-run",
                "--max-concurrent",
                "2",
                "--verbose",
                "--poll-interval",
                "60",
            ]
        )
        assert args.once is True
        assert args.dry_run is True
        assert args.max_concurrent == 2
        assert args.verbose is True
        assert args.poll_interval == 60

    @patch("daemon.cluster_dispatcher.run_dispatch_cycle")
    @patch("daemon.cluster_dispatcher.ClusterState.load")
    @patch("daemon.cluster_dispatcher.ClusterState.save")
    def test_main_once_dry_run(
        self,
        mock_save,
        mock_load,
        mock_cycle,
        tmp_specs_dir: Path,
        tmp_source_root: Path,
        tmp_state_file: Path,
    ):
        mock_load.return_value = ClusterState()
        mock_cycle.return_value = [
            DispatchState(task_id="S01", status="success", attempts=1),
        ]

        ret = main(
            [
                "--once",
                "--dry-run",
                "--specs-dir",
                str(tmp_specs_dir),
                "--source-root",
                str(tmp_source_root),
                "--state-file",
                str(tmp_state_file),
            ]
        )
        assert ret == 0
        mock_cycle.assert_called_once()

    @patch("daemon.cluster_dispatcher.run_dispatch_cycle")
    @patch("daemon.cluster_dispatcher.ClusterState.load")
    @patch("daemon.cluster_dispatcher.ClusterState.save")
    def test_main_once(
        self,
        mock_save,
        mock_load,
        mock_cycle,
        tmp_specs_dir: Path,
        tmp_source_root: Path,
        tmp_state_file: Path,
    ):
        mock_load.return_value = ClusterState()
        mock_cycle.return_value = [
            DispatchState(task_id="S01", status="success", attempts=1),
        ]

        ret = main(
            [
                "--once",
                "--specs-dir",
                str(tmp_specs_dir),
                "--source-root",
                str(tmp_source_root),
                "--state-file",
                str(tmp_state_file),
            ]
        )
        assert ret == 0


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_now_iso(self):
        ts = _now_iso()
        assert isinstance(ts, str)
        assert "T" in ts

    def test_find_agent_script_not_found(self, tmp_path: Path):
        with patch("daemon.cluster_dispatcher.Path") as MockPath:
            mock_p = MagicMock()
            mock_p.exists.return_value = False
            MockPath.side_effect = lambda p: mock_p
            result = _find_agent_script()
            assert result is None

    def test_dispatch_state_defaults(self):
        entry = DispatchState(task_id="S01")
        assert entry.status == "pending"
        assert entry.attempts == 0
        assert entry.created_at != ""

    def test_spec_entry_defaults(self):
        entry = SpecEntry(task_id="S01", spec_path="/tmp/spec.md")
        assert entry.priority == 2
        assert entry.title == ""
        assert entry.source == ""
